from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
import models
from auth import hash_password
from services import hypothesis_generation
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
                    strava_activity_id=80000 + i,
                    activity_source="strava",
                    external_activity_id=str(80000 + i),
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


async def _hypotheses(user_id: str) -> list[models.AthleteHypothesis]:
    async with TestSessionLocal() as db:
        return await crud.list_athlete_hypotheses(db, user_id, include_resolved=True)


_SAMPLE_CANDIDATES = [
    {
        "statement": "Upper-body strength suppresses next-day HR response",
        "category": "fatigue_response",
        "confidence": 0.38,
        "rationale": "HR ~8 bpm low the day after gym sessions on 05-03 and 05-10.",
    },
    {
        "statement": "Rides stronger in the second half of a block",
        "category": "general",
        "confidence": 0.4,
        "rationale": "Power trended up across the last two blocks.",
    },
]


@pytest.mark.asyncio
async def test_generates_and_stores_hypotheses(monkeypatch):
    user_id = await _create_user_with_history(
        email="hypo@example.com", activity_count=10
    )

    async def fake_generate(
        metrics_section, *, existing_facts, existing_hypotheses, provider
    ):
        assert metrics_section  # history context is built and passed through
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        hypothesis_generation.ai_service,
        "generate_athlete_hypotheses",
        fake_generate,
    )

    result = await hypothesis_generation.run_hypothesis_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 5, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.generated == 2
    assert result.failed == 0
    stored = {h.statement for h in await _hypotheses(user_id)}
    assert "Upper-body strength suppresses next-day HR response" in stored
    assert "Rides stronger in the second half of a block" in stored
    assert all(h.status == "proposed" for h in await _hypotheses(user_id))


@pytest.mark.asyncio
async def test_skips_users_with_too_little_history(monkeypatch):
    await _create_user_with_history(
        email="thin@example.com",
        activity_count=hypothesis_generation.HYPOTHESIS_MIN_ACTIVITIES - 1,
    )
    called = False

    async def fake_generate(*args, **kwargs):
        nonlocal called
        called = True
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        hypothesis_generation.ai_service,
        "generate_athlete_hypotheses",
        fake_generate,
    )

    result = await hypothesis_generation.run_hypothesis_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 5, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.skipped == 1
    assert result.generated == 0
    assert called is False


@pytest.mark.asyncio
async def test_existing_knowledge_is_passed_to_the_model(monkeypatch):
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
        await crud.propose_athlete_hypothesis(
            db,
            user_id,
            statement="Fuels poorly on long rides",
            category="fueling_hydration",
        )
        await db.commit()

    seen_facts: list[str] = []
    seen_hypotheses: list[str] = []

    async def fake_generate(
        metrics_section, *, existing_facts, existing_hypotheses, provider
    ):
        seen_facts.extend(existing_facts)
        seen_hypotheses.extend(existing_hypotheses)
        return []

    monkeypatch.setattr(
        hypothesis_generation.ai_service,
        "generate_athlete_hypotheses",
        fake_generate,
    )

    await hypothesis_generation.run_hypothesis_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 5, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert "Prefers morning rides" in seen_facts
    assert "Fuels poorly on long rides" in seen_hypotheses


@pytest.mark.asyncio
async def test_rerun_strengthens_existing_hypothesis(monkeypatch):
    user_id = await _create_user_with_history(
        email="strengthen@example.com", activity_count=10
    )

    async def fake_generate(
        metrics_section, *, existing_facts, existing_hypotheses, provider
    ):
        return [_SAMPLE_CANDIDATES[0]]

    monkeypatch.setattr(
        hypothesis_generation.ai_service,
        "generate_athlete_hypotheses",
        fake_generate,
    )

    await hypothesis_generation.run_hypothesis_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 5, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )
    await hypothesis_generation.run_hypothesis_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 15, 5, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    stored = [
        h
        for h in await _hypotheses(user_id)
        if h.statement == _SAMPLE_CANDIDATES[0]["statement"]
    ]
    assert len(stored) == 1
    assert stored[0].evidence_count == 2


@pytest.mark.asyncio
async def test_isolates_per_user_failures(monkeypatch):
    failing_id = await _create_user_with_history(
        email="fail@example.com", activity_count=10
    )
    ok_id = await _create_user_with_history(
        email="ok@example.com", activity_count=10
    )

    async def fake_generate(
        metrics_section, *, existing_facts, existing_hypotheses, provider
    ):
        return _SAMPLE_CANDIDATES

    original = crud.propose_athlete_hypothesis

    async def flaky_propose(db, user_id, **kwargs):
        if user_id == failing_id:
            raise RuntimeError("boom")
        return await original(db, user_id, **kwargs)

    monkeypatch.setattr(
        hypothesis_generation.ai_service,
        "generate_athlete_hypotheses",
        fake_generate,
    )
    monkeypatch.setattr(
        hypothesis_generation.crud, "propose_athlete_hypothesis", flaky_propose
    )

    result = await hypothesis_generation.run_hypothesis_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 5, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 2
    assert result.failed == 1
    assert {h.statement for h in await _hypotheses(ok_id)}
    assert not await _hypotheses(failing_id)


def test_seconds_until_next_weekly_run_uses_monday_05():
    seconds = hypothesis_generation.seconds_until_next_weekly_run(
        datetime(2026, 6, 8, 1, 0, tzinfo=timezone.utc),  # Monday 01:00
        timezone_name="UTC",
        weekday=hypothesis_generation.HYPOTHESIS_GENERATION_WEEKDAY,
        hour=hypothesis_generation.HYPOTHESIS_GENERATION_HOUR,
    )
    assert seconds == 4 * 60 * 60
