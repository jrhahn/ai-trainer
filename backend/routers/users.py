"""User profile, plan, workout, chat, and coach-memory routes."""

import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import async_session_maker, get_db
from services import ai_service, metrics_service
from services import assessment_pipeline
from services import athlete_inquiry
from services import plan_pipeline
from services.analysis import (
    AVG_POWER_TO_FTP_RATIO,
    build_ride_metrics_chain,
    check_ftp_against_map,
)
from services.activity_imports import ImportedActivity, find_existing_import
from services.dates import app_today_iso
from services import llm as llm_service
from services.token_accounting import track_llm_usage
from services.ride_matching import apply_ride_plan_matches
from services.weather_service import (
    backfill_missing_ride_weather,
    clear_forecast_cache,
    daily_forecast_for_user,
)

router = APIRouter(prefix="/users/me", tags=["users"])

logger = logging.getLogger(__name__)


_RACE_MEMORY_HEADING = "Race calendar:"
_weather_backfill_users_in_progress: set[str] = set()


def _ride_metric_log_sample(
    rides: list[models.RideMetric], limit: int = 10
) -> list[dict[str, object]]:
    return [
        {
            "id": ride.strava_activity_id,
            "name": ride.activity_name,
            "date": ride.activity_date,
            "sport_type": ride.sport_type,
        }
        for ride in rides[:limit]
    ]


def _default_provider() -> str:
    if settings.gemini_api_key:
        return "gemini"
    if settings.openai_api_key:
        return "openai"
    return "gemini"


def _provider(user: models.User) -> str:
    stored = user.ai_provider or "openai"
    if stored == "gemini" and (user.user_gemini_api_key or settings.gemini_api_key):
        return "gemini"
    if stored == "openai" and (user.user_openai_api_key or settings.openai_api_key):
        return "openai"
    return _default_provider()


async def _backfill_ride_weather_bg(user_id: str, access_token: str | None) -> None:
    """Background weather backfill so dashboard hydration reads stored data immediately."""
    try:
        async with async_session_maker() as session:
            await backfill_missing_ride_weather(
                session, user_id, access_token, limit=90
            )
            await session.commit()
    except Exception:
        logger.info("Ride weather backfill skipped", exc_info=True)
    finally:
        _weather_backfill_users_in_progress.discard(user_id)


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
        follows_training_plan=user.follows_training_plan,
        max_heart_rate=user.max_heart_rate,
        resting_heart_rate=user.resting_heart_rate,
        current_ftp=user.current_ftp,
        fitness_level=user.fitness_level,
        ai_provider=user.ai_provider,
        consumed_tokens=user.consumed_tokens or 0,
        ftp_plausibility_warning=ftp_plausibility_warning,
        rider_assessment=rider_assessment,
        strava_connection=strava_connection,
        intervals_connection=intervals_connection,
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

    next_event = sorted(
        upcoming, key=lambda event: (event.date, event.start_time or "")
    )[0]
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
    # Lock the row for the read-modify-write so a concurrent coach-memory edit
    # (e.g. PUT /coach-memory) can't be clobbered by this merge (#346).
    existing_memory = await crud.get_coach_memory(
        db, current_user.id, for_update=True
    )
    merged_memory = _merge_race_events_into_memory(
        existing_memory.memory if existing_memory is not None else "",
        events,
    )
    await crud.upsert_coach_memory(db, current_user.id, merged_memory)
    return events


async def _ftp_plausibility_warning(
    db: AsyncSession, user: models.User
) -> str | None:
    """Check the stored FTP against the athlete's recorded MAP proxy.

    Advisory only — a bad ratio is surfaced, never silently corrected.  A
    lookup failure must not break profile reads, so errors degrade to no
    warning.
    """
    if not user.current_ftp:
        return None
    try:
        map_5min = await crud.get_latest_map_5min(db, user.id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "MAP lookup failed for FTP plausibility check", exc_info=True
        )
        return None
    return check_ftp_against_map(user.current_ftp, map_5min)


@router.get("", response_model=schemas.UserResponse)
async def get_me(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.UserResponse:
    return _user_to_response(
        current_user, await _ftp_plausibility_warning(db, current_user)
    )


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
    return _user_to_response(
        current_user, await _ftp_plausibility_warning(db, current_user)
    )


@router.delete("")
async def delete_me(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    await db.delete(current_user)
    await db.flush()
    return {"status": "deleted"}


class PlanDayHistoryEntry(schemas.CamelModel):
    """One athlete-visible plan-day change (see #343 / #357).

    ``source`` is the raw trigger key (``coach_chat``, ``ride_review``, …); the
    frontend maps it to a friendly label. ``applied=False`` marks an automated
    change that a user pin or completed day blocked ("attempted but kept").
    """

    id: str
    date: str
    source: str
    applied: bool
    recorded_at: datetime
    # Shared by all rows from one coach run so the frontend can collapse a run
    # into a single timeline card; null for rows written before #435.
    batch_id: str | None
    old_day: Any | None
    new_day: Any | None
    # One-line coach rationale for this day's change (#439); null when the run was
    # not narrated (user edits, initial generation) or predates the feature.
    reason: str | None = None
    # True when this run was narrated as a coach chat message (#439). The frontend
    # suppresses the redundant Coach-Timeline card for narrated runs.
    narrated: bool = False


class PlanDayHistoryResponse(schemas.CamelModel):
    entries: list[PlanDayHistoryEntry]
    total: int


class PlanDayHistoryDateCount(schemas.CamelModel):
    date: str
    count: int


class PlanDayHistoryStatsResponse(schemas.CamelModel):
    by_source: dict[str, int]
    applied_count: int
    blocked_count: int
    most_changed_dates: list[PlanDayHistoryDateCount]
    total: int


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
    # Route the manual edit through the shared pipeline so hard availability
    # constraints are enforced and concurrent edits are protected, instead of
    # blindly persisting whatever the client sent. Validate each day as a
    # canonical PlanDay and clear the server-authoritative fields — pinning
    # (``source``), completion state (``completed``) and workout ``feedback``
    # are owned by the server, so a client must not be able to forge them.
    sanitized: list[schemas.PlanDay] = []
    for raw_day in body.plan:
        day = schemas.PlanDay.model_validate(raw_day)
        day.source = None
        day.completed = None
        day.feedback = None
        sanitized.append(day)
    existing = await crud.get_training_plan(db, current_user.id)
    base_plan = existing.plan if existing is not None else []
    merged = await plan_pipeline.commit_plan(
        db, current_user, sanitized, base_plan=base_plan, source="user_edit"
    )
    return schemas.PlanResponse(plan=merged.plan)


@router.get("/plan-history", response_model=PlanDayHistoryResponse)
async def get_plan_history(
    date: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> PlanDayHistoryResponse:
    """Return the athlete's own per-day plan change log, newest first.

    Optionally filter to a single ``date`` (ISO ``YYYY-MM-DD``). Each entry shows
    the day before/after and which trigger caused it; ``applied=false`` entries
    are automated changes a user pin or completed day blocked. See #343 / #357.
    """
    rows = await crud.list_plan_day_history(
        db, current_user.id, date=date, limit=limit
    )
    narrated_batches = await crud.get_narrated_batch_ids(
        db, current_user.id, [row.batch_id for row in rows if row.batch_id]
    )
    return PlanDayHistoryResponse(
        entries=[
            PlanDayHistoryEntry(
                id=row.id,
                date=row.date,
                source=row.source,
                applied=row.applied,
                recorded_at=row.recorded_at,
                batch_id=row.batch_id,
                old_day=row.old_day,
                new_day=row.new_day,
                reason=row.reason,
                narrated=row.batch_id in narrated_batches,
            )
            for row in rows
        ],
        total=len(rows),
    )


@router.get("/plan-history/stats", response_model=PlanDayHistoryStatsResponse)
async def get_plan_history_stats(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> PlanDayHistoryStatsResponse:
    """Aggregate the athlete's plan-change log: counts by trigger, applied vs
    blocked, and the most-changed days. Read-only analytics. See #357."""
    stats = await crud.plan_day_history_stats(db, current_user.id)
    return PlanDayHistoryStatsResponse(
        by_source=stats["by_source"],
        applied_count=stats["applied_count"],
        blocked_count=stats["blocked_count"],
        most_changed_dates=[
            PlanDayHistoryDateCount(date=d["date"], count=d["count"])
            for d in stats["most_changed_dates"]
        ],
        total=stats["total"],
    )


@router.get("/workouts")
async def get_workouts(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict[str, dict]:
    logs = await crud.get_workout_logs(db, current_user.id)
    result: dict[str, dict] = {}
    for log in sorted(logs, key=lambda row: (row.date, row.slot or 0)):
        entry = {
            "actualDurationMinutes": log.actual_duration_minutes,
            "averagePower": log.average_power,
            "averageHeartRate": log.average_heart_rate,
            "peakPower": log.peak_power,
            "perceivedEffort": log.perceived_effort,
            "notes": log.notes,
            "completedAt": log.completed_at,
            "slot": log.slot or 0,
        }
        # Keyed by session (#496): the first session keeps the bare date so every
        # existing client keeps reading exactly what it read before, and only the
        # extra sessions of a two-a-day add a "date#slot" key.
        result[log.date if not log.slot else f"{log.date}#{log.slot}"] = entry
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
        slot=schemas.normalize_slot(body.slot),
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Race event not found"
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Race event not found"
        )
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
        messages=[
            schemas.ChatMessageSchema.model_validate(m, from_attributes=True)
            for m in messages
        ]
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
    return schemas.CoachMemoryResponse(
        memory=memory.memory if memory is not None else ""
    )


@router.put("/coach-memory", response_model=schemas.CoachMemoryResponse)
async def save_coach_memory(
    body: schemas.CoachMemoryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.CoachMemoryResponse:
    memory = await crud.upsert_coach_memory(db, current_user.id, body.memory)
    return schemas.CoachMemoryResponse(memory=memory.memory)


@router.get("/athlete-context", response_model=schemas.AthleteContextSchema)
async def get_athlete_context(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteContextSchema:
    context = await crud.get_athlete_context(db, current_user.id)
    if context is None:
        return schemas.AthleteContextSchema()
    return schemas.AthleteContextSchema.model_validate(context, from_attributes=True)


@router.put("/athlete-context", response_model=schemas.AthleteContextSchema)
async def save_athlete_context(
    body: schemas.AthleteContextRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteContextSchema:
    context = await crud.upsert_athlete_context(
        db,
        current_user.id,
        **body.model_dump(),
    )
    return schemas.AthleteContextSchema.model_validate(context, from_attributes=True)


@router.get("/athlete-model", response_model=schemas.AthleteModelSchema)
async def get_athlete_model(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteModelSchema:
    model = await crud.get_athlete_model(db, current_user.id)
    if model is None:
        return schemas.AthleteModelSchema()
    return schemas.AthleteModelSchema.model_validate(model, from_attributes=True)


@router.put("/athlete-model", response_model=schemas.AthleteModelSchema)
async def save_athlete_model(
    body: schemas.AthleteModelRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteModelSchema:
    model = await crud.upsert_athlete_model(
        db,
        current_user.id,
        **body.model_dump(),
    )
    return schemas.AthleteModelSchema.model_validate(model, from_attributes=True)


@router.get(
    "/athlete-memory-facts", response_model=schemas.AthleteMemoryFactsResponse
)
async def list_athlete_memory_facts(
    include_inactive: bool = Query(False, alias="includeInactive"),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteMemoryFactsResponse:
    facts = await crud.list_athlete_memory_facts(
        db, current_user.id, include_inactive=include_inactive
    )
    return schemas.AthleteMemoryFactsResponse(
        facts=[
            schemas.AthleteMemoryFactSchema.model_validate(
                fact, from_attributes=True
            )
            for fact in facts
        ]
    )


@router.post(
    "/athlete-memory-facts",
    response_model=schemas.AthleteMemoryFactSchema,
    status_code=status.HTTP_201_CREATED,
)
async def observe_athlete_memory_fact(
    body: schemas.AthleteMemoryFactObservationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteMemoryFactSchema:
    fact = await crud.observe_athlete_memory_fact(
        db,
        current_user.id,
        **body.model_dump(),
    )
    return schemas.AthleteMemoryFactSchema.model_validate(
        fact, from_attributes=True
    )


@router.patch(
    "/athlete-memory-facts/{fact_id}",
    response_model=schemas.AthleteMemoryFactSchema,
)
async def correct_athlete_memory_fact(
    fact_id: str,
    body: schemas.AthleteMemoryFactUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteMemoryFactSchema:
    try:
        fact = await crud.update_athlete_memory_fact(
            db,
            current_user.id,
            fact_id,
            **body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if fact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return schemas.AthleteMemoryFactSchema.model_validate(
        fact, from_attributes=True
    )


@router.delete(
    "/athlete-memory-facts/{fact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_athlete_memory_fact(
    fact_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    deleted = await crud.delete_athlete_memory_fact(db, current_user.id, fact_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/athlete-hypotheses", response_model=schemas.AthleteHypothesesResponse
)
async def list_athlete_hypotheses(
    include_resolved: bool = Query(False, alias="includeResolved"),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteHypothesesResponse:
    hypotheses = await crud.list_athlete_hypotheses(
        db, current_user.id, include_resolved=include_resolved
    )
    return schemas.AthleteHypothesesResponse(
        hypotheses=[
            schemas.AthleteHypothesisSchema.model_validate(h, from_attributes=True)
            for h in hypotheses
        ]
    )


@router.patch(
    "/athlete-hypotheses/{hypothesis_id}",
    response_model=schemas.AthleteHypothesisSchema,
)
async def update_athlete_hypothesis(
    hypothesis_id: str,
    body: schemas.AthleteHypothesisUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteHypothesisSchema:
    try:
        hypothesis = await crud.update_athlete_hypothesis(
            db,
            current_user.id,
            hypothesis_id,
            **body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if hypothesis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return schemas.AthleteHypothesisSchema.model_validate(
        hypothesis, from_attributes=True
    )


@router.delete(
    "/athlete-hypotheses/{hypothesis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_athlete_hypothesis(
    hypothesis_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    deleted = await crud.delete_athlete_hypothesis(
        db, current_user.id, hypothesis_id
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/open-questions", response_model=schemas.AthleteOpenQuestionsResponse
)
async def list_athlete_open_questions(
    include_resolved: bool = Query(False, alias="includeResolved"),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteOpenQuestionsResponse:
    questions = await crud.list_athlete_open_questions(
        db, current_user.id, include_resolved=include_resolved
    )
    return schemas.AthleteOpenQuestionsResponse(
        open_questions=[
            schemas.AthleteOpenQuestionSchema.model_validate(q, from_attributes=True)
            for q in questions
        ]
    )


@router.patch(
    "/open-questions/{question_id}",
    response_model=schemas.AthleteOpenQuestionSchema,
)
async def update_athlete_open_question(
    question_id: str,
    body: schemas.AthleteOpenQuestionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteOpenQuestionSchema:
    try:
        question = await crud.update_athlete_open_question(
            db,
            current_user.id,
            question_id,
            **body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return schemas.AthleteOpenQuestionSchema.model_validate(
        question, from_attributes=True
    )


@router.delete(
    "/open-questions/{question_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_athlete_open_question(
    question_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    deleted = await crud.delete_athlete_open_question(
        db, current_user.id, question_id
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/inquiries", response_model=schemas.AthleteInquiriesResponse)
async def list_athlete_inquiries(
    include_resolved: bool = Query(False, alias="includeResolved"),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteInquiriesResponse:
    inquiries = await crud.list_athlete_inquiries(
        db, current_user.id, include_resolved=include_resolved
    )
    return schemas.AthleteInquiriesResponse(
        inquiries=[
            schemas.AthleteInquirySchema.model_validate(i, from_attributes=True)
            for i in inquiries
        ]
    )


@router.post(
    "/inquiries/{inquiry_id}/answer",
    response_model=schemas.AthleteInquiryAnswerResponse,
)
async def answer_athlete_inquiry(
    inquiry_id: str,
    body: schemas.AthleteInquiryAnswerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteInquiryAnswerResponse:
    """Answer a pinned inquiry (#506).

    The answer is judged, and the reply the coach gives back is persisted into the
    chat so the exchange stays part of the conversation rather than vanishing with
    the pin.
    """
    inquiry = await crud.get_athlete_inquiry(db, current_user.id, inquiry_id)
    if inquiry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if inquiry.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This question is no longer waiting for an answer",
        )

    question_asked = inquiry.question
    try:
        inquiry, accepted, coach_reply = await athlete_inquiry.submit_inquiry_answer(
            db, current_user, inquiry, body.answer
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # Record the exchange in the chat: the question as the coach asked it, the
    # athlete's answer, and what the coach said back. Without this the answered
    # question disappears from the timeline entirely once the pin clears.
    now_iso = datetime.now(timezone.utc).isoformat()
    await crud.create_chat_message(
        db, current_user.id, role="assistant", content=question_asked, timestamp=now_iso
    )
    await crud.create_chat_message(
        db, current_user.id, role="user", content=body.answer.strip(), timestamp=now_iso
    )
    if coach_reply.strip():
        await crud.create_chat_message(
            db,
            current_user.id,
            role="assistant",
            content=coach_reply.strip(),
            timestamp=now_iso,
        )

    return schemas.AthleteInquiryAnswerResponse(
        inquiry=schemas.AthleteInquirySchema.model_validate(
            inquiry, from_attributes=True
        ),
        accepted=accepted,
        coach_reply=coach_reply,
    )


@router.post(
    "/inquiries/{inquiry_id}/dismiss", response_model=schemas.AthleteInquirySchema
)
async def dismiss_athlete_inquiry(
    inquiry_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteInquirySchema:
    inquiry = await crud.dismiss_athlete_inquiry(db, current_user.id, inquiry_id)
    if inquiry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return schemas.AthleteInquirySchema.model_validate(inquiry, from_attributes=True)


@router.delete(
    "/inquiries/{inquiry_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_athlete_inquiry(
    inquiry_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    deleted = await crud.delete_athlete_inquiry(db, current_user.id, inquiry_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/validation-experiments", response_model=schemas.AthleteExperimentsResponse
)
async def list_validation_experiments(
    include_resolved: bool = Query(False, alias="includeResolved"),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteExperimentsResponse:
    experiments = await crud.list_athlete_experiments(
        db, current_user.id, include_resolved=include_resolved
    )
    return schemas.AthleteExperimentsResponse(
        experiments=[
            schemas.AthleteExperimentSchema.model_validate(e, from_attributes=True)
            for e in experiments
        ]
    )


@router.patch(
    "/validation-experiments/{experiment_id}",
    response_model=schemas.AthleteExperimentSchema,
)
async def update_validation_experiment(
    experiment_id: str,
    body: schemas.AthleteExperimentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteExperimentSchema:
    try:
        experiment = await crud.update_athlete_experiment(
            db,
            current_user.id,
            experiment_id,
            **body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if experiment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return schemas.AthleteExperimentSchema.model_validate(
        experiment, from_attributes=True
    )


@router.delete(
    "/validation-experiments/{experiment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_validation_experiment(
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    deleted = await crud.delete_athlete_experiment(
        db, current_user.id, experiment_id
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/predictions", response_model=schemas.AthletePredictionsResponse
)
async def list_athlete_predictions(
    include_resolved: bool = Query(False, alias="includeResolved"),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthletePredictionsResponse:
    predictions = await crud.list_athlete_predictions(
        db, current_user.id, include_resolved=include_resolved
    )
    evaluated, correct = await crud.get_athlete_prediction_accuracy(
        db, current_user.id
    )
    return schemas.AthletePredictionsResponse(
        predictions=[
            schemas.AthletePredictionSchema.model_validate(p, from_attributes=True)
            for p in predictions
        ],
        accuracy=schemas.AthletePredictionAccuracy(
            evaluated=evaluated,
            correct=correct,
            accuracy=(correct / evaluated) if evaluated else None,
        ),
    )


@router.patch(
    "/predictions/{prediction_id}",
    response_model=schemas.AthletePredictionSchema,
)
async def update_athlete_prediction(
    prediction_id: str,
    body: schemas.AthletePredictionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthletePredictionSchema:
    try:
        prediction = await crud.update_athlete_prediction(
            db,
            current_user.id,
            prediction_id,
            **body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if prediction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return schemas.AthletePredictionSchema.model_validate(
        prediction, from_attributes=True
    )


@router.delete(
    "/predictions/{prediction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_athlete_prediction(
    prediction_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    deleted = await crud.delete_athlete_prediction(
        db, current_user.id, prediction_id
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/memory-privacy", response_model=schemas.MemoryPrivacySettingsSchema)
async def get_memory_privacy_settings(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.MemoryPrivacySettingsSchema:
    return schemas.MemoryPrivacySettingsSchema(
        memory_updates_enabled=current_user.memory_updates_enabled
    )


@router.put("/memory-privacy", response_model=schemas.MemoryPrivacySettingsSchema)
async def update_memory_privacy_settings(
    body: schemas.MemoryPrivacySettingsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.MemoryPrivacySettingsSchema:
    current_user.memory_updates_enabled = body.memory_updates_enabled
    await db.flush()
    await db.commit()
    return schemas.MemoryPrivacySettingsSchema(
        memory_updates_enabled=current_user.memory_updates_enabled
    )


@router.delete("/memory", status_code=status.HTTP_204_NO_CONTENT)
async def clear_all_memory(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    await crud.clear_athlete_memory(db, current_user.id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/memory-export", response_model=schemas.MemoryExportSchema)
async def export_memory(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.MemoryExportSchema:
    coach_memory_row = await crud.get_coach_memory(db, current_user.id)
    athlete_context_row = await crud.get_athlete_context(db, current_user.id)
    athlete_model_row = await crud.get_athlete_model(db, current_user.id)
    facts = await crud.list_athlete_memory_facts(db, current_user.id, include_inactive=True)
    hypotheses = await crud.list_athlete_hypotheses(
        db, current_user.id, include_resolved=True
    )
    open_questions = await crud.list_athlete_open_questions(
        db, current_user.id, include_resolved=True
    )
    experiments = await crud.list_athlete_experiments(
        db, current_user.id, include_resolved=True
    )
    predictions = await crud.list_athlete_predictions(
        db, current_user.id, include_resolved=True
    )
    inquiries = await crud.list_athlete_inquiries(
        db, current_user.id, include_resolved=True
    )
    return schemas.MemoryExportSchema(
        exported_at=datetime.now(timezone.utc),
        memory_updates_enabled=current_user.memory_updates_enabled,
        coach_memory=coach_memory_row.memory if coach_memory_row is not None else "",
        athlete_context=(
            schemas.AthleteContextSchema.model_validate(
                athlete_context_row, from_attributes=True
            )
            if athlete_context_row is not None
            else None
        ),
        athlete_model=(
            schemas.AthleteModelSchema.model_validate(
                athlete_model_row, from_attributes=True
            )
            if athlete_model_row is not None
            else None
        ),
        memory_facts=[
            schemas.AthleteMemoryFactSchema.model_validate(f, from_attributes=True)
            for f in facts
        ],
        hypotheses=[
            schemas.AthleteHypothesisSchema.model_validate(h, from_attributes=True)
            for h in hypotheses
        ],
        open_questions=[
            schemas.AthleteOpenQuestionSchema.model_validate(q, from_attributes=True)
            for q in open_questions
        ],
        experiments=[
            schemas.AthleteExperimentSchema.model_validate(e, from_attributes=True)
            for e in experiments
        ],
        predictions=[
            schemas.AthletePredictionSchema.model_validate(p, from_attributes=True)
            for p in predictions
        ],
        inquiries=[
            schemas.AthleteInquirySchema.model_validate(i, from_attributes=True)
            for i in inquiries
        ],
    )


@router.get("/ai-key/status", response_model=schemas.AIKeyStatusSchema)
async def get_ai_key_status(
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AIKeyStatusSchema:
    return schemas.AIKeyStatusSchema(
        provider=current_user.ai_provider or "openai",
        has_openai_key=bool(current_user.user_openai_api_key),
        has_gemini_key=bool(current_user.user_gemini_api_key),
    )


@router.put("/ai-key", response_model=schemas.AIKeyStatusSchema)
async def save_ai_key(
    body: schemas.AIKeySaveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AIKeyStatusSchema:
    if body.provider == "openai":
        current_user.user_openai_api_key = body.api_key
    elif body.provider == "gemini":
        current_user.user_gemini_api_key = body.api_key
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider: {body.provider}",
        )
    current_user.ai_provider = body.provider
    await db.commit()
    return schemas.AIKeyStatusSchema(
        provider=current_user.ai_provider,
        has_openai_key=bool(current_user.user_openai_api_key),
        has_gemini_key=bool(current_user.user_gemini_api_key),
    )


@router.delete("/ai-key", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ai_key(
    provider: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    if provider == "openai":
        current_user.user_openai_api_key = None
    elif provider == "gemini":
        current_user.user_gemini_api_key = None
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider: {provider}",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/ai-key/test")
async def test_ai_key(
    body: schemas.AIKeySaveRequest,
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    """Validate an API key without storing it."""
    token = llm_service.set_user_ai_keys({body.provider: body.api_key})
    try:
        provider_instance = llm_service.get_provider(
            body.provider, task=llm_service.TASK_CLASSIFY
        )
        await provider_instance.chat("You are a connectivity test.", "Reply with the single word: ok")
        return {"ok": True}
    except llm_service.AIKeyNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except Exception as exc:
        logger.warning("AI key validation failed for user %s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Key validation failed. Check that the key is correct and has the required permissions.",
        )
    finally:
        llm_service.reset_user_ai_keys(token)


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
                map_5min=s.map_5min,
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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RideMetricHistoryResponse:
    """Return the most recent 90 per-ride CTL/ATL/TSB records in chronological order.

    Used by the expert-mode time-series charts to show how training load metrics
    develop over time with one data point per ride.
    """
    if (
        current_user.strava_token is not None
        and current_user.id not in _weather_backfill_users_in_progress
    ):
        _weather_backfill_users_in_progress.add(current_user.id)
        background_tasks.add_task(
            _backfill_ride_weather_bg,
            current_user.id,
            current_user.strava_token.access_token,
        )

    rides = await crud.get_ride_metrics_history(db, current_user.id, limit=90)
    existing_plan = await crud.get_training_plan(db, current_user.id)
    if rides and existing_plan is not None:
        await apply_ride_plan_matches(
            db,
            current_user.id,
            existing_plan.plan,
            [ride.strava_activity_id for ride in rides],
        )

    # get_ride_metrics_history returns newest-first; reverse for chronological charting
    rides = list(reversed(rides))
    logger.info(
        "Ride metrics history response user=%s count=%s sample=%s",
        current_user.id,
        len(rides),
        _ride_metric_log_sample(rides),
    )
    return schemas.RideMetricHistoryResponse(
        rides=[
            schemas.RideMetricSchema.model_validate(r, from_attributes=True)
            for r in rides
        ]
    )


def _home_location_schema(
    row: models.AthleteHomeLocation | None,
) -> schemas.AthleteHomeLocationSchema | None:
    if row is None:
        return None
    return schemas.AthleteHomeLocationSchema.model_validate(
        row, from_attributes=True
    )


@router.get("/home-location", response_model=schemas.AthleteHomeLocationResponse)
async def get_home_location(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteHomeLocationResponse:
    """Return the athlete's persisted training location, if one is known (#495)."""
    row = await crud.get_athlete_home_location(db, current_user.id)
    return schemas.AthleteHomeLocationResponse(location=_home_location_schema(row))


@router.put("/home-location", response_model=schemas.AthleteHomeLocationResponse)
async def save_home_location(
    body: schemas.AthleteHomeLocationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteHomeLocationResponse:
    """Set the athlete's training location explicitly.

    Stored as ``user_set``, which pins it against every later inference pass — the
    athlete's own answer outranks a cluster of ride starts. Also drops the cached
    forecast so the next dashboard load reflects the new location immediately.
    """
    row = await crud.upsert_athlete_home_location(
        db,
        current_user.id,
        latitude=body.latitude,
        longitude=body.longitude,
        label=body.label,
        source="user_set",
        confidence=1.0,
    )
    await db.commit()
    clear_forecast_cache()
    return schemas.AthleteHomeLocationResponse(location=_home_location_schema(row))


@router.get("/weather-forecast", response_model=schemas.WeatherForecastResponse)
async def get_weather_forecast(
    days: int = 14,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.WeatherForecastResponse:
    """Return the upcoming daily outlook for the athlete's training location (#495).

    Powers the weather icon + temperature shown on planned days in the dashboard.
    Served from the hourly per-location forecast cache, so repeated dashboard loads
    cost no upstream calls, and degrades to an empty list when no location is known
    or Open-Meteo is unreachable.
    """
    location, forecast = await daily_forecast_for_user(
        db, current_user.id, max(1, min(days, 16))
    )
    stored = await crud.get_athlete_home_location(db, current_user.id)
    location_schema = _home_location_schema(stored)
    if location_schema is None and location is not None:
        # Falling back to the latest ride with GPS: report it honestly rather than
        # implying a persisted attribute exists.
        location_schema = schemas.AthleteHomeLocationSchema(
            latitude=location.latitude,
            longitude=location.longitude,
            label=location.label,
            source=location.source,
            confidence=location.confidence,
        )
    return schemas.WeatherForecastResponse(
        location=location_schema,
        days=[schemas.DailyForecastSchema(**day) for day in forecast],
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
    """Set the athlete's quick "how the legs felt" rating for a Strava activity.

    This is the one-tap dashboard signal.  It writes only the ``feel_legs``
    column (``None`` clears it), so it never clobbers notes captured
    conversationally via the coach chat.  When set, ``feel_legs`` is surfaced to
    every AI coach prompt via ``ride_metrics_context_section()``.  Richer
    feedback — perceived effort, notes, plan-match corrections — is captured in
    conversation with the coach, not here.
    """
    row = await crud.set_ride_feel_legs(
        db,
        current_user.id,
        strava_activity_id,
        body.legs,
        external_activity_id=body.external_activity_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found",
        )

    # The leg-feel rating feeds the login summary, so mark it stale to regenerate.
    await assessment_pipeline.notify_changed(db, current_user)

    return schemas.RideFeedbackResponse(
        strava_activity_id=strava_activity_id,
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


@dataclass
class _ParsedFitActivity:
    source_id: int
    source_metadata: dict[str, Any]
    sport_type: str
    duration_seconds: int
    duration_minutes: int
    ride_date: str
    completed_at: str
    activity_name: str
    avg_power: int | None
    avg_hr: int | None
    start_lat: float | None
    start_lng: float | None
    streams: dict[str, dict[str, list[Any]]]

    def to_imported_activity(self) -> ImportedActivity:
        return ImportedActivity(
            source="fit",
            external_activity_id=str(self.source_id),
            name=self.activity_name,
            start_datetime=self.completed_at,
            activity_date=self.ride_date,
            sport_type=self.sport_type,
            duration_seconds=self.duration_seconds,
            start_lat=self.start_lat,
            start_lng=self.start_lng,
            streams=self.streams,
            metadata=self.source_metadata,
            summary_avg_power_w=self.avg_power,
            legacy_activity_id=self.source_id,
        )


def _fit_field_map(message: Any) -> dict[str, Any]:
    return {field.name: field.value for field in message}


def _as_datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average_int(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def _semicircles_to_degrees(value: Any) -> float | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    return numeric * (180.0 / 2**31)


def _json_safe_fit_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _fit_source_id(raw: bytes, metadata: dict[str, Any]) -> int:
    metadata_basis = json.dumps(metadata, sort_keys=True, default=str)
    basis = metadata_basis if metadata else hashlib.sha256(raw).hexdigest()
    # 60 bits keeps the synthetic id positive and comfortably inside BIGINT.
    return int(hashlib.sha256(basis.encode()).hexdigest()[:15], 16)


def _parse_fit_activity(raw: bytes, filename: str, FitFile: Any) -> _ParsedFitActivity:
    try:
        fit = FitFile(io.BytesIO(raw))
        fit.parse()
    except Exception as exc:
        logger.warning("Failed to parse .fit file %s: %s", filename, exc)
        raise ValueError("Could not parse the uploaded .fit file") from exc

    sport_type = "cycling"
    total_elapsed_seconds = 0
    avg_power: int | None = None
    avg_hr: int | None = None
    start_time: datetime | None = None
    metadata: dict[str, Any] = {}

    for message_type in ("file_id", "activity"):
        for record in fit.get_messages(message_type):
            fields = _fit_field_map(record)
            for key in (
                "serial_number",
                "time_created",
                "manufacturer",
                "product",
                "timestamp",
                "local_timestamp",
            ):
                if key in fields and fields[key] is not None:
                    metadata[f"{message_type}_{key}"] = _json_safe_fit_value(
                        fields[key]
                    )

    for record in fit.get_messages("session"):
        fields = _fit_field_map(record)
        if fields.get("sport"):
            sport_type = str(fields["sport"]).lower()
        total_elapsed_seconds = (
            _as_int(fields.get("total_elapsed_time"))
            or _as_int(fields.get("total_timer_time"))
            or total_elapsed_seconds
        )
        avg_power = _as_int(fields.get("avg_power")) or avg_power
        avg_hr = _as_int(fields.get("avg_heart_rate")) or avg_hr
        start_time = _as_datetime(fields.get("start_time")) or start_time
        for key in (
            "start_time",
            "sport",
            "sub_sport",
            "total_elapsed_time",
            "total_timer_time",
            "enhanced_avg_speed",
        ):
            if key in fields and fields[key] is not None:
                metadata[f"session_{key}"] = _json_safe_fit_value(fields[key])

    timestamps: list[datetime] = []
    watts: list[int] = []
    watt_times: list[int] = []
    heart_rates: list[int] = []
    hr_times: list[int] = []
    cadences: list[int] = []
    cadence_times: list[int] = []
    altitudes: list[float] = []
    altitude_times: list[int] = []
    speeds: list[float] = []
    speed_times: list[int] = []
    latlng: list[list[float]] = []
    start_lat: float | None = None
    start_lng: float | None = None

    for record in fit.get_messages("record"):
        fields = _fit_field_map(record)
        timestamp = _as_datetime(fields.get("timestamp"))
        if timestamp is None:
            continue
        timestamps.append(timestamp)
        first_timestamp = timestamps[0]
        elapsed = max(0, int((timestamp - first_timestamp).total_seconds()))

        power = _as_int(fields.get("power"))
        if power is not None and power > 0:
            watts.append(power)
            watt_times.append(elapsed)

        heart_rate = _as_int(fields.get("heart_rate"))
        if heart_rate is not None and heart_rate > 0:
            heart_rates.append(heart_rate)
            hr_times.append(elapsed)

        cadence = _as_int(fields.get("cadence"))
        if cadence is not None and cadence > 0:
            cadences.append(cadence)
            cadence_times.append(elapsed)

        altitude = _as_float(fields.get("enhanced_altitude")) or _as_float(
            fields.get("altitude")
        )
        if altitude is not None:
            altitudes.append(round(altitude, 2))
            altitude_times.append(elapsed)

        speed = _as_float(fields.get("enhanced_speed")) or _as_float(
            fields.get("speed")
        )
        if speed is not None:
            speeds.append(round(speed, 3))
            speed_times.append(elapsed)

        lat = _semicircles_to_degrees(fields.get("position_lat"))
        lng = _semicircles_to_degrees(fields.get("position_long"))
        if lat is not None and lng is not None:
            point = [round(lat, 6), round(lng, 6)]
            latlng.append(point)
            if start_lat is None or start_lng is None:
                start_lat, start_lng = point

    if not total_elapsed_seconds and len(timestamps) >= 2:
        total_elapsed_seconds = int((timestamps[-1] - timestamps[0]).total_seconds())
    if start_time is None and timestamps:
        start_time = timestamps[0]
    if avg_power is None:
        avg_power = _average_int(watts)
    if avg_hr is None:
        avg_hr = _average_int(heart_rates)

    total_elapsed_seconds = max(1, total_elapsed_seconds)
    duration_minutes = max(1, round(total_elapsed_seconds / 60))
    completed_at = (
        start_time.isoformat() if start_time else datetime.now(timezone.utc).isoformat()
    )
    ride_date = completed_at[:10]

    streams: dict[str, dict[str, list[Any]]] = {}
    if watts and watt_times and len(watts) == len(watt_times):
        streams["watts"] = {"data": watts}
        streams["time"] = {"data": watt_times}
    if heart_rates:
        streams["heartrate"] = {"data": heart_rates}
        streams["heartrate_time"] = {"data": hr_times}
    if cadences:
        streams["cadence"] = {"data": cadences}
        streams["cadence_time"] = {"data": cadence_times}
    if altitudes:
        streams["altitude"] = {"data": altitudes}
        streams["altitude_time"] = {"data": altitude_times}
    if speeds:
        streams["velocity_smooth"] = {"data": speeds}
        streams["velocity_time"] = {"data": speed_times}
    if latlng:
        streams["latlng"] = {"data": latlng}

    if start_time is not None:
        metadata.setdefault("activity_start_time", start_time.isoformat())
    metadata.setdefault("sport_type", sport_type)
    metadata.setdefault("duration_seconds", total_elapsed_seconds)
    source_id = _fit_source_id(raw, metadata)

    return _ParsedFitActivity(
        source_id=source_id,
        source_metadata=metadata,
        sport_type=sport_type,
        duration_seconds=total_elapsed_seconds,
        duration_minutes=duration_minutes,
        ride_date=ride_date,
        completed_at=completed_at,
        activity_name=f"FIT {sport_type.title()} {ride_date}",
        avg_power=avg_power,
        avg_hr=avg_hr,
        start_lat=start_lat,
        start_lng=start_lng,
        streams=streams,
    )


async def _analyse_fit_import(
    db: AsyncSession,
    current_user: models.User,
    parsed: _ParsedFitActivity,
) -> dict | None:
    provider = current_user.ai_provider or "openai"
    async with track_llm_usage(db, current_user, source="api:analyse-fit-import"):
        try:
            ai_result = await ai_service.analyse_fit_activity(
                sport_type=parsed.sport_type,
                duration_minutes=parsed.duration_minutes,
                avg_power=parsed.avg_power,
                avg_hr=parsed.avg_hr,
                max_heart_rate=current_user.max_heart_rate,
                provider=provider,
            )
        except Exception:
            logger.warning(
                "AI analysis failed for .fit upload; skipping feedback", exc_info=True
            )
            return None

    if ai_result:
        for field in ("rideInsights", "lastRideFeedback", "loginSummary", "notes"):
            value = ai_result.get(field)
            if value is not None and not isinstance(value, str):
                ai_result[field] = json.dumps(value)

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
        # These fields feed the login summary but no fresh summary was generated
        # here; mark it stale so it regenerates on the next dashboard load.
        await assessment_pipeline.notify_changed(db, current_user)

    return ai_result


async def _store_fit_import(
    db: AsyncSession,
    current_user: models.User,
    filename: str,
    parsed: _ParsedFitActivity,
    seen_source_ids: set[int],
) -> schemas.FitUploadFileResult:
    if parsed.source_id in seen_source_ids:
        return schemas.FitUploadFileResult(
            filename=filename,
            status="skipped",
            message="Duplicate FIT activity in this upload batch",
            sport_type=parsed.sport_type,
            duration_minutes=parsed.duration_minutes,
            average_power=parsed.avg_power,
            average_heart_rate=parsed.avg_hr,
        )

    seen_source_ids.add(parsed.source_id)
    imported_activity = parsed.to_imported_activity()
    existing_metric = await find_existing_import(db, current_user.id, imported_activity)
    if existing_metric is not None:
        return schemas.FitUploadFileResult(
            filename=filename,
            status="skipped",
            message="FIT activity was already imported",
            activity_id=existing_metric.id,
            sport_type=parsed.sport_type,
            duration_minutes=parsed.duration_minutes,
            average_power=parsed.avg_power,
            average_heart_rate=parsed.avg_hr,
        )

    log = await crud.upsert_workout_log(
        db,
        current_user.id,
        parsed.ride_date,
        actual_duration_minutes=parsed.duration_minutes,
        average_power=parsed.avg_power,
        average_heart_rate=parsed.avg_hr,
        peak_power=None,
        perceived_effort=5,
        notes=f"Imported from .fit file: {filename}",
        completed_at=parsed.completed_at,
        sport_type=parsed.sport_type,
    )

    ai_result = await _analyse_fit_import(db, current_user, parsed)
    ftp_value = ai_result.get("estimatedFTP") if ai_result else None
    if (
        ftp_value is None
        and parsed.sport_type.lower() not in ("running", "run")
        and parsed.avg_power
    ):
        ftp_value = round(parsed.avg_power * AVG_POWER_TO_FTP_RATIO)

    if ftp_value is not None:
        try:
            await crud.create_athlete_metric_snapshot(
                db,
                current_user.id,
                ftp=ftp_value,
                source="fit_upload",
            )
        except Exception:
            logger.warning(
                "Failed to write metric snapshot for .fit upload", exc_info=True
            )

    latest_metric = await crud.get_latest_ride_metric(db, current_user.id)
    seed_ctl = (
        latest_metric.ctl_after if latest_metric and latest_metric.ctl_after else 0.0
    )
    seed_atl = (
        latest_metric.atl_after if latest_metric and latest_metric.atl_after else 0.0
    )
    ftp_for_chain = float(current_user.current_ftp or ftp_value or 0)
    ride_input = imported_activity.to_ride_input()
    metrics_chain = build_ride_metrics_chain(
        [ride_input], ftp_for_chain, seed_ctl, seed_atl
    )
    if metrics_chain:
        metric = metrics_chain[0]
        metric["avg_power_w"] = metric.get("avg_power_w") or parsed.avg_power
        await crud.upsert_ride_metric(db, current_user.id, **metric)
        existing_plan = await crud.get_training_plan(db, current_user.id)
        training_plan = existing_plan.plan if existing_plan is not None else []
        await apply_ride_plan_matches(
            db,
            current_user.id,
            training_plan,
            [parsed.source_id],
        )

    await db.flush()
    return schemas.FitUploadFileResult(
        filename=filename,
        status="imported",
        message="Imported FIT activity",
        activity_id=log.id,
        sport_type=parsed.sport_type,
        duration_minutes=parsed.duration_minutes,
        average_power=parsed.avg_power,
        average_heart_rate=parsed.avg_hr,
    )


def _fit_file_parser_or_503() -> Any:
    try:
        from fitparse import FitFile  # noqa: PLC0415
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="fitparse library not installed; .fit upload is unavailable",
        )
    return FitFile


@router.post("/upload-fit", response_model=schemas.FitUploadResponse)
async def upload_fit_file(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.FitUploadResponse:
    """Ingest one .fit file and store workout plus normalized ride metrics."""
    FitFile = _fit_file_parser_or_503()
    filename = file.filename or ""
    if not filename.lower().endswith(".fit"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only .fit files are accepted",
        )

    raw = await file.read()
    try:
        parsed = _parse_fit_activity(raw, filename, FitFile)
        result = await _store_fit_import(db, current_user, filename, parsed, set())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return schemas.FitUploadResponse(
        status="ok" if result.status == "imported" else result.status,
        activity_id=result.activity_id or "",
        sport_type=result.sport_type or parsed.sport_type,
        duration_minutes=result.duration_minutes or parsed.duration_minutes,
        average_power=result.average_power,
        average_heart_rate=result.average_heart_rate,
    )


@router.post("/upload-fit/bulk", response_model=schemas.FitBulkUploadResponse)
async def upload_fit_files_bulk(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.FitBulkUploadResponse:
    """Import multiple .fit files, reporting success, duplicate, and failure per file."""
    FitFile = _fit_file_parser_or_503()
    results: list[schemas.FitUploadFileResult] = []
    seen_source_ids: set[int] = set()

    for file in files:
        filename = file.filename or "unnamed"
        if not filename.lower().endswith(".fit"):
            results.append(
                schemas.FitUploadFileResult(
                    filename=filename,
                    status="failed",
                    message="Only .fit files are accepted",
                )
            )
            continue

        raw = await file.read()
        try:
            parsed = _parse_fit_activity(raw, filename, FitFile)
            result = await _store_fit_import(
                db, current_user, filename, parsed, seen_source_ids
            )
        except ValueError as exc:
            result = schemas.FitUploadFileResult(
                filename=filename,
                status="failed",
                message=str(exc),
            )
        results.append(result)

    imported = sum(1 for result in results if result.status == "imported")
    skipped = sum(1 for result in results if result.status == "skipped")
    failed = sum(1 for result in results if result.status == "failed")
    return schemas.FitBulkUploadResponse(
        status="ok" if failed == 0 else "partial",
        total=len(results),
        imported=imported,
        skipped=skipped,
        failed=failed,
        files=results,
    )
