"""A second import, or a late ride, leaves fitness and fatigue as one import would
(ai-trainer-ops#278).

Both paths seeded the load chain from the newest stored row, as if every ride
they brought came after it. A history import run again (the app's own advice
when streams were not ready) stacked the window's load onto the last run, and
older rides imported after newer ones were chained from the newest row while
the rows after them never counted them.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete, select

import crud
import models
from auth import decode_token
from database import async_session_maker
from routers import intervals as intervals_router
from services import activity_sync, metrics_service
from tests.conftest import TestSessionLocal
from tests.test_activity_sync import _create_user

# activity id -> (day, constant power over the hour)
RIDES = {
    "i-a": ("2026-06-02", 220),
    "i-b": ("2026-06-05", 240),
    "i-c": ("2026-06-20", 230),
}


def _serve_intervals(monkeypatch, ids: list[str]) -> None:
    """intervals.icu answers with these rides: an hour each at their power."""

    async def fake_fetch_recent_activities(*args, **kwargs):
        return [
            {
                "id": raw_id,
                "name": f"Ride {raw_id}",
                "type": "Ride",
                "start_date_local": f"{RIDES[raw_id][0]}T08:00:00",
                "moving_time": 3600,
            }
            for raw_id in ids
        ]

    async def fake_fetch_activity_detail(api_key, raw_id):
        return {"average_watts": RIDES[raw_id][1]}

    async def fake_fetch_activity_streams(api_key, raw_id):
        times = list(range(0, 3601, 60))
        return {"watts": [RIDES[raw_id][1]] * len(times), "time": times}

    monkeypatch.setattr(intervals_router, "fetch_recent_activities", fake_fetch_recent_activities)
    monkeypatch.setattr(intervals_router, "fetch_activity_detail", fake_fetch_activity_detail)
    monkeypatch.setattr(intervals_router, "fetch_activity_streams", fake_fetch_activity_streams)


async def _import(user_id: str) -> None:
    await intervals_router.run_intervals_import(
        user_id=user_id,
        api_key="secret",
        athlete_id="0",
        oldest=date(2026, 6, 1),
        newest=date(2026, 6, 30),
        ftp=280,
    )
    assert intervals_router._intervals_import_progress[user_id]["status"] == "done"


async def _chain(user_id: str) -> list[tuple[str, float, float, float]]:
    async with async_session_maker() as session:
        rows = await crud.get_all_ride_metrics_ordered(session, user_id)
    return [
        (row.activity_date, round(row.ctl_after, 6), round(row.atl_after, 6), round(row.tsb_after, 6))
        for row in rows
    ]


@pytest.mark.asyncio
async def test_an_intervals_import_run_twice_keeps_every_fitness_value(auth_headers, monkeypatch):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    _serve_intervals(monkeypatch, ["i-a", "i-b"])

    await _import(user_id)
    first = await _chain(user_id)
    await _import(user_id)

    assert len(first) == 2
    assert await _chain(user_id) == first


@pytest.mark.asyncio
async def test_older_rides_imported_after_a_newer_one_give_the_chain_of_one_import(
    auth_headers, monkeypatch
):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    _serve_intervals(monkeypatch, ["i-c"])
    await _import(user_id)
    _serve_intervals(monkeypatch, ["i-a", "i-b"])
    await _import(user_id)
    in_two = await _chain(user_id)

    async with async_session_maker() as session:
        await session.execute(delete(models.RideMetric).where(models.RideMetric.user_id == user_id))
        await session.commit()
    _serve_intervals(monkeypatch, ["i-a", "i-b", "i-c"])
    await _import(user_id)

    assert [day for day, *_ in in_two] == ["2026-06-02", "2026-06-05", "2026-06-20"]
    assert in_two == await _chain(user_id)


@pytest.mark.asyncio
async def test_a_late_older_ride_from_the_sync_counts_toward_the_rows_after_it(monkeypatch):
    """A ride from June 1 that syncs after the June 9 session: the June 9 row's
    fitness has to include it, and the June 1 row comes first in the chain."""
    user_id = await _create_user(email="late-ride-278@example.com", strava=True, strava_cursor=100)
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=50,
            activity_date="2026-06-09",
            sport_type="Ride",
            tss=60.0,
            summary="Endurance · 1h",
        )
        metrics_service.replay_load_chain(await crud.get_all_ride_metrics_ordered(db, user_id))
        await db.commit()
        before = (await crud.get_latest_ride_metric(db, user_id)).ctl_after

    async def fake_fetch_recent_strava_activities(*args, **kwargs):
        return [
            {
                "id": 101,
                "name": "Late upload",
                "type": "Ride",
                "sport_type": "Ride",
                "start_date": "2026-06-01T06:00:00Z",
                "start_date_local": "2026-06-01T08:00:00",
                "elapsed_time": 3600,
            }
        ]

    async def fake_fetch_streams(*args, **kwargs):
        times = list(range(0, 3601, 60))
        return {"watts": {"data": [230] * len(times)}, "time": {"data": times}}

    async def fake_weather(*args, **kwargs):
        return {}

    async def fake_review(*args, **kwargs):
        return "ok", []

    async def fake_learn(db, user, **kwargs):
        return None

    monkeypatch.setattr(
        activity_sync, "fetch_recent_strava_activities", fake_fetch_recent_strava_activities
    )
    monkeypatch.setattr(activity_sync, "fetch_strava_activity_streams", fake_fetch_streams)
    monkeypatch.setattr(activity_sync, "enrich_activity_weather", fake_weather)
    monkeypatch.setattr(activity_sync, "review_matched_ride_and_adapt", fake_review)
    monkeypatch.setattr(activity_sync, "learn_from_completed_workouts", fake_learn)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await activity_sync.sync_strava_for_user(db, user)
        await db.commit()
    assert result.imported == 1

    async with TestSessionLocal() as db:
        rows = list(
            await db.scalars(
                select(models.RideMetric)
                .where(models.RideMetric.user_id == user_id)
                .order_by(models.RideMetric.activity_date)
            )
        )
    assert [row.activity_date for row in rows] == ["2026-06-01", "2026-06-09"]
    late, newer = rows
    assert newer.ctl_after > before
    assert late.ctl_after < newer.ctl_after
