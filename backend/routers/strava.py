"""Strava OAuth and activity proxy routes."""

import asyncio
import logging
import secrets
import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import async_session_maker, get_db
from services import ride_ingest
from services.analysis import estimate_ftp_over_time
from services.activity_imports import (
    ImportedActivity,
    reported_distance_km,
    reported_elevation_m,
)
from services.progress_store import mark_finished, prune_progress, try_mark_running
from services.strava_service import (
    STRAVA_OAUTH_BASE,
    STRAVA_REFUSED_DETAIL,
    ensure_fresh_strava_token,
    sanitize_streams,
)
from services.weather_service import (
    enrich_activity_weather,
    home_coordinates_for_user,
)

router = APIRouter(tags=["strava"])
STATE_TTL_SECONDS = 600
_oauth_states: dict[str, tuple[str, float]] = {}

logger = logging.getLogger(__name__)

# Per-user background import progress  {user_id: {status, total, processed, skipped, error}}.
# In-process, single-replica only (see docs/multi_replica.md); bounded via
# prune_progress so completed entries don't accumulate forever (#326).
_import_progress: dict[int, dict] = {}


def _optional_int(value: object) -> int | None:
    """Round a provider number to an int, or ``None`` if it is not one."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(value)


class RefreshResponse(schemas.CamelModel):
    # The access and refresh tokens stay server-side: no client reads them, and
    # returning the decrypted secrets in the body was needless exposure
    # (ops#132). Only the new expiry is reported.
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
    if not settings.strava_enabled:
        # Same words as below: the app tells this apart from a gateway 503 by
        # "not configured" (StravaConnect.tsx).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strava is not configured on this server. Connect Strava to intervals.icu instead.",
        )
    if not strava_client_id or not strava_client_secret:
        # 503, not 500: nothing crashed, the server is not set up for it.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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

    return RefreshResponse(expires_at=data["expires_at"])


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
        keys = "watts,heartrate,cadence,velocity_smooth,altitude,distance,time,latlng"
        # Resolved once for the whole import: indoor rides carry no GPS, so their
        # conditions come from the athlete's training location (#495).
        async with async_session_maker() as db:
            home_coordinates = await home_coordinates_for_user(db, user_id)
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
                    activity.get("moving_time") or activity.get("elapsed_time") or 0
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
                        streams = sanitize_streams(resp.json())
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
                    activity, streams=streams, fallback_coordinates=home_coordinates
                )

                imported_activity = ImportedActivity(
                    source="strava",
                    external_activity_id=str(activity_id),
                    name=activity_name if isinstance(activity_name, str) else None,
                    start_datetime=start_date_local or start_date or None,
                    activity_date=activity_date,
                    sport_type=sport_type,
                    duration_seconds=duration_seconds,
                    distance_km=reported_distance_km(activity.get("distance")),
                    elevation_gain_m=reported_elevation_m(
                        activity.get("total_elevation_gain")
                    ),
                    streams=streams,
                    weather=weather_fields,
                    metadata={"strava_activity_id": activity_id},
                    # Strava reports an average HR for activities that carry no
                    # stream at all — the gym/hike case, where it is the only
                    # intensity signal there is (#579).
                    summary_avg_hr_bpm=_optional_int(activity.get("average_heartrate")),
                )
                rides.append(imported_activity.to_ride_input())
                _import_progress[user_id]["processed"] = idx + 1

        # --- Store in batches through the shared ingest (#248) ---
        # Oldest first, each batch in a transaction of its own. The ingest
        # annotates the logged effort (#714), prices against the athlete's
        # threshold pace (#716), seeds the chain from the newest stored ride and
        # replays it when a batch is older than that ride: an import without
        # replace_existing chained its rides from an empty ledger and never
        # replayed, so their fitness ignored every earlier ride. It also
        # reconciles the hand-logged sessions in the batch's transaction (#745),
        # matches the rides to the plan and tells the rides node (#245). A ride
        # that cannot be priced is skipped, not the whole import.
        rides.sort(key=lambda ride: ride["activity_date"])
        imported = 0
        BATCH = 50
        for i in range(0, len(rides), BATCH):
            async with async_session_maker() as db:
                import_user = await crud.get_user_by_id(db, user_id)
                if import_user is None:
                    break
                result = await ride_ingest.ingest_rides(
                    db,
                    import_user,
                    rides[i : i + BATCH],
                    ftp=ftp,
                    policy=ride_ingest.HISTORY,
                )
                await db.commit()
            imported += result.stored
            skipped += len(result.unpriced)
            _import_progress[user_id]["imported"] = imported
            _import_progress[user_id]["skipped"] = skipped

        # --- Estimate FTP over time from steady intervals ---
        try:
            ftp_series = estimate_ftp_over_time(
                rides,
                max_heart_rate=max_heart_rate,
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
                            map_5min=point.get("map_5min"),
                            source="ftp_estimation",
                            recorded_at=ride_dt,
                        )
                    await db.commit()

        _import_progress[user_id] = mark_finished(
            {
                "status": "done",
                "total": len(all_activities),
                "processed": len(all_activities),
                "imported": imported,
                "skipped": skipped,
                "error": "",
            }
        )
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
        _import_progress[user_id] = mark_finished(
            {
                "status": "error",
                "total": prev.get("total", 0),
                "processed": prev.get("processed", 0),
                "imported": prev.get("imported", 0),
                "skipped": prev.get("skipped", 0),
                "error": str(exc),
            }
        )


@router.get("/strava/activities")
async def get_strava_activities(
    db: AsyncSession = Depends(get_db, scope="function"),
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
    # Strava's own status is not passed on: its 401 signed the athlete out of
    # the app (ai-trainer-ops#95).
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=403, detail=STRAVA_REFUSED_DETAIL)
    if not resp.is_success:
        raise HTTPException(
            status_code=502, detail="Strava did not answer just now. Try again later."
        )
    activities: list[dict] = resp.json()
    if after_id is not None:
        activities = [
            a for a in activities if isinstance(a.get("id"), int) and a["id"] > after_id
        ]
    return activities


@router.delete("/strava/disconnect")
async def disconnect_strava(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    await crud.delete_strava_token(db, current_user.id)
    return {"status": "disconnected"}


@router.post("/strava/import-history", status_code=202)
async def import_strava_history(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
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

    # Drop stale completed entries so the store can't grow without bound (#326).
    prune_progress(_import_progress)

    # Atomically claim the running slot: if one is already running, bail out
    # without stacking a duplicate background task.
    started = try_mark_running(
        _import_progress,
        current_user.id,
        {
            "status": "running",
            "total": 0,
            "processed": 0,
            "imported": 0,
            "skipped": 0,
            "error": "",
        },
    )
    if not started:
        return {"status": "already_running"}

    background_tasks.add_task(
        _run_import_background,
        user_id=current_user.id,
        access_token=access_token,
        ftp=ftp,
        after_ts=after_ts,
        replace_existing=replace_existing,
        max_heart_rate=current_user.max_heart_rate,
    )

    return {"status": "started"}


@router.get("/strava/import-progress")
async def get_import_progress(
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ImportProgressResponse:
    """Return the current background import progress for the authenticated user."""
    prune_progress(_import_progress)
    progress = _import_progress.get(current_user.id)
    if progress is None:
        return schemas.ImportProgressResponse(status="idle")
    return schemas.ImportProgressResponse(**progress)
