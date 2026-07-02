from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from auth import decode_token
from database import async_session_maker
import models
import schemas
from routers import intervals as intervals_router
from services.intervals_service import IntervalsAuthError, intervals_activity_id


@pytest.mark.asyncio
async def test_intervals_connection_can_be_configured(client, auth_headers):
    response = await client.put(
        "/api/v1/intervals/connection",
        headers=auth_headers,
        json={
            "apiKey": "secret-intervals-key",
            "athleteId": "0",
            "athleteName": "Intervals Rider",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "athleteId": "0",
        "athleteName": "Intervals Rider",
    }

    profile = await client.get("/api/v1/users/me", headers=auth_headers)
    assert profile.status_code == 200
    assert profile.json()["intervalsConnection"] == {
        "athleteId": "0",
        "athleteName": "Intervals Rider",
    }
    assert profile.json()["intervalsAnalysisComplete"] is False
    assert profile.json()["lastIntervalsActivityId"] is None


@pytest.mark.asyncio
async def test_get_intervals_activities_returns_items_until_cursor(
    client, auth_headers, monkeypatch
):
    async def fake_fetch_recent_activities(*args, **kwargs):
        return [
            {
                "id": "new",
                "name": "New Ride",
                "type": "Ride",
                "start_date_local": "2026-06-07T08:00:00",
                "moving_time": 1800,
                "average_watts": 220,
            },
            {
                "id": "old",
                "name": "Already Seen Ride",
                "type": "Ride",
                "start_date_local": "2026-06-06T08:00:00",
                "moving_time": 1800,
                "average_watts": 180,
            },
        ]

    monkeypatch.setattr(
        intervals_router, "fetch_recent_activities", fake_fetch_recent_activities
    )

    await client.put(
        "/api/v1/intervals/connection",
        headers=auth_headers,
        json={"apiKey": "secret", "athleteId": "0"},
    )
    old_cursor = intervals_activity_id("old")
    response = await client.get(
        f"/api/v1/intervals/activities?after_id={old_cursor}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert [activity["name"] for activity in body] == ["New Ride"]
    assert body[0]["id"] == intervals_activity_id("new")
    assert body[0]["distance"] == 0
    assert body[0]["total_elevation_gain"] == 0
    schemas.StravaActivitySchema.model_validate(body[0])


@pytest.mark.asyncio
async def test_get_intervals_activities_includes_current_app_day(
    client, auth_headers, monkeypatch
):
    captured: dict[str, object] = {}

    async def fake_fetch_recent_activities(*args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(intervals_router, "app_today", lambda: date(2026, 6, 10))
    monkeypatch.setattr(
        intervals_router, "fetch_recent_activities", fake_fetch_recent_activities
    )

    await client.put(
        "/api/v1/intervals/connection",
        headers=auth_headers,
        json={"apiKey": "secret", "athleteId": "0"},
    )
    response = await client.get(
        "/api/v1/intervals/activities?months=1",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert captured["oldest"] == date(2026, 5, 11)
    assert captured["newest"] == date(2026, 6, 11)


@pytest.mark.asyncio
async def test_get_intervals_activities_accepts_js_rounded_cursor(
    client, auth_headers, monkeypatch
):
    async def fake_fetch_recent_activities(*args, **kwargs):
        return [
            {
                "id": "i156039190",
                "name": "Mittelberg Hiking",
                "type": "Hike",
                "start_date_local": "2026-06-10T09:26:13",
                "moving_time": 7200,
            },
            {
                "id": "i154017725",
                "name": "Darmstadt Road Cycling",
                "type": "Ride",
                "start_date_local": "2026-06-03T17:25:00",
                "moving_time": 3600,
            },
        ]

    monkeypatch.setattr(
        intervals_router, "fetch_recent_activities", fake_fetch_recent_activities
    )

    await client.put(
        "/api/v1/intervals/connection",
        headers=auth_headers,
        json={"apiKey": "secret", "athleteId": "0"},
    )
    response = await client.get(
        "/api/v1/intervals/activities?after_id=8736852676899880000",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert [activity["name"] for activity in body] == ["Mittelberg Hiking"]
    assert body[0]["type"] == "Hike"


def test_intervals_activity_response_defaults_match_analysis_schema():
    activity = intervals_router._activity_response(
        {
            "id": "no-distance",
            "name": "No Distance Ride",
            "type": "Ride",
            "start_date_local": "2026-06-07T08:00:00",
            "moving_time": 1800,
        }
    )

    parsed = schemas.StravaActivitySchema.model_validate(activity)

    assert parsed.distance == 0
    assert parsed.total_elevation_gain == 0
    assert parsed.moving_time == 1800


@pytest.mark.asyncio
async def test_intervals_import_success_and_dedupe(auth_headers, monkeypatch):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    async def fake_fetch_recent_activities(*args, **kwargs):
        return [
            {
                "id": "i123",
                "name": "Garmin Morning Ride",
                "type": "Ride",
                "start_date_local": "2026-06-01T07:30:00",
                "moving_time": 240,
            }
        ]

    async def fake_fetch_activity_detail(*args, **kwargs):
        return {"average_watts": 210, "icu_training_load": 12.5}

    async def fake_fetch_activity_streams(*args, **kwargs):
        return {
            "watts": [200, 220, 210, 230],
            "time": [0, 60, 120, 180],
            "heartrate": [145, 150, 152, 154],
        }

    monkeypatch.setattr(
        intervals_router, "fetch_recent_activities", fake_fetch_recent_activities
    )
    monkeypatch.setattr(
        intervals_router, "fetch_activity_detail", fake_fetch_activity_detail
    )
    monkeypatch.setattr(
        intervals_router, "fetch_activity_streams", fake_fetch_activity_streams
    )

    for _ in range(2):
        await intervals_router.run_intervals_import(
            user_id=user_id,
            api_key="secret",
            athlete_id="0",
            oldest=date(2026, 6, 1),
            newest=date(2026, 6, 7),
            ftp=280,
        )

    progress = intervals_router._intervals_import_progress[user_id]
    assert progress["status"] == "done"
    assert progress["imported"] == 1

    async with async_session_maker() as session:
        rows = list(
            await session.scalars(
                select(models.RideMetric).where(models.RideMetric.user_id == user_id)
            )
        )

    assert len(rows) == 1
    ride = rows[0]
    assert ride.strava_activity_id == intervals_activity_id("i123")
    assert ride.activity_name == "Garmin Morning Ride"
    assert ride.avg_power_w == 215
    assert ride.tss is not None


@pytest.mark.asyncio
async def test_intervals_import_auth_failure_sets_error(auth_headers, monkeypatch):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    async def fake_fetch_recent_activities(*args, **kwargs):
        raise IntervalsAuthError("Intervals.icu credentials were rejected")

    monkeypatch.setattr(
        intervals_router, "fetch_recent_activities", fake_fetch_recent_activities
    )

    await intervals_router.run_intervals_import(
        user_id=user_id,
        api_key="bad-secret",
        athlete_id="0",
        oldest=date(2026, 6, 1),
        newest=date(2026, 6, 7),
        ftp=280,
    )

    progress = intervals_router._intervals_import_progress[user_id]
    assert progress["status"] == "error"
    assert "credentials were rejected" in progress["error"]


@pytest.mark.asyncio
async def test_intervals_import_summary_only_when_streams_missing(
    auth_headers, monkeypatch
):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    async def fake_fetch_recent_activities(*args, **kwargs):
        return [
            {
                "id": "i-summary",
                "name": "Summary Only Ride",
                "type": "Ride",
                "start_date_local": "2026-06-02T08:00:00",
                "moving_time": 3600,
                "average_watts": 180,
                "icu_weighted_avg_watts": 190,
                "icu_training_load": 42.0,
            }
        ]

    async def fake_fetch_activity_detail(*args, **kwargs):
        return {}

    async def fake_fetch_activity_streams(*args, **kwargs):
        return {}

    monkeypatch.setattr(
        intervals_router, "fetch_recent_activities", fake_fetch_recent_activities
    )
    monkeypatch.setattr(
        intervals_router, "fetch_activity_detail", fake_fetch_activity_detail
    )
    monkeypatch.setattr(
        intervals_router, "fetch_activity_streams", fake_fetch_activity_streams
    )

    await intervals_router.run_intervals_import(
        user_id=user_id,
        api_key="secret",
        athlete_id="0",
        oldest=date(2026, 6, 1),
        newest=date(2026, 6, 7),
        ftp=250,
    )

    async with async_session_maker() as session:
        ride = await session.scalar(
            select(models.RideMetric).where(models.RideMetric.user_id == user_id)
        )

    assert ride is not None
    assert ride.activity_name == "Summary Only Ride"
    assert ride.avg_power_w == 180
    assert ride.normalized_power_w == 190
    assert ride.tss == 42.0
    assert ride.ride_purpose == "unknown"


@pytest.mark.asyncio
async def test_intervals_import_skips_activity_on_transient_failure(
    auth_headers, monkeypatch
):
    """A transient intervals failure is surfaced in failed_activities, not imported degraded (#352)."""
    from services.intervals_service import IntervalsDataUnavailable

    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    async def fake_fetch_recent_activities(*args, **kwargs):
        return [
            {
                "id": "i-transient",
                "name": "Streams down",
                "type": "Ride",
                "start_date_local": "2026-06-02T08:00:00",
                "moving_time": 3600,
            }
        ]

    async def fake_fetch_activity_detail(*args, **kwargs):
        return {}

    async def fake_fetch_activity_streams(*args, **kwargs):
        raise IntervalsDataUnavailable("transient 429")

    monkeypatch.setattr(
        intervals_router, "fetch_recent_activities", fake_fetch_recent_activities
    )
    monkeypatch.setattr(
        intervals_router, "fetch_activity_detail", fake_fetch_activity_detail
    )
    monkeypatch.setattr(
        intervals_router, "fetch_activity_streams", fake_fetch_activity_streams
    )

    await intervals_router.run_intervals_import(
        user_id=user_id,
        api_key="secret",
        athlete_id="0",
        oldest=date(2026, 6, 1),
        newest=date(2026, 6, 7),
        ftp=250,
    )

    progress = intervals_router._intervals_import_progress[user_id]
    assert progress["status"] == "done"
    assert progress["imported"] == 0
    assert len(progress["failed_activities"]) == 1
    assert "retry" in progress["failed_activities"][0]["reason"].lower()

    async with async_session_maker() as session:
        ride = await session.scalar(
            select(models.RideMetric).where(models.RideMetric.user_id == user_id)
        )
    assert ride is None  # nothing imported with degraded data
