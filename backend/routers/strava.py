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
from services.analysis import build_ride_metrics_chain, estimate_ftp_over_time
from services.strava_service import (
    STRAVA_OAUTH_BASE,
    ensure_fresh_strava_token,
    fetch_activity_streams,
)

router = APIRouter(tags=["strava"])
STATE_TTL_SECONDS = 600
_oauth_states: dict[str, tuple[str, float]] = {}

logger = logging.getLogger(__name__)

# Per-user background import progress  {user_id: {status, total, processed, skipped, error}}
_import_progress: dict[int, dict] = {}


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
    user_id: int,
    access_token: str,
    ftp: float,
    after_ts: int,
    max_heart_rate: int | None = None,
    resting_heart_rate: int | None = None,
) -> None:
    """Fetch Strava activities and build the ride-metrics chain in the background.

    Opens its own DB session so it is not tied to the request lifecycle.
    After building the ride-metrics chain, runs ``estimate_ftp_over_time`` on
    the fetched stream data and persists per-ride FTP estimates as
    ``AthleteMetricSnapshot`` rows (source = ``"ftp_estimation"``).
    """
    _import_progress[user_id] = {"status": "running", "total": 0, "processed": 0, "skipped": 0, "error": ""}
    try:
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
        keys = "watts,heartrate,cadence,velocity_smooth,altitude,time"
        async with httpx.AsyncClient() as client:
            for idx, activity in enumerate(all_activities):
                activity_id = activity.get("id")
                if not isinstance(activity_id, int):
                    skipped += 1
                    _import_progress[user_id]["skipped"] = skipped
                    _import_progress[user_id]["processed"] = idx + 1
                    continue

                start_date: str = activity.get("start_date", "")
                activity_date = start_date[:10] if start_date else ""
                if not activity_date:
                    skipped += 1
                    _import_progress[user_id]["skipped"] = skipped
                    _import_progress[user_id]["processed"] = idx + 1
                    continue

                sport_type: str = activity.get("sport_type") or activity.get("type") or "cycling"
                duration_seconds: int = int(activity.get("elapsed_time") or activity.get("moving_time") or 0)

                resp = await _get_with_retry(
                    client,
                    f"{STRAVA_OAUTH_BASE}/api/v3/activities/{activity_id}/streams",
                    params={"keys": keys, "key_by_type": "true"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                streams: dict = resp.json() if resp.is_success else {}

                rides.append({
                    "strava_activity_id": activity_id,
                    "activity_date": activity_date,
                    "sport_type": sport_type,
                    "duration_seconds": duration_seconds,
                    "streams": streams,
                })
                _import_progress[user_id]["processed"] = idx + 1

        # --- Build chain and persist in batches ---
        metrics_chain = build_ride_metrics_chain(rides, ftp, initial_ctl=0.0, initial_atl=0.0)
        BATCH = 50
        for i in range(0, len(metrics_chain), BATCH):
            async with async_session_maker() as db:
                for m in metrics_chain[i : i + BATCH]:
                    await crud.upsert_ride_metric(db, user_id, **m)
                await db.commit()

        # --- Estimate FTP over time from steady intervals ---
        ftp_series = estimate_ftp_over_time(
            rides,
            max_heart_rate=max_heart_rate,
            resting_heart_rate=resting_heart_rate,
        )
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
            "processed": len(metrics_chain),
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

    # Prevent stacking duplicate background tasks: if one is already running, bail out.
    current_progress = _import_progress.get(current_user.id, {})
    if current_progress.get("status") == "running":
        return {"status": "already_running"}

    # Mark started immediately so the progress endpoint sees "running" right away
    _import_progress[current_user.id] = {"status": "running", "total": 0, "processed": 0, "skipped": 0, "error": ""}

    background_tasks.add_task(
        _run_import_background,
        user_id=current_user.id,
        access_token=access_token,
        ftp=ftp,
        after_ts=after_ts,
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
