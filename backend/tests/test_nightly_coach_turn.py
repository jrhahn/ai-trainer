"""The nightly job is a coach turn, not a second planner (#666).

Production, 30 days: `nightly_maintenance` applied 309 day changes across 30
batches — one batch every single night — against the coach's 45. Most of it was
churn: the job asked the model for the whole remaining plan back, so eleven days
returned with fresh prose whether or not anything about them had changed, and
the athlete watched their week move under them.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
import models
from auth import hash_password
from services import plan_maintenance
from tests.conftest import TestSessionLocal


def _day(date: str, workout_type: str = "endurance", completed: bool = False) -> dict:
    return {
        "date": date,
        "workoutType": workout_type,
        "title": f"Ride {date}",
        "description": "Endurance ride",
        "durationMinutes": 60,
        "completed": completed,
    }


def _window(start_day: int = 9, count: int = 12) -> list[dict]:
    return [_day(f"2026-06-{start_day + i:02d}") for i in range(count)]


# ---------------------------------------------------------------------------
# When the run is worth making
# ---------------------------------------------------------------------------


def test_a_healthy_plan_gives_no_reason_to_run():
    assert plan_maintenance.maintenance_reasons(_window(), "2026-06-09") == []


def test_a_session_missed_yesterday_is_a_reason():
    plan = [_day("2026-06-08"), *_window()]
    assert "missed_session" in plan_maintenance.maintenance_reasons(plan, "2026-06-09")


def test_a_session_missed_last_week_is_not():
    """The old condition stayed true for the rest of the plan's life."""
    plan = [_day("2026-06-01"), *_window()]
    assert plan_maintenance.maintenance_reasons(plan, "2026-06-09") == []
    # …while the predicate it replaced still sees it, which is the whole point.
    assert plan_maintenance.has_stale_incomplete_days(plan, "2026-06-09") is True


def test_a_completed_past_day_is_not_a_missed_session():
    plan = [_day("2026-06-08", completed=True), *_window()]
    assert plan_maintenance.maintenance_reasons(plan, "2026-06-09") == []


def test_a_missed_rest_day_is_not_a_missed_session():
    """Nobody fails to rest."""
    plan = [_day("2026-06-08", workout_type="rest"), *_window()]
    assert plan_maintenance.maintenance_reasons(plan, "2026-06-09") == []


def test_a_short_window_is_a_reason():
    plan = _window(count=4)
    assert plan_maintenance.maintenance_reasons(plan, "2026-06-09") == ["short_horizon"]


# ---------------------------------------------------------------------------
# Folding updates into the stored plan
# ---------------------------------------------------------------------------


def test_a_day_the_run_did_not_name_comes_back_byte_identical():
    plan = _window(count=3)
    proposed = plan_maintenance.apply_maintenance_updates(
        plan,
        [{"date": "2026-06-09", "title": "Reworked", "durationMinutes": 75}],
        "2026-06-09",
    )
    by_date = {d["date"]: d for d in proposed}
    assert by_date["2026-06-10"] == plan[1]
    assert by_date["2026-06-11"] == plan[2]
    assert by_date["2026-06-09"]["title"] == "Reworked"


def test_no_updates_is_a_no_op():
    assert plan_maintenance.apply_maintenance_updates(_window(), [], "2026-06-09") is None
    assert (
        plan_maintenance.apply_maintenance_updates(_window(), None, "2026-06-09") is None
    )


def test_a_new_date_inside_the_window_extends_the_plan():
    """Nothing else lengthens the plan now that #664 took generate off the sync path."""
    plan = _window(count=3)
    proposed = plan_maintenance.apply_maintenance_updates(
        plan, [_day("2026-06-12")], "2026-06-09"
    )
    assert [d["date"] for d in proposed][-1] == "2026-06-12"
    assert proposed[:3] == plan


def test_a_date_in_the_past_is_not_appended():
    plan = _window(count=3)
    assert (
        plan_maintenance.apply_maintenance_updates(
            plan, [_day("2026-06-01")], "2026-06-09"
        )
        is None
    )


def test_a_date_beyond_the_horizon_is_not_appended():
    plan = _window(count=3)
    assert (
        plan_maintenance.apply_maintenance_updates(
            plan, [_day("2026-09-01")], "2026-06-09"
        )
        is None
    )


# ---------------------------------------------------------------------------
# The run itself
# ---------------------------------------------------------------------------


async def _create_user(email: str, plan: list[dict]) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Test Rider",
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


def _silence_weather(monkeypatch):
    async def fake_weather(*args, **kwargs):
        return ""

    monkeypatch.setattr(
        plan_maintenance, "training_weather_context_for_user", fake_weather
    )


@pytest.mark.asyncio
async def test_a_run_with_no_reason_never_reaches_the_model(monkeypatch):
    user_id = await _create_user("no-reason@example.com", _window())
    called = False

    async def fake_adapt(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", fake_adapt)
    _silence_weather(monkeypatch)

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert called is False
    assert result.skipped == 1
    async with TestSessionLocal() as db:
        assert await crud.list_plan_day_history(db, user_id) == []


@pytest.mark.asyncio
async def test_an_empty_update_list_writes_nothing_and_says_nothing(monkeypatch):
    """"The plan already suits you" is a valid answer and must stay silent."""
    plan = [_day("2026-06-08"), *_window()]
    user_id = await _create_user("nothing-to-do@example.com", plan)
    narrated = False

    async def fake_adapt(*args, **kwargs):
        return []

    async def fake_narrate(*args, **kwargs):
        nonlocal narrated
        narrated = True

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", fake_adapt)
    monkeypatch.setattr(
        plan_maintenance.coach_summary, "narrate_plan_changes", fake_narrate
    )
    _silence_weather(monkeypatch)

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.updated == 0
    assert narrated is False
    async with TestSessionLocal() as db:
        assert await crud.list_plan_day_history(db, user_id) == []
    assert await _stored_plan(user_id) == plan


async def _stored_plan(user_id: str) -> list[dict]:
    async with TestSessionLocal() as db:
        row = await crud.get_training_plan(db, user_id)
        assert row is not None
        return row.plan


@pytest.mark.asyncio
async def test_the_run_is_handed_the_change_history_and_coherence_warnings(monkeypatch):
    """Without these it plans the week knowing nothing of last night's chat."""
    plan = [_day("2026-06-08"), *_window()]
    user_id = await _create_user("context@example.com", plan)
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await crud.record_plan_day_changes(
            db,
            user.id,
            [
                {
                    "date": "2026-06-10",
                    "slot": 0,
                    "old_day": _day("2026-06-10"),
                    "new_day": _day("2026-06-10", workout_type="strength"),
                    "applied": True,
                }
            ],
            "coach_chat",
        )
        await db.commit()

    seen: dict = {}

    async def fake_adapt(*args, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", fake_adapt)
    _silence_weather(monkeypatch)

    await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert "plan_change_history" in seen
    assert "2026-06-10" in seen["plan_change_history"]
    # …and it says what the change was, not merely that one happened.
    assert "strength" in seen["plan_change_history"].lower()
    assert "plan_coherence_warnings" in seen


@pytest.mark.asyncio
async def test_the_run_extends_a_short_window_without_touching_existing_days(monkeypatch):
    plan = _window(count=4)
    user_id = await _create_user("extend@example.com", plan)

    async def fake_adapt(*args, **kwargs):
        return [_day("2026-06-13"), _day("2026-06-14")]

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", fake_adapt)
    _silence_weather(monkeypatch)

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.updated == 1
    stored = await _stored_plan(user_id)
    assert [d["date"] for d in stored][:4] == [d["date"] for d in plan]
    assert stored[:4] == plan
    assert [d["date"] for d in stored][-2:] == ["2026-06-13", "2026-06-14"]
