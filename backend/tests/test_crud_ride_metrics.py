"""Unit tests for the crud data-access layer: ride metrics, metric snapshots, and the rides still to review.

Split out of ``test_crud.py`` (ai-trainer-ops#259); its history is there.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db: AsyncSession, email: str = "test@example.com") -> models.User:
    return await crud.create_user(
        db, email=email, name="Test User", hashed_password="hashed"
    )


@pytest.mark.asyncio
async def test_get_ride_metrics_by_activity_ids_accepts_external_activity_id(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=7629419622326427463,
        external_activity_id="i157147093",
        activity_date="2026-06-14",
        activity_start_datetime="2026-06-14T08:19:31",
        activity_name="Oberursel (Taunus) Mountain Biking",
        sport_type="MountainBikeRide",
    )

    rides = await crud.get_ride_metrics_by_activity_ids(db, user.id, ["i157147093"])

    assert len(rides) == 1
    assert rides[0].activity_name == "Oberursel (Taunus) Mountain Biking"


@pytest.mark.asyncio
async def test_upsert_ride_metric_uses_source_external_identity_for_imports(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=7629419622326427463,
        activity_source="intervals",
        external_activity_id="i157147093",
        activity_date="2026-06-14",
        activity_start_datetime="2026-06-14T08:19:31",
        activity_name="Original imported activity",
        sport_type="MountainBikeRide",
    )
    second = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=7629419622326427000,
        activity_source="intervals",
        external_activity_id="i157147093",
        activity_date="2026-06-14",
        activity_start_datetime="2026-06-14T08:19:31",
        activity_name="Updated imported activity",
        sport_type="MountainBikeRide",
    )

    history = await crud.get_ride_metrics_history(db, user.id)

    assert second.id == first.id
    assert second.strava_activity_id == 7629419622326427000
    assert len(history) == 1
    assert history[0].activity_name == "Updated imported activity"


@pytest.mark.asyncio
async def test_upsert_ride_metric_reuses_near_duplicate_activity(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=7629419622326427463,
        activity_source="strava",
        external_activity_id="7629419622326427463",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T08:19:31",
        activity_name="Darmstadt Mountain Biking",
        sport_type="Ride",
        duration_seconds=10800,
        summary="first analysis",
    )
    second = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=7629419622326427000,
        activity_source="intervals",
        external_activity_id="i157147093",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T08:19:45",
        activity_name="Darmstadt Mountain Biking",
        sport_type="MountainBikeRide",
        duration_seconds=10812,
        summary="canonical analysis",
    )

    history = await crud.get_ride_metrics_history(db, user.id)

    assert second.id == first.id
    assert second.strava_activity_id == 7629419622326427000
    assert len(history) == 1
    assert history[0].summary == "canonical analysis"


@pytest.mark.asyncio
async def test_get_ride_metrics_history_collapses_stale_near_duplicates(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    db.add_all(
        [
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62001,
                activity_source="strava",
                external_activity_id="62001",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T08:19:31",
                activity_name="Darmstadt Mountain Biking",
                sport_type="Ride",
                duration_seconds=10800,
            ),
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62002,
                activity_source="intervals",
                external_activity_id="i157147093",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T08:19:45",
                activity_name="Darmstadt Mountain Biking",
                sport_type="MountainBikeRide",
                duration_seconds=10812,
            ),
        ]
    )
    await db.flush()

    history = await crud.get_ride_metrics_history(db, user.id)

    assert len(history) == 1
    assert history[0].strava_activity_id in {62001, 62002}


@pytest.mark.asyncio
async def test_get_ride_metrics_history_collapses_duration_drift_duplicates(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    db.add_all(
        [
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62101,
                activity_source="strava",
                external_activity_id="62101",
                activity_date="2026-06-21",
                activity_start_datetime="2026-06-21T08:03:21+02:00",
                activity_name="Darmstadt Mountain Biking",
                sport_type="MountainBikeRide",
                duration_seconds=96 * 60,
            ),
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62102,
                activity_source="intervals",
                external_activity_id="i62102",
                activity_date="2026-06-21",
                activity_start_datetime="2026-06-21T08:05:00",
                activity_name="Darmstadt Mountain Biking",
                sport_type="cycling",
                duration_seconds=99 * 60,
            ),
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62020,
                activity_source="strava",
                external_activity_id="62020",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T09:00:00+02:00",
                activity_name="Darmstadt Road Cycling",
                sport_type="Ride",
                duration_seconds=193 * 60,
            ),
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62021,
                activity_source="intervals",
                external_activity_id="i62021",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T09:08:00",
                activity_name="Darmstadt Road Cycling",
                sport_type="cycling",
                duration_seconds=205 * 60,
            ),
        ]
    )
    await db.flush()

    history = await crud.get_ride_metrics_history(db, user.id)

    assert len(history) == 2
    assert {ride.activity_name for ride in history} == {
        "Darmstadt Mountain Biking",
        "Darmstadt Road Cycling",
    }


@pytest.mark.asyncio
async def test_get_ride_metrics_history_collapses_contained_ride_duplicate(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    db.add_all(
        [
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62301,
                activity_source="strava",
                external_activity_id="62301",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T09:00:00",
                activity_name="Darmstadt Road Cycling",
                sport_type="cycling",
                duration_seconds=205 * 60,
            ),
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=62302,
                activity_source="intervals",
                external_activity_id="i62302",
                activity_date="2026-06-20",
                activity_start_datetime="2026-06-20T09:04:00",
                activity_name="Darmstadt Mountain Biking",
                sport_type="MountainBikeRide",
                duration_seconds=63 * 60,
            ),
        ]
    )
    await db.flush()

    history = await crud.get_ride_metrics_history(db, user.id)

    assert len(history) == 1
    assert history[0].strava_activity_id == 62301
    assert history[0].duration_seconds == 205 * 60


@pytest.mark.asyncio
async def test_upsert_ride_metric_preserves_longer_contained_duplicate(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=62401,
        activity_source="strava",
        external_activity_id="62401",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T09:00:00",
        activity_name="Darmstadt Road Cycling",
        sport_type="cycling",
        duration_seconds=205 * 60,
    )
    second = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=62402,
        activity_source="intervals",
        external_activity_id="i62402",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T09:04:00",
        activity_name="Darmstadt Mountain Biking",
        sport_type="MountainBikeRide",
        duration_seconds=63 * 60,
    )

    history = await crud.get_ride_metrics_history(db, user.id)

    assert second.id == first.id
    assert second.strava_activity_id == 62401
    assert second.duration_seconds == 205 * 60
    assert len(history) == 1


@pytest.mark.asyncio
async def test_upsert_ride_metric_keeps_separate_same_name_rides_with_different_starts(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=62011,
        activity_source="fit",
        external_activity_id="fit-62011",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T08:00:00+00:00",
        activity_name="FIT Cycling 2026-06-20",
        sport_type="cycling",
        duration_seconds=3600,
    )
    second = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=62012,
        activity_source="fit",
        external_activity_id="fit-62012",
        activity_date="2026-06-20",
        activity_start_datetime="2026-06-20T10:00:00+00:00",
        activity_name="FIT Cycling 2026-06-20",
        sport_type="cycling",
        duration_seconds=3600,
    )

    history = await crud.get_ride_metrics_history(db, user.id)

    assert second.id != first.id
    assert len(history) == 2
    assert {ride.strava_activity_id for ride in history} == {62011, 62012}


# ---------------------------------------------------------------------------
# AthleteMetricSnapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_athlete_metric_snapshot(db: AsyncSession) -> None:
    user = await _make_user(db)
    snapshot = await crud.create_athlete_metric_snapshot(
        db,
        user.id,
        ftp=285,
        ctl=45.3,
        atl=52.1,
        tsb=-6.8,
        source="strava_analysis",
    )
    assert snapshot.ftp == 285
    assert snapshot.ctl == 45.3
    assert snapshot.atl == 52.1
    assert snapshot.tsb == -6.8
    assert snapshot.source == "strava_analysis"
    assert snapshot.user_id == user.id
    assert snapshot.id is not None
    assert snapshot.recorded_at is not None


@pytest.mark.asyncio
async def test_create_athlete_metric_snapshot_nulls(db: AsyncSession) -> None:
    user = await _make_user(db)
    snapshot = await crud.create_athlete_metric_snapshot(
        db,
        user.id,
        ftp=None,
    )
    assert snapshot.ftp is None
    assert snapshot.ctl is None
    assert snapshot.atl is None
    assert snapshot.tsb is None


@pytest.mark.asyncio
async def test_get_athlete_metric_history_empty(db: AsyncSession) -> None:
    user = await _make_user(db)
    history = await crud.get_athlete_metric_history(db, user.id)
    assert history == []


@pytest.mark.asyncio
async def test_get_athlete_metric_history_ordered_ascending(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.create_athlete_metric_snapshot(db, user.id, ftp=250)
    await crud.create_athlete_metric_snapshot(db, user.id, ftp=265)
    await crud.create_athlete_metric_snapshot(db, user.id, ftp=280)

    history = await crud.get_athlete_metric_history(db, user.id)
    assert len(history) == 3
    # Should be ordered oldest → newest (recorded_at ascending)
    ftps = [s.ftp for s in history]
    assert ftps == [250, 265, 280]


@pytest.mark.asyncio
async def test_get_athlete_metric_history_limit(db: AsyncSession) -> None:
    user = await _make_user(db)
    for ftp in range(100, 200, 5):  # 20 snapshots
        await crud.create_athlete_metric_snapshot(db, user.id, ftp=ftp)

    history = await crud.get_athlete_metric_history(db, user.id, limit=5)
    assert len(history) == 5
    ftps = [s.ftp for s in history]
    assert ftps == [175, 180, 185, 190, 195]


# ---------------------------------------------------------------------------
# get_unreviewed_ride_metrics / mark_rides_as_reviewed
# ---------------------------------------------------------------------------


async def _make_ride(
    db: AsyncSession,
    user_id: str,
    strava_activity_id: int,
    activity_date: str,
) -> models.RideMetric:
    return await crud.upsert_ride_metric(
        db,
        user_id,
        strava_activity_id=strava_activity_id,
        activity_date=activity_date,
        sport_type="cycling",
        duration_seconds=3600,
        tss=80.0,
    )


@pytest.mark.asyncio
async def test_get_unreviewed_ride_metrics_empty(db: AsyncSession) -> None:
    user = await _make_user(db)
    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert unreviewed == []


@pytest.mark.asyncio
async def test_get_unreviewed_ride_metrics_returns_all_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    await _make_ride(db, user.id, 1001, "2026-05-01")
    await _make_ride(db, user.id, 1002, "2026-05-02")
    await _make_ride(db, user.id, 1003, "2026-05-03")

    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert len(unreviewed) == 3
    # Should be ordered oldest first
    dates = [m.activity_date for m in unreviewed]
    assert dates == ["2026-05-01", "2026-05-02", "2026-05-03"]


@pytest.mark.asyncio
async def test_get_unreviewed_ride_metrics_excludes_reviewed(db: AsyncSession) -> None:
    user = await _make_user(db)
    await _make_ride(db, user.id, 2001, "2026-05-01")
    await _make_ride(db, user.id, 2002, "2026-05-02")

    # Mark first ride as reviewed
    await crud.mark_rides_as_reviewed(db, user.id, [2001])

    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert len(unreviewed) == 1
    assert unreviewed[0].strava_activity_id == 2002


@pytest.mark.asyncio
async def test_mark_rides_as_reviewed_sets_timestamp(db: AsyncSession) -> None:
    user = await _make_user(db)
    await _make_ride(db, user.id, 3001, "2026-05-01")

    count = await crud.mark_rides_as_reviewed(db, user.id, [3001])
    assert count == 1

    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert unreviewed == []


@pytest.mark.asyncio
async def test_mark_rides_as_reviewed_empty_list(db: AsyncSession) -> None:
    user = await _make_user(db)
    await _make_ride(db, user.id, 4001, "2026-05-01")

    count = await crud.mark_rides_as_reviewed(db, user.id, [])
    assert count == 0

    # Ride should still be unreviewed
    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert len(unreviewed) == 1


@pytest.mark.asyncio
async def test_mark_rides_as_reviewed_only_affects_given_ids(db: AsyncSession) -> None:
    user = await _make_user(db)
    await _make_ride(db, user.id, 5001, "2026-05-01")
    await _make_ride(db, user.id, 5002, "2026-05-02")
    await _make_ride(db, user.id, 5003, "2026-05-03")

    count = await crud.mark_rides_as_reviewed(db, user.id, [5001, 5003])
    assert count == 2

    unreviewed = await crud.get_unreviewed_ride_metrics(db, user.id)
    assert len(unreviewed) == 1
    assert unreviewed[0].strava_activity_id == 5002


@pytest.mark.asyncio
async def test_upsert_ride_metric_persists_perf_signals(db: AsyncSession) -> None:
    user = await _make_user(db, "perf-signals@example.com")
    signals = {"duration_s": 3600, "power_curve": {"5": 360, "20": 300}}
    ride = await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=12345,
        activity_date="2026-07-20",
        perf_signals=signals,
    )
    assert ride.perf_signals == signals

    history = await crud.get_ride_metrics_history(db, user.id)
    assert history[0].perf_signals["power_curve"]["5"] == 360
