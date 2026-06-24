import glob
import os
import sys
from unittest.mock import AsyncMock

# On NixOS, the dynamic linker reads LD_LIBRARY_PATH only at process startup,
# so os.environ changes made at runtime never reach dlopen().  If we haven't
# already re-exec'd with the correct path, do it now — before any C extensions
# (greenlet, aiosqlite …) are loaded.
if not os.environ.get("_PYTEST_NIXOS_REEXEC"):
    _candidates = sorted(glob.glob("/nix/store/*/lib/libstdc++.so.6"))
    if _candidates:
        _lib_dir = os.path.dirname(_candidates[0])
        _env = {**os.environ, "LD_LIBRARY_PATH": _lib_dir, "_PYTEST_NIXOS_REEXEC": "1"}
        os.execvpe(sys.executable, [sys.executable, "-m", "pytest"] + sys.argv[1:], _env)

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./pytest.db")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-bytes-long")
os.environ.setdefault("STRAVA_CLIENT_ID", "test-client-id")
os.environ.setdefault("STRAVA_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("FRONTEND_URL", "http://localhost:5173")
os.environ.setdefault("BACKEND_URL", "http://localhost:8000")
os.environ.setdefault("OPENAI_API_KEY", "test-openai")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini")

from database import Base, get_db  # noqa: E402
from main import app  # noqa: E402
import routers.ai as ai_router  # noqa: E402
import routers.intervals as intervals_router  # noqa: E402
import services.ai_service as ai_service  # noqa: E402

TEST_DATABASE_URL = os.environ["DATABASE_URL"]
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


app.dependency_overrides[get_db] = override_get_db
# Redirect the background task's direct session factory to the test database
ai_router.async_session_maker = TestSessionLocal
intervals_router.async_session_maker = TestSessionLocal


@pytest_asyncio.fixture(autouse=True)
async def reset_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient):
    payload = {
        "name": "Test Rider",
        "email": "rider@example.com",
        "password": "Str0ng!Pass",
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_ai_service(monkeypatch):
    mocks = {
        "analyse_strava_activities": AsyncMock(
            return_value={
                "estimatedFTP": 280,
                "riderType": "allrounder",
                "notes": "Balanced rider",
                "rideInsights": "Your last ride was an endurance effort at 68% FTP. No intervals detected.",
                "lastRideFeedback": "Great endurance ride! You held 188W avg (68% FTP) for 90 minutes with stable HR. Next session, try adding 2×20 min at 85% FTP to build your tempo base.",
            }
        ),
        "analyse_fit_activity": AsyncMock(
            return_value={
                "estimatedFTP": 210,
                "riderType": "endurance",
                "notes": "Solid aerobic base from .fit upload.",
                "rideInsights": "Steady effort across the session.",
                "lastRideFeedback": "Good steady effort. Keep building that aerobic base.",
            }
        ),
        "generate_training_plan": AsyncMock(
            return_value=[
                {
                    "date": "2026-04-10",
                    "workoutType": "endurance",
                    "title": "Endurance Ride",
                    "description": "Steady aerobic ride",
                    "durationMinutes": 90,
                }
            ]
        ),
        "adapt_training_plan": AsyncMock(
            return_value=[
                {
                    "date": "2026-04-10",
                    "workoutType": "recovery",
                    "title": "Recovery Spin",
                    "description": "Easy spin",
                    "durationMinutes": 45,
                }
            ]
        ),
        "ask_trainer": AsyncMock(
            return_value={
                "response": "Take it easy tomorrow.",
                "plan_updates": [
                    {
                        "date": "2026-04-10",
                        "workoutType": "rest",
                        "title": "Rest Day",
                        "description": "Full rest",
                        "durationMinutes": 0,
                    }
                ],
                "sources": [],
            }
        ),
        "race_event_feedback": AsyncMock(
            return_value="That race fits well; add climbing work and a short taper. Want me to adapt the plan?"
        ),
        "update_coach_memory": AsyncMock(return_value="Prefers morning workouts."),
        "extract_athlete_facts": AsyncMock(return_value=[]),
        "rate_completed_workout": AsyncMock(
            return_value={
                "feedback": "Strong execution overall.",
                "flag_for_adaptation": False,
                "needs_athlete_feedback": False,
                "follow_up_question": None,
                "suggested_feedback_tags": [],
            }
        ),
        "recommend_next_session": AsyncMock(
            return_value={
                "response": "Keep the next ride easy.",
                "next_session_recommendation": "Keep the next session as planned.",
                "recommendation_type": "keep_as_planned",
                "plan_updates": None,
            }
        ),
        "classify_question": AsyncMock(
            return_value={"category": "plan_query", "needs_science_rag": False}
        ),
        "batch_review_rides": AsyncMock(return_value="Good training block."),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(ai_service, name, mock)
    return mocks
