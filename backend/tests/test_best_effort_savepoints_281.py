"""A best-effort step that fails in the database leaves the main write standing
(ai-trainer-ops#281).

A failed flush, or in Postgres any failed statement, leaves the session's
transaction unusable. The best-effort blocks caught the exception and logged
"best-effort", and the request's own commit then failed with PendingRollbackError:
a 500, and the plan, the log or the synced ride rolled back with it. Each of
those steps now runs in a savepoint of its own.
"""

from __future__ import annotations

import pytest

import crud
import models
from services import activity_sync
from services.pipeline_graph import PipelineGraph
from tests.conftest import TestSessionLocal
from tests.test_activity_sync import _create_user


async def _fail_a_flush(db) -> None:
    """A statement that fails in the database, not in Python: email is NOT NULL."""
    db.add(models.User(email=None, name="Broken", hashed_password="x"))
    await db.flush()


@pytest.mark.asyncio
async def test_a_failing_pipeline_handler_leaves_the_upstream_write_committed():
    graph = PipelineGraph()

    async def broken(db, **_context):
        await _fail_a_flush(db)

    graph.register("source")
    graph.register("sink", depends_on=("source",), on_upstream_changed=broken)

    async with TestSessionLocal() as db:
        db.add(models.User(email="kept-281@example.com", name="Kept", hashed_password="x"))
        await db.flush()
        await graph.notify_changed("source", db=db)
        await db.commit()

    async with TestSessionLocal() as db:
        assert await crud.get_user_by_email_simple(db, "kept-281@example.com") is not None


@pytest.mark.asyncio
async def test_a_failing_completion_mark_leaves_the_synced_ride_stored(monkeypatch):
    user_id = await _create_user(email="sync-281@example.com", strava=True, strava_cursor=100)

    async def fake_fetch_recent_strava_activities(*args, **kwargs):
        return [
            {
                "id": 101,
                "name": "Morning Ride",
                "type": "Ride",
                "sport_type": "Ride",
                "start_date": "2026-06-10T06:00:00Z",
                "start_date_local": "2026-06-10T08:00:00",
                "elapsed_time": 3600,
            }
        ]

    async def fake_fetch_streams(*args, **kwargs):
        times = list(range(0, 3601, 60))
        return {"watts": {"data": [210] * len(times)}, "time": {"data": times}}

    async def fake_weather(*args, **kwargs):
        return {}

    async def fake_review(*args, **kwargs):
        return "ok", []

    async def fake_learn(db, user, **kwargs):
        return None

    async def broken_mark(db, *args, **kwargs):
        await _fail_a_flush(db)

    monkeypatch.setattr(
        activity_sync, "fetch_recent_strava_activities", fake_fetch_recent_strava_activities
    )
    monkeypatch.setattr(activity_sync, "fetch_strava_activity_streams", fake_fetch_streams)
    monkeypatch.setattr(activity_sync, "enrich_activity_weather", fake_weather)
    monkeypatch.setattr(activity_sync, "review_matched_ride_and_adapt", fake_review)
    monkeypatch.setattr(activity_sync, "learn_from_completed_workouts", fake_learn)
    monkeypatch.setattr(activity_sync, "mark_matched_days_completed", broken_mark)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await activity_sync.sync_strava_for_user(db, user)
        await db.commit()
    assert result.imported == 1

    async with TestSessionLocal() as db:
        assert await crud.get_latest_ride_metric(db, user_id) is not None
