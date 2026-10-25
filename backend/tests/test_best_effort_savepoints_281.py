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


@pytest.mark.asyncio
async def test_a_failing_reclassify_backfill_leaves_the_sync_session_usable(monkeypatch):
    """The backfill commits its retired ids itself, so its savepoint sits
    inside it, around everything before that commit."""
    from services.dates import app_today
    from services.intervals_service import intervals_activity_id

    user_id = await _create_user(
        email="reclassify-281@example.com",
        intervals=True,
        intervals_cursor=intervals_activity_id("cursor"),
    )
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=intervals_activity_id("i281"),
            activity_source="intervals",
            external_activity_id="i281",
            activity_date=app_today().isoformat(),
            sport_type="cycling",
            duration_seconds=3600,
            normalized_power_w=240,
            tss=80.0,
            ftp_used=250,
            ride_purpose="unknown",
            classification_confidence="low",
            classification_reason="Insufficient stream data to classify ride reliably.",
        )
        await crud.upsert_rider_assessment(db, user_id, estimated_ftp=250)
        await db.commit()

    async def fake_detail(api_key, activity_id):
        return {
            "icu_intervals": [
                {"type": "WORK", "moving_time": 240, "average_watts": 300},
                {"type": "RECOVERY", "moving_time": 180, "average_watts": 120},
                {"type": "WORK", "moving_time": 240, "average_watts": 305},
                {"type": "WORK", "moving_time": 240, "average_watts": 298},
            ]
        }

    async def broken_notify(db, user):
        await _fail_a_flush(db)

    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_detail", fake_detail)
    # The backfill tells the rides node what it changed (ai-trainer-ops#245).
    monkeypatch.setattr(activity_sync.rides_pipeline, "notify_changed", broken_notify)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        with pytest.raises(Exception):
            await activity_sync._reclassify_unknown_intervals_rides(db, user)
        # The rest of the tick goes on in the same session.
        await crud.upsert_rider_assessment(db, user_id, estimated_ftp=251)
        await db.commit()

    async with TestSessionLocal() as db:
        assert (await crud.get_rider_assessment(db, user_id)).estimated_ftp == 251


@pytest.mark.asyncio
async def test_a_failure_after_the_strava_refresh_keeps_the_rotated_token(
    client, auth_headers, mock_ai_service, monkeypatch
):
    """Strava revokes the old refresh token as soon as it hands out a new one, so
    a savepoint that rolled the rotation back would leave a dead token behind
    (review of the train, jrhahn/ai-trainer#1079)."""
    import routers.ai as ai_router
    from auth import decode_token

    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        user.strava_token = models.StravaToken(
            user_id=user_id,
            access_token="old-access",
            refresh_token="old-refresh",
            expires_at=0,
            athlete_id=1,
            athlete_name="Rider",
        )
        await db.commit()

    async def rotating_refresh(token, db):
        token.access_token = "new-access"
        token.refresh_token = "new-refresh"
        token.expires_at = 4102444800
        await db.flush()
        return "new-access"

    async def streams(*_args, **_kwargs):
        return {"watts": {"data": [200] * 10}, "time": {"data": list(range(10))}}

    def compare_breaks(*_args, **_kwargs):
        raise ValueError("the comparison broke after the refresh")

    monkeypatch.setattr(ai_router, "ensure_fresh_strava_token", rotating_refresh)
    monkeypatch.setattr(ai_router, "fetch_activity_streams", streams)
    monkeypatch.setattr(ai_router, "compare_planned_vs_actual", compare_breaks)

    response = await client.post(
        "/api/v1/ai/rate-workout",
        headers=auth_headers,
        json={
            "day": {
                "date": "2026-04-10",
                "workoutType": "endurance",
                "title": "Easy Ride",
                "description": "Easy aerobic ride",
                "durationMinutes": 90,
                "feedback": {
                    "actualDurationMinutes": 88,
                    "perceivedEffort": 2,
                    "completedAt": "2026-04-10T10:00:00Z",
                },
            },
            "stravaActivityId": 123,
        },
    )

    assert response.status_code == 200, response.text
    async with TestSessionLocal() as db:
        token = (await crud.get_user_by_id(db, user_id)).strava_token
    assert token.refresh_token == "new-refresh"


@pytest.mark.asyncio
async def test_a_failing_narration_keeps_the_adapted_plan(mock_ai_service, monkeypatch):
    """Before the savepoints a failed narration lost only the narration; the plan
    the feedback changed stood. It still does."""
    from datetime import timedelta

    import routers.ai as ai_router
    from services.dates import app_today
    from tests.test_plan_pipeline import _create_user as _create_planned_user
    from tests.test_plan_pipeline import _day

    day = (app_today() + timedelta(days=2)).isoformat()
    user_id = await _create_planned_user("adapt-281@example.com", [_day(day)])
    mock_ai_service["adapt_training_plan"].return_value = [
        {"date": day, "workoutType": "recovery", "title": "Recovery Spin", "durationMinutes": 30}
    ]

    async def narration_breaks(*_args, **_kwargs):
        raise RuntimeError("the narration broke after the commit")

    monkeypatch.setattr(ai_router.coach_summary, "narrate_plan_changes", narration_breaks)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = list((await crud.get_training_plan(db, user_id)).plan)
        await ai_router._auto_adapt_plan(
            db, user, plan, {"date": day, "feedback": {"perceivedEffort": 9}}, "gemini"
        )
        await db.commit()

    async with TestSessionLocal() as db:
        stored = {d["date"]: d for d in (await crud.get_training_plan(db, user_id)).plan}
    assert stored[day]["workoutType"] == "recovery"
