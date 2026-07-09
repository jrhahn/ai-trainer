"""Service tests for the weekly prediction-evaluation job (#383)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
import models
from auth import hash_password
from services import prediction_evaluation
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


async def _predictions(user_id: str) -> list[models.AthletePrediction]:
    async with TestSessionLocal() as db:
        return await crud.list_athlete_predictions(
            db, user_id, include_resolved=True
        )


_SAMPLE_CANDIDATES = [
    {
        "prediction": "The athlete will be fully recovered tomorrow",
        "expected_outcome": "Resting HR back to baseline, ready for intensity",
        "horizon": "tomorrow",
        "confidence": 0.7,
        "category": "fatigue_response",
    },
    {
        "prediction": "Fatigue forces an easier week within 10 days",
        "expected_outcome": "A drop in weekly TSS",
        "horizon": "within 10 days",
        "confidence": 0.5,
        "category": "general",
    },
]


@pytest.mark.asyncio
async def test_generates_predictions_when_none_pending(monkeypatch):
    user_id = await _create_user_with_history(
        email="predict@example.com", activity_count=10
    )

    async def fake_generate(metrics_section, *, existing_predictions, provider):
        assert metrics_section
        return _SAMPLE_CANDIDATES

    async def fake_evaluate(*args, **kwargs):
        raise AssertionError("nothing pending should be evaluated")

    monkeypatch.setattr(
        prediction_evaluation.ai_service,
        "generate_athlete_predictions",
        fake_generate,
    )
    monkeypatch.setattr(
        prediction_evaluation.ai_service,
        "evaluate_athlete_predictions",
        fake_evaluate,
    )

    result = await prediction_evaluation.run_prediction_evaluation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 7, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.generated == 2
    assert result.evaluated == 0
    assert result.failed == 0
    stored = {p.prediction for p in await _predictions(user_id)}
    assert "The athlete will be fully recovered tomorrow" in stored
    assert all(p.status == "pending" for p in await _predictions(user_id))


@pytest.mark.asyncio
async def test_evaluates_pending_predictions_and_adjusts_confidence(monkeypatch):
    user_id = await _create_user_with_history(
        email="evaluate@example.com", activity_count=10
    )
    async with TestSessionLocal() as db:
        await crud.record_athlete_prediction(
            db,
            user_id,
            prediction="The athlete will be fully recovered tomorrow",
            expected_outcome="Resting HR baseline",
            confidence=0.5,
        )
        await db.commit()

    async def fake_evaluate(metrics_section, predictions, *, provider):
        assert predictions  # the pending prediction is passed for scoring
        return [{"index": 0, "correct": False, "actual_outcome": "Still fatigued"}]

    async def fake_generate(metrics_section, *, existing_predictions, provider):
        return []

    monkeypatch.setattr(
        prediction_evaluation.ai_service,
        "evaluate_athlete_predictions",
        fake_evaluate,
    )
    monkeypatch.setattr(
        prediction_evaluation.ai_service,
        "generate_athlete_predictions",
        fake_generate,
    )

    result = await prediction_evaluation.run_prediction_evaluation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 7, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.evaluated == 1
    stored = await _predictions(user_id)
    assert len(stored) == 1
    assert stored[0].status == "incorrect"
    assert stored[0].actual_outcome == "Still fatigued"
    # Confidence reduced on a wrong call (0.5 - 0.2).
    assert stored[0].confidence == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_skips_users_with_too_little_history(monkeypatch):
    await _create_user_with_history(
        email="thin@example.com",
        activity_count=prediction_evaluation.PREDICTION_MIN_ACTIVITIES - 1,
    )
    called = False

    async def fake_generate(*args, **kwargs):
        nonlocal called
        called = True
        return _SAMPLE_CANDIDATES

    monkeypatch.setattr(
        prediction_evaluation.ai_service,
        "generate_athlete_predictions",
        fake_generate,
    )

    result = await prediction_evaluation.run_prediction_evaluation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 7, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.skipped == 1
    assert result.generated == 0
    assert called is False


@pytest.mark.asyncio
async def test_existing_predictions_are_passed_to_the_model(monkeypatch):
    user_id = await _create_user_with_history(
        email="existing@example.com", activity_count=10
    )
    async with TestSessionLocal() as db:
        await crud.record_athlete_prediction(
            db,
            user_id,
            prediction="Will PR the local climb",
            expected_outcome="A new best time",
        )
        await db.commit()

    seen_existing: list[str] = []

    async def fake_evaluate(metrics_section, predictions, *, provider):
        return []

    async def fake_generate(metrics_section, *, existing_predictions, provider):
        seen_existing.extend(existing_predictions)
        return []

    monkeypatch.setattr(
        prediction_evaluation.ai_service,
        "evaluate_athlete_predictions",
        fake_evaluate,
    )
    monkeypatch.setattr(
        prediction_evaluation.ai_service,
        "generate_athlete_predictions",
        fake_generate,
    )

    await prediction_evaluation.run_prediction_evaluation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 7, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert "Will PR the local climb" in seen_existing


@pytest.mark.asyncio
async def test_isolates_per_user_failures(monkeypatch):
    failing_id = await _create_user_with_history(
        email="fail@example.com", activity_count=10
    )
    ok_id = await _create_user_with_history(
        email="ok@example.com", activity_count=10
    )

    async def fake_generate(metrics_section, *, existing_predictions, provider):
        return _SAMPLE_CANDIDATES

    original = crud.record_athlete_prediction

    async def flaky_record(db, user_id, **kwargs):
        if user_id == failing_id:
            raise RuntimeError("boom")
        return await original(db, user_id, **kwargs)

    monkeypatch.setattr(
        prediction_evaluation.ai_service,
        "generate_athlete_predictions",
        fake_generate,
    )
    monkeypatch.setattr(
        prediction_evaluation.crud, "record_athlete_prediction", flaky_record
    )

    result = await prediction_evaluation.run_prediction_evaluation(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 7, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 2
    assert result.failed == 1
    assert {p.prediction for p in await _predictions(ok_id)}
    assert not await _predictions(failing_id)


def test_seconds_until_next_weekly_run_uses_monday_07():
    seconds = prediction_evaluation.seconds_until_next_weekly_run(
        datetime(2026, 6, 8, 1, 0, tzinfo=timezone.utc),  # Monday 01:00
        timezone_name="UTC",
        weekday=prediction_evaluation.PREDICTION_EVALUATION_WEEKDAY,
        hour=prediction_evaluation.PREDICTION_EVALUATION_HOUR,
    )
    assert seconds == 6 * 60 * 60
