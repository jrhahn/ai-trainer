from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
import models
from auth import hash_password
from services import experiment_suggestion
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


async def _create_user(*, email: str, onboarded: bool = True) -> str:
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
        await db.commit()
        return user.id


async def _add_hypothesis(user_id: str, **kwargs) -> None:
    async with TestSessionLocal() as db:
        await crud.propose_athlete_hypothesis(db, user_id, **kwargs)
        await db.commit()


async def _experiments(user_id: str) -> list[models.AthleteExperiment]:
    async with TestSessionLocal() as db:
        return await crud.list_athlete_experiments(
            db, user_id, include_resolved=True
        )


_SAMPLE_CANDIDATES = [
    {
        "question": "Does upper-body strength suppress next-day HR response?",
        "protocol": "Repeat the gym session and compare HR on the next easy ride.",
        "rationale": "A clear HR drop the following day confirms the idea.",
        "category": "fatigue_response",
    },
    {
        "question": "Which bike is faster for the same power?",
        "protocol": "Compare both bikes over the same climb using identical pedals.",
        "rationale": "Whichever is quicker at equal power wins.",
        "category": "general",
    },
]


@pytest.mark.asyncio
async def test_suggests_and_stores_experiments(monkeypatch):
    user_id = await _create_user(email="exp@example.com")
    await _add_hypothesis(
        user_id,
        statement="Upper-body strength suppresses next-day HR response",
        category="fatigue_response",
        rationale="HR ran low after two gym sessions.",
    )

    async def fake_generate(uncertainties_section, *, existing_experiments, provider):
        assert "Upper-body strength" in uncertainties_section
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        experiment_suggestion.ai_service,
        "generate_validation_experiments",
        fake_generate,
    )

    result = await experiment_suggestion.run_experiment_suggestion(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc),
    )

    assert result.checked == 1
    assert result.generated == 2
    assert result.failed == 0
    protocols = {e.protocol for e in await _experiments(user_id)}
    assert (
        "Compare both bikes over the same climb using identical pedals." in protocols
    )
    assert all(e.status == "suggested" for e in await _experiments(user_id))


@pytest.mark.asyncio
async def test_links_experiment_to_matching_hypothesis(monkeypatch):
    user_id = await _create_user(email="link@example.com")
    async with TestSessionLocal() as db:
        hypothesis = await crud.propose_athlete_hypothesis(
            db,
            user_id,
            statement="Upper-body strength suppresses next-day HR response",
            category="fatigue_response",
        )
        await db.commit()
        hypothesis_id = hypothesis.id

    async def fake_generate(uncertainties_section, *, existing_experiments, provider):
        return [
            {
                "question": "Upper-body strength suppresses next-day HR response",
                "protocol": "Repeat the gym session and compare next-day HR.",
                "rationale": "A drop confirms it.",
                "category": "fatigue_response",
            }
        ]

    monkeypatch.setattr(
        experiment_suggestion.ai_service,
        "generate_validation_experiments",
        fake_generate,
    )

    await experiment_suggestion.run_experiment_suggestion(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc),
    )

    stored = await _experiments(user_id)
    assert len(stored) == 1
    assert stored[0].hypothesis_id == hypothesis_id


@pytest.mark.asyncio
async def test_skips_users_without_open_hypotheses(monkeypatch):
    await _create_user(email="nohypo@example.com")
    called = False

    async def fake_generate(*args, **kwargs):
        nonlocal called
        called = True
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        experiment_suggestion.ai_service,
        "generate_validation_experiments",
        fake_generate,
    )

    result = await experiment_suggestion.run_experiment_suggestion(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc),
    )

    assert result.checked == 1
    assert result.skipped == 1
    assert result.generated == 0
    assert called is False


@pytest.mark.asyncio
async def test_rerun_refreshes_existing_experiment(monkeypatch):
    user_id = await _create_user(email="rerun@example.com")
    await _add_hypothesis(
        user_id, statement="Fuels poorly on long rides", category="fueling_hydration"
    )

    async def fake_generate(uncertainties_section, *, existing_experiments, provider):
        return [_SAMPLE_CANDIDATES[1]]

    monkeypatch.setattr(
        experiment_suggestion.ai_service,
        "generate_validation_experiments",
        fake_generate,
    )

    await experiment_suggestion.run_experiment_suggestion(
        TestSessionLocal, now=datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc)
    )
    await experiment_suggestion.run_experiment_suggestion(
        TestSessionLocal, now=datetime(2026, 6, 15, 6, 0, tzinfo=timezone.utc)
    )

    stored = await _experiments(user_id)
    assert len(stored) == 1


@pytest.mark.asyncio
async def test_isolates_per_user_failures(monkeypatch):
    failing_id = await _create_user(email="fail-exp@example.com")
    ok_id = await _create_user(email="ok-exp@example.com")
    await _add_hypothesis(failing_id, statement="A", category="general")
    await _add_hypothesis(ok_id, statement="B", category="general")

    async def fake_generate(uncertainties_section, *, existing_experiments, provider):
        return _SAMPLE_CANDIDATES

    original = crud.suggest_athlete_experiment

    async def flaky_suggest(db, user_id, **kwargs):
        if user_id == failing_id:
            raise RuntimeError("boom")
        return await original(db, user_id, **kwargs)

    monkeypatch.setattr(
        experiment_suggestion.ai_service,
        "generate_validation_experiments",
        fake_generate,
    )
    monkeypatch.setattr(
        experiment_suggestion.crud, "suggest_athlete_experiment", flaky_suggest
    )

    result = await experiment_suggestion.run_experiment_suggestion(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 6, 0, tzinfo=timezone.utc),
    )

    assert result.checked == 2
    assert result.failed == 1
    assert {e.protocol for e in await _experiments(ok_id)}
    assert not await _experiments(failing_id)


def test_seconds_until_next_weekly_run_uses_monday_06():
    seconds = experiment_suggestion.seconds_until_next_weekly_run(
        datetime(2026, 6, 8, 1, 0, tzinfo=timezone.utc),  # Monday 01:00
        timezone_name="UTC",
        weekday=experiment_suggestion.EXPERIMENT_SUGGESTION_WEEKDAY,
        hour=experiment_suggestion.EXPERIMENT_SUGGESTION_HOUR,
    )
    assert seconds == 5 * 60 * 60
