from __future__ import annotations

from datetime import datetime, timezone

import pytest

import crud
import models
from auth import hash_password
from services import plan_maintenance
from tests.conftest import TestSessionLocal


def _day(date: str, completed: bool = False) -> dict:
    return {
        "date": date,
        "workoutType": "endurance",
        "title": f"Ride {date}",
        "description": "Endurance ride",
        "durationMinutes": 60,
        "completed": completed,
    }


async def _create_user_with_plan(
    *,
    email: str,
    plan: list[dict],
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
        await crud.upsert_training_plan(db, user.id, plan)
        await db.commit()
        return user.id


async def _get_plan(user_id: str) -> list[dict]:
    async with TestSessionLocal() as db:
        plan = await crud.get_training_plan(db, user_id)
        assert plan is not None
        return plan.plan


@pytest.mark.asyncio
async def test_daily_maintenance_updates_stale_incomplete_plan(monkeypatch):
    user_id = await _create_user_with_plan(
        email="stale@example.com",
        plan=[_day("2026-06-08"), _day("2026-06-10")],
    )

    async def fake_adapt_training_plan(plan, *args, **kwargs):
        return [{**plan[0], "date": "2026-06-09"}, plan[1]]

    monkeypatch.setattr(
        plan_maintenance.ai_service,
        "adapt_training_plan",
        fake_adapt_training_plan,
    )

    async def fake_weather_context(*args, **kwargs):
        return ""

    monkeypatch.setattr(
        plan_maintenance,
        "training_weather_context_for_user",
        fake_weather_context,
    )

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.updated == 1
    assert result.failed == 0
    updated_plan = await _get_plan(user_id)
    assert updated_plan[0]["date"] == "2026-06-09"


@pytest.mark.asyncio
async def test_daily_maintenance_is_noop_for_current_plan(monkeypatch):
    user_id = await _create_user_with_plan(
        email="current@example.com",
        plan=[_day("2026-06-09"), _day("2026-06-10")],
    )
    called = False

    async def fake_adapt_training_plan(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        plan_maintenance.ai_service,
        "adapt_training_plan",
        fake_adapt_training_plan,
    )

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 1
    assert result.skipped == 1
    assert called is False
    assert await _get_plan(user_id) == [_day("2026-06-09"), _day("2026-06-10")]


@pytest.mark.asyncio
async def test_daily_maintenance_skips_users_who_are_not_onboarded(monkeypatch):
    await _create_user_with_plan(
        email="not-onboarded@example.com",
        plan=[_day("2026-06-08")],
        onboarded=False,
    )
    called = False

    async def fake_adapt_training_plan(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        plan_maintenance.ai_service,
        "adapt_training_plan",
        fake_adapt_training_plan,
    )

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 0
    assert called is False


@pytest.mark.asyncio
async def test_daily_maintenance_uses_timezone_for_today(monkeypatch):
    user_id = await _create_user_with_plan(
        email="timezone@example.com",
        plan=[_day("2026-06-08")],
    )

    async def fake_adapt_training_plan(plan, *args, **kwargs):
        return [{**plan[0], "date": "2026-06-09"}]

    monkeypatch.setattr(
        plan_maintenance.ai_service,
        "adapt_training_plan",
        fake_adapt_training_plan,
    )

    async def fake_weather_context(*args, **kwargs):
        return ""

    monkeypatch.setattr(
        plan_maintenance,
        "training_weather_context_for_user",
        fake_weather_context,
    )

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 8, 22, 30, tzinfo=timezone.utc),
        timezone_name="Europe/Berlin",
    )

    assert result.updated == 1
    updated_plan = await _get_plan(user_id)
    assert updated_plan[0]["date"] == "2026-06-09"


@pytest.mark.asyncio
async def test_daily_maintenance_is_idempotent_for_same_day(monkeypatch):
    user_id = await _create_user_with_plan(
        email="idempotent@example.com",
        plan=[_day("2026-06-08")],
    )

    async def fake_adapt_training_plan(plan, *args, **kwargs):
        return [{**plan[0], "date": "2026-06-09"}]

    monkeypatch.setattr(
        plan_maintenance.ai_service,
        "adapt_training_plan",
        fake_adapt_training_plan,
    )

    async def fake_weather_context(*args, **kwargs):
        return ""

    monkeypatch.setattr(
        plan_maintenance,
        "training_weather_context_for_user",
        fake_weather_context,
    )

    first = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )
    second = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 9, 2, 10, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert first.updated == 1
    assert second.updated == 0
    assert second.skipped == 1
    assert (await _get_plan(user_id))[0]["date"] == "2026-06-09"


@pytest.mark.asyncio
async def test_daily_maintenance_isolates_per_user_failures(monkeypatch):
    failing_user_id = await _create_user_with_plan(
        email="failing@example.com",
        plan=[_day("2026-06-08")],
    )
    ok_user_id = await _create_user_with_plan(
        email="ok@example.com",
        plan=[_day("2026-06-08")],
    )

    async def fake_adapt_training_plan(plan, _feedback, profile, *args, **kwargs):
        if profile["email"] == "failing@example.com":
            raise RuntimeError("boom")
        return [{**plan[0], "date": "2026-06-09"}]

    monkeypatch.setattr(
        plan_maintenance.ai_service,
        "adapt_training_plan",
        fake_adapt_training_plan,
    )

    async def fake_weather_context(*args, **kwargs):
        return ""

    monkeypatch.setattr(
        plan_maintenance,
        "training_weather_context_for_user",
        fake_weather_context,
    )

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 9, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.checked == 2
    assert result.failed == 1
    assert result.updated == 1
    assert (await _get_plan(failing_user_id))[0]["date"] == "2026-06-08"
    assert (await _get_plan(ok_user_id))[0]["date"] == "2026-06-09"


def test_seconds_until_next_daily_run_uses_configured_hour_and_timezone():
    seconds = plan_maintenance.seconds_until_next_daily_run(
        datetime(2026, 6, 9, 23, 0, tzinfo=timezone.utc),
        timezone_name="Europe/Berlin",
    )

    assert seconds == 60 * 60
