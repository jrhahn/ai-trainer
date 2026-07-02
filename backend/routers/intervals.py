"""Intervals.icu credential and activity import routes."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import async_session_maker, get_db
from services.analysis import build_ride_metrics_chain
from services.dates import app_today
from services.intervals_service import (
    IntervalsAPIError,
    IntervalsAuthError,
    IntervalsDataUnavailable,
    apply_summary_fallback,
    fetch_activity_detail,
    fetch_activity_streams,
    fetch_recent_activities,
    map_activity_to_ride_input,
    sanitize_intervals_streams,
)
from services.ride_matching import apply_ride_plan_matches

router = APIRouter(tags=["intervals"])
logger = logging.getLogger(__name__)

_intervals_import_progress: dict[str, dict] = {}


class IntervalsCredentialsRequest(schemas.CamelModel):
    api_key: str
    athlete_id: str = "0"
    athlete_name: str = ""


class IntervalsConnectionResponse(schemas.CamelModel):
    connected: bool
    athlete_id: str | None = None
    athlete_name: str | None = None


def _activity_response(activity: dict, detail: dict | None = None) -> dict:
    source = {**activity, **(detail or {})}
    raw_id = source.get("id")
    activity_id = 0 if raw_id is None else _hashed_activity_id(raw_id)
    start_date = (
        source.get("start_date")
        or source.get("start_date_local")
        or source.get("start_time")
        or source.get("date")
    )
    moving_time = (
        source.get("moving_time")
        or source.get("elapsed_time")
        or source.get("duration")
    )
    moving_time = _number_or_default(moving_time, 0)
    elapsed_time = _number_or_default(
        source.get("elapsed_time") or moving_time, moving_time
    )
    return {
        "id": activity_id,
        "name": source.get("name") or source.get("title") or "Intervals.icu activity",
        "type": source.get("type") or source.get("sport") or "Ride",
        "sport_type": source.get("type") or source.get("sport") or "Ride",
        "distance": _number_or_default(source.get("distance"), 0),
        "moving_time": moving_time,
        "elapsed_time": elapsed_time,
        "total_elevation_gain": _number_or_default(
            source.get("total_elevation_gain")
            or source.get("elevation_gain")
            or source.get("elev_gain")
            or source.get("ascent"),
            0,
        ),
        "start_date": start_date,
        "start_date_local": source.get("start_date_local") or start_date,
        "average_watts": source.get("average_watts")
        or source.get("icu_weighted_avg_watts"),
        "weighted_average_watts": source.get("icu_weighted_avg_watts"),
        "average_heartrate": source.get("average_heartrate"),
        "average_cadence": source.get("average_cadence"),
    }


def _hashed_activity_id(raw_id: object) -> int:
    from services.intervals_service import intervals_activity_id

    return intervals_activity_id(raw_id)


def _number_or_default(value: object, default: int | float) -> int | float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return value
    return default


def _intervals_activity_window(
    months: int, today: date | None = None
) -> tuple[date, date]:
    """Return an Intervals date window that includes the current app-local day."""
    local_today = today or app_today()
    return local_today - timedelta(days=months * 30), local_today + timedelta(days=1)


def _activity_log_entry(activity: dict) -> dict[str, object]:
    raw_id = activity.get("id")
    return {
        "raw_id": raw_id,
        "app_id": _hashed_activity_id(raw_id) if raw_id is not None else None,
        "name": activity.get("name") or activity.get("title"),
        "type": activity.get("type") or activity.get("sport"),
        "start": activity.get("start_date_local")
        or activity.get("start_date")
        or activity.get("start_time")
        or activity.get("date"),
    }


def _activity_log_sample(
    activities: list[dict], limit: int = 10
) -> list[dict[str, object]]:
    return [_activity_log_entry(activity) for activity in activities[:limit]]


def _activity_response_log_sample(
    activities: list[dict], limit: int = 10
) -> list[dict[str, object]]:
    return [
        {
            "app_id": activity.get("id"),
            "name": activity.get("name"),
            "type": activity.get("type") or activity.get("sport_type"),
            "start": activity.get("start_date_local") or activity.get("start_date"),
        }
        for activity in activities[:limit]
    ]


def _activity_id_matches_cursor(activity_id: int, cursor: int) -> bool:
    if activity_id == cursor:
        return True
    # Intervals activity ids are stable 63-bit hashes. JavaScript clients parse
    # those JSON numbers as IEEE-754 values, so values above MAX_SAFE_INTEGER can
    # round slightly before being sent back as after_id.
    return abs(activity_id - cursor) <= 2048


@router.get("/intervals/connection", response_model=IntervalsConnectionResponse)
async def get_intervals_connection(
    current_user: models.User = Depends(auth.get_current_user),
) -> IntervalsConnectionResponse:
    token = current_user.intervals_token
    if token is None:
        return IntervalsConnectionResponse(connected=False)
    return IntervalsConnectionResponse(
        connected=True,
        athlete_id=token.athlete_id,
        athlete_name=token.athlete_name,
    )


@router.put("/intervals/connection", response_model=IntervalsConnectionResponse)
async def save_intervals_connection(
    body: IntervalsCredentialsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> IntervalsConnectionResponse:
    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Intervals.icu API key is required")
    token = await crud.upsert_intervals_token(
        db,
        current_user.id,
        api_key=api_key,
        athlete_id=body.athlete_id.strip() or "0",
        athlete_name=body.athlete_name.strip(),
    )
    return IntervalsConnectionResponse(
        connected=True,
        athlete_id=token.athlete_id,
        athlete_name=token.athlete_name,
    )


@router.delete("/intervals/connection")
async def delete_intervals_connection(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict[str, str]:
    await crud.delete_intervals_token(db, current_user.id)
    return {"status": "disconnected"}


@router.get("/intervals/activities")
async def get_intervals_activities(
    current_user: models.User = Depends(auth.get_current_user),
    after_id: int | None = None,
    months: int = 1,
) -> list[dict]:
    token = current_user.intervals_token
    if token is None:
        raise HTTPException(status_code=404, detail="Intervals.icu not connected")

    months = max(1, min(months, 24))
    oldest, newest = _intervals_activity_window(months)
    logger.info(
        "Fetching Intervals.icu activities for user=%s athlete=%s oldest=%s newest=%s",
        current_user.id,
        token.athlete_id,
        oldest.isoformat(),
        newest.isoformat(),
    )
    activities = await fetch_recent_activities(
        token.api_key,
        token.athlete_id,
        oldest=oldest,
        newest=newest,
    )
    logger.info(
        "Intervals.icu returned activities user=%s athlete=%s count=%s sample=%s",
        current_user.id,
        token.athlete_id,
        len(activities),
        _activity_log_sample(activities),
    )

    result: list[dict] = []
    for activity in activities:
        raw_id = activity.get("id")
        if raw_id is None:
            continue
        activity_id = _hashed_activity_id(raw_id)
        if after_id is not None and _activity_id_matches_cursor(activity_id, after_id):
            break
        result.append(_activity_response(activity))
        if after_id is None and len(result) >= 10:
            break

    cursor_found = after_id is None or any(
        _activity_id_matches_cursor(_hashed_activity_id(activity.get("id")), after_id)
        for activity in activities
        if activity.get("id") is not None
    )
    response_result = result if cursor_found else []
    logger.info(
        "Intervals.icu activities response user=%s after_id=%s cursor_found=%s fetched_before_cursor=%s returned=%s sample=%s",
        current_user.id,
        after_id,
        cursor_found,
        len(result),
        len(response_result),
        _activity_response_log_sample(response_result),
    )

    if after_id is not None and not cursor_found:
        logger.warning(
            "Intervals.icu after_id cursor was not found user=%s after_id=%s nearest_sample=%s",
            current_user.id,
            after_id,
            _activity_log_sample(activities),
        )
    return response_result


@router.post("/intervals/import-history", status_code=202)
async def import_intervals_history(
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(auth.get_current_user),
    months: int = 3,
) -> dict[str, str]:
    token = current_user.intervals_token
    if token is None:
        raise HTTPException(status_code=404, detail="Intervals.icu not connected")

    current_progress = _intervals_import_progress.get(current_user.id, {})
    if current_progress.get("status") == "running":
        return {"status": "already_running"}

    months = max(1, min(months, 24))
    oldest, newest = _intervals_activity_window(months)
    ftp = float(current_user.current_ftp or 0)
    if current_user.rider_assessment and current_user.rider_assessment.estimated_ftp:
        ftp = float(current_user.rider_assessment.estimated_ftp)

    _intervals_import_progress[current_user.id] = {
        "status": "running",
        "total": 0,
        "processed": 0,
        "imported": 0,
        "skipped": 0,
        "failed_activities": [],
        "error": "",
    }
    background_tasks.add_task(
        run_intervals_import,
        user_id=current_user.id,
        api_key=token.api_key,
        athlete_id=token.athlete_id,
        oldest=oldest,
        newest=newest,
        ftp=ftp,
    )
    return {"status": "started"}


@router.get("/intervals/import-progress")
async def get_intervals_import_progress(
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ImportProgressResponse:
    progress = _intervals_import_progress.get(current_user.id)
    if progress is None:
        return schemas.ImportProgressResponse(status="idle")
    return schemas.ImportProgressResponse(**progress)


async def run_intervals_import(
    *,
    user_id: str,
    api_key: str,
    athlete_id: str,
    oldest: date,
    newest: date,
    ftp: float,
) -> None:
    try:
        logger.info(
            "Starting Intervals.icu history import user=%s athlete=%s oldest=%s newest=%s",
            user_id,
            athlete_id,
            oldest.isoformat(),
            newest.isoformat(),
        )
        activities = await fetch_recent_activities(
            api_key,
            athlete_id,
            oldest=oldest,
            newest=newest,
        )
        logger.info(
            "Intervals.icu history import fetched user=%s athlete=%s count=%s sample=%s",
            user_id,
            athlete_id,
            len(activities),
            _activity_log_sample(activities),
        )
        _intervals_import_progress[user_id] = {
            "status": "running",
            "total": len(activities),
            "processed": 0,
            "imported": 0,
            "skipped": 0,
            "failed_activities": [],
            "error": "",
        }

        rides: list[dict] = []
        failed: list[dict] = []
        for idx, activity in enumerate(activities):
            raw_id = activity.get("id")
            detail: dict = {}
            streams: dict = {}
            try:
                if raw_id is not None:
                    detail = await fetch_activity_detail(api_key, raw_id)
                    streams = sanitize_intervals_streams(
                        await fetch_activity_streams(api_key, raw_id)
                    )
                ride = map_activity_to_ride_input(activity, detail, streams)
                if ride is None:
                    logger.warning(
                        "Intervals.icu activity could not be mapped user=%s activity=%s",
                        user_id,
                        _activity_log_entry(activity),
                    )
                    failed.append(
                        {
                            "activity_id": None,
                            "activity_name": activity.get("name"),
                            "activity_date": None,
                            "reason": "Missing activity id or start date",
                        }
                    )
                else:
                    rides.append(ride)
            except IntervalsAuthError:
                raise
            except IntervalsDataUnavailable as exc:
                # Transient failure: surface for retry instead of importing an
                # activity with missing stream/detail data as if complete (#352).
                logger.warning(
                    "Intervals.icu streams/detail unavailable (transient) user=%s activity=%s error=%s",
                    user_id,
                    _activity_log_entry(activity),
                    exc,
                )
                failed.append(
                    {
                        "activity_id": None,
                        "activity_name": activity.get("name"),
                        "activity_date": None,
                        "reason": "Streams temporarily unavailable — retry the import",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Intervals.icu activity processing failed user=%s activity=%s error=%s",
                    user_id,
                    _activity_log_entry(activity),
                    exc,
                )
                failed.append(
                    {
                        "activity_id": None,
                        "activity_name": activity.get("name"),
                        "activity_date": None,
                        "reason": str(exc),
                    }
                )
            _intervals_import_progress[user_id]["processed"] = idx + 1

        latest_metric = None
        async with async_session_maker() as db:
            latest_metric = await crud.get_latest_ride_metric(db, user_id)
        seed_ctl = (
            latest_metric.ctl_after
            if latest_metric and latest_metric.ctl_after
            else 0.0
        )
        seed_atl = (
            latest_metric.atl_after
            if latest_metric and latest_metric.atl_after
            else 0.0
        )
        metrics_chain = build_ride_metrics_chain(rides, ftp, seed_ctl, seed_atl)
        logger.info(
            "Intervals.icu history import mapped user=%s rides=%s metrics=%s failed=%s metric_ids=%s",
            user_id,
            len(rides),
            len(metrics_chain),
            len(failed),
            [metric["strava_activity_id"] for metric in metrics_chain[:10]],
        )
        rides_by_id = {ride["strava_activity_id"]: ride for ride in rides}
        for metric in metrics_chain:
            ride = rides_by_id.get(metric["strava_activity_id"])
            if ride is not None:
                apply_summary_fallback(metric, ride)

        async with async_session_maker() as db:
            for metric in metrics_chain:
                await crud.upsert_ride_metric(db, user_id, **metric)
            if metrics_chain:
                existing_plan = await crud.get_training_plan(db, user_id)
                training_plan = existing_plan.plan if existing_plan is not None else []
                await apply_ride_plan_matches(
                    db,
                    user_id,
                    training_plan,
                    [m["strava_activity_id"] for m in metrics_chain],
                )
            await db.commit()
        logger.info(
            "Intervals.icu history import persisted user=%s imported=%s skipped=%s failed=%s",
            user_id,
            len(metrics_chain),
            len(failed),
            failed[:10],
        )

        _intervals_import_progress[user_id] = {
            "status": "done",
            "total": len(activities),
            "processed": len(activities),
            "imported": len(metrics_chain),
            "skipped": len(failed),
            "failed_activities": failed,
            "error": "",
        }
    except IntervalsAuthError as exc:
        _intervals_import_progress[user_id] = {
            "status": "error",
            "total": 0,
            "processed": 0,
            "imported": 0,
            "skipped": 0,
            "failed_activities": [],
            "error": str(exc),
        }
    except IntervalsAPIError as exc:
        _intervals_import_progress[user_id] = {
            "status": "error",
            "total": 0,
            "processed": 0,
            "imported": 0,
            "skipped": 0,
            "failed_activities": [],
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Intervals.icu import failed for user %s: %s", user_id, exc, exc_info=True
        )
        prev = _intervals_import_progress.get(user_id, {})
        _intervals_import_progress[user_id] = {
            "status": "error",
            "total": prev.get("total", 0),
            "processed": prev.get("processed", 0),
            "imported": prev.get("imported", 0),
            "skipped": prev.get("skipped", 0),
            "failed_activities": prev.get("failed_activities", []),
            "error": str(exc),
        }
