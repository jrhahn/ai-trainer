"""The activity sync visits every connected athlete, plan or not (ai-trainer-ops#279).

It used to take its athletes from ``get_users_with_training_plans``. When the
first plan failed to generate during onboarding (a Gemini 429, a timeout), the
athlete was onboarded and connected and their history import worked, but no
later ride ever synced and nothing said why.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

import crud
import models
from services import activity_sync
from tests.conftest import TestSessionLocal
from tests.test_activity_sync import _create_user


async def _drop_plan(user_id: str) -> None:
    async with TestSessionLocal() as db:
        await db.execute(
            delete(models.TrainingPlan).where(models.TrainingPlan.user_id == user_id)
        )
        await db.commit()


def _record_visits(monkeypatch) -> dict[str, list[str]]:
    visited: dict[str, list[str]] = {"strava": [], "intervals": []}

    async def fake_strava(db, user):
        visited["strava"].append(user.id)
        return activity_sync.SourceSyncResult(source="strava", checked=1)

    async def fake_intervals(db, user):
        visited["intervals"].append(user.id)
        return activity_sync.SourceSyncResult(source="intervals", checked=1)

    monkeypatch.setattr(activity_sync, "sync_strava_for_user", fake_strava)
    monkeypatch.setattr(activity_sync, "sync_intervals_for_user", fake_intervals)
    return visited


@pytest.mark.asyncio
async def test_an_athlete_whose_first_plan_failed_is_still_synced(monkeypatch):
    planless = await _create_user(email="no-plan-279@example.com", intervals=True)
    await _drop_plan(planless)
    visited = _record_visits(monkeypatch)

    result = await activity_sync.run_activity_sync(TestSessionLocal)

    assert planless in visited["intervals"]
    assert result.users == 1


@pytest.mark.asyncio
async def test_an_athlete_without_a_connection_is_left_out(monkeypatch):
    await _create_user(email="no-source-279@example.com")
    connected = await _create_user(email="strava-279@example.com", strava=True)
    visited = _record_visits(monkeypatch)

    result = await activity_sync.run_activity_sync(TestSessionLocal)

    assert visited["strava"] == [connected]
    assert result.users == 1


@pytest.mark.asyncio
async def test_the_plan_jobs_still_take_only_athletes_with_a_plan():
    planless = await _create_user(email="plan-jobs-279@example.com", intervals=True)
    await _drop_plan(planless)

    async with TestSessionLocal() as db:
        with_plans = [user.id for user in await crud.get_users_with_training_plans(db)]
        with_sources = [
            user.id for user in await crud.get_users_with_activity_sources(db)
        ]

    assert planless not in with_plans
    assert planless in with_sources
