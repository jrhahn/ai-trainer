from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
import models
from auth import hash_password
from services import contradiction_detection
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
            current_ftp=320,
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
                    tss=90.0,
                )
            )
        await db.commit()
        return user.id


async def _seed_fact(user_id: str, fact: str, confidence: float = 0.8) -> str:
    async with TestSessionLocal() as db:
        stored = await crud.observe_athlete_memory_fact(
            db, user_id, fact=fact, category="fitness", confidence=confidence
        )
        await db.commit()
        return stored.id


async def _facts(user_id: str) -> list[models.AthleteMemoryFact]:
    async with TestSessionLocal() as db:
        return await crud.list_athlete_memory_facts(db, user_id, include_inactive=True)


@pytest.mark.asyncio
async def test_flags_contradicted_fact(monkeypatch):
    user_id = await _create_user_with_history(
        email="contradict@example.com", activity_count=6
    )
    fact_id = await _seed_fact(user_id, "FTP is around 320 W")

    async def fake_detect(metrics_section, facts, *, provider):
        assert metrics_section  # history context is built and passed through
        assert facts == ["FTP is around 320 W"]
        return [{"factIndex": 0, "reason": "Held 400 W repeatedly — above 320 W."}]

    monkeypatch.setattr(
        contradiction_detection.ai_service,
        "detect_athlete_fact_contradictions",
        fake_detect,
    )

    result = await contradiction_detection.run_contradiction_detection(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 4, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.flagged == 1
    assert result.failed == 0
    flagged = {f.id: f for f in await _facts(user_id)}[fact_id]
    assert flagged.status == "needs_validation"
    assert "400 W" in (flagged.contradiction_note or "")


@pytest.mark.asyncio
async def test_skips_users_without_checkable_facts(monkeypatch):
    await _create_user_with_history(email="nofacts@example.com", activity_count=6)
    called = False

    async def fake_detect(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        contradiction_detection.ai_service,
        "detect_athlete_fact_contradictions",
        fake_detect,
    )

    result = await contradiction_detection.run_contradiction_detection(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 4, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.skipped == 1
    assert result.flagged == 0
    assert called is False


@pytest.mark.asyncio
async def test_skips_users_with_too_little_history(monkeypatch):
    user_id = await _create_user_with_history(
        email="thin@example.com",
        activity_count=contradiction_detection.CONTRADICTION_MIN_ACTIVITIES - 1,
    )
    await _seed_fact(user_id, "FTP is around 320 W")
    called = False

    async def fake_detect(*args, **kwargs):
        nonlocal called
        called = True
        return [{"factIndex": 0, "reason": "x"}]

    monkeypatch.setattr(
        contradiction_detection.ai_service,
        "detect_athlete_fact_contradictions",
        fake_detect,
    )

    result = await contradiction_detection.run_contradiction_detection(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 4, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.skipped == 1
    assert called is False


@pytest.mark.asyncio
async def test_already_flagged_facts_are_not_rechecked(monkeypatch):
    user_id = await _create_user_with_history(
        email="flagged@example.com", activity_count=6
    )
    fact_id = await _seed_fact(user_id, "FTP is around 320 W")
    async with TestSessionLocal() as db:
        fact = await crud.get_athlete_memory_fact(db, user_id, fact_id)
        await crud.record_athlete_memory_fact_contradiction(
            db, fact, reason="Already flagged."
        )
        await db.commit()

    seen_facts: list[list[str]] = []

    async def fake_detect(metrics_section, facts, *, provider):
        seen_facts.append(facts)
        return []

    monkeypatch.setattr(
        contradiction_detection.ai_service,
        "detect_athlete_fact_contradictions",
        fake_detect,
    )

    result = await contradiction_detection.run_contradiction_detection(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 4, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    # No checkable facts remain, so the model is never consulted.
    assert seen_facts == []
    assert result.skipped == 1


@pytest.mark.asyncio
async def test_isolates_per_user_failures(monkeypatch):
    failing_id = await _create_user_with_history(
        email="fail@example.com", activity_count=6
    )
    ok_id = await _create_user_with_history(email="ok@example.com", activity_count=6)
    await _seed_fact(failing_id, "FTP is around 320 W")
    ok_fact_id = await _seed_fact(ok_id, "FTP is around 320 W")

    async def fake_detect(metrics_section, facts, *, provider):
        return [{"factIndex": 0, "reason": "Evidence disagrees."}]

    original = crud.record_athlete_memory_fact_contradiction

    async def flaky_record(db, fact, **kwargs):
        if fact.user_id == failing_id:
            raise RuntimeError("boom")
        return await original(db, fact, **kwargs)

    monkeypatch.setattr(
        contradiction_detection.ai_service,
        "detect_athlete_fact_contradictions",
        fake_detect,
    )
    monkeypatch.setattr(
        contradiction_detection.crud,
        "record_athlete_memory_fact_contradiction",
        flaky_record,
    )

    result = await contradiction_detection.run_contradiction_detection(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 4, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 2
    assert result.failed == 1
    ok_fact = {f.id: f for f in await _facts(ok_id)}[ok_fact_id]
    assert ok_fact.status == "needs_validation"


def test_job_schedules_after_insight_generation():
    # Monday 04:00 sits after the Monday 03:00 insight-generation window.
    seconds = contradiction_detection.seconds_until_next_weekly_run(
        datetime(2026, 6, 8, 1, 0, tzinfo=timezone.utc),  # Monday 01:00
        timezone_name="UTC",
        weekday=contradiction_detection.CONTRADICTION_DETECTION_WEEKDAY,
        hour=contradiction_detection.CONTRADICTION_DETECTION_HOUR,
    )
    assert seconds == 3 * 60 * 60
