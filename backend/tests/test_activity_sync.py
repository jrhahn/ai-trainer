from __future__ import annotations

import time

import pytest

import crud
import models
from auth import hash_password
from services import activity_sync
from services.intervals_service import intervals_activity_id
from tests.conftest import TestSessionLocal


def _day(date: str) -> dict:
    return {
        "date": date,
        "workoutType": "endurance",
        "title": "Endurance Ride",
        "description": "Steady ride",
        "durationMinutes": 60,
        "completed": False,
    }


async def _create_user(
    *,
    email: str,
    strava: bool = False,
    intervals: bool = False,
    strava_cursor: int | None = None,
    intervals_cursor: int | None = None,
    strava_enabled: bool = True,
    intervals_enabled: bool = True,
) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Sync Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
            last_strava_activity_id=strava_cursor,
            last_intervals_activity_id=intervals_cursor,
            strava_auto_sync_enabled=strava_enabled,
            intervals_auto_sync_enabled=intervals_enabled,
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, [_day("2026-06-10")])
        if strava:
            user.strava_token = models.StravaToken(
                user_id=user.id,
                access_token="access",
                refresh_token="refresh",
                expires_at=int(time.time()) + 3600,
                athlete_id=123,
                athlete_name="Sync Rider",
            )
        if intervals:
            user.intervals_token = models.IntervalsToken(
                user_id=user.id,
                api_key="intervals-key",
                athlete_id="i123",
                athlete_name="Sync Rider",
            )
        await db.commit()
        return user.id


async def _get_user(user_id: str) -> models.User:
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        return user


@pytest.mark.asyncio
async def test_activity_sync_skips_disabled_intervals_source(monkeypatch):
    user_id = await _create_user(
        email="disabled-intervals@example.com",
        intervals=True,
        intervals_cursor=intervals_activity_id("old"),
        intervals_enabled=False,
    )
    called = False

    async def fake_fetch_recent_intervals_activities(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        activity_sync,
        "fetch_recent_intervals_activities",
        fake_fetch_recent_intervals_activities,
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_intervals_for_user(db, user)

    assert result.skipped == 1
    assert called is False


@pytest.mark.asyncio
async def test_activity_sync_imports_strava_and_keeps_intervals_cursor(monkeypatch):
    user_id = await _create_user(
        email="strava-sync@example.com",
        strava=True,
        intervals=True,
        strava_cursor=100,
        intervals_cursor=intervals_activity_id("intervals-old"),
    )
    review_calls = 0

    async def fake_fetch_recent_strava_activities(*args, **kwargs):
        return [
            {
                "id": 101,
                "name": "Morning Ride",
                "type": "Ride",
                "sport_type": "Ride",
                "start_date": "2026-06-10T08:00:00Z",
                "start_date_local": "2026-06-10T10:00:00",
                "elapsed_time": 3600,
            }
        ]

    async def fake_fetch_streams(*args, **kwargs):
        return {
            "watts": {"data": [200, 210, 220]},
            "time": {"data": [0, 60, 120]},
        }

    async def fake_weather(*args, **kwargs):
        return {}

    async def fake_review(*args, **kwargs):
        nonlocal review_calls
        review_calls += 1
        return "ok", []

    monkeypatch.setattr(
        activity_sync,
        "fetch_recent_strava_activities",
        fake_fetch_recent_strava_activities,
    )
    monkeypatch.setattr(
        activity_sync, "fetch_strava_activity_streams", fake_fetch_streams
    )
    monkeypatch.setattr(activity_sync, "enrich_activity_weather", fake_weather)
    monkeypatch.setattr(activity_sync, "review_matched_ride_and_adapt", fake_review)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_strava_for_user(db, user)
        await db.commit()

    user = await _get_user(user_id)
    assert result.imported == 1
    assert result.failed == 0
    assert user.last_strava_activity_id == 101
    assert user.last_intervals_activity_id == intervals_activity_id("intervals-old")
    assert review_calls == 1


@pytest.mark.asyncio
async def test_activity_sync_skips_duplicate_intervals_activity(monkeypatch):
    existing_id = intervals_activity_id("new")
    user_id = await _create_user(
        email="duplicate-intervals@example.com",
        intervals=True,
        intervals_cursor=intervals_activity_id("old"),
    )
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=existing_id,
            activity_date="2026-06-10",
            activity_name="Already Imported",
        )
        await db.commit()

    async def fake_fetch_recent_intervals_activities(*args, **kwargs):
        return [
            {
                "id": "new",
                "name": "Already Imported",
                "type": "Ride",
                "start_date_local": "2026-06-10T08:00:00",
                "elapsed_time": 3600,
            },
            {
                "id": "old",
                "name": "Old Ride",
                "type": "Ride",
                "start_date_local": "2026-06-09T08:00:00",
                "elapsed_time": 3600,
            },
        ]

    async def fail_review(*args, **kwargs):
        raise AssertionError("duplicate activity should not be adapted")

    monkeypatch.setattr(
        activity_sync,
        "fetch_recent_intervals_activities",
        fake_fetch_recent_intervals_activities,
    )
    monkeypatch.setattr(activity_sync, "review_matched_ride_and_adapt", fail_review)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_intervals_for_user(db, user)
        await db.commit()

    user = await _get_user(user_id)
    assert result.imported == 0
    assert result.skipped == 1
    assert user.last_intervals_activity_id == existing_id


@pytest.mark.asyncio
async def test_strava_cursor_does_not_advance_past_failed_imports(monkeypatch):
    """Regression for #301: cursor must only advance to max *successfully imported* ID."""
    user_id = await _create_user(
        email="cursor-regression@example.com",
        strava=True,
        strava_cursor=100,
    )

    async def fake_fetch_recent_strava_activities(*args, **kwargs):
        return [
            # id=102 will fail mapping; id=101 will succeed
            {
                "id": 102,
                "name": "Bad Ride",
                "type": "Ride",
                "sport_type": "Ride",
                "start_date": "2026-06-11T08:00:00Z",
                "start_date_local": "2026-06-11T10:00:00",
                "elapsed_time": 3600,
            },
            {
                "id": 101,
                "name": "Good Ride",
                "type": "Ride",
                "sport_type": "Ride",
                "start_date": "2026-06-10T08:00:00Z",
                "start_date_local": "2026-06-10T10:00:00",
                "elapsed_time": 3600,
            },
        ]

    async def fake_fetch_streams(*args, **kwargs):
        return {"watts": {"data": [200]}, "time": {"data": [0]}}

    async def fake_weather(*args, **kwargs):
        return {}

    async def fake_review(*args, **kwargs):
        return "ok", []

    original_map = activity_sync._strava_activity_to_imported_activity

    def failing_map(activity, streams, weather):
        if activity["id"] == 102:
            return None
        return original_map(activity, streams, weather)

    monkeypatch.setattr(activity_sync, "fetch_recent_strava_activities", fake_fetch_recent_strava_activities)
    monkeypatch.setattr(activity_sync, "fetch_strava_activity_streams", fake_fetch_streams)
    monkeypatch.setattr(activity_sync, "enrich_activity_weather", fake_weather)
    monkeypatch.setattr(activity_sync, "review_matched_ride_and_adapt", fake_review)
    monkeypatch.setattr(activity_sync, "_strava_activity_to_imported_activity", failing_map)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_strava_for_user(db, user)
        await db.commit()

    user = await _get_user(user_id)
    assert result.imported == 1
    assert result.skipped == 1
    # cursor must not jump to 102 (the failed activity) — only to 101
    assert user.last_strava_activity_id == 101


@pytest.mark.asyncio
async def test_strava_cursor_stays_put_when_all_activities_fail_mapping(monkeypatch):
    """Regression for #301: cursor must not move at all when every activity fails mapping."""
    user_id = await _create_user(
        email="cursor-allbad@example.com",
        strava=True,
        strava_cursor=100,
    )

    async def fake_fetch_recent_strava_activities(*args, **kwargs):
        return [
            {
                "id": 200,
                "name": "Bad Ride",
                "type": "Ride",
                "sport_type": "Ride",
                "start_date": "2026-06-10T08:00:00Z",
                "start_date_local": "2026-06-10T10:00:00",
                "elapsed_time": 3600,
            }
        ]

    async def fake_fetch_streams(*args, **kwargs):
        return {}

    async def fake_weather(*args, **kwargs):
        return {}

    monkeypatch.setattr(activity_sync, "fetch_recent_strava_activities", fake_fetch_recent_strava_activities)
    monkeypatch.setattr(activity_sync, "fetch_strava_activity_streams", fake_fetch_streams)
    monkeypatch.setattr(activity_sync, "enrich_activity_weather", fake_weather)
    monkeypatch.setattr(activity_sync, "_strava_activity_to_imported_activity", lambda *_: None)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_strava_for_user(db, user)
        await db.commit()

    user = await _get_user(user_id)
    assert result.imported == 0
    # cursor must remain at 100, not advance to 200
    assert user.last_strava_activity_id == 100


@pytest.mark.asyncio
async def test_activity_sync_isolates_per_user_source_failures(monkeypatch):
    failing_id = await _create_user(
        email="activity-sync-failing@example.com",
        strava=True,
        strava_cursor=100,
    )
    ok_id = await _create_user(
        email="activity-sync-ok@example.com",
        strava=True,
        strava_cursor=100,
    )

    async def fake_strava(db, user):
        if user.id == failing_id:
            raise RuntimeError("boom")
        return activity_sync.SourceSyncResult(source="strava", checked=1, imported=1)

    async def fake_intervals(db, user):
        return activity_sync.SourceSyncResult(source="intervals", checked=1, skipped=1)

    monkeypatch.setattr(activity_sync, "sync_strava_for_user", fake_strava)
    monkeypatch.setattr(activity_sync, "sync_intervals_for_user", fake_intervals)

    result = await activity_sync.run_activity_sync(TestSessionLocal)

    assert ok_id != failing_id
    assert result.users == 2
    assert result.imported == 1
    assert result.failed == 1
