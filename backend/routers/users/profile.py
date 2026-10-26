"""The athlete's account: read it, change it, delete it and export it."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import get_db
from routers.dependencies import enforce_login_rate_limit, require_password
from services import account_export
from services import authelia_store
from domain import day_preference
from services import opening_message
from services.athlete_language import interface_language
from services.analysis import check_ftp_against_map
from services.dates import app_today, app_today_iso, known_timezone, request_timezone
from services.key_hint import key_hint

router = APIRouter(prefix="/users/me", tags=["users"])

logger = logging.getLogger(__name__)


def _user_to_response(
    user: models.User, ftp_plausibility_warning: str | None = None
) -> schemas.UserResponse:
    strava_connection = None
    if user.strava_token is not None:
        strava_connection = schemas.StravaConnectionSchema(
            athlete_id=user.strava_token.athlete_id,
            athlete_name=user.strava_token.athlete_name,
        )

    intervals_connection = None
    if user.intervals_token is not None:
        intervals_connection = schemas.IntervalsConnectionSchema(
            athlete_id=user.intervals_token.athlete_id,
            athlete_name=user.intervals_token.athlete_name,
            key_hint=key_hint(user.intervals_token.api_key),
        )

    rider_assessment = None
    if user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            user.rider_assessment, from_attributes=True
        )

    return schemas.UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        is_onboarded=user.is_onboarded,
        strava_analysis_complete=user.strava_analysis_complete,
        last_strava_activity_id=user.last_strava_activity_id,
        strava_auto_sync_enabled=user.strava_auto_sync_enabled,
        intervals_analysis_complete=user.intervals_analysis_complete,
        last_intervals_activity_id=user.last_intervals_activity_id,
        intervals_auto_sync_enabled=user.intervals_auto_sync_enabled,
        bike_type=user.bike_type,
        training_goal=user.training_goal,
        race_date=user.race_date,
        race_description=user.race_description,
        weekly_hours=user.weekly_hours,
        sports=user.sports,
        # Monday first, also for a row stored in tap order (ai-trainer-ops#190).
        training_days=schemas.week_ordered(user.training_days)
        if user.training_days
        else user.training_days,
        follows_training_plan=user.follows_training_plan,
        max_heart_rate=user.max_heart_rate,
        resting_heart_rate=user.resting_heart_rate,
        current_ftp=user.current_ftp,
        threshold_pace_seconds_per_km=user.threshold_pace_seconds_per_km,
        weight_kg=user.weight_kg,
        fitness_level=user.fitness_level,
        day_preferences=day_preference.merged(user.day_preferences, []),
        ai_provider=user.ai_provider,
        consumed_tokens=user.consumed_tokens or 0,
        ftp_plausibility_warning=ftp_plausibility_warning,
        rider_assessment=rider_assessment,
        strava_connection=strava_connection,
        intervals_connection=intervals_connection,
        strava_available=settings.strava_available,
    )


async def _ftp_plausibility_warning(db: AsyncSession, user: models.User) -> str | None:
    """Check the stored FTP against the athlete's recorded MAP proxy.

    Advisory only — a bad ratio is surfaced, never silently corrected.  A
    lookup failure must not break profile reads, so errors degrade to no
    warning.
    """
    if not user.current_ftp:
        return None
    try:
        async with db.begin_nested():
            map_5min = await crud.get_latest_map_5min(db, user.id)
    except Exception:  # noqa: BLE001
        logger.warning("MAP lookup failed for FTP plausibility check", exc_info=True)
        return None
    return check_ftp_against_map(user.current_ftp, map_5min)


@router.get("", response_model=schemas.UserResponse)
async def get_me(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.UserResponse:
    # The nightly job counts the athlete's own day (ai-trainer-ops#128). The
    # browser sends its zone with every call; this one runs when the app opens.
    stated = known_timezone(request_timezone(request))
    if stated is not None and stated != current_user.timezone_name:
        current_user.timezone_name = stated
    # And the language it shows, so the night's plan texts can be written in it
    # (ai-trainer-ops#271).
    language = interface_language(request)
    if language is not None and language != current_user.interface_language:
        current_user.interface_language = language
    return _user_to_response(
        current_user, await _ftp_plausibility_warning(db, current_user)
    )


@router.put("", response_model=schemas.UserResponse)
async def update_me(
    body: schemas.UpdateProfileRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.UserResponse:
    updates = body.model_dump(exclude_unset=True)
    if updates.get("training_goal") == "general_fitness":
        updates["race_date"] = None
        updates["race_description"] = None
    opening: opening_message.OpeningMessage | None = None
    if (
        updates.get("is_onboarded") is True
        and not current_user.is_onboarded
        and current_user.opening_message_at is None
    ):
        # Onboarding writes ``isOnboarded`` last, once the plan exists
        # (ai-trainer-ops#41), so this is the moment the coach can speak first
        # (#153). Composed before anything below writes, so the model call
        # holds no lock. The interface language is the only language evidence
        # a new account has; nothing has been said in the chat yet. The facts
        # are the stored profile: onboarding sends ``isOnboarded`` on its own,
        # after the profile write.
        opening = await opening_message.compose_opening_message(
            db,
            current_user,
            language=interface_language(request),
            today=app_today(
                timezone_name=known_timezone(request_timezone(request))
                or current_user.timezone_name
            ),
        )
    for field, value in updates.items():
        setattr(current_user, field, value)
    await db.flush()
    if opening is not None:
        await opening_message.post_opening_message(db, current_user, opening)
    await db.refresh(current_user)
    return _user_to_response(
        current_user, await _ftp_plausibility_warning(db, current_user)
    )


@router.delete("")
async def delete_me(
    body: schemas.AccountDeleteRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    """Delete the account, its data, and the credential that opens it.

    Two things were missing (ai-trainer-ops#35). The password, because a bare
    session was enough to destroy an athlete's whole history while the two
    neighbouring step-up routes both asked for more. And the Authelia entry: with
    header auth on, the password lives in ``users_database.yml`` and this route
    only dropped the database row, so ``POST /auth/login`` — which recreates a
    missing row from a valid credential — brought the account back on the next
    sign-in. Measured: delete, log in with the same password, 200 and a fresh
    JWT.

    The credential goes first. A crash between the two then leaves an account
    nobody can log into, which is the recoverable side; the other order leaves a
    credential with no account, which is the bug above.

    The issued token needs no revoking: ``get_current_user`` loads the row to
    authenticate, so it 401s on the next request by itself. That is also why the
    rate limit is keyed on the email rather than the user — there is no user
    left to key on by the time it matters.
    """
    enforce_login_rate_limit(current_user.email)
    await require_password(current_user, body.password)

    try:
        await asyncio.to_thread(authelia_store.delete_user, current_user.email)
    except RuntimeError as exc:
        # Reporting an account deleted while its password still works is worse
        # than failing the request: the athlete stops looking for the problem.
        logger.error("Refusing to delete %s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The account could not be deleted. Please try again later.",
        ) from exc

    await db.delete(current_user)
    await db.flush()
    return {"status": "deleted"}


@router.get("/export")
async def export_me(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> JSONResponse:
    """Everything stored about the account, as one JSON file (ai-trainer-ops#6).

    Art. 15 asks for access, Art. 20 for a structured, machine-readable format;
    one file serves both. ``/memory-export`` stays: it is the coaching memory in
    the shape the settings page shows it, not the account.

    No password step-up, unlike deletion: everything in the file is already
    readable through this session, and credentials are not in it.
    """
    payload = await account_export.export_account(db, current_user)
    filename = f"ai-trainer-export-{app_today_iso()}.json"
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
