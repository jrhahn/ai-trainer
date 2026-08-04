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
async def test_strava_transient_stream_failure_not_imported_and_retried(monkeypatch):
    """A transient stream failure must not import degraded data or advance past it (#325)."""
    from services.strava_service import StravaStreamUnavailable

    user_id = await _create_user(
        email="stream-transient@example.com", strava=True, strava_cursor=100
    )

    async def fake_fetch_recent_strava_activities(*args, **kwargs):
        return [
            {
                "id": 101,
                "name": "Good Ride",
                "type": "Ride",
                "sport_type": "Ride",
                "start_date": "2026-06-10T08:00:00Z",
                "start_date_local": "2026-06-10T10:00:00",
                "elapsed_time": 3600,
            },
            {
                "id": 102,
                "name": "Streams down",
                "type": "Ride",
                "sport_type": "Ride",
                "start_date": "2026-06-11T08:00:00Z",
                "start_date_local": "2026-06-11T10:00:00",
                "elapsed_time": 3600,
            },
        ]

    async def fake_fetch_streams(access_token, activity_id):
        if activity_id == 102:
            raise StravaStreamUnavailable("429")
        return {"watts": {"data": [200]}, "time": {"data": [0]}}

    async def fake_weather(*args, **kwargs):
        return {}

    async def fake_review(*args, **kwargs):
        return "ok", []

    monkeypatch.setattr(activity_sync, "fetch_recent_strava_activities", fake_fetch_recent_strava_activities)
    monkeypatch.setattr(activity_sync, "fetch_strava_activity_streams", fake_fetch_streams)
    monkeypatch.setattr(activity_sync, "enrich_activity_weather", fake_weather)
    monkeypatch.setattr(activity_sync, "review_matched_ride_and_adapt", fake_review)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_strava_for_user(db, user)
        await db.commit()

    user = await _get_user(user_id)
    assert result.imported == 1  # only the good ride
    assert result.skipped == 1  # the transient one is skipped, not imported degraded
    # cursor must not advance to/past 102, so it is retried next tick
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
async def test_intervals_cursor_does_not_advance_past_failed_imports(monkeypatch):
    """Regression for #322: intervals cursor must stop below the first failed import.

    Intervals ids are content hashes, so the cursor can only move by list
    position. With a middle activity failing to map, the cursor must stop just
    below it so the failure — and everything newer — is retried next sync.
    """
    user_id = await _create_user(
        email="intervals-cursor-regression@example.com",
        intervals=True,
        intervals_cursor=intervals_activity_id("intervals-old"),
    )

    # Newest-first, exactly as intervals.icu returns them.
    async def fake_fetch_recent_intervals_activities(*args, **kwargs):
        return [
            {
                "id": "newest",
                "name": "Newest Ride",
                "type": "Ride",
                "start_date_local": "2026-06-12T08:00:00",
                "elapsed_time": 3600,
            },
            {
                "id": "bad",
                "name": "Broken Ride",
                "type": "Ride",
                "start_date_local": "2026-06-11T08:00:00",
                "elapsed_time": 3600,
            },
            {
                "id": "older-good",
                "name": "Older Ride",
                "type": "Ride",
                "start_date_local": "2026-06-10T08:00:00",
                "elapsed_time": 3600,
            },
        ]

    original_map = activity_sync.map_activity_to_imported_activity

    def failing_map(activity, detail, streams):
        if activity.get("id") == "bad":
            return None
        return original_map(activity, detail, streams)

    async def fake_detail(*args, **kwargs):
        return {}

    async def fake_streams(*args, **kwargs):
        return {}

    async def fake_persist(db, user, activities):
        return len(activities), 0

    monkeypatch.setattr(
        activity_sync,
        "fetch_recent_intervals_activities",
        fake_fetch_recent_intervals_activities,
    )
    monkeypatch.setattr(activity_sync, "map_activity_to_imported_activity", failing_map)
    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_detail", fake_detail)
    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_streams", fake_streams)
    monkeypatch.setattr(activity_sync, "_persist_and_adapt", fake_persist)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_intervals_for_user(db, user)
        await db.commit()

    user = await _get_user(user_id)
    assert result.imported == 2
    assert result.skipped == 1
    # cursor must stop at the older good ride, NOT jump to the newest one past
    # the failed "bad" activity in between
    assert user.last_intervals_activity_id == intervals_activity_id("older-good")
    assert user.last_intervals_activity_id != intervals_activity_id("newest")


@pytest.mark.asyncio
async def test_intervals_transient_stream_failure_not_imported_and_retried(monkeypatch):
    """A transient intervals failure skips the activity and holds the cursor (#352)."""
    from services.intervals_service import IntervalsDataUnavailable

    user_id = await _create_user(
        email="intervals-transient@example.com",
        intervals=True,
        intervals_cursor=intervals_activity_id("intervals-old"),
    )

    async def fake_fetch_recent_intervals_activities(*args, **kwargs):
        return [
            {"id": "newest", "name": "Newest", "type": "Ride", "start_date_local": "2026-06-12T08:00:00", "elapsed_time": 3600},
            {"id": "bad", "name": "Streams down", "type": "Ride", "start_date_local": "2026-06-11T08:00:00", "elapsed_time": 3600},
            {"id": "older-good", "name": "Older", "type": "Ride", "start_date_local": "2026-06-10T08:00:00", "elapsed_time": 3600},
        ]

    async def fake_detail(*args, **kwargs):
        return {}

    async def fake_streams(api_key, activity_id):
        if activity_id == "bad":
            raise IntervalsDataUnavailable("429")
        return {}

    async def fake_persist(db, user, activities):
        return len(activities), 0

    monkeypatch.setattr(activity_sync, "fetch_recent_intervals_activities", fake_fetch_recent_intervals_activities)
    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_detail", fake_detail)
    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_streams", fake_streams)
    monkeypatch.setattr(activity_sync, "_persist_and_adapt", fake_persist)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_intervals_for_user(db, user)
        await db.commit()

    user = await _get_user(user_id)
    assert result.imported == 2  # newest + older-good
    assert result.skipped == 1  # "bad" transiently failed — not imported degraded
    # cursor must stop below "bad" so it (and everything newer) is retried
    assert user.last_intervals_activity_id == intervals_activity_id("older-good")


@pytest.mark.asyncio
async def test_intervals_missing_activity_lets_the_cursor_move_past_it(monkeypatch):
    """A 404 during import is permanent, so the cursor must not stall on it (#517).

    Contrast with the transient case above: holding the cursor below a dead id
    would re-request it on every tick for as long as the account exists.
    """
    from services.intervals_service import IntervalsActivityNotFound

    user_id = await _create_user(
        email="intervals-gone@example.com",
        intervals=True,
        intervals_cursor=intervals_activity_id("intervals-old"),
    )

    async def fake_fetch_recent_intervals_activities(*args, **kwargs):
        return [
            {"id": "newest", "name": "Newest", "type": "Ride", "start_date_local": "2026-06-12T08:00:00", "elapsed_time": 3600},
            {"id": "gone", "name": "Deleted", "type": "Ride", "start_date_local": "2026-06-11T08:00:00", "elapsed_time": 3600},
            {"id": "older-good", "name": "Older", "type": "Ride", "start_date_local": "2026-06-10T08:00:00", "elapsed_time": 3600},
        ]

    detail_calls: list[str] = []

    async def fake_detail(api_key, activity_id):
        detail_calls.append(activity_id)
        if activity_id == "gone":
            raise IntervalsActivityNotFound("no activity gone")
        return {}

    async def fake_streams(api_key, activity_id):
        return {}

    async def fake_persist(db, user, activities):
        return len(activities), 0

    monkeypatch.setattr(activity_sync, "fetch_recent_intervals_activities", fake_fetch_recent_intervals_activities)
    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_detail", fake_detail)
    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_streams", fake_streams)
    monkeypatch.setattr(activity_sync, "_persist_and_adapt", fake_persist)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_intervals_for_user(db, user)
        await db.commit()

    user = await _get_user(user_id)
    assert result.imported == 2  # newest + older-good
    assert result.skipped == 1  # the dead one
    assert "gone" in detail_calls
    # The cursor clears the dead activity instead of stalling below it.
    assert user.last_intervals_activity_id == intervals_activity_id("newest")


@pytest.mark.asyncio
async def test_intervals_cursor_stays_put_when_all_activities_fail_mapping(monkeypatch):
    """Regression for #322: intervals cursor must not move when every import fails."""
    original_cursor = intervals_activity_id("intervals-old")
    user_id = await _create_user(
        email="intervals-cursor-allbad@example.com",
        intervals=True,
        intervals_cursor=original_cursor,
    )

    async def fake_fetch_recent_intervals_activities(*args, **kwargs):
        return [
            {
                "id": "bad-1",
                "name": "Broken Ride 1",
                "type": "Ride",
                "start_date_local": "2026-06-12T08:00:00",
                "elapsed_time": 3600,
            },
            {
                "id": "bad-2",
                "name": "Broken Ride 2",
                "type": "Ride",
                "start_date_local": "2026-06-11T08:00:00",
                "elapsed_time": 3600,
            },
        ]

    monkeypatch.setattr(
        activity_sync,
        "fetch_recent_intervals_activities",
        fake_fetch_recent_intervals_activities,
    )
    monkeypatch.setattr(
        activity_sync, "map_activity_to_imported_activity", lambda *_: None
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_intervals_for_user(db, user)
        await db.commit()

    user = await _get_user(user_id)
    assert result.imported == 0
    # cursor must remain exactly where it was
    assert user.last_intervals_activity_id == original_cursor


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


@pytest.mark.asyncio
async def test_import_triggers_continuous_learning(monkeypatch):
    """A freshly imported workout runs one learning step for the athlete (#388)."""
    user_id = await _create_user(
        email="learn-on-import@example.com", strava=True, strava_cursor=100
    )
    learned_for: list[str] = []

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
        return {"watts": {"data": [200, 210, 220]}, "time": {"data": [0, 60, 120]}}

    async def fake_weather(*args, **kwargs):
        return {}

    async def fake_review(*args, **kwargs):
        return "ok", []

    async def fake_learn(db, user, **kwargs):
        learned_for.append(user.id)

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
    monkeypatch.setattr(activity_sync, "learn_from_completed_workouts", fake_learn)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_strava_for_user(db, user)

    assert result.imported == 1
    assert learned_for == [user_id]


@pytest.mark.asyncio
async def test_no_import_skips_continuous_learning(monkeypatch):
    """Learning is not triggered on a sync that imports nothing new."""
    user_id = await _create_user(
        email="learn-noop@example.com", strava=True, strava_cursor=100
    )
    learn_calls = 0

    async def fake_fetch_recent_strava_activities(*args, **kwargs):
        return []  # nothing newer than the cursor

    async def fake_learn(db, user, **kwargs):
        nonlocal learn_calls
        learn_calls += 1

    monkeypatch.setattr(
        activity_sync,
        "fetch_recent_strava_activities",
        fake_fetch_recent_strava_activities,
    )
    monkeypatch.setattr(activity_sync, "learn_from_completed_workouts", fake_learn)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        assert user is not None
        result = await activity_sync.sync_strava_for_user(db, user)

    assert result.imported == 0
    assert learn_calls == 0


@pytest.mark.asyncio
async def test_reclassify_backfill_upgrades_unknown_intervals_ride(monkeypatch):
    """A still-'unknown' intervals ride is reclassified from provider laps while
    preserving athlete edits, and the login summary is invalidated (#482)."""
    from services.dates import app_today

    user_id = await _create_user(
        email="reclassify@example.com",
        intervals=True,
        intervals_cursor=intervals_activity_id("cursor"),
    )
    today = app_today()

    async with TestSessionLocal() as db:
        # An in-window intervals ride the old classifier left as unknown/low.
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=intervals_activity_id("i777"),
            activity_source="intervals",
            external_activity_id="i777",
            activity_date=today.isoformat(),
            sport_type="cycling",
            duration_seconds=6072,
            normalized_power_w=242,
            tss=96.0,
            ftp_used=250,
            ride_purpose="unknown",
            classification_confidence="low",
            classification_reason="Insufficient stream data to classify ride reliably.",
        )
        # An athlete edit that must survive reclassification.
        row = await crud.get_ride_metric_by_external_id(db, user_id, "i777")
        row.label_override = "Perfect"
        # A cached summary that must be cleared when a ride is reclassified.
        await crud.upsert_rider_assessment(
            db, user_id, estimated_ftp=250, login_summary="stale summary"
        )
        await db.commit()

    async def fake_detail(api_key, activity_id):
        assert activity_id == "i777"
        return {
            "icu_intervals": [
                {"type": "WORK", "moving_time": 240, "average_watts": 300},
                {"type": "RECOVERY", "moving_time": 180, "average_watts": 120},
                {"type": "WORK", "moving_time": 240, "average_watts": 305},
                {"type": "WORK", "moving_time": 240, "average_watts": 298},
                {"type": "WORK", "moving_time": 240, "average_watts": 302},
            ]
        }

    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_detail", fake_detail)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        changed = await activity_sync._reclassify_unknown_intervals_rides(db, user)
        await db.commit()

    assert changed == 1
    async with TestSessionLocal() as db:
        row = await crud.get_ride_metric_by_external_id(db, user_id, "i777")
        assert row.ride_purpose == "interval_vo2max"
        assert row.classification_confidence == "high"
        assert row.label_override == "Perfect"  # edit preserved
        assessment = await crud.get_rider_assessment(db, user_id)
        assert not assessment.login_summary  # invalidated


@pytest.mark.asyncio
async def test_reclassify_backfill_retires_ids_the_provider_lost(monkeypatch):
    """A 404 retires the row instead of being re-requested on the next tick.

    The float64-corrupted ids from #427 can never resolve again, so the backfill
    used to burn a candidate slot and a provider request on each of them every
    30 minutes, forever (#517).
    """
    from services.dates import app_today
    from services.intervals_service import IntervalsActivityNotFound

    user_id = await _create_user(
        email="reclassify-dead@example.com",
        intervals=True,
        intervals_cursor=intervals_activity_id("cursor"),
    )
    dead_id = "978266860298001700"

    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=int(dead_id),
            activity_source="intervals",
            external_activity_id=dead_id,
            activity_date=app_today().isoformat(),
            sport_type="cycling",
            duration_seconds=6259,
            tss=88.0,
            ftp_used=250,
            ride_purpose="unknown",
        )
        await db.commit()

    requested: list[str] = []

    async def fake_detail(api_key, activity_id):
        requested.append(activity_id)
        raise IntervalsActivityNotFound(f"no activity {activity_id}")

    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_detail", fake_detail)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        changed = await activity_sync._reclassify_unknown_intervals_rides(db, user)
        await db.commit()

    assert changed == 0
    assert requested == [dead_id]

    async with TestSessionLocal() as db:
        row = await crud.get_ride_metric_by_external_id(db, user_id, dead_id)
        assert row.provider_unfetchable_at is not None
        # The ride's own imported metrics are untouched by the marker.
        assert row.tss == 88.0

    # Second tick: the retired row is no longer a candidate.
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await activity_sync._reclassify_unknown_intervals_rides(db, user)

    assert requested == [dead_id]  # not asked again


@pytest.mark.asyncio
async def test_reclassify_backfill_commits_retirement_before_the_rest_of_the_sync(
    monkeypatch,
):
    """The marker survives a later failure in the same sync tick (#517).

    sync_intervals_for_user runs the backfill first and only commits at the very
    end, so without an eager commit a failing list fetch would roll the markers
    back and the next tick would repeat the same 404s.
    """
    from services.dates import app_today
    from services.intervals_service import (
        IntervalsAPIError,
        IntervalsActivityNotFound,
    )

    user_id = await _create_user(
        email="reclassify-dead-then-fail@example.com",
        intervals=True,
        intervals_cursor=intervals_activity_id("cursor"),
    )
    dead_id = "6755760447976027000"

    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=int(dead_id),
            activity_source="intervals",
            external_activity_id=dead_id,
            activity_date=app_today().isoformat(),
            sport_type="cycling",
            duration_seconds=343,
            ftp_used=250,
            ride_purpose="unknown",
        )
        await db.commit()

    async def fake_detail(api_key, activity_id):
        raise IntervalsActivityNotFound(f"no activity {activity_id}")

    async def failing_list(*args, **kwargs):
        raise IntervalsAPIError("Intervals.icu list error 500")

    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_detail", fake_detail)
    monkeypatch.setattr(
        activity_sync, "fetch_recent_intervals_activities", failing_list
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        with pytest.raises(IntervalsAPIError):
            await activity_sync.sync_intervals_for_user(db, user)
        await db.rollback()  # what run_activity_sync's error path leaves behind

    async with TestSessionLocal() as db:
        row = await crud.get_ride_metric_by_external_id(db, user_id, dead_id)
        assert row.provider_unfetchable_at is not None


@pytest.mark.asyncio
async def test_reclassify_backfill_skips_rides_outside_window(monkeypatch):
    """Rides older than the backfill window are never re-fetched."""
    from datetime import timedelta

    from services.dates import app_today

    user_id = await _create_user(
        email="reclassify-old@example.com",
        intervals=True,
        intervals_cursor=intervals_activity_id("cursor"),
    )
    old_date = (
        app_today()
        - timedelta(days=activity_sync.INTERVALS_RECLASSIFY_WINDOW_DAYS + 5)
    ).isoformat()

    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=intervals_activity_id("i-old"),
            activity_source="intervals",
            external_activity_id="i-old",
            activity_date=old_date,
            sport_type="cycling",
            duration_seconds=6072,
            ftp_used=250,
            ride_purpose="unknown",
        )
        await db.commit()

    fetched = False

    async def fake_detail(api_key, activity_id):
        nonlocal fetched
        fetched = True
        return {}

    monkeypatch.setattr(activity_sync, "fetch_intervals_activity_detail", fake_detail)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        changed = await activity_sync._reclassify_unknown_intervals_rides(db, user)

    assert changed == 0
    assert fetched is False  # out-of-window ride never re-fetched
