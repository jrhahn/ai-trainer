"""Tests for the weekly athlete open-question generation job (#385)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
import models
from auth import hash_password
from services import open_question_generation
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
                    strava_activity_id=90000 + i,
                    activity_source="strava",
                    external_activity_id=str(90000 + i),
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


async def _questions(user_id: str) -> list[models.AthleteOpenQuestion]:
    async with TestSessionLocal() as db:
        return await crud.list_athlete_open_questions(
            db, user_id, include_resolved=True
        )


_SAMPLE_CANDIDATES = [
    {
        "question": "Is FTP underestimated?",
        "category": "general",
        "evidence": "Recent VO2 intervals held above threshold.",
        "needs": "30-minute threshold test.",
        "resolved": False,
        "resolution": "",
    },
    {
        "question": "Does the MTB position improve sustainable power?",
        "category": "preferred_workouts",
        "evidence": "MTB NP trended higher on matched climbs.",
        "needs": "Same power pedals on both bikes.",
        "resolved": False,
        "resolution": "",
    },
]


@pytest.mark.asyncio
async def test_generates_and_stores_open_questions(monkeypatch):
    user_id = await _create_user_with_history(
        email="oq@example.com", activity_count=10
    )

    async def fake_generate(
        metrics_section, *, existing_facts, existing_questions, provider
    ):
        assert metrics_section
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        open_question_generation.ai_service,
        "generate_open_questions",
        fake_generate,
    )

    result = await open_question_generation.run_open_question_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 6, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.generated == 2
    assert result.failed == 0
    stored = {q.question for q in await _questions(user_id)}
    assert "Is FTP underestimated?" in stored
    assert "Does the MTB position improve sustainable power?" in stored
    assert all(q.status == "open" for q in await _questions(user_id))


@pytest.mark.asyncio
async def test_skips_users_with_too_little_history(monkeypatch):
    await _create_user_with_history(
        email="thinoq@example.com",
        activity_count=open_question_generation.OPEN_QUESTION_MIN_ACTIVITIES - 1,
    )
    called = False

    async def fake_generate(*args, **kwargs):
        nonlocal called
        called = True
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        open_question_generation.ai_service,
        "generate_open_questions",
        fake_generate,
    )

    result = await open_question_generation.run_open_question_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 6, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.skipped == 1
    assert result.generated == 0
    assert called is False


@pytest.mark.asyncio
async def test_existing_knowledge_is_passed_to_the_model(monkeypatch):
    user_id = await _create_user_with_history(
        email="existingoq@example.com", activity_count=10
    )
    async with TestSessionLocal() as db:
        await crud.observe_athlete_memory_fact(
            db,
            user_id,
            fact="Prefers morning rides",
            category="preferred_workouts",
        )
        await crud.record_athlete_open_question(
            db, user_id, question="Is FTP underestimated?"
        )
        await db.commit()

    seen_facts: list[str] = []
    seen_questions: list[str] = []

    async def fake_generate(
        metrics_section, *, existing_facts, existing_questions, provider
    ):
        seen_facts.extend(existing_facts)
        seen_questions.extend(existing_questions)
        return []

    monkeypatch.setattr(
        open_question_generation.ai_service,
        "generate_open_questions",
        fake_generate,
    )

    await open_question_generation.run_open_question_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 6, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert "Prefers morning rides" in seen_facts
    assert "Is FTP underestimated?" in seen_questions


@pytest.mark.asyncio
async def test_rerun_accrues_evidence_on_existing_question(monkeypatch):
    user_id = await _create_user_with_history(
        email="accrueoq@example.com", activity_count=10
    )

    async def fake_generate(
        metrics_section, *, existing_facts, existing_questions, provider
    ):
        return [_SAMPLE_CANDIDATES[0]]

    monkeypatch.setattr(
        open_question_generation.ai_service,
        "generate_open_questions",
        fake_generate,
    )

    for week in (8, 15):
        await open_question_generation.run_open_question_generation(
            TestSessionLocal,
            now=datetime(2026, 6, week, 6, 5, tzinfo=timezone.utc),
            timezone_name="UTC",
        )

    stored = [
        q
        for q in await _questions(user_id)
        if q.question == _SAMPLE_CANDIDATES[0]["question"]
    ]
    assert len(stored) == 1
    assert stored[0].evidence_count == 2


@pytest.mark.asyncio
async def test_isolates_per_user_failures(monkeypatch):
    failing_id = await _create_user_with_history(
        email="failoq@example.com", activity_count=10
    )
    ok_id = await _create_user_with_history(
        email="okoq@example.com", activity_count=10
    )

    async def fake_generate(
        metrics_section, *, existing_facts, existing_questions, provider
    ):
        return _SAMPLE_CANDIDATES

    original = crud.record_athlete_open_question

    async def flaky_record(db, user_id, **kwargs):
        if user_id == failing_id:
            raise RuntimeError("boom")
        return await original(db, user_id, **kwargs)

    monkeypatch.setattr(
        open_question_generation.ai_service,
        "generate_open_questions",
        fake_generate,
    )
    monkeypatch.setattr(
        open_question_generation.crud, "record_athlete_open_question", flaky_record
    )

    result = await open_question_generation.run_open_question_generation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 6, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 2
    assert result.failed == 1
    assert {q.question for q in await _questions(ok_id)}
    assert not await _questions(failing_id)


def test_seconds_until_next_weekly_run_uses_monday_06():
    seconds = open_question_generation.seconds_until_next_weekly_run(
        datetime(2026, 6, 8, 1, 0, tzinfo=timezone.utc),  # Monday 01:00
        timezone_name="UTC",
        weekday=open_question_generation.OPEN_QUESTION_GENERATION_WEEKDAY,
        hour=open_question_generation.OPEN_QUESTION_GENERATION_HOUR,
    )
    assert seconds == 5 * 60 * 60
