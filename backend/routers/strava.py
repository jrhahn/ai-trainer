"""Strava OAuth and activity proxy routes."""

import asyncio
import logging
import secrets
import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import async_session_maker, get_db
from services.ride_matching import apply_ride_plan_matches, review_matched_ride_and_adapt
from services.analysis import build_ride_metrics_chain
from services.strava_service import (
    STRAVA_OAUTH_BASE,
    ensure_fresh_strava_token,
    fetch_activity_streams,
)

router = APIRouter(tags=["strava"])
STATE_TTL_SECONDS = 600
_oauth_states: dict[str, tuple[str, float]] = {}

logger = logging.getLogger(__name__)

# Per-user background import progress mirror for compatibility with older tests.
# The API reads from strava_import_jobs so progress survives reloads/restarts.
_import_progress: dict[str, dict] = {}


def _failure_for_activity(activity: dict, reason: str) -> dict:
    activity_id = activity.get("id")
    if not isinstance(activity_id, int):
        activity_id = None
    start_date = activity.get("start_date")
    return {
        "activityId": activity_id,
        "activityName": activity.get("name") or "Unnamed activity",
        "activityDate": start_date[:10] if isinstance(start_date, str) and start_date else None,
        "reason": reason,
    }


def _sanitize_streams(streams: object) -> dict:
    """Return a safe Strava-streams mapping for analysis functions.

    Strava can occasionally return partial or malformed payloads for individual
    activities. This keeps one bad payload from crashing the full import run.
    """
    if not isinstance(streams, dict):
        return {}
    cleaned: dict[str, dict[str, list[float]]] = {}
    for key in ("watts", "heartrate", "cadence", "velocity_smooth", "altitude", "time"):
        stream_obj = streams.get(key)
        if not isinstance(stream_obj, dict):
            continue
        data = stream_obj.get("data")
        if not isinstance(data, list):
            continue
        # Keep only numeric samples to avoid type errors in downstream math.
        numeric = [float(v) for v in data if isinstance(v, (int, float))]
        cleaned[key] = {"data": numeric}
    return cleaned


def _build_metrics_chain_resilient(rides: list[dict], ftp: float) -> tuple[list[dict], list[dict]]:
    """Build metrics while tolerating failures on individual rides.

    Returns ``(metrics_chain, failed_activities)``.
    """
    if not rides:
        return [], []

    metrics_chain: list[dict] = []
    failed_activities: list[dict] = []
    ctl = 0.0
    atl = 0.0

    for ride in sorted(rides, key=lambda r: r["activity_date"]):
        try:
            chunk = build_ride_metrics_chain([ride], ftp, initial_ctl=ctl, initial_atl=atl)
        except Exception as exc:  # noqa: BLE001
            failed_activities.append(
                {
                    "activityId": ride.get("strava_activity_id"),
                    "activityName": ride.get("activity_name") or "Unnamed activity",
                    "activityDate": ride.get("activity_date"),
                    "reason": f"Metric calculation failed: {exc}",
                }
            )
            continue
        if not chunk:
            failed_activities.append(
                {
                    "activityId": ride.get("strava_activity_id"),
                    "activityName": ride.get("activity_name") or "Unnamed activity",
                    "activityDate": ride.get("activity_date"),
                    "reason": "No ride metrics could be calculated",
                }
            )
            continue
        metric = chunk[0]
        ctl = float(metric.get("ctl_after") or ctl)
        atl = float(metric.get("atl_after") or atl)
        metrics_chain.append(metric)

    return metrics_chain, failed_activities


def _progress_payload(
    *,
    job_id: str | None = None,
    status: str = "idle",
    total: int = 0,
    processed: int = 0,
    imported: int = 0,
    skipped: int = 0,
    failed_activities: list[dict] | None = None,
    error: str = "",
) -> dict:
    return {
        "job_id": job_id,
        "status": status,
        "total": total,
        "processed": processed,
        "imported": imported,
        "skipped": skipped,
        "failed_activities": failed_activities or [],
        "error": error,
    }


def _mirror_progress(user_id: str, payload: dict) -> None:
    _import_progress[user_id] = {
        "jobId": payload.get("job_id"),
        "status": payload.get("status", "idle"),
        "total": payload.get("total", 0),
        "processed": payload.get("processed", 0),
        "imported": payload.get("imported", 0),
        "skipped": payload.get("skipped", 0),
        "failedActivities": payload.get("failed_activities", []),
        "error": payload.get("error", ""),
    }


async def _update_import_job(user_id: str, job_id: str | None, **updates) -> None:
    payload = _progress_payload(job_id=job_id, **updates)
    _mirror_progress(user_id, payload)
    if job_id is None:
        return
    async with async_session_maker() as db:
        await crud.update_strava_import_job(db, job_id, **updates)
        await db.commit()


class RefreshRequest(schemas.CamelModel):
    refresh_token: str


class RefreshResponse(schemas.CamelModel):
    access_token: str
    refresh_token: str
    expires_at: int


class StravaAuthResponse(schemas.CamelModel):
    auth_url: str


def _strava_client_id() -> str:
    return settings.strava_client_id


def _strava_client_secret() -> str:
    return settings.strava_client_secret


def _frontend_url() -> str:
    return settings.primary_frontend_url


def _backend_url() -> str:
    return settings.effective_backend_url


def _default_provider() -> str:
    if settings.gemini_api_key:
        return "gemini"
    if settings.openai_api_key:
        return "openai"
    return "gemini"


def _provider(user: models.User) -> str:
    stored = user.ai_provider
    if stored == "gemini" and settings.gemini_api_key:
        return "gemini"
    if stored == "openai" and settings.openai_api_key:
        return "openai"
    return _default_provider()


@router.get("/auth/strava")
async def strava_auth(
    current_user: models.User = Depends(auth.get_current_user),
) -> StravaAuthResponse:
    strava_client_id = _strava_client_id()
    strava_client_secret = _strava_client_secret()
    if not strava_client_id or not strava_client_secret:
        raise HTTPException(
            status_code=500,
            detail=(
                "Strava credentials are not configured on the server. "
                "The server owner must set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET once; "
                "users do not need their own API keys."
            ),
        )

    now = time.time()
    for key, (_, expires_at) in list(_oauth_states.items()):
        if expires_at <= now:
            del _oauth_states[key]

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = (current_user.id, now + STATE_TTL_SECONDS)

    callback_uri = f"{_backend_url()}/api/v1/auth/strava/callback"
    params = urllib.parse.urlencode(
        {
            "client_id": strava_client_id,
            "redirect_uri": callback_uri,
            "response_type": "code",
            "approval_prompt": "force",
            "scope": "read,activity:read_all",
            "state": state,
        }
    )
    return StravaAuthResponse(auth_url=f"{STRAVA_OAUTH_BASE}/oauth/authorize?{params}")


@router.get("/auth/strava/callback")
async def strava_callback(
    db: AsyncSession = Depends(get_db),
    code: str = "",
    error: str = "",
    state: str = "",
) -> RedirectResponse:
    if error or not code or not state:
        safe_error = urllib.parse.quote(
            "Strava authorisation was denied or failed. Please try again."
        )
        return RedirectResponse(f"{_frontend_url()}/strava/callback?error={safe_error}")

    state_data = _oauth_states.pop(state, None)
    if state_data is None:
        return RedirectResponse(
            f"{_frontend_url()}/strava/callback?error=Invalid%20or%20expired%20state"
        )

    user_id, expires_at = state_data
    if expires_at <= time.time():
        return RedirectResponse(f"{_frontend_url()}/strava/callback?error=State%20expired")

    user = await crud.get_user_by_id(db, user_id)
    if user is None:
        return RedirectResponse(f"{_frontend_url()}/strava/callback?error=User%20not%20found")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRAVA_OAUTH_BASE}/oauth/token",
            json={
                "client_id": _strava_client_id(),
                "client_secret": _strava_client_secret(),
                "code": code,
                "grant_type": "authorization_code",
            },
        )

    if not resp.is_success:
        msg = urllib.parse.quote("Token exchange failed. Please try again.")
        return RedirectResponse(f"{_frontend_url()}/strava/callback?error={msg}")

    data = resp.json()
    athlete = data.get("athlete", {})
    athlete_name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()

    await crud.upsert_strava_token(
        db,
        user.id,
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=data["expires_at"],
        athlete_id=athlete.get("id", 0),
        athlete_name=athlete_name,
    )
    await db.flush()
    return RedirectResponse(f"{_frontend_url()}/strava/callback?success=true")


@router.post("/auth/strava/refresh", response_model=RefreshResponse)
async def strava_refresh(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> RefreshResponse:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRAVA_OAUTH_BASE}/oauth/token",
            json={
                "client_id": _strava_client_id(),
                "client_secret": _strava_client_secret(),
                "refresh_token": body.refresh_token,
                "grant_type": "refresh_token",
            },
        )

    if not resp.is_success:
        raise HTTPException(status_code=400, detail="Failed to refresh Strava token")

    data = resp.json()
    token_row = await crud.get_strava_token(db, current_user.id)
    if token_row is not None:
        token_row.access_token = data["access_token"]
        token_row.refresh_token = data["refresh_token"]
        token_row.expires_at = data["expires_at"]
        await db.flush()

    return RefreshResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=data["expires_at"],
    )


async def _get_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """GET with one automatic retry after 16 s on HTTP 429."""
    resp = await client.get(url, **kwargs)
    if resp.status_code == 429:
        await asyncio.sleep(16)
        resp = await client.get(url, **kwargs)
    return resp


async def _run_import_background(
    user_id: str,
    access_token: str,
    ftp: float,
    after_ts: int,
    replace_existing: bool = False,
    max_heart_rate: int | None = None,
    job_id: str | None = None,
) -> None:
    """Fetch Strava activities and build the ride-metrics chain in the background.

    Opens its own DB session so it is not tied to the request lifecycle.
    After building the ride-metrics chain, runs ``estimate_ftp_over_time`` on
    the fetched stream data and persists per-ride FTP estimates as
    ``AthleteMetricSnapshot`` rows (source = ``"ftp_estimation"``).
    """
    if job_id is None:
        async with async_session_maker() as db:
            job = await crud.create_strava_import_job(db, user_id)
            await db.commit()
            job_id = job.id

    failed_activities: list[dict] = []
    total = 0
    processed = 0
    imported = 0
    skipped = 0
    await _update_import_job(
        user_id,
        job_id,
        status="running",
        total=total,
        processed=processed,
        imported=imported,
        skipped=skipped,
        failed_activities=failed_activities,
        error="",
    )
    try:
        if replace_existing:
            async with async_session_maker() as db:
                await crud.delete_all_ride_metrics(db, user_id)
                await crud.delete_athlete_metric_snapshots(db, user_id)
                await db.commit()

        # --- Paginate activity list ---
        all_activities: list[dict] = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                resp = await _get_with_retry(
                    client,
                    f"{STRAVA_OAUTH_BASE}/api/v3/athlete/activities",
                    params={"per_page": 100, "page": page, "after": after_ts},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if not resp.is_success:
                    raise RuntimeError(f"Strava list error {resp.status_code}")
                batch: list[dict] = resp.json()
                if not batch:
                    break
                all_activities.extend(batch)
                page += 1

        total = len(all_activities)
        await _update_import_job(
            user_id,
            job_id,
            status="running",
            total=total,
            processed=processed,
            imported=imported,
            skipped=skipped,
            failed_activities=failed_activities,
            error="",
        )

        # --- Fetch streams per activity ---
        rides: list[dict] = []
        keys = "watts,heartrate,cadence,velocity_smooth,altitude,time"
        async with httpx.AsyncClient() as client:
            for idx, activity in enumerate(all_activities):
                activity_id = activity.get("id")
                if not isinstance(activity_id, int):
                    skipped += 1
                    processed = idx + 1
                    failed_activities.append(_failure_for_activity(activity, "Missing Strava activity ID"))
                    await _update_import_job(
                        user_id,
                        job_id,
                        status="running",
                        total=total,
                        processed=processed,
                        imported=imported,
                        skipped=skipped,
                        failed_activities=failed_activities,
                        error="",
                    )
                    continue

                start_date: str = activity.get("start_date", "")
                activity_date = start_date[:10] if start_date else ""
                if not activity_date:
                    skipped += 1
                    processed = idx + 1
                    failed_activities.append(_failure_for_activity(activity, "Missing activity date"))
                    await _update_import_job(
                        user_id,
                        job_id,
                        status="running",
                        total=total,
                        processed=processed,
                        imported=imported,
                        skipped=skipped,
                        failed_activities=failed_activities,
                        error="",
                    )
                    continue

                sport_type: str = activity.get("sport_type") or activity.get("type") or "cycling"
                duration_seconds: int = int(activity.get("elapsed_time") or activity.get("moving_time") or 0)

                try:
                    resp = await _get_with_retry(
                        client,
                        f"{STRAVA_OAUTH_BASE}/api/v3/activities/{activity_id}/streams",
                        params={"keys": keys, "key_by_type": "true"},
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    if not resp.is_success:
                        skipped += 1
                        processed = idx + 1
                        failed_activities.append(
                            _failure_for_activity(activity, f"Stream download failed with HTTP {resp.status_code}")
                        )
                        await _update_import_job(
                            user_id,
                            job_id,
                            status="running",
                            total=total,
                            processed=processed,
                            imported=imported,
                            skipped=skipped,
                            failed_activities=failed_activities,
                            error="",
                        )
                        continue
                    streams = _sanitize_streams(resp.json())
                    rides.append({
                        "strava_activity_id": activity_id,
                        "activity_name": activity.get("name") or "Unnamed activity",
                        "activity_start_datetime": start_date or None,
                        "activity_date": activity_date,
                        "sport_type": sport_type,
                        "duration_seconds": duration_seconds,
                        "streams": streams,
                    })
                    imported = len(rides)
                except Exception as exc:  # noqa: BLE001
                    # Keep the import moving even when one activity fails.
                    skipped += 1
                    failed_activities.append(_failure_for_activity(activity, f"Stream download failed: {exc}"))
                processed = idx + 1
                await _update_import_job(
                    user_id,
                    job_id,
                    status="running",
                    total=total,
                    processed=processed,
                    imported=imported,
                    skipped=skipped,
                    failed_activities=failed_activities,
                    error="",
                )

        # --- Build chain and persist in batches ---
        metrics_chain, failed_metric_activities = _build_metrics_chain_resilient(rides, ftp)
        failed_activities.extend(failed_metric_activities)
        skipped += len(failed_metric_activities)
        imported = len(metrics_chain)
        await _update_import_job(
            user_id,
            job_id,
            status="running",
            total=total,
            processed=processed,
            imported=imported,
            skipped=skipped,
            failed_activities=failed_activities,
            error="",
        )
        BATCH = 50
        for i in range(0, len(metrics_chain), BATCH):
            async with async_session_maker() as db:
                ride_meta_by_id = {r["strava_activity_id"]: r for r in rides}
                for m in metrics_chain[i : i + BATCH]:
                    meta = ride_meta_by_id.get(m["strava_activity_id"], {})
                    m["activity_name"] = meta.get("activity_name")
                    m["activity_start_datetime"] = meta.get("activity_start_datetime")
                    await crud.upsert_ride_metric(db, user_id, **m)
                await db.commit()

        if metrics_chain:
            streams_by_activity_id = {
                ride["strava_activity_id"]: ride.get("streams") or {}
                for ride in rides
            }
            async with async_session_maker() as db:
                user = await crud.get_user_by_id(db, user_id)
                existing_plan = await crud.get_training_plan(db, user_id)
                training_plan = existing_plan.plan if existing_plan is not None else []
                auto_matched = await apply_ride_plan_matches(
                    db,
                    user_id,
                    training_plan,
                    [m["strava_activity_id"] for m in metrics_chain],
                )
                if user is not None:
                    for ride in auto_matched:
                        await review_matched_ride_and_adapt(
                            db,
                            user,
                            ride,
                            training_plan,
                            provider=_provider(user),
                            streams=streams_by_activity_id.get(ride.strava_activity_id),
                        )
                await db.commit()

        await _update_import_job(
            user_id,
            job_id,
            status="done",
            total=total,
            processed=processed,
            imported=imported,
            skipped=skipped,
            failed_activities=failed_activities,
            error="",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Background Strava import failed for user %s after processing %s/%s activities: %s",
            user_id,
            processed,
            total,
            exc,
            exc_info=True,
        )
        await _update_import_job(
            user_id,
            job_id,
            status="error",
            total=total,
            processed=processed,
            imported=imported,
            skipped=skipped,
            failed_activities=failed_activities,
            error=str(exc),
        )


@router.get("/strava/activities")
async def get_strava_activities(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
    after_id: int | None = None,
) -> list[dict]:
    if current_user.strava_token is None:
        raise HTTPException(status_code=404, detail="Strava not connected")

    access_token = await ensure_fresh_strava_token(current_user.strava_token, db)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{STRAVA_OAUTH_BASE}/api/v3/athlete/activities",
            params={"per_page": 10},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail="Failed to fetch Strava activities")
    activities: list[dict] = resp.json()
    if after_id is not None:
        activities = [a for a in activities if isinstance(a.get("id"), int) and a["id"] > after_id]
    return activities


@router.delete("/strava/disconnect")
async def disconnect_strava(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    await crud.delete_strava_token(db, current_user.id)
    return {"status": "disconnected"}


@router.post("/strava/import-history", status_code=202)
async def import_strava_history(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
    months: int = 6,
    replace_existing: bool = False,
) -> dict:
    """Start a background import of recent Strava activities.

    Returns 202 immediately; progress can be polled via GET /strava/import-progress.
    """
    if current_user.strava_token is None:
        raise HTTPException(status_code=404, detail="Strava not connected")

    access_token = await ensure_fresh_strava_token(current_user.strava_token, db)

    ftp: float = 0.0
    if current_user.rider_assessment and current_user.rider_assessment.estimated_ftp:
        ftp = float(current_user.rider_assessment.estimated_ftp)
    elif current_user.current_ftp:
        ftp = float(current_user.current_ftp)

    months = max(1, min(months, 24))
    after_ts = int((datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp())

    # Prevent stacking duplicate background tasks: if one is already running, return it.
    running_job = await crud.get_running_strava_import_job(db, current_user.id)
    if running_job is not None:
        return {"status": "already_running", "jobId": running_job.id}

    job = await crud.create_strava_import_job(db, current_user.id)
    await db.commit()

    _mirror_progress(
        current_user.id,
        _progress_payload(
            job_id=job.id,
            status="running",
            total=0,
            processed=0,
            imported=0,
            skipped=0,
            failed_activities=[],
            error="",
        ),
    )

    background_tasks.add_task(
        _run_import_background,
        user_id=current_user.id,
        access_token=access_token,
        ftp=ftp,
        after_ts=after_ts,
        replace_existing=replace_existing,
        max_heart_rate=current_user.max_heart_rate,
        job_id=job.id,
    )

    return {"status": "started", "jobId": job.id}


@router.get("/strava/import-progress")
async def get_import_progress(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ImportProgressResponse:
    """Return the current background import progress for the authenticated user."""
    job = await crud.get_latest_strava_import_job(db, current_user.id)
    if job is None:
        return schemas.ImportProgressResponse(status="idle")
    failed_activities = job.failed_activities if isinstance(job.failed_activities, list) else []
    return schemas.ImportProgressResponse(
        job_id=job.id,
        status=job.status,
        total=job.total,
        processed=job.processed,
        imported=job.imported,
        skipped=job.skipped,
        failed_activities=failed_activities,
        error=job.error,
    )
