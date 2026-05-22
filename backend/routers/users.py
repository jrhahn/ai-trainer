"""User profile, plan, workout, chat, and coach-memory routes."""

import io
import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import get_db
from services import ai_service, metrics_service
from services.analysis import AVG_POWER_TO_FTP_RATIO
from services.dates import app_today_iso
from services.llm import begin_token_usage_collection, finish_token_usage_collection
from services.ride_matching import review_matched_ride_and_adapt

router = APIRouter(prefix="/users/me", tags=["users"])

logger = logging.getLogger(__name__)


_RACE_MEMORY_HEADING = "Race calendar:"


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


async def _persist_collected_token_usage(
    db: AsyncSession, user: models.User, token
) -> None:
    consumed = finish_token_usage_collection(token)
    if consumed:
        await crud.increment_user_consumed_tokens(db, user, consumed)


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
        max_heart_rate=user.max_heart_rate,
        resting_heart_rate=user.resting_heart_rate,
        current_ftp=user.current_ftp,
        fitness_level=user.fitness_level,
        ai_provider=user.ai_provider,
        consumed_tokens=user.consumed_tokens or 0,
        rider_assessment=rider_assessment,
        strava_connection=strava_connection,
    )


def _format_race_event_for_memory(event: models.RaceEvent) -> str:
    time_part = f" at {event.start_time}" if event.start_time else ""
    return (
        f"{event.date}{time_part}: {event.distance_km:g} km, "
        f"{event.elevation_m} m climbing"
    )


def _race_events_memory_section(events: list[models.RaceEvent]) -> str:
    if not events:
        return ""
    lines = [_RACE_MEMORY_HEADING]
    lines.extend(f"- {_format_race_event_for_memory(event)}" for event in events)
    return "\n".join(lines)


def _merge_race_events_into_memory(memory: str, events: list[models.RaceEvent]) -> str:
    cleaned = re.sub(
        rf"(?:\n\n)?{re.escape(_RACE_MEMORY_HEADING)}\n(?:- .*(?:\n|$))*",
        "",
        memory or "",
    ).strip()
    section = _race_events_memory_section(events)
    return "\n\n".join(part for part in (cleaned, section) if part)


def _sync_profile_next_race(user: models.User, events: list[models.RaceEvent]) -> None:
    today = app_today_iso()
    upcoming = [event for event in events if event.date >= today]
    if not upcoming:
        user.race_date = None
        user.race_description = None
        return

    next_event = sorted(upcoming, key=lambda event: (event.date, event.start_time or ""))[0]
    user.training_goal = "race"
    user.race_date = next_event.date
    user.race_description = (
        f"Calendar race: {next_event.distance_km:g} km with "
        f"{next_event.elevation_m} m climbing"
    )


async def _sync_race_context(
    db: AsyncSession, current_user: models.User
) -> list[models.RaceEvent]:
    events = await crud.get_race_events(db, current_user.id)
    _sync_profile_next_race(current_user, events)
    existing_memory = await crud.get_coach_memory(db, current_user.id)
    merged_memory = _merge_race_events_into_memory(
        existing_memory.memory if existing_memory is not None else "",
        events,
    )
    await crud.upsert_coach_memory(db, current_user.id, merged_memory)
    return events


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
    updates = body.model_dump(exclude_unset=True)
    if updates.get("training_goal") == "general_fitness":
        updates["race_date"] = None
        updates["race_description"] = None
    for field, value in updates.items():
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


@router.get("/race-events", response_model=schemas.RaceEventsResponse)
async def get_race_events(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RaceEventsResponse:
    events = await crud.get_race_events(db, current_user.id)
    return schemas.RaceEventsResponse(
        events=[
            schemas.RaceEventResponse.model_validate(event, from_attributes=True)
            for event in events
        ]
    )


@router.post("/race-events", response_model=schemas.RaceEventResponse)
async def create_race_event(
    body: schemas.RaceEventRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RaceEventResponse:
    event = await crud.create_race_event(
        db,
        current_user.id,
        date=body.date,
        start_time=body.start_time,
        distance_km=body.distance_km,
        elevation_m=body.elevation_m,
    )
    await _sync_race_context(db, current_user)
    return schemas.RaceEventResponse.model_validate(event, from_attributes=True)


@router.put("/race-events/{event_id}", response_model=schemas.RaceEventResponse)
async def update_race_event(
    event_id: str,
    body: schemas.RaceEventRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RaceEventResponse:
    event = await crud.get_race_event(db, current_user.id, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Race event not found")
    updated = await crud.update_race_event(
        db,
        event,
        date=body.date,
        start_time=body.start_time,
        distance_km=body.distance_km,
        elevation_m=body.elevation_m,
    )
    await _sync_race_context(db, current_user)
    return schemas.RaceEventResponse.model_validate(updated, from_attributes=True)


@router.delete("/race-events/{event_id}")
async def delete_race_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    event = await crud.get_race_event(db, current_user.id, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Race event not found")
    await crud.delete_race_event(db, event)
    await _sync_race_context(db, current_user)
    return {"status": "deleted"}


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
                ctl=s.ctl,
                atl=s.atl,
                tsb=s.tsb,
                source=s.source,
            )
            for s in snapshots
        ]
    )


@router.get("/ride-metrics-history", response_model=schemas.RideMetricHistoryResponse)
async def get_ride_metrics_history(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RideMetricHistoryResponse:
    """Return the most recent 90 per-ride CTL/ATL/TSB records in chronological order.

    Used by the expert-mode time-series charts to show how training load metrics
    develop over time with one data point per ride.
    """
    rides = await crud.get_ride_metrics_history(db, current_user.id, limit=90)
    # get_ride_metrics_history returns newest-first; reverse for chronological charting
    rides = list(reversed(rides))
    return schemas.RideMetricHistoryResponse(
        rides=[
            schemas.RideMetricSchema.model_validate(r, from_attributes=True)
            for r in rides
        ]
    )


@router.patch(
    "/ride-feedback/{strava_activity_id}",
    response_model=schemas.RideFeedbackResponse,
)
async def save_ride_feedback(
    strava_activity_id: int,
    body: schemas.RideFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RideFeedbackResponse:
    """Save structured post-ride subjective feedback for a specific Strava activity.

    The four feedback fields (RPE, legs feeling, intent, optional note) are
    formatted into a single human-readable ``user_note`` string that is stored
    on the ``RideMetric`` row.  This note is then automatically included in all
    AI coach prompts via ``ride_metrics_context_section()``.
    """
    parts = [
        f"RPE {body.rpe}/10",
        f"legs: {body.legs}",
        f"intent: {body.intent}",
    ]
    if body.note:
        parts.append(body.note)
    user_note = " | ".join(parts)

    row = await crud.update_ride_metric_notes(
        db, current_user.id, strava_activity_id, user_note=user_note
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found",
        )

    coach_note = None
    plan_updates = None
    if row.plan_match_status in {"auto_matched", "manual_matched"} and row.matched_plan_date:
        existing_plan = await crud.get_training_plan(db, current_user.id)
        plan = existing_plan.plan if existing_plan is not None else []
        usage_token = begin_token_usage_collection()
        coach_note, raw_plan_updates = await review_matched_ride_and_adapt(
            db,
            current_user,
            row,
            plan,
            provider=_provider(current_user),
        )
        await _persist_collected_token_usage(db, current_user, usage_token)
        plan_updates = (
            [schemas.PlanDayUpdateSchema.model_validate(u) for u in raw_plan_updates]
            if raw_plan_updates
            else None
        )

    return schemas.RideFeedbackResponse(
        strava_activity_id=strava_activity_id,
        user_note=user_note,
        coach_note=coach_note,
        plan_updates=plan_updates,
        ride=schemas.RideMetricSchema.model_validate(row, from_attributes=True),
    )


@router.post("/recalculate-metrics", response_model=schemas.RecalculateMetricsResponse)
async def recalculate_metrics(
    body: schemas.RecalculateMetricsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RecalculateMetricsResponse:
    """Recompute TSS, CTL, ATL, and TSB for all stored rides using a given FTP."""
    try:
        updated, ftp_used = await metrics_service.recalculate_metrics_for_user(
            db, current_user, body.ftp_override
        )
    except ValueError as exc:
        request_id = getattr(request.state, "request_id", None)
        logger.warning(
            "recalculate-metrics rejected for user %s (request_id=%s): %s",
            current_user.id,
            request_id,
            exc,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return schemas.RecalculateMetricsResponse(updated=updated, ftp_used=ftp_used)


@router.post("/estimate-ftp", response_model=schemas.EstimateFTPResponse)
async def estimate_ftp(
    body: schemas.EstimateFTPRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.EstimateFTPResponse:
    """Save updated heart-rate values and return the user-entered FTP.

    Persists ``max_heart_rate`` to the user profile when supplied so that
    subsequent Strava imports and analyses automatically use the new values.

    Returns the FTP value set directly on the user profile (``current_ftp``).
    FTP is never estimated or derived from activity data.
    """
    # --- Persist new HR values when provided ---
    if body.max_heart_rate is not None:
        current_user.max_heart_rate = body.max_heart_rate
    if body.resting_heart_rate is not None:
        current_user.resting_heart_rate = body.resting_heart_rate
    await db.flush()

    # Return the user-entered FTP directly from the profile
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
    usage_token = begin_token_usage_collection()
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
        finish_token_usage_collection(usage_token)
        logger.warning("AI analysis failed for .fit upload; skipping feedback", exc_info=True)
    else:
        await _persist_collected_token_usage(db, current_user, usage_token)

    # Save rider assessment feedback if AI succeeded
    if ai_result:
        # Normalise text fields: LLM may return dicts or lists instead of strings.
        # The DB columns expect plain strings.
        for _field in ("rideInsights", "lastRideFeedback", "loginSummary", "notes"):
            _val = ai_result.get(_field)
            if _val is not None and not isinstance(_val, str):
                ai_result[_field] = json.dumps(_val)

        await crud.upsert_rider_assessment(
            db,
            current_user.id,
            estimated_ftp=ai_result.get("estimatedFTP"),
            rider_type=ai_result.get("riderType"),
            notes=ai_result.get("notes"),
            hr_zones=ai_result.get("hrZones"),
            ride_insights=ai_result.get("rideInsights"),
            last_ride_feedback=ai_result.get("lastRideFeedback"),
        )

    # Write a time-series metric snapshot regardless of AI result
    ftp_value = ai_result.get("estimatedFTP") if ai_result else None

    # For cycling without AI: fall back to avg_power-based FTP estimate
    if ftp_value is None and sport_type.lower() not in ("running", "run") and avg_power:
        ftp_value = round(avg_power * AVG_POWER_TO_FTP_RATIO)

    if ftp_value is not None:
        try:
            await crud.create_athlete_metric_snapshot(
                db,
                current_user.id,
                ftp=ftp_value,
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
