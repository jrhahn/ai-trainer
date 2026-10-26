"""Intervals.icu credential and activity import routes."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import async_session_maker, get_db
from services import history_review, import_jobs, ride_ingest
from services.athlete_language import interface_language
from services.dates import app_today, request_timezone
from services.intervals_service import (
    IntervalsAPIError,
    IntervalsActivityNotFound,
    IntervalsAuthError,
    IntervalsDataUnavailable,
    fetch_activity_detail,
    fetch_activity_listing,
    fetch_activity_streams,
    fetch_recent_activities,
    map_activity_to_ride_input,
    sanitize_intervals_streams,
)
from services.key_hint import key_hint
from services.progress_store import mark_finished, prune_progress, try_mark_running
from services.token_accounting import flush_deferred_usage

router = APIRouter(tags=["intervals"])
logger = logging.getLogger(__name__)

# In-process, single-replica only (see docs/multi_replica.md); bounded via
# prune_progress so completed entries don't accumulate forever (#326).
_intervals_import_progress: dict[str, dict] = {}


class IntervalsCredentialsRequest(schemas.CamelModel):
    api_key: str
    athlete_id: str = "0"
    athlete_name: str = ""


class IntervalsConnectionResponse(schemas.CamelModel):
    connected: bool
    athlete_id: str | None = None
    athlete_name: str | None = None
    # The key's first and last four characters, never more (ai-trainer-ops#307).
    key_hint: str | None = None


class IntervalsCheckResponse(schemas.CamelModel):
    """What the last month at intervals.icu holds for the coach."""

    # Activities the app can read: a device synced directly, or an upload.
    usable: int
    # Activities intervals.icu got from Strava and may not pass on.
    from_strava: int


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
        # Raw intervals id (``i166933341``) as a string; the numeric ``id`` above
        # is a 19-digit hash that loses precision as a JS Number, so persistence
        # must key off this instead (#429 Bug B).
        "external_id": None if raw_id is None else str(raw_id),
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
    months: int, today: date | None = None, *, days: int | None = None
) -> tuple[date, date]:
    """Return an Intervals date window that includes the current app-local day.

    *days*, when given, is the window's length instead of *months* × 30.
    """
    local_today = today or app_today()
    length = days if days is not None else months * 30
    return local_today - timedelta(days=length), local_today + timedelta(days=1)


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
        key_hint=key_hint(token.api_key),
    )


@router.put("/intervals/connection", response_model=IntervalsConnectionResponse)
async def save_intervals_connection(
    body: IntervalsCredentialsRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> IntervalsConnectionResponse:
    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Intervals.icu API key is required")
    athlete_id = body.athlete_id.strip() or "0"
    # A key intervals.icu refuses is no connection (ai-trainer-ops#212): stored,
    # it showed as "connected" in onboarding beside the error, and offered an
    # analysis that could only fail. An outage is not the athlete's mistake, so
    # the key is kept then, and the check after saving says intervals.icu did
    # not answer.
    oldest, newest = _intervals_activity_window(1)
    try:
        await fetch_activity_listing(api_key, athlete_id, oldest=oldest, newest=newest)
    except IntervalsAuthError as exc:
        # 403, not 401: the app's client reads a 401 as its own session ending.
        raise HTTPException(
            status_code=403, detail="Intervals.icu rejected the API key"
        ) from exc
    except (IntervalsAPIError, httpx.HTTPError):
        pass
    token = await crud.upsert_intervals_token(
        db,
        current_user.id,
        api_key=api_key,
        athlete_id=athlete_id,
        athlete_name=body.athlete_name.strip(),
    )
    return IntervalsConnectionResponse(
        connected=True,
        athlete_id=token.athlete_id,
        athlete_name=token.athlete_name,
        key_hint=key_hint(token.api_key),
    )


@router.delete("/intervals/connection")
async def delete_intervals_connection(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict[str, str]:
    await crud.delete_intervals_token(db, current_user.id)
    return {"status": "disconnected"}


@router.get("/intervals/check", response_model=IntervalsCheckResponse)
async def check_intervals_connection(
    current_user: models.User = Depends(auth.get_current_user),
) -> IntervalsCheckResponse:
    """Whether the rides of the last month actually reach the app.

    "The key works" is not enough: rides that came to intervals.icu from Strava
    are withheld from its API, so an athlete who only synced Strava sees a
    working key and gets nothing. This counts both, so onboarding can say which
    it is and what to do (connect the device directly, upload the archive).
    """
    token = current_user.intervals_token
    if token is None:
        raise HTTPException(status_code=404, detail="Intervals.icu not connected")
    oldest, newest = _intervals_activity_window(1)
    try:
        usable, from_strava = await fetch_activity_listing(
            token.api_key, token.athlete_id, oldest=oldest, newest=newest
        )
    except IntervalsAuthError as exc:
        # 403, not 401: the app's client reads a 401 as its own session ending.
        raise HTTPException(
            status_code=403, detail="Intervals.icu rejected the API key"
        ) from exc
    except (IntervalsAPIError, httpx.HTTPError) as exc:
        raise HTTPException(
            status_code=502, detail="Intervals.icu did not answer"
        ) from exc
    return IntervalsCheckResponse(usable=len(usable), from_strava=from_strava)


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
    # Told apart for the client (ai-trainer-ops#88): a rejected key is the
    # athlete's to fix, an outage is not. 403 rather than 401, because the app's
    # client reads any 401 as its own session ending and signs the athlete out.
    try:
        activities = await fetch_recent_activities(
            token.api_key,
            token.athlete_id,
            oldest=oldest,
            newest=newest,
        )
    except IntervalsAuthError as exc:
        raise HTTPException(
            status_code=403, detail="Intervals.icu rejected the API key"
        ) from exc
    except (IntervalsAPIError, httpx.HTTPError) as exc:
        raise HTTPException(
            status_code=502, detail="Intervals.icu did not answer"
        ) from exc
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
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(auth.get_current_user),
    months: int = 3,
    days: int | None = None,
    analyse: bool = False,
) -> dict[str, str]:
    """Import the history in the background; ``analyse`` has the coach look at
    it once afterwards (ai-trainer-ops#306).

    *days* sets the window instead of *months*: the connect imports six weeks,
    which hold the four the athlete thinks of and the 42 days the fitness value
    is built from.
    """
    token = current_user.intervals_token
    if token is None:
        raise HTTPException(status_code=404, detail="Intervals.icu not connected")

    # Drop stale completed entries so the store can't grow without bound (#326).
    prune_progress(_intervals_import_progress)

    months = max(1, min(months, 24))
    if days is not None:
        days = max(7, min(days, 730))
    oldest, newest = _intervals_activity_window(months, days=days)
    ftp = float(current_user.current_ftp or 0)
    if current_user.rider_assessment and current_user.rider_assessment.estimated_ftp:
        ftp = float(current_user.rider_assessment.estimated_ftp)

    # Atomically claim the running slot: bail if an import is already running.
    started = try_mark_running(
        _intervals_import_progress,
        current_user.id,
        {
            "status": "running",
            "total": 0,
            "processed": 0,
            "imported": 0,
            "skipped": 0,
            "failed_activities": [],
            "error": "",
        },
    )
    if not started:
        return {"status": "already_running"}
    background_tasks.add_task(
        run_intervals_import,
        user_id=current_user.id,
        api_key=token.api_key,
        athlete_id=token.athlete_id,
        oldest=oldest,
        newest=newest,
        ftp=ftp,
        analyse=analyse,
        timezone_name=request_timezone(request),
        language=interface_language(request),
    )
    return {"status": "started"}


@router.get("/intervals/import-progress")
async def get_intervals_import_progress(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ImportProgressResponse:
    prune_progress(_intervals_import_progress)
    progress = _intervals_import_progress.get(current_user.id)
    if progress is not None:
        return schemas.ImportProgressResponse(**progress)
    # Nothing in this process's memory. An import a restart cut short is still
    # on record, and "idle" told the athlete nothing had been asked for (#284).
    job = await import_jobs.latest(db, current_user.id, import_jobs.INTERVALS)
    if job is not None and job.status in ("running", "interrupted"):
        return schemas.ImportProgressResponse(
            status="interrupted",
            total=job.total,
            processed=job.processed,
            imported=job.imported,
            skipped=job.skipped,
            failed_activities=job.failed_activities or [],
            error=import_jobs.IMPORT_ERRORS["interrupted"],
        )
    return schemas.ImportProgressResponse(status="idle")


# Activities fetched, then stored in one transaction, at a time: a restart in
# the middle of a 24-month import loses the batch in flight rather than every
# activity fetched so far (#284).
IMPORT_BATCH = 25

_JOB_COUNTS = (
    "total",
    "processed",
    "imported",
    "skipped",
    "failed_activities",
    "error",
)


async def run_intervals_import(
    *,
    user_id: str,
    api_key: str,
    athlete_id: str,
    oldest: date,
    newest: date,
    ftp: float,
    analyse: bool = False,
    timezone_name: str | None = None,
    language: str | None = None,
) -> None:
    job_id: str | None = None
    # This run's own progress, so an early end never reports an earlier run's.
    progress: dict = {
        "status": "running",
        "total": 0,
        "processed": 0,
        "imported": 0,
        "skipped": 0,
        "failed_activities": [],
        "error": "",
    }
    _intervals_import_progress[user_id] = progress
    try:
        async with async_session_maker() as db:
            job_id = await import_jobs.start(db, user_id, import_jobs.INTERVALS)
            await db.commit()
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
        progress["total"] = len(activities)

        for first in range(0, len(activities), IMPORT_BATCH):
            rides: list[dict] = []
            for offset, activity in enumerate(activities[first : first + IMPORT_BATCH]):
                ride = await _fetch_ride(
                    api_key, user_id, activity, progress["failed_activities"]
                )
                if ride is not None:
                    rides.append(ride)
                progress["processed"] = first + offset + 1
                progress["skipped"] = len(progress["failed_activities"])
            await _store_batch(user_id, rides, ftp, job_id=job_id, progress=progress)

        logger.info(
            "Intervals.icu history import persisted user=%s imported=%s skipped=%s failed=%s",
            user_id,
            progress["imported"],
            progress["skipped"],
            progress["failed_activities"][:10],
        )
        await _close_job(job_id, "done", progress)
        if analyse:
            # The rides are in; the import's record says so before the coach
            # takes its time over them.
            progress["status"] = "analysing"
            review = await _review_history(
                user_id, activities, timezone_name=timezone_name, language=language
            )
            progress["analysed"] = review.analysed
            progress["plan_changes"] = review.plan_changes
        _intervals_import_progress[user_id] = mark_finished(
            {**progress, "status": "done"}
        )
    except IntervalsAuthError:
        await _end_with_error(user_id, job_id, progress, "rejected")
    except IntervalsAPIError as exc:
        logger.warning(
            "Intervals.icu history import stopped for user %s: %s", user_id, exc
        )
        await _end_with_error(user_id, job_id, progress, "unavailable")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Intervals.icu import failed for user %s: %s", user_id, exc, exc_info=True
        )
        await _end_with_error(user_id, job_id, progress, "failed")


async def _review_history(
    user_id: str,
    activities: list[dict],
    *,
    timezone_name: str | None,
    language: str | None,
) -> history_review.HistoryReview:
    """The coach's one look at the imported rides, in a session of its own.

    Never raises: the rides are stored either way, and when the look fails the
    dashboard's analysis runs instead, as before (ai-trainer-ops#306).
    """
    rides = []
    for activity in activities:
        try:
            rides.append(
                schemas.StravaActivitySchema.model_validate(
                    _activity_response(activity)
                ).model_dump()
            )
        except ValidationError:
            # No date: the import has counted it as failed already.
            continue
    rides.sort(key=lambda r: r.get("start_date_local") or "", reverse=True)
    try:
        async with async_session_maker() as db:
            try:
                user = await crud.get_user_by_id(db, user_id)
                if user is None:
                    return history_review.NOT_REVIEWED
                review = await history_review.review_imported_rides(
                    db, user, rides, timezone_name=timezone_name, language=language
                )
                await db.commit()
                return review
            except Exception:
                await db.rollback()
                raise
            finally:
                # What a failed look spent is written once the rollback is done
                # (#560).
                await flush_deferred_usage(db)
    except Exception:
        logger.warning(
            "Intervals.icu history review failed for user %s", user_id, exc_info=True
        )
        return history_review.NOT_REVIEWED


async def _fetch_ride(
    api_key: str, user_id: str, activity: dict, failed: list[dict]
) -> dict | None:
    """One listed activity with its detail and streams, as a ride to price, or
    ``None`` with the reason added to ``failed``. A refused key ends the import."""
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
            failed.append(_failure(activity, "Missing activity id or start date"))
        return ride
    except IntervalsAuthError:
        raise
    except IntervalsActivityNotFound:
        # Listed but no longer fetchable — report it as skipped instead
        # of as an opaque error, and never retry it (#517).
        logger.warning(
            "Intervals.icu activity no longer exists user=%s activity=%s",
            user_id,
            _activity_log_entry(activity),
        )
        failed.append(_failure(activity, "Activity no longer exists at Intervals.icu"))
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
            _failure(activity, "Streams temporarily unavailable — retry the import")
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Intervals.icu activity processing failed user=%s activity=%s error=%s",
            user_id,
            _activity_log_entry(activity),
            exc,
        )
        # The exception's text stays in the log: it is ours, not the athlete's (#284).
        failed.append(_failure(activity, import_jobs.ACTIVITY_FAILED))
    return None


def _failure(activity: dict, reason: str) -> dict:
    return {
        "activity_id": None,
        "activity_name": activity.get("name"),
        "activity_date": None,
        "reason": reason,
    }


async def _store_batch(
    user_id: str, rides: list[dict], ftp: float, *, job_id: str | None, progress: dict
) -> None:
    """Store one batch of rides through the shared ingest (#248), together with
    how far the import is, in one transaction (#284).

    The ingest replays the chain when a batch brings rides older than the newest
    stored one, which a history import does: run again, without the replay, it
    stacked the window's load onto the last run (#156, #278).
    """
    async with async_session_maker() as db:
        stored_now = 0
        user = await crud.get_user_by_id(db, user_id)
        if rides and user is not None:
            result = await ride_ingest.ingest_rides(
                db, user, rides, ftp=ftp, policy=ride_ingest.HISTORY
            )
            stored_now = result.stored
            for ride in result.unpriced:
                progress["failed_activities"].append(
                    {
                        "activity_id": None,
                        "activity_name": ride.get("activity_name"),
                        "activity_date": ride.get("activity_date"),
                        "reason": import_jobs.ACTIVITY_FAILED,
                    }
                )
            progress["skipped"] = len(progress["failed_activities"])
            logger.info(
                "Intervals.icu history import stored user=%s rides=%s metrics=%s unpriced=%s",
                user_id,
                len(rides),
                stored_now,
                len(result.unpriced),
            )
        stored = {key: progress[key] for key in _JOB_COUNTS}
        stored["imported"] += stored_now
        if job_id is not None:
            await import_jobs.record(db, job_id, **stored)
        await db.commit()
    progress["imported"] = stored["imported"]


async def _close_job(job_id: str | None, status: str, payload: dict) -> None:
    """Close the import's row. Its rows are stored by now, so a failure here is
    logged rather than turned into a failed import."""
    if job_id is None:
        return
    try:
        async with async_session_maker() as db:
            await import_jobs.record(
                db, job_id, status=status, **{key: payload[key] for key in _JOB_COUNTS}
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Could not close import %s", job_id, exc_info=True)


async def _end_with_error(
    user_id: str, job_id: str | None, progress: dict, reason: str
) -> None:
    """End the import with a fixed sentence for the athlete (#284), keeping the
    counts of what was stored before it stopped."""
    ended = {**progress, "status": "error", "error": import_jobs.IMPORT_ERRORS[reason]}
    _intervals_import_progress[user_id] = mark_finished(ended)
    await _close_job(job_id, "error", ended)
