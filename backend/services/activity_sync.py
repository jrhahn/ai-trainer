"""Backend-owned activity sync and plan adaptation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import crud
import models
from config import settings
from services.analysis import build_ride_metrics_chain
from services.activity_imports import (
    ImportedActivity,
    find_existing_import,
    to_ride_inputs,
)
from services.dates import app_today
from services.intervals_service import (
    fetch_activity_detail as fetch_intervals_activity_detail,
    fetch_activity_streams as fetch_intervals_activity_streams,
    fetch_recent_activities as fetch_recent_intervals_activities,
    intervals_activity_id,
    map_activity_to_imported_activity,
    sanitize_intervals_streams,
    apply_summary_fallback,
)
from services.ride_matching import (
    apply_ride_plan_matches,
    review_matched_ride_and_adapt,
)
from services.llm import resolve_user_provider
from services.scheduler import ScheduledJob
from services.strava_service import (
    STRAVA_OAUTH_BASE,
    ensure_fresh_strava_token,
    fetch_activity_streams as fetch_strava_activity_streams,
)
from services.weather_service import enrich_activity_weather

logger = logging.getLogger(__name__)

INTERVALS_CURSOR_TOLERANCE = 2048


@dataclass(slots=True)
class SourceSyncResult:
    source: str
    checked: int = 0
    imported: int = 0
    skipped: int = 0
    adapted: int = 0
    failed: int = 0


@dataclass(slots=True)
class ActivitySyncResult:
    users: int = 0
    source_checks: int = 0
    imported: int = 0
    skipped: int = 0
    adapted: int = 0
    failed: int = 0

    def add(self, source: SourceSyncResult) -> None:
        self.source_checks += source.checked
        self.imported += source.imported
        self.skipped += source.skipped
        self.adapted += source.adapted
        self.failed += source.failed



def _intervals_cursor_matches(activity_id: int, cursor: int) -> bool:
    return (
        activity_id == cursor or abs(activity_id - cursor) <= INTERVALS_CURSOR_TOLERANCE
    )


def _sanitize_strava_streams(streams: object) -> dict:
    if not isinstance(streams, dict):
        return {}
    cleaned: dict[str, dict[str, list]] = {}
    for key in ("watts", "heartrate", "cadence", "velocity_smooth", "altitude", "time"):
        stream_obj = streams.get(key)
        if not isinstance(stream_obj, dict):
            continue
        data = stream_obj.get("data")
        if not isinstance(data, list):
            continue
        numeric = [float(v) for v in data if isinstance(v, (int, float))]
        cleaned[key] = {"data": numeric}
    latlng_obj = streams.get("latlng")
    if isinstance(latlng_obj, dict):
        data = latlng_obj.get("data")
        if isinstance(data, list):
            points = [
                [float(p[0]), float(p[1])]
                for p in data
                if isinstance(p, (list, tuple))
                and len(p) >= 2
                and isinstance(p[0], (int, float))
                and isinstance(p[1], (int, float))
            ]
            cleaned["latlng"] = {"data": points}
    return cleaned


async def fetch_recent_strava_activities(
    access_token: str, per_page: int = 10
) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{STRAVA_OAUTH_BASE}/api/v3/athlete/activities",
            params={"per_page": per_page},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if not resp.is_success:
        raise RuntimeError(f"Strava list error {resp.status_code}")
    data = resp.json()
    return data if isinstance(data, list) else []


def _strava_activity_to_imported_activity(
    activity: dict, streams: dict, weather: dict
) -> ImportedActivity | None:
    activity_id = activity.get("id")
    if not isinstance(activity_id, int):
        return None
    start_date: str = activity.get("start_date", "")
    start_date_local: str = activity.get("start_date_local", "")
    activity_date_source = start_date_local or start_date
    activity_date = activity_date_source[:10] if activity_date_source else ""
    if not activity_date:
        return None
    return ImportedActivity(
        source="strava",
        external_activity_id=str(activity_id),
        name=activity.get("name") if isinstance(activity.get("name"), str) else None,
        start_datetime=start_date_local or start_date or None,
        activity_date=activity_date,
        sport_type=activity.get("sport_type") or activity.get("type") or "cycling",
        duration_seconds=int(
            activity.get("elapsed_time") or activity.get("moving_time") or 0
        ),
        streams=streams,
        start_lat=(
            float(activity["start_latlng"][0])
            if isinstance(activity.get("start_latlng"), list)
            and len(activity["start_latlng"]) >= 2
            and isinstance(activity["start_latlng"][0], (int, float))
            else None
        ),
        start_lng=(
            float(activity["start_latlng"][1])
            if isinstance(activity.get("start_latlng"), list)
            and len(activity["start_latlng"]) >= 2
            and isinstance(activity["start_latlng"][1], (int, float))
            else None
        ),
        weather=weather,
        metadata={"strava_activity_id": activity_id},
    )


async def _persist_and_adapt(
    db: AsyncSession,
    user: models.User,
    activities: list[ImportedActivity],
) -> tuple[int, int]:
    if not activities:
        return 0, 0
    rides = to_ride_inputs(activities)

    latest_metric = await crud.get_latest_ride_metric(db, user.id)
    seed_ctl = (
        latest_metric.ctl_after if latest_metric and latest_metric.ctl_after else 0.0
    )
    seed_atl = (
        latest_metric.atl_after if latest_metric and latest_metric.atl_after else 0.0
    )
    ftp = float(user.current_ftp or 0)
    if (
        ftp <= 0
        and user.rider_assessment is not None
        and user.rider_assessment.estimated_ftp
    ):
        ftp = float(user.rider_assessment.estimated_ftp)

    metrics_chain = build_ride_metrics_chain(rides, ftp, seed_ctl, seed_atl)
    rides_by_id = {ride["strava_activity_id"]: ride for ride in rides}
    for metric in metrics_chain:
        ride = rides_by_id.get(metric["strava_activity_id"])
        if ride is not None:
            apply_summary_fallback(metric, ride)
        await crud.upsert_ride_metric(db, user.id, **metric)

    if not metrics_chain:
        return 0, 0

    existing_plan = await crud.get_training_plan(db, user.id)
    training_plan = existing_plan.plan if existing_plan is not None else []
    imported_ids = [metric["strava_activity_id"] for metric in metrics_chain]
    auto_matched = await apply_ride_plan_matches(
        db, user.id, training_plan, imported_ids
    )
    streams_by_id = {
        ride["strava_activity_id"]: ride.get("streams") or {} for ride in rides
    }
    adapted = 0
    for ride_metric in auto_matched:
        await review_matched_ride_and_adapt(
            db,
            user,
            ride_metric,
            training_plan,
            provider=resolve_user_provider(user),
            streams=streams_by_id.get(ride_metric.strava_activity_id),
        )
        adapted += 1
    return len(metrics_chain), adapted


async def sync_strava_for_user(db: AsyncSession, user: models.User) -> SourceSyncResult:
    result = SourceSyncResult(source="strava", checked=1)
    if user.strava_token is None or not user.strava_auto_sync_enabled:
        result.skipped = 1
        return result

    access_token = await ensure_fresh_strava_token(user.strava_token, db)
    activities = await fetch_recent_strava_activities(access_token)
    valid_ids = [
        activity.get("id")
        for activity in activities
        if isinstance(activity.get("id"), int)
    ]
    if not valid_ids:
        return result

    latest_seen = max(valid_ids)
    if user.last_strava_activity_id is None:
        user.last_strava_activity_id = latest_seen
        result.skipped = len(valid_ids)
        logger.info(
            "Strava activity sync initialized cursor user=%s cursor=%s",
            user.id,
            latest_seen,
        )
        return result

    new_activities = [
        activity
        for activity in activities
        if isinstance(activity.get("id"), int)
        and activity["id"] > int(user.last_strava_activity_id)
    ]
    imported_ids: list[int] = []
    imported_activities: list[ImportedActivity] = []
    for activity in new_activities:
        activity_id = int(activity["id"])
        raw_streams = await fetch_strava_activity_streams(access_token, activity_id)
        streams = _sanitize_strava_streams(raw_streams)
        weather = await enrich_activity_weather(activity, streams=streams)
        imported = _strava_activity_to_imported_activity(activity, streams, weather)
        if imported is None:
            result.skipped += 1
            continue
        if await find_existing_import(db, user.id, imported) is not None:
            result.skipped += 1
            continue
        imported_activities.append(imported)
        imported_ids.append(activity_id)

    imported, adapted = await _persist_and_adapt(db, user, imported_activities)
    result.imported = imported
    result.adapted = adapted
    if imported_ids:
        max_imported_id = max(imported_ids)
        if max_imported_id > int(user.last_strava_activity_id or 0):
            user.last_strava_activity_id = max_imported_id
    logger.info(
        "Strava activity sync user=%s fetched=%s new=%s imported=%s skipped=%s adapted=%s",
        user.id,
        len(activities),
        len(new_activities),
        result.imported,
        result.skipped,
        result.adapted,
    )
    return result


async def sync_intervals_for_user(
    db: AsyncSession, user: models.User
) -> SourceSyncResult:
    result = SourceSyncResult(source="intervals", checked=1)
    if user.intervals_token is None or not user.intervals_auto_sync_enabled:
        result.skipped = 1
        return result

    today = app_today()
    activities = await fetch_recent_intervals_activities(
        user.intervals_token.api_key,
        user.intervals_token.athlete_id,
        oldest=today - timedelta(days=30),
        newest=today + timedelta(days=1),
    )
    valid = [
        (activity, intervals_activity_id(activity.get("id")))
        for activity in activities
        if activity.get("id") is not None
    ]
    if not valid:
        return result

    latest_seen = valid[0][1]
    if user.last_intervals_activity_id is None:
        user.last_intervals_activity_id = latest_seen
        result.skipped = len(valid)
        logger.info(
            "Intervals activity sync initialized cursor user=%s cursor=%s",
            user.id,
            latest_seen,
        )
        return result

    new_activities: list[dict] = []
    cursor_found = False
    for activity, activity_id in valid:
        if _intervals_cursor_matches(activity_id, int(user.last_intervals_activity_id)):
            cursor_found = True
            break
        new_activities.append(activity)
    if not cursor_found:
        logger.info(
            "Intervals activity sync cursor not in recent window user=%s cursor=%s fetched=%s",
            user.id,
            user.last_intervals_activity_id,
            len(valid),
        )

    imported_activities: list[ImportedActivity] = []
    for activity in new_activities:
        summary_imported = map_activity_to_imported_activity(activity, None, {})
        if summary_imported is None:
            result.skipped += 1
            continue
        if await find_existing_import(db, user.id, summary_imported) is not None:
            result.skipped += 1
            continue
        detail = await fetch_intervals_activity_detail(
            user.intervals_token.api_key, activity.get("id")
        )
        streams = sanitize_intervals_streams(
            await fetch_intervals_activity_streams(
                user.intervals_token.api_key, activity.get("id")
            )
        )
        imported = map_activity_to_imported_activity(activity, detail, streams)
        if imported is None:
            result.skipped += 1
            continue
        imported_activities.append(imported)

    imported, adapted = await _persist_and_adapt(db, user, imported_activities)
    result.imported = imported
    result.adapted = adapted
    if latest_seen != user.last_intervals_activity_id:
        user.last_intervals_activity_id = latest_seen
    logger.info(
        "Intervals activity sync user=%s fetched=%s new=%s imported=%s skipped=%s adapted=%s cursor_found=%s",
        user.id,
        len(activities),
        len(new_activities),
        result.imported,
        result.skipped,
        result.adapted,
        cursor_found,
    )
    return result


async def run_activity_sync(
    session_factory: async_sessionmaker[AsyncSession],
) -> ActivitySyncResult:
    started = datetime.now(timezone.utc)
    result = ActivitySyncResult()
    logger.info("Activity sync started")

    async with session_factory() as db:
        users = await crud.get_users_with_training_plans(db)

    result.users = len(users)
    for user_ref in users:
        for source, sync_fn in (
            ("strava", sync_strava_for_user),
            ("intervals", sync_intervals_for_user),
        ):
            try:
                async with session_factory() as db:
                    user = await crud.get_user_by_id(db, user_ref.id)
                    if user is None:
                        source_result = SourceSyncResult(
                            source=source, checked=1, skipped=1
                        )
                    else:
                        source_result = await sync_fn(db, user)
                    result.add(source_result)
                    await db.commit()
            except Exception:
                result.failed += 1
                logger.warning(
                    "Activity sync failed user=%s source=%s",
                    user_ref.id,
                    source,
                    exc_info=True,
                )

    duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "Activity sync finished users=%s source_checks=%s imported=%s skipped=%s adapted=%s failed=%s duration_ms=%s",
        result.users,
        result.source_checks,
        result.imported,
        result.skipped,
        result.adapted,
        result.failed,
        duration_ms,
    )
    return result


def activity_sync_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> ScheduledJob:
    async def _run() -> object:
        return await run_activity_sync(session_factory)

    return ScheduledJob(
        name="activity-sync",
        run=_run,
        next_delay=lambda: max(60, int(settings.activity_sync_interval_seconds)),
    )
