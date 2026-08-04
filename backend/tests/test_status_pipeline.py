"""The status pipeline: the coach owns the badge, and the plan invalidates it (#499).

The badge is stored, not recomputed per render, so these lock in the two things
that keeps honest: a plan or activity change must clear it, and regeneration must
always leave something renderable behind — the dashboard paints the badge before
any provider call can be retried.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import crud
import models
from auth import hash_password
from services import ai_service, plan_pipeline, status_pipeline
from services.dates import app_today
from tests.conftest import TestSessionLocal


def _day(date: str, workout_type: str = "endurance", duration: int = 60) -> dict:
    return {
        "date": date,
        "workoutType": workout_type,
        "title": f"Workout {date}",
        "description": "Session",
        "durationMinutes": duration,
    }


async def _create_user(email: str, plan: list[dict]) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, plan)
        await crud.upsert_rider_assessment(db, user.id, estimated_ftp=250)
        await crud.set_training_status(
            db,
            user.id,
            label="On track",
            tone="positive",
            rationale="Everything done.",
        )
        await db.commit()
        return user.id


@pytest.mark.asyncio
async def test_plan_change_invalidates_the_stored_badge():
    """A plan write must clear the badge — last week's verdict no longer applies."""
    d = (app_today() + timedelta(days=3)).isoformat()
    user_id = await _create_user("status-invalidate@example.com", [_day(d)])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, [_day(d, "intervals")], base_plan=[_day(d)], source="adapt"
        )
        await db.commit()

    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.training_status_label is None
        assert assessment.training_status_rationale is None


@pytest.mark.asyncio
async def test_regenerate_falls_back_when_the_provider_fails(monkeypatch):
    """A provider outage must not leave the dashboard with a blank badge."""
    d = (app_today() - timedelta(days=2)).isoformat()
    user_id = await _create_user("status-fallback@example.com", [_day(d)])

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ai_service, "generate_training_status", _boom)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        label, tone, rationale = await status_pipeline.regenerate(db, user)
        await db.commit()

    assert label and tone and rationale

    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.training_status_label == label


@pytest.mark.asyncio
async def test_regenerate_persists_the_coach_written_badge(monkeypatch):
    d = (app_today() - timedelta(days=2)).isoformat()
    user_id = await _create_user("status-persist@example.com", [_day(d)])

    async def _badge(*_args, **_kwargs):
        return ("Ahead of plan", "positive", "You added an unplanned long ride.")

    monkeypatch.setattr(ai_service, "generate_training_status", _badge)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await status_pipeline.regenerate(db, user)
        await db.commit()

    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.training_status_label == "Ahead of plan"
        assert assessment.training_status_tone == "positive"
        assert "unplanned long ride" in assessment.training_status_rationale


@pytest.mark.asyncio
async def test_status_write_leaves_the_rest_of_the_assessment_alone():
    """A badge write must not disturb the assessment's other fields.

    ``upsert_rider_assessment`` rewrites hr_zones/ride_insights from its
    arguments, so the badge deliberately goes through its own setter.
    """
    user_id = await _create_user("status-isolated@example.com", [])

    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(
            db,
            user_id,
            estimated_ftp=250,
            ride_insights="Strong threshold work.",
            hr_zones={"z2": [120, 140]},
        )
        await db.commit()

    async with TestSessionLocal() as db:
        await crud.set_training_status(
            db, user_id, label="Easing off", tone="steady", rationale="Taper week."
        )
        await db.commit()

    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.ride_insights == "Strong threshold work."
        assert assessment.hr_zones == {"z2": [120, 140]}
        assert assessment.training_status_label == "Easing off"


@pytest.mark.asyncio
async def test_invalidate_reports_whether_anything_was_cleared():
    user_id = await _create_user("status-invalidate-flag@example.com", [])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert await status_pipeline.invalidate(db, user) is True
        # Already clear — nothing further to do, so no write is reported.
        assert await status_pipeline.invalidate(db, user) is False
        await db.commit()
