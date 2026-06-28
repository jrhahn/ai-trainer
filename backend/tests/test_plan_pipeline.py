"""Tests for the unified plan pipeline (services/plan_pipeline.py).

These lock in the two cross-cutting guarantees every plan-writing trigger now
inherits: hard availability constraints are enforced, and concurrent user edits
survive a write (the reload-revert, #339).
"""

from __future__ import annotations

import pytest

import crud
import models
from auth import hash_password
from services import plan_pipeline, summary_pipeline
from tests.conftest import TestSessionLocal


def _day(date: str, workout_type: str = "endurance", duration: int = 60, completed: bool = False) -> dict:
    return {
        "date": date,
        "workoutType": workout_type,
        "title": f"Workout {date}",
        "description": "Session",
        "durationMinutes": duration,
        "completed": completed,
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
        await db.commit()
        return user.id


@pytest.mark.asyncio
async def test_commit_plan_enforces_no_training_constraint():
    """A proposed training day on a hard no_training date is forced to rest."""
    d = "2026-07-10"
    user_id = await _create_user("pipe-constraint@example.com", [_day(d, "intervals")])
    async with TestSessionLocal() as db:
        await crud.upsert_availability_constraint(
            db, user_id, constraint_type="no_training", constraint_date=d, expires_on=d
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan(
            db, user, [_day(d, "intervals")], base_plan=[_day(d, "intervals")]
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["workoutType"] == "rest"
    assert day["durationMinutes"] == 0


@pytest.mark.asyncio
async def test_commit_plan_preserves_concurrent_user_edit():
    """A day the user changed concurrently must not be overwritten (#339)."""
    d = "2026-07-11"
    base = [_day(d, "endurance")]
    user_id = await _create_user("pipe-merge@example.com", base)

    # User edits the day in the DB after the trigger captured `base`.
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [_day(d, "rest", duration=0)])
        await db.commit()

    # Trigger proposes a different value, still based on the stale `base`.
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan(
            db, user, [_day(d, "intervals")], base_plan=base
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["workoutType"] == "rest"  # the user's concurrent edit wins


@pytest.mark.asyncio
async def test_commit_plan_updates_drops_constraint_violating_update():
    """commit_plan_updates must not apply an update that violates a constraint."""
    d = "2026-07-12"
    user_id = await _create_user("pipe-updates@example.com", [_day(d, "rest", duration=0)])
    async with TestSessionLocal() as db:
        await crud.upsert_availability_constraint(
            db, user_id, constraint_type="no_training", constraint_date=d, expires_on=d
        )
        await db.commit()

    base = [_day(d, "rest", duration=0)]
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": d, "workoutType": "intervals", "durationMinutes": 90}],
            base_plan=base,
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["workoutType"] == "rest"


@pytest.mark.asyncio
async def test_commit_plan_enforces_required_workout():
    """A pinned required session is coerced onto a day that falls short."""
    d = "2026-07-18"
    user_id = await _create_user("pipe-required@example.com", [_day(d, "rest", duration=0)])
    async with TestSessionLocal() as db:
        await crud.upsert_availability_constraint(
            db,
            user_id,
            constraint_type="required_workout",
            constraint_date=d,
            expires_on=d,
            required_workout={"workoutType": "endurance", "minDurationMinutes": 120},
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan(
            db,
            user,
            [_day(d, "rest", duration=0)],
            base_plan=[_day(d, "rest", duration=0)],
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["workoutType"] == "endurance"
    assert day["durationMinutes"] == 120


@pytest.mark.asyncio
async def test_plan_change_invalidates_login_summary():
    """A real plan change clears the login summary so it regenerates on load."""
    d = "2026-07-20"
    user_id = await _create_user("pipe-summary@example.com", [_day(d, "endurance")])
    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(
            db,
            user_id,
            estimated_ftp=250,
            rider_type="allrounder",
            login_summary="Old summary based on the previous plan.",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, [_day(d, "intervals")], base_plan=[_day(d, "endurance")]
        )
        await db.commit()

    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.login_summary is None


@pytest.mark.asyncio
async def test_no_op_plan_write_keeps_login_summary():
    """An unchanged plan write must not invalidate the summary."""
    d = "2026-07-21"
    plan = [_day(d, "endurance")]
    user_id = await _create_user("pipe-summary-noop@example.com", plan)
    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(
            db, user_id, estimated_ftp=250, rider_type="allrounder",
            login_summary="Still valid summary.",
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(db, user, plan, base_plan=plan)
        await db.commit()

    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.login_summary == "Still valid summary."


@pytest.mark.asyncio
async def test_summary_regenerate_persists(monkeypatch):
    """summary_pipeline.regenerate writes a freshly generated summary."""
    d = "2026-07-22"
    user_id = await _create_user("pipe-regen@example.com", [_day(d, "endurance")])
    async with TestSessionLocal() as db:
        await crud.upsert_rider_assessment(
            db, user_id, estimated_ftp=250, rider_type="allrounder",
        )
        await db.commit()

    async def fake_generate(**_):
        return "Fresh summary built from the current plan."

    monkeypatch.setattr(
        summary_pipeline.ai_service, "generate_login_summary", fake_generate
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await summary_pipeline.regenerate(db, user, provider="gemini")
        await db.commit()

    assert result == "Fresh summary built from the current plan."
    async with TestSessionLocal() as db:
        assessment = await crud.get_rider_assessment(db, user_id)
        assert assessment.login_summary == "Fresh summary built from the current plan."
