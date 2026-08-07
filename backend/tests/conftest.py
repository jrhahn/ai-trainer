import asyncio
import atexit
import glob
import os
import sys
import tempfile
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

_DB_FILE_PREFIX = "ai-trainer-pytest-"
_DB_SIDECAR_SUFFIXES = ("", "-journal", "-wal", "-shm")


def _stale_db_files(directory: str) -> list[str]:
    """Return leftover test databases whose pytest process is gone (#520).

    A run that is killed (tool timeout, Ctrl-C) never reaches :mod:`atexit`, so
    its database file survives.  It is harmless — no live run shares a name with
    it — but sweeping it keeps the directory from filling up over months.
    """
    stale: list[str] = []
    for path in glob.glob(os.path.join(directory, f"{_DB_FILE_PREFIX}*.db")):
        pid = os.path.basename(path)[len(_DB_FILE_PREFIX) : -len(".db")]
        try:
            os.kill(int(pid), 0)
        except (ValueError, ProcessLookupError):
            stale.append(path)
        except PermissionError:  # someone else's live process
            continue
    return stale


def _test_database_url() -> str:
    """Pick a private, RAM-backed sqlite file for this pytest process (#520).

    The suite used to hard-code ``./pytest.db``, which made the database a
    *shared, persistent* resource: a killed run left it corrupted and the next
    run failed with ``no such table`` (looking exactly like a schema regression,
    cf. #496), and two concurrent runs gave each other ``disk I/O error``.  A
    per-process file removes the sharing, and putting it in ``/dev/shm`` removes
    the fsync — measured at roughly two thirds of the fixture cost, since the
    schema is rebuilt on every test.

    ``/dev/shm`` is Linux-only, so anything else falls back to the temp
    directory; ``DATABASE_URL`` still wins over both.
    """
    directory = "/dev/shm"
    if not os.access(directory, os.W_OK):
        directory = tempfile.gettempdir()
    for path in _stale_db_files(directory):
        _unlink_db(path)
    return os.path.join(directory, f"{_DB_FILE_PREFIX}{os.getpid()}.db")


def _unlink_db(path: str) -> None:
    for suffix in _DB_SIDECAR_SUFFIXES:
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


_TEST_DB_PATH = _test_database_url()
if not os.environ.get("DATABASE_URL"):
    atexit.register(_unlink_db, _TEST_DB_PATH)
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB_PATH}")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-bytes-long")
os.environ.setdefault("STRAVA_CLIENT_ID", "test-client-id")
os.environ.setdefault("STRAVA_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("FRONTEND_URL", "http://localhost:5173")
os.environ.setdefault("BACKEND_URL", "http://localhost:8000")
os.environ.setdefault("OPENAI_API_KEY", "test-openai")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini")

from database import Base, get_db, make_session_dependency  # noqa: E402
from main import app  # noqa: E402
import routers.ai as ai_router  # noqa: E402
import routers.intervals as intervals_router  # noqa: E402
import services.ai_service as ai_service  # noqa: E402

TEST_DATABASE_URL = os.environ["DATABASE_URL"]
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


# The real dependency against the test database, not a copy of it: the deferred
# LLM-usage flush lives in that function's ``finally`` (#560), and a hand-rolled
# override would silently not have it.
override_get_db = make_session_dependency(TestSessionLocal)

app.dependency_overrides[get_db] = override_get_db
# Redirect the background task's direct session factory to the test database
ai_router.async_session_maker = TestSessionLocal
intervals_router.async_session_maker = TestSessionLocal


async def _create_schema() -> None:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    # The connections this opened belong to the loop we are about to close, so
    # hand the pool back empty; pytest's own loop then opens its own.
    await test_engine.dispose()


# Once per process, not once per test.  Building the schema means 26 tables and
# 35 indexes, and doing that 1501 times was ~16 of the suite's ~19 minutes (#520).
asyncio.run(_create_schema())

# Children before parents, so the deletes below never trip a foreign key.
_TABLES_NEWEST_FIRST = list(reversed(Base.metadata.sorted_tables))


@pytest_asyncio.fixture(autouse=True)
async def reset_db():
    """Give each test an empty database — by emptying it, not by rebuilding it.

    Deleting rows rather than rolling back a wrapping transaction is deliberate:
    plenty of tests commit for real, and several exercise background tasks that
    open their *own* sessions (see ``ai_router.async_session_maker`` above).  A
    shared outer transaction would have to be threaded through all of them.
    Emptying the tables leaves every one of those paths exactly as it was, so
    this is a speed change and not a semantics change.
    """
    async with test_engine.begin() as conn:
        for table in _TABLES_NEWEST_FIRST:
            await conn.execute(table.delete())
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


@pytest.fixture(autouse=True)
def science_corpus_present(monkeypatch):
    """Pretend the cycling-science corpus is populated.

    The suite runs on SQLite, where ``knowledge_corpus_is_populated`` is always
    False, so without this the classification/RAG gate added in #515 would be
    permanently closed and every test covering that path would silently stop
    exercising it. The gate's own behaviour — both answers — is tested directly
    in ``tests/test_rag.py``.
    """
    import routers.ai as ai_router

    monkeypatch.setattr(
        ai_router, "knowledge_corpus_is_populated", AsyncMock(return_value=True)
    )
