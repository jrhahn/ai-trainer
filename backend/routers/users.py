"""User profile, plan, workout, chat, and coach-memory routes."""

import io
import json
import logging
import math
from datetime import date as _date
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import get_db
from services import ai_service
from services.analysis import AVG_POWER_TO_FTP_RATIO, LTHR_RATIO, apply_ctl_atl_decay, compute_ride_tss

router = APIRouter(prefix="/users/me", tags=["users"])

logger = logging.getLogger(__name__)


def _user_to_response(user: models.User) -> schemas.UserResponse:
    strava_connection = None
    if user.strava_token is not None:
        strava_connection = schemas.StravaConnectionSchema(
            athlete_id=user.strava_token.athlete_id,
            athlete_name=user.strava_token.athlete_name,
        )

    rider_assessment = None
    if user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(user.rider_assessment, from_attributes=True)

    return schemas.UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        is_onboarded=user.is_onboarded,
        strava_analysis_complete=user.strava_analysis_complete,
        last_strava_activity_id=user.last_strava_activity_id,
        bike_type=user.bike_type,
        training_goal=user.training_goal,
        race_date=user.race_date,
        race_description=user.race_description,
        weekly_hours=user.weekly_hours,
        follows_training_plan=user.follows_training_plan,
        resting_heart_rate=user.resting_heart_rate,
        max_heart_rate=user.max_heart_rate,
        threshold_heart_rate=user.threshold_heart_rate,
        current_ftp=user.current_ftp,
        fitness_level=user.fitness_level,
        ai_provider=user.ai_provider,
        rider_assessment=rider_assessment,
        strava_connection=strava_connection,
    )


@router.get("", response_model=schemas.UserResponse)
async def get_me(
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.UserResponse:
    return _user_to_response(current_user)


@router.put("", response_model=schemas.UserResponse)
async def update_me(
    body: schemas.UpdateProfileRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.UserResponse:
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)
    await db.flush()
    await db.refresh(current_user)
    return _user_to_response(current_user)


@router.delete("")
async def delete_me(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    await db.delete(current_user)
    await db.flush()
    return {"status": "deleted"}


@router.get("/plan", response_model=schemas.PlanResponse)
async def get_plan(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.PlanResponse:
    plan = await crud.get_training_plan(db, current_user.id)
    return schemas.PlanResponse(plan=plan.plan if plan is not None else [])


@router.put("/plan", response_model=schemas.PlanResponse)
async def save_plan(
    body: schemas.PlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.PlanResponse:
    plan = await crud.upsert_training_plan(db, current_user.id, body.plan)
    return schemas.PlanResponse(plan=plan.plan)


@router.get("/workouts")
async def get_workouts(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict[str, dict]:
    logs = await crud.get_workout_logs(db, current_user.id)
    result: dict[str, dict] = {}
    for log in logs:
        result[log.date] = {
            "actualDurationMinutes": log.actual_duration_minutes,
            "averagePower": log.average_power,
            "averageHeartRate": log.average_heart_rate,
            "peakPower": log.peak_power,
            "perceivedEffort": log.perceived_effort,
            "notes": log.notes,
            "completedAt": log.completed_at,
        }
    return result


@router.post("/workouts/{date}")
async def save_workout(
    date: str,
    body: schemas.WorkoutLogRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    feedback = body.feedback
    await crud.upsert_workout_log(
        db,
        current_user.id,
        date,
        actual_duration_minutes=feedback.actual_duration_minutes,
        average_power=feedback.average_power,
        average_heart_rate=feedback.average_heart_rate,
        peak_power=feedback.peak_power,
        perceived_effort=feedback.perceived_effort,
        notes=feedback.notes,
        completed_at=feedback.completed_at,
    )
    return {"status": "ok"}


@router.get("/chat", response_model=schemas.ChatHistoryResponse)
async def get_chat(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ChatHistoryResponse:
    messages = await crud.get_chat_messages(db, current_user.id)
    return schemas.ChatHistoryResponse(
        messages=[schemas.ChatMessageSchema.model_validate(m, from_attributes=True) for m in messages]
    )


@router.post("/chat", response_model=schemas.ChatMessageSchema)
async def add_chat_message(
    body: schemas.ChatMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ChatMessageSchema:
    message = await crud.create_chat_message(
        db,
        current_user.id,
        role=body.role,
        content=body.content,
        timestamp=body.timestamp,
        plan_update_count=body.plan_update_count,
    )
    return schemas.ChatMessageSchema.model_validate(message, from_attributes=True)


@router.delete("/chat")
async def clear_chat(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    await crud.delete_chat_messages(db, current_user.id)
    return {"status": "deleted"}


@router.get("/coach-memory", response_model=schemas.CoachMemoryResponse)
async def get_coach_memory(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.CoachMemoryResponse:
    memory = await crud.get_coach_memory(db, current_user.id)
    return schemas.CoachMemoryResponse(memory=memory.memory if memory is not None else "")


@router.put("/coach-memory", response_model=schemas.CoachMemoryResponse)
async def save_coach_memory(
    body: schemas.CoachMemoryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.CoachMemoryResponse:
    memory = await crud.upsert_coach_memory(db, current_user.id, body.memory)
    return schemas.CoachMemoryResponse(memory=memory.memory)


@router.get("/metrics-history", response_model=schemas.MetricsHistoryResponse)
async def get_metrics_history(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.MetricsHistoryResponse:
    snapshots = await crud.get_athlete_metric_history(db, current_user.id)
    return schemas.MetricsHistoryResponse(
        snapshots=[
            schemas.AthleteMetricSnapshotSchema(
                recorded_at=s.recorded_at.isoformat(),
                ftp=s.ftp,
                threshold_hr=s.threshold_hr,
                ctl=s.ctl,
                atl=s.atl,
                tsb=s.tsb,
                source=s.source,
            )
            for s in snapshots
        ]
    )


@router.post("/recalculate-metrics", response_model=schemas.RecalculateMetricsResponse)
async def recalculate_metrics(
    body: schemas.RecalculateMetricsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RecalculateMetricsResponse:
    """Recompute TSS, CTL, ATL, and TSB for all stored rides using a given FTP.

    When ``ftp_override`` is supplied the user's ``current_ftp`` profile field
    is updated before the recalculation so subsequent analyses use the new
    value automatically.

    The operation rebuilds the entire CTL/ATL/TSB chain from the oldest stored
    ride forward, using the normalised-power values already stored in the
    ``ride_metrics`` table (no Strava API calls required).

    All existing ``athlete_metric_snapshots`` are deleted and replaced with a
    single new snapshot that reflects the final CTL/ATL/TSB after the rebuild.
    """
    # Determine FTP to use
    ftp_value: int | None = body.ftp_override
    if ftp_value is None:
        if current_user.rider_assessment and current_user.rider_assessment.estimated_ftp:
            ftp_value = current_user.rider_assessment.estimated_ftp
        elif current_user.current_ftp:
            ftp_value = current_user.current_ftp

    if not ftp_value or ftp_value <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No FTP value available. Provide ftp_override or set current_ftp first.",
        )

    # Persist FTP override on the user profile
    if body.ftp_override is not None:
        current_user.current_ftp = body.ftp_override

    # Fetch all ride metrics sorted chronologically
    all_metrics = await crud.get_all_ride_metrics_ordered(db, current_user.id)

    if not all_metrics:
        await db.flush()
        return schemas.RecalculateMetricsResponse(updated=0, ftp_used=ftp_value)

    ftp_float = float(ftp_value)
    ctl = 0.0
    atl = 0.0
    prev_date_str: str | None = None
    updated = 0

    for metric in all_metrics:
        # Re-derive TSS from stored normalised power and new FTP
        np_w = metric.normalized_power_w
        duration_s = metric.duration_seconds or 0
        new_tss: float | None = None
        new_if: float | None = None

        if np_w and duration_s > 0:
            new_tss = compute_ride_tss(float(duration_s), float(np_w), ftp_float)
            new_if = round(float(np_w) / ftp_float, 3)

        # Determine gap since last ride for CTL/ATL decay
        gap_days = 1
        if prev_date_str is not None:
            try:
                prev_d = _date.fromisoformat(prev_date_str)
                curr_d = _date.fromisoformat(metric.activity_date)
                gap_days = max(1, (curr_d - prev_d).days)
            except ValueError:
                gap_days = 1

        ride_tss = new_tss if new_tss is not None else 0.0
        ctl, atl = apply_ctl_atl_decay(ctl, atl, ride_tss, gap_days=gap_days)
        tsb = ctl - atl
        prev_date_str = metric.activity_date

        # Persist updated values
        metric.tss = round(new_tss, 1) if new_tss is not None else None
        metric.intensity_factor = new_if
        metric.ftp_used = ftp_value
        metric.ctl_after = round(ctl, 2)
        metric.atl_after = round(atl, 2)
        metric.tsb_after = round(tsb, 2)
        updated += 1

    # Replace all metric snapshots with a fresh single snapshot
    await crud.delete_athlete_metric_snapshots(db, current_user.id)
    await crud.create_athlete_metric_snapshot(
        db,
        current_user.id,
        ftp=ftp_value,
        threshold_hr=current_user.threshold_heart_rate,
        ctl=round(ctl, 1),
        atl=round(atl, 1),
        tsb=round(ctl - atl, 1),
        source="manual_recalculate",
    )

    await db.flush()
    return schemas.RecalculateMetricsResponse(updated=updated, ftp_used=ftp_value)


@router.post("/estimate-ftp", response_model=schemas.EstimateFTPResponse)
async def estimate_ftp(
    body: schemas.EstimateFTPRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.EstimateFTPResponse:
    """Save updated heart-rate values and return the best available FTP estimate.

    Persists ``max_heart_rate`` and/or ``resting_heart_rate`` to the user
    profile when supplied so that subsequent Strava imports and analyses
    automatically use the new values.

    The FTP returned is sourced from (in priority order):

    1. Most recent ``AthleteMetricSnapshot`` with a non-null FTP.
    2. ``RiderAssessment.estimated_ftp``.
    3. ``User.current_ftp`` set manually on the profile.

    The caller is expected to present this value to the user, allow an
    override, and then call ``POST /users/me/recalculate-metrics`` with the
    confirmed FTP to rebuild TSS / CTL / ATL / TSB.
    """
    # --- Persist new HR values when provided ---
    if body.max_heart_rate is not None:
        current_user.max_heart_rate = body.max_heart_rate
    if body.resting_heart_rate is not None:
        current_user.resting_heart_rate = body.resting_heart_rate
    elif current_user.resting_heart_rate is None:
        # Default resting HR to 60 when the user has never set one.
        current_user.resting_heart_rate = 60
    await db.flush()

    # --- Find best available FTP estimate ---
    # 1. Most recent AthleteMetricSnapshot with a non-null FTP
    snapshots = await crud.get_athlete_metric_history(db, current_user.id)
    # snapshots are oldest-first; iterate in reverse to find the most recent
    for snap in reversed(snapshots):
        if snap.ftp is not None:
            return schemas.EstimateFTPResponse(
                estimated_ftp=snap.ftp,
                source=snap.source or "ftp_estimation",
            )

    # 2. RiderAssessment.estimated_ftp
    if current_user.rider_assessment and current_user.rider_assessment.estimated_ftp:
        return schemas.EstimateFTPResponse(
            estimated_ftp=current_user.rider_assessment.estimated_ftp,
            source="rider_assessment",
        )

    # 3. Manually set profile FTP
    if current_user.current_ftp:
        return schemas.EstimateFTPResponse(
            estimated_ftp=current_user.current_ftp,
            source="profile",
        )

    return schemas.EstimateFTPResponse(estimated_ftp=None, source="none")


@router.post("/upload-fit", response_model=schemas.FitUploadResponse)
async def upload_fit_file(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.FitUploadResponse:
    """Ingest a .fit file (Garmin/Wahoo/Zwift export) and store as a workout log.

    The parsed activity is normalised into the same shape the analysis service
    already expects, and a ``WorkoutLog`` row is written for the ride date.
    Returns summary metrics extracted from the file.
    """
    try:
        from fitparse import FitFile  # noqa: PLC0415
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="fitparse library not installed; .fit upload is unavailable",
        )

    if not file.filename or not file.filename.lower().endswith(".fit"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only .fit files are accepted",
        )

    raw = await file.read()
    try:
        fit = FitFile(io.BytesIO(raw))
        fit.parse()
    except Exception as exc:
        logger.warning("Failed to parse .fit file: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not parse the uploaded .fit file",
        ) from exc

    # --- Extract summary from session messages ---
    sport_type = "cycling"
    total_elapsed_seconds = 0
    avg_power: int | None = None
    avg_hr: int | None = None
    start_time: datetime | None = None

    for record in fit.get_messages("session"):
        for data in record:
            name = data.name
            value = data.value
            if name == "sport" and value:
                sport_type = str(value).lower()
            elif name == "total_elapsed_time" and value:
                total_elapsed_seconds = int(value)
            elif name == "avg_power" and value:
                avg_power = int(value)
            elif name == "avg_heart_rate" and value:
                avg_hr = int(value)
            elif name == "start_time" and value:
                start_time = value if isinstance(value, datetime) else None

    # Fall back to record messages if session data is sparse
    if not total_elapsed_seconds:
        timestamps = []
        powers: list[int] = []
        hrs: list[int] = []
        for record in fit.get_messages("record"):
            record_data = {d.name: d.value for d in record}
            if record_data.get("timestamp"):
                timestamps.append(record_data["timestamp"])
            if record_data.get("power") and record_data["power"] > 0:
                powers.append(int(record_data["power"]))
            if record_data.get("heart_rate") and record_data["heart_rate"] > 0:
                hrs.append(int(record_data["heart_rate"]))
        if len(timestamps) >= 2:
            delta = timestamps[-1] - timestamps[0]
            total_elapsed_seconds = int(delta.total_seconds())
        if powers and avg_power is None:
            avg_power = round(sum(powers) / len(powers))
        if hrs and avg_hr is None:
            avg_hr = round(sum(hrs) / len(hrs))
        if timestamps and start_time is None:
            start_time = timestamps[0]

    duration_minutes = max(1, round(total_elapsed_seconds / 60))
    ride_date = (
        start_time.strftime("%Y-%m-%d")
        if start_time
        else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    completed_at = (
        start_time.isoformat()
        if start_time
        else datetime.now(timezone.utc).isoformat()
    )

    log = await crud.upsert_workout_log(
        db,
        current_user.id,
        ride_date,
        actual_duration_minutes=duration_minutes,
        average_power=avg_power,
        average_heart_rate=avg_hr,
        peak_power=None,
        perceived_effort=5,
        notes=f"Imported from .fit file: {file.filename}",
        completed_at=completed_at,
        sport_type=sport_type,
    )

    # --- Run analysis and write a metric snapshot ---
    # Determine provider from user profile
    provider = current_user.ai_provider or "openai"
    max_hr = current_user.max_heart_rate

    # Trigger AI analysis in the background (best-effort; don't fail the upload if AI is down)
    ai_result: dict | None = None
    try:
        ai_result = await ai_service.analyse_fit_activity(
            sport_type=sport_type,
            duration_minutes=duration_minutes,
            avg_power=avg_power,
            avg_hr=avg_hr,
            max_heart_rate=max_hr,
            provider=provider,
        )
    except Exception:
        logger.warning("AI analysis failed for .fit upload; skipping feedback", exc_info=True)

    # Save rider assessment feedback if AI succeeded
    if ai_result:
        # Normalise rideInsights: LLM may return list or string; column expects string.
        _ri = ai_result.get("rideInsights")
        if _ri is not None and not isinstance(_ri, str):
            ai_result["rideInsights"] = json.dumps(_ri)

        await crud.upsert_rider_assessment(
            db,
            current_user.id,
            estimated_ftp=ai_result.get("estimatedFTP"),
            estimated_threshold_hr=ai_result.get("estimatedThresholdHR"),
            rider_type=ai_result.get("riderType"),
            notes=ai_result.get("notes"),
            hr_zones=ai_result.get("hrZones"),
            ride_insights=ai_result.get("rideInsights"),
            last_ride_feedback=ai_result.get("lastRideFeedback"),
        )

    # Write a time-series metric snapshot regardless of AI result
    ftp_value = ai_result.get("estimatedFTP") if ai_result else None
    threshold_hr_value = ai_result.get("estimatedThresholdHR") if ai_result else None

    # For cycling without AI: fall back to avg_power-based FTP estimate
    if ftp_value is None and sport_type.lower() not in ("running", "run") and avg_power:
        ftp_value = round(avg_power * AVG_POWER_TO_FTP_RATIO)

    # For any sport without AI: fall back to LTHR estimate if max HR is known
    if threshold_hr_value is None and max_hr:
        threshold_hr_value = round(max_hr * LTHR_RATIO)

    if ftp_value is not None or threshold_hr_value is not None:
        try:
            await crud.create_athlete_metric_snapshot(
                db,
                current_user.id,
                ftp=ftp_value,
                threshold_hr=threshold_hr_value,
                source="fit_upload",
            )
        except Exception:
            logger.warning("Failed to write metric snapshot for .fit upload", exc_info=True)

    await db.flush()

    return schemas.FitUploadResponse(
        status="ok",
        activity_id=log.id,
        sport_type=sport_type,
        duration_minutes=duration_minutes,
        average_power=avg_power,
        average_heart_rate=avg_hr,
    )
