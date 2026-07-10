from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
import models
from auth import hash_password
from services import insight_generation
from tests.conftest import TestSessionLocal


def _day(date: str, completed: bool = True) -> dict:
    return {
        "date": date,
        "workoutType": "endurance",
        "title": f"Ride {date}",
        "description": "Endurance ride",
        "durationMinutes": 60,
        "completed": completed,
    }


async def _create_user_with_history(
    *,
    email: str,
    activity_count: int,
    onboarded: bool = True,
) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Test Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=onboarded,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, [_day("2026-06-08")])
        for i in range(activity_count):
            db.add(
                models.RideMetric(
                    user_id=user.id,
                    strava_activity_id=70000 + i,
                    activity_source="strava",
                    external_activity_id=str(70000 + i),
                    activity_date=f"2026-05-{(i % 28) + 1:02d}",
                    activity_start_datetime=f"2026-05-{(i % 28) + 1:02d}T08:00:00",
                    activity_name=f"Ride {i}",
                    sport_type="Ride",
                    duration_seconds=3600,
                    tss=60.0,
                )
            )
        await db.commit()
        return user.id


async def _facts(user_id: str) -> list[models.AthleteMemoryFact]:
    async with TestSessionLocal() as db:
        return await crud.list_athlete_memory_facts(db, user_id, include_inactive=True)


@pytest.fixture(autouse=True)
def _stub_derive_athlete_model(monkeypatch):
    """Keep the long-term-model derivation from hitting the network by default.

    Tests that care about it override this with their own stub.
    """

    async def _noop(metrics_section, *, current_model=None, provider="openai"):
        return None

    monkeypatch.setattr(
        insight_generation.ai_service, "derive_athlete_model", _noop
    )


_SAMPLE_CANDIDATES = [
    {
        "fact": "Performs best after one recovery day",
        "category": "fatigue_response",
        "confidence": 0.6,
        "source_snippet": "Strong sessions on 2026-05-03 and 2026-05-10 both followed a rest day.",
    },
    {
        "fact": "Tolerates heat well",
        "category": "general",
        "confidence": 0.5,
        "source_snippet": "Held target power on warm-weather rides.",
    },
]


@pytest.mark.asyncio
async def test_generates_and_stores_insights(monkeypatch):
    user_id = await _create_user_with_history(
        email="insights@example.com", activity_count=10
    )

    async def fake_generate(metrics_section, *, existing_facts, provider):
        assert metrics_section  # history context is built and passed through
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        insight_generation.ai_service, "generate_athlete_insights", fake_generate
    )

    result = await insight_generation.run_insight_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 3, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.generated == 2
    assert result.failed == 0
    stored = {fact.fact for fact in await _facts(user_id)}
    assert "Performs best after one recovery day" in stored
    assert "Tolerates heat well" in stored


@pytest.mark.asyncio
async def test_skips_users_with_too_little_history(monkeypatch):
    await _create_user_with_history(
        email="thin@example.com",
        activity_count=insight_generation.INSIGHT_MIN_ACTIVITIES - 1,
    )
    called = False

    async def fake_generate(*args, **kwargs):
        nonlocal called
        called = True
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        insight_generation.ai_service, "generate_athlete_insights", fake_generate
    )

    result = await insight_generation.run_insight_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 3, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.skipped == 1
    assert result.generated == 0
    assert called is False


@pytest.mark.asyncio
async def test_existing_facts_are_passed_to_the_model(monkeypatch):
    user_id = await _create_user_with_history(
        email="existing@example.com", activity_count=10
    )
    async with TestSessionLocal() as db:
        await crud.observe_athlete_memory_fact(
            db,
            user_id,
            fact="Prefers morning rides",
            category="preferred_workouts",
        )
        await db.commit()

    seen_existing: list[str] = []

    async def fake_generate(metrics_section, *, existing_facts, provider):
        seen_existing.extend(existing_facts)
        return []

    monkeypatch.setattr(
        insight_generation.ai_service, "generate_athlete_insights", fake_generate
    )

    await insight_generation.run_insight_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 3, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert "Prefers morning rides" in seen_existing


@pytest.mark.asyncio
async def test_rerun_strengthens_existing_insight(monkeypatch):
    user_id = await _create_user_with_history(
        email="strengthen@example.com", activity_count=10
    )

    async def fake_generate(metrics_section, *, existing_facts, provider):
        return [_SAMPLE_CANDIDATES[0]]

    monkeypatch.setattr(
        insight_generation.ai_service, "generate_athlete_insights", fake_generate
    )

    await insight_generation.run_insight_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 3, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )
    await insight_generation.run_insight_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 15, 3, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    facts = [f for f in await _facts(user_id) if f.fact == _SAMPLE_CANDIDATES[0]["fact"]]
    assert len(facts) == 1
    assert facts[0].observation_count == 2


@pytest.mark.asyncio
async def test_isolates_per_user_failures(monkeypatch):
    failing_id = await _create_user_with_history(
        email="fail@example.com", activity_count=10
    )
    ok_id = await _create_user_with_history(
        email="ok@example.com", activity_count=10
    )

    async def fake_generate(metrics_section, *, existing_facts, provider):
        # The failing user has the earliest id-ordered email; branch on stored facts
        return _SAMPLE_CANDIDATES

    original = crud.observe_athlete_memory_fact

    async def flaky_observe(db, user_id, **kwargs):
        if user_id == failing_id:
            raise RuntimeError("boom")
        return await original(db, user_id, **kwargs)

    monkeypatch.setattr(
        insight_generation.ai_service, "generate_athlete_insights", fake_generate
    )
    monkeypatch.setattr(
        insight_generation.crud, "observe_athlete_memory_fact", flaky_observe
    )

    result = await insight_generation.run_insight_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 3, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 2
    assert result.failed == 1
    assert {f.fact for f in await _facts(ok_id)}
    assert not await _facts(failing_id)


@pytest.mark.asyncio
async def test_derives_and_stores_long_term_athlete_model(monkeypatch):
    user_id = await _create_user_with_history(
        email="model@example.com", activity_count=10
    )

    async def fake_generate(metrics_section, *, existing_facts, provider):
        return []

    seen_current: list[object] = []

    async def fake_derive(metrics_section, *, current_model=None, provider="openai"):
        assert metrics_section
        seen_current.append(current_model)
        return {
            "ftp_watts": 262,
            "vo2max": 58.0,
            "pacing_quality": "even pacing on long efforts",
            "recovery_ability": "recovers fast",
            "threshold_durability": "holds threshold ~30 min",
            "heat_tolerance": "fades above 28C",
            "preferred_training_style": "structured intervals",
            "strengths": ["threshold", "consistency"],
            "weaknesses": ["sprint"],
            "risk_factors": ["ramps volume quickly"],
            "summary": "Durable threshold rider.",
            "confidence": 0.7,
        }

    monkeypatch.setattr(
        insight_generation.ai_service, "generate_athlete_insights", fake_generate
    )
    monkeypatch.setattr(
        insight_generation.ai_service, "derive_athlete_model", fake_derive
    )

    await insight_generation.run_insight_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 3, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    async with TestSessionLocal() as db:
        model = await crud.get_athlete_model(db, user_id)
    assert model is not None
    assert model.ftp_watts == 262
    assert model.threshold_durability == "holds threshold ~30 min"
    assert model.strengths == ["threshold", "consistency"]
    assert model.confidence == 0.7
    # No model existed on the first run.
    assert seen_current == [None]


@pytest.mark.asyncio
async def test_model_derivation_failure_keeps_insights(monkeypatch):
    user_id = await _create_user_with_history(
        email="modelfail@example.com", activity_count=10
    )

    async def fake_generate(metrics_section, *, existing_facts, provider):
        return [_SAMPLE_CANDIDATES[0]]

    async def boom(metrics_section, *, current_model=None, provider="openai"):
        raise RuntimeError("derive exploded")

    monkeypatch.setattr(
        insight_generation.ai_service, "generate_athlete_insights", fake_generate
    )
    monkeypatch.setattr(
        insight_generation.ai_service, "derive_athlete_model", boom
    )

    result = await insight_generation.run_insight_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 3, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.failed == 0
    assert {f.fact for f in await _facts(user_id)} == {_SAMPLE_CANDIDATES[0]["fact"]}
    async with TestSessionLocal() as db:
        assert await crud.get_athlete_model(db, user_id) is None


def test_seconds_until_next_weekly_run_same_day():
    seconds = insight_generation.seconds_until_next_weekly_run(
        datetime(2026, 6, 8, 1, 0, tzinfo=timezone.utc),  # Monday 01:00
        timezone_name="UTC",
    )
    assert seconds == 2 * 60 * 60


def test_seconds_until_next_weekly_run_rolls_to_next_week():
    seconds = insight_generation.seconds_until_next_weekly_run(
        datetime(2026, 6, 10, 3, 0, tzinfo=timezone.utc),  # Wednesday 03:00
        timezone_name="UTC",
    )
    # Next Monday (2026-06-15) 03:00 → 5 days out.
    assert seconds == 5 * 24 * 60 * 60
