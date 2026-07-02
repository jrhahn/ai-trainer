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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import async_session_maker, get_db
from services.analysis import build_ride_metrics_chain, estimate_ftp_over_time
from services.activity_imports import ImportedActivity
from services.ride_matching import apply_ride_plan_matches
from services.strava_service import (
    STRAVA_OAUTH_BASE,
    ensure_fresh_strava_token,
    fetch_activity_streams,
)
from services.weather_service import enrich_activity_weather

router = APIRouter(tags=["strava"])
STATE_TTL_SECONDS = 600
_oauth_states: dict[str, tuple[str, float]] = {}

logger = logging.getLogger(__name__)

# Per-user background import progress  {user_id: {status, total, processed, skipped, error}}
_import_progress: dict[int, dict] = {}


def _sanitize_streams(streams: object) -> dict:
    """Return a safe Strava-streams mapping for analysis functions.

    Strava can occasionally return partial or malformed payloads for individual
    activities. This keeps one bad payload from crashing the full import run.
    """
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
        # Keep only numeric samples to avoid type errors in downstream math.
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


def _build_metrics_chain_resilient(
    rides: list[dict], ftp: float
) -> tuple[list[dict], int]:
    """Build metrics while tolerating failures on individual rides.

    Returns ``(metrics_chain, failed_count)``.
    """
    if not rides:
        return [], 0

    metrics_chain: list[dict] = []
    failed = 0
    ctl = 0.0
    atl = 0.0

    for ride in sorted(rides, key=lambda r: r["activity_date"]):
        try:
            chunk = build_ride_metrics_chain(
                [ride], ftp, initial_ctl=ctl, initial_atl=atl
            )
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        if not chunk:
            continue
        metric = chunk[0]
        ctl = float(metric.get("ctl_after") or ctl)
        atl = float(metric.get("atl_after") or atl)
        metrics_chain.append(metric)

    return metrics_chain, failed


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
        return RedirectResponse(
            f"{_frontend_url()}/strava/callback?error=State%20expired"
        )

    user = await crud.get_user_by_id(db, user_id)
    if user is None:
        return RedirectResponse(
            f"{_frontend_url()}/strava/callback?error=User%20not%20found"
        )

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
    athlete_name = (
        f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()
    )

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
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> RefreshResponse:
    """Force-refresh the authenticated user's stored Strava token.

    The refresh token comes from the stored row, never from the client, and the
    row is locked for the refresh so it cannot race the background sync (Strava
    rotates and invalidates the previous refresh token on every refresh).
    """
    token_row = (
        await db.execute(
            select(models.StravaToken)
            .where(models.StravaToken.user_id == current_user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if token_row is None:
        raise HTTPException(status_code=404, detail="Strava not connected")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRAVA_OAUTH_BASE}/oauth/token",
            json={
                "client_id": _strava_client_id(),
                "client_secret": _strava_client_secret(),
                "refresh_token": token_row.refresh_token,
                "grant_type": "refresh_token",
            },
        )

    if not resp.is_success:
        raise HTTPException(status_code=400, detail="Failed to refresh Strava token")

    data = resp.json()
    token_row.access_token = data["access_token"]
    token_row.refresh_token = data["refresh_token"]
    token_row.expires_at = data["expires_at"]
    await db.flush()

    return RefreshResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=data["expires_at"],
    )


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, **kwargs
) -> httpx.Response:
    """GET with one automatic retry after 16 s on HTTP 429."""
    resp = await client.get(url, **kwargs)
    if resp.status_code == 429:
        await asyncio.sleep(16)
        resp = await client.get(url, **kwargs)
    return resp


async def _run_import_background(
    user_id: int,
    access_token: str,
    ftp: float,
    after_ts: int,
    replace_existing: bool = False,
    max_heart_rate: int | None = None,
    resting_heart_rate: int | None = None,
) -> None:
    """Fetch Strava activities and build the activity-metrics chain in the background.

    Opens its own DB session so it is not tied to the request lifecycle.
    After building the metrics chain, runs ``estimate_ftp_over_time`` on
    the fetched stream data and persists per-activity FTP estimates as
    ``AthleteMetricSnapshot`` rows (source = ``"ftp_estimation"``).
    """
    _import_progress[user_id] = {
        "status": "running",
        "total": 0,
        "processed": 0,
        "imported": 0,
        "skipped": 0,
        "error": "",
    }
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

        _import_progress[user_id]["total"] = len(all_activities)

        # --- Fetch streams per activity ---
        rides: list[dict] = []
        skipped = 0
        keys = "watts,heartrate,cadence,velocity_smooth,altitude,time,latlng"
        async with httpx.AsyncClient() as client:
            for idx, activity in enumerate(all_activities):
                activity_id = activity.get("id")
                if not isinstance(activity_id, int):
                    skipped += 1
                    _import_progress[user_id]["skipped"] = skipped
                    _import_progress[user_id]["processed"] = idx + 1
                    continue

                start_date: str = activity.get("start_date", "")
                start_date_local: str = activity.get("start_date_local", "")
                activity_date_source = start_date_local or start_date
                activity_date = (
                    activity_date_source[:10] if activity_date_source else ""
                )
                if not activity_date:
                    skipped += 1
                    _import_progress[user_id]["skipped"] = skipped
                    _import_progress[user_id]["processed"] = idx + 1
                    continue

                sport_type: str = (
                    activity.get("sport_type") or activity.get("type") or "cycling"
                )
                duration_seconds: int = int(
                    activity.get("elapsed_time") or activity.get("moving_time") or 0
                )
                activity_name = activity.get("name")

                streams: dict = {}
                stream_transient_failure = False
                try:
                    resp = await _get_with_retry(
                        client,
                        f"{STRAVA_OAUTH_BASE}/api/v3/activities/{activity_id}/streams",
                        params={"keys": keys, "key_by_type": "true"},
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    if resp.is_success:
                        streams = _sanitize_streams(resp.json())
                    elif resp.status_code == 429 or resp.status_code >= 500:
                        # Transient error survived the retry — don't import degraded.
                        stream_transient_failure = True
                    # else: permanent non-success → activity genuinely has no
                    # streams; import summary-only data as before.
                except Exception:  # noqa: BLE001
                    stream_transient_failure = True

                if stream_transient_failure:
                    # Skip rather than bake in stream-less data as if complete;
                    # re-running the import (idempotent upsert) will pick it up
                    # once Strava recovers (#325).
                    logger.warning(
                        "Transient stream download failure for Strava activity %s; "
                        "skipping to avoid a degraded import",
                        activity_id,
                    )
                    skipped += 1
                    _import_progress[user_id]["skipped"] = skipped
                    _import_progress[user_id]["processed"] = idx + 1
                    continue

                weather_fields = await enrich_activity_weather(
                    activity, streams=streams
                )

                imported_activity = ImportedActivity(
                    source="strava",
                    external_activity_id=str(activity_id),
                    name=activity_name if isinstance(activity_name, str) else None,
                    start_datetime=start_date_local or start_date or None,
                    activity_date=activity_date,
                    sport_type=sport_type,
                    duration_seconds=duration_seconds,
                    streams=streams,
                    weather=weather_fields,
                    metadata={"strava_activity_id": activity_id},
                )
                rides.append(imported_activity.to_ride_input())
                _import_progress[user_id]["processed"] = idx + 1

        # --- Build chain and persist in batches ---
        metrics_chain, failed_metrics = _build_metrics_chain_resilient(rides, ftp)
        skipped += failed_metrics
        _import_progress[user_id]["skipped"] = skipped
        BATCH = 50
        for i in range(0, len(metrics_chain), BATCH):
            async with async_session_maker() as db:
                for m in metrics_chain[i : i + BATCH]:
                    await crud.upsert_ride_metric(db, user_id, **m)
                await db.commit()

            _import_progress[user_id]["imported"] = min(i + BATCH, len(metrics_chain))

        if metrics_chain:
            async with async_session_maker() as db:
                existing_plan = await crud.get_training_plan(db, user_id)
                training_plan = existing_plan.plan if existing_plan is not None else []
                await apply_ride_plan_matches(
                    db,
                    user_id,
                    training_plan,
                    [m["strava_activity_id"] for m in metrics_chain],
                )
                await db.commit()

        # --- Estimate FTP over time from steady intervals ---
        try:
            ftp_series = estimate_ftp_over_time(
                rides,
                max_heart_rate=max_heart_rate,
                resting_heart_rate=resting_heart_rate,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "FTP-over-time estimation failed during Strava import for user %s",
                user_id,
                exc_info=True,
            )
            ftp_series = []
        if ftp_series:
            for i in range(0, len(ftp_series), BATCH):
                async with async_session_maker() as db:
                    for point in ftp_series[i : i + BATCH]:
                        try:
                            ride_dt = datetime.fromisoformat(point["date"]).replace(
                                tzinfo=timezone.utc
                            )
                        except ValueError:
                            ride_dt = datetime.now(timezone.utc)
                        await crud.create_athlete_metric_snapshot(
                            db,
                            user_id,
                            ftp=point["ftp"],
                            threshold_hr=None,
                            source="ftp_estimation",
                            recorded_at=ride_dt,
                        )
                    await db.commit()

        _import_progress[user_id] = {
            "status": "done",
            "total": len(all_activities),
            "processed": len(all_activities),
            "imported": len(metrics_chain),
            "skipped": skipped,
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001
        prev = _import_progress.get(user_id, {})
        logger.error(
            "Background Strava import failed for user %s after processing %s/%s activities: %s",
            user_id,
            prev.get("processed", 0),
            prev.get("total", 0),
            exc,
            exc_info=True,
        )
        _import_progress[user_id] = {
            "status": "error",
            "total": prev.get("total", 0),
            "processed": prev.get("processed", 0),
            "imported": prev.get("imported", 0),
            "skipped": prev.get("skipped", 0),
            "error": str(exc),
        }


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
        raise HTTPException(
            status_code=resp.status_code, detail="Failed to fetch Strava activities"
        )
    activities: list[dict] = resp.json()
    if after_id is not None:
        activities = [
            a for a in activities if isinstance(a.get("id"), int) and a["id"] > after_id
        ]
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
    after_ts = int(
        (datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp()
    )

    # Prevent stacking duplicate background tasks: if one is already running, bail out.
    current_progress = _import_progress.get(current_user.id, {})
    if current_progress.get("status") == "running":
        return {"status": "already_running"}

    # Mark started immediately so the progress endpoint sees "running" right away
    _import_progress[current_user.id] = {
        "status": "running",
        "total": 0,
        "processed": 0,
        "imported": 0,
        "skipped": 0,
        "error": "",
    }

    background_tasks.add_task(
        _run_import_background,
        user_id=current_user.id,
        access_token=access_token,
        ftp=ftp,
        after_ts=after_ts,
        replace_existing=replace_existing,
        max_heart_rate=current_user.max_heart_rate,
        resting_heart_rate=current_user.resting_heart_rate,
    )

    return {"status": "started"}


@router.get("/strava/import-progress")
async def get_import_progress(
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ImportProgressResponse:
    """Return the current background import progress for the authenticated user."""
    progress = _import_progress.get(current_user.id)
    if progress is None:
        return schemas.ImportProgressResponse(status="idle")
    return schemas.ImportProgressResponse(**progress)
