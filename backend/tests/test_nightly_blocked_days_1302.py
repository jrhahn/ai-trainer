"""The night writes a blocked day as blocked even when nothing else changes.

The constraints are applied on every plan write, so a day stays as the last
write left it until something writes again. An athlete away for three weeks
rides nothing, the model has nothing to change, and a blocked day an older gate
wrote as a 180-minute rest stayed so until it had passed (#1302 fixed the gate,
not the days it had written).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

import models
from services import ai_service, plan_maintenance
from tests.conftest import TestSessionLocal
from tests.test_nightly_plan_translations_271 import NOW
from tests.test_plan_maintenance import _create_user_with_plan, _day, _get_plan

TODAY = NOW.date()


def _iso(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def _blocked_as_a_long_rest(day: str, *, completed: bool = False) -> dict:
    """A blocked day as the gate before #1302 left it: rest, with a ride's window."""
    return {
        "date": day,
        "workoutType": "rest",
        "title": "Unavailable",
        "description": "No training scheduled because of an athlete availability constraint.",
        "durationMinutes": 180,
        "durationMinMinutes": 150,
        "durationMaxMinutes": 210,
        "completed": completed,
    }


async def _blocks(user_id: str, *days: str) -> None:
    async with TestSessionLocal() as db:
        for day in days:
            db.add(
                models.AthleteAvailabilityConstraint(
                    user_id=user_id,
                    constraint_type="no_training",
                    constraint_date=day,
                    reason="travelling",
                    source="chat",
                )
            )
        await db.commit()


async def _history_rows(user_id: str) -> int:
    async with TestSessionLocal() as db:
        return await db.scalar(
            select(func.count())
            .select_from(models.PlanDayHistory)
            .where(models.PlanDayHistory.user_id == user_id)
        )


async def _chat_messages(user_id: str) -> int:
    async with TestSessionLocal() as db:
        return await db.scalar(
            select(func.count())
            .select_from(models.ChatMessage)
            .where(models.ChatMessage.user_id == user_id)
        )


@pytest.fixture
def model(monkeypatch):
    """A model that changes nothing, counting how often it is asked."""
    asked: list[int] = []

    async def adapt(*args, **kwargs):
        asked.append(1)
        return []

    async def no_weather(*args, **kwargs):
        return ""

    monkeypatch.setattr(ai_service, "adapt_training_plan", adapt)
    monkeypatch.setattr(
        plan_maintenance, "training_weather_context_for_user", no_weather
    )
    return asked


def _by_date(plan: list[dict]) -> dict[str, dict]:
    return {day["date"]: day for day in plan}


@pytest.mark.asyncio
@pytest.mark.parametrize("horizon", [5, 14], ids=["model asked", "no reason to ask"])
async def test_a_blocked_day_left_as_a_long_rest_is_written_as_blocked(model, horizon):
    blocked = _iso(3)
    plan = [_day(_iso(i)) for i in range(horizon) if i != 3]
    plan.append(_blocked_as_a_long_rest(blocked))
    user_id = await _create_user_with_plan(email="away@example.com", plan=plan)
    await _blocks(user_id, blocked)

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal, now=NOW, timezone_name="UTC"
    )

    assert result.updated == 1
    day = _by_date(await _get_plan(user_id))[blocked]
    assert day["durationMinutes"] == 0
    assert day.get("durationMinMinutes") is None
    assert day.get("durationMaxMinutes") is None
    # The model is asked only when the night has its own reason to.
    assert len(model) == (1 if horizon < plan_maintenance.MIN_FUTURE_PLAN_DAYS else 0)
    # The athlete said the day is free: no message tells them so again.
    assert await _chat_messages(user_id) == 0


@pytest.mark.asyncio
async def test_the_next_night_writes_nothing(model):
    blocked = _iso(2)
    plan = [_day(_iso(i)) for i in range(14) if i != 2]
    plan.append(_blocked_as_a_long_rest(blocked))
    user_id = await _create_user_with_plan(email="twice@example.com", plan=plan)
    await _blocks(user_id, blocked)

    await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal, now=NOW, timezone_name="UTC"
    )
    written = await _get_plan(user_id)
    rows = await _history_rows(user_id)
    assert rows > 0

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal, now=NOW, timezone_name="UTC"
    )

    assert result.updated == 0
    assert await _get_plan(user_id) == written
    assert await _history_rows(user_id) == rows


@pytest.mark.asyncio
async def test_a_past_or_completed_blocked_day_stays_as_it_is(model):
    past, done = _iso(-1), _iso(2)
    plan = [_day(_iso(i)) for i in range(14) if i != 2]
    plan += [
        _blocked_as_a_long_rest(past),
        _blocked_as_a_long_rest(done, completed=True),
    ]
    user_id = await _create_user_with_plan(email="kept@example.com", plan=plan)
    await _blocks(user_id, past, done)

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal, now=NOW, timezone_name="UTC"
    )

    assert result.updated == 0
    stored = _by_date(await _get_plan(user_id))
    assert stored[past]["durationMinutes"] == 180
    assert stored[done]["durationMinutes"] == 180


@pytest.mark.asyncio
async def test_a_plan_without_blocked_days_is_not_written(model):
    plan = [_day(_iso(i)) for i in range(14)]
    user_id = await _create_user_with_plan(email="free@example.com", plan=plan)

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal, now=NOW, timezone_name="UTC"
    )

    assert result.updated == 0
    assert await _history_rows(user_id) == 0
