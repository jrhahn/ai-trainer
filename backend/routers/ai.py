"""AI routes."""

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import get_db
from routers.strava import ensure_fresh_strava_token, fetch_activity_streams
from services import ai_service
from services.ai_service import MAX_CONVERSATION_HISTORY
from services.rag import retrieve_cycling_context

router = APIRouter(prefix="/ai", tags=["ai"])

logger = logging.getLogger(__name__)


def _default_provider() -> str:
    """Return the best available provider based on configured API keys.

    Preference order: gemini (if GEMINI_API_KEY is set) → openai (if
    OPENAI_API_KEY is set) → gemini (last resort so the error message
    mentions the correct service).
    """
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "gemini"


def _provider(user: models.User) -> str:
    stored = user.ai_provider
    if stored == "gemini" and os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if stored == "openai" and os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return _default_provider()


def _user_to_profile_dict(user: models.User) -> dict:
    """Build a camelCase profile dict from the user model (mirrors UserProfileSchema by_alias)."""
    return {
        "name": user.name or "",
        "email": user.email,
        "bikeType": user.bike_type or "",
        "trainingGoal": user.training_goal or "",
        "raceDate": user.race_date,
        "raceDescription": user.race_description,
        "weeklyHours": user.weekly_hours,
        "followsTrainingPlan": user.follows_training_plan,
        "restingHeartRate": user.resting_heart_rate,
        "maxHeartRate": user.max_heart_rate,
        "thresholdHeartRate": user.threshold_heart_rate,
        "currentFTP": user.current_ftp,
        "fitnessLevel": user.fitness_level or "",
    }


@router.post("/analyse-activities", response_model=schemas.AnalyseActivitiesResponse)
async def analyse_activities(
    body: schemas.AnalyseActivitiesRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AnalyseActivitiesResponse:
    # Fetch per-second stream data for each activity from Strava
    streams_by_id: dict[str, dict] = {}
    if current_user.strava_token is not None:
        try:
            access_token = await ensure_fresh_strava_token(current_user.strava_token, db)
            for activity in body.activities:
                streams = await fetch_activity_streams(access_token, activity.id)
                if streams:
                    streams_by_id[str(activity.id)] = streams
        except Exception:
            logger.warning(
                "Failed to fetch Strava streams; falling back to summary-only analysis",
                exc_info=True,
            )

    result = await ai_service.analyse_strava_activities(
        [activity.model_dump() for activity in body.activities],
        provider=_provider(current_user),
        streams_by_id=streams_by_id,
        max_heart_rate=body.max_heart_rate,
    )
    await crud.upsert_rider_assessment(
        db,
        current_user.id,
        estimated_ftp=result.get("estimatedFTP"),
        estimated_threshold_hr=result.get("estimatedThresholdHR"),
        rider_type=result.get("riderType"),
        notes=result.get("notes"),
        hr_zones=result.get("hrZones"),
        ride_insights=result.get("rideInsights"),
        last_ride_feedback=result.get("lastRideFeedback"),
    )
    current_user.strava_analysis_complete = True
    # Track the most recent activity analysed so the frontend can detect new rides.
    if body.activities:
        newest_id = max(a.id for a in body.activities)
        if current_user.last_strava_activity_id is None or newest_id > current_user.last_strava_activity_id:
            current_user.last_strava_activity_id = newest_id
    await db.flush()

    assessment_schema = schemas.RiderAssessmentSchema.model_validate(result)
    raw_updates = result.get("planUpdates") or []
    plan_updates = [schemas.PlanDayUpdateSchema.model_validate(u) for u in raw_updates] if raw_updates else None
    return schemas.AnalyseActivitiesResponse(assessment=assessment_schema, plan_updates=plan_updates)


@router.post("/generate-plan")
async def generate_plan(
    body: schemas.GeneratePlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> list[dict]:
    profile = _user_to_profile_dict(current_user)
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    plan = await ai_service.generate_training_plan(
        profile,
        provider=_provider(current_user),
        rider_assessment=rider_assessment,
    )
    await crud.upsert_training_plan(db, current_user.id, plan)
    return plan


@router.post("/adapt-plan")
async def adapt_plan(
    body: schemas.AdaptPlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> list[dict]:
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []
    profile = _user_to_profile_dict(current_user)
    updated_plan = await ai_service.adapt_training_plan(
        plan,
        [feedback.model_dump(by_alias=True) for feedback in body.recent_feedback],
        profile,
        provider=_provider(current_user),
    )
    await crud.upsert_training_plan(db, current_user.id, updated_plan)
    return updated_plan


@router.post("/ask-trainer", response_model=schemas.AskTrainerResponse)
async def ask_trainer(
    body: schemas.AskTrainerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AskTrainerResponse:
    # Load state from DB
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []
    profile = _user_to_profile_dict(current_user)
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    coach_memory_row = await crud.get_coach_memory(db, current_user.id)
    coach_memory = coach_memory_row.memory if coach_memory_row is not None else ""
    chat_messages = await crud.get_chat_messages(db, current_user.id)
    conversation_history = [
        {"role": msg.role, "content": msg.content}
        for msg in chat_messages[-MAX_CONVERSATION_HISTORY:]
    ]

    # Retrieve relevant cycling science context from the knowledge base.
    science_context, rag_sources = await retrieve_cycling_context(db, body.question)

    result = await ai_service.ask_trainer(
        body.question,
        plan,
        profile,
        provider=_provider(current_user),
        rider_assessment=rider_assessment,
        coach_memory=coach_memory,
        conversation_history=conversation_history,
        context_workout=body.context_workout,
        science_context=science_context,
    )

    now = datetime.now(timezone.utc).isoformat()
    plan_updates = result.get("plan_updates") or []

    # Persist user and assistant chat messages
    await crud.create_chat_message(
        db, current_user.id, role="user", content=body.question, timestamp=now,
    )
    await crud.create_chat_message(
        db,
        current_user.id,
        role="assistant",
        content=result["response"],
        timestamp=now,
        plan_update_count=len(plan_updates) if plan_updates else None,
    )

    # Update coach memory
    updated_memory = await ai_service.update_coach_memory(
        coach_memory,
        body.question,
        result["response"],
        provider=_provider(current_user),
    )
    if updated_memory and updated_memory != coach_memory:
        await crud.upsert_coach_memory(db, current_user.id, updated_memory)

    # Apply plan updates if any
    if plan_updates:
        updates_by_date = {u["date"]: u for u in plan_updates}
        updated_plan = [
            {**day, **{k: v for k, v in updates_by_date[day["date"]].items() if v is not None}}
            if day.get("date") in updates_by_date
            else day
            for day in plan
        ]
        await crud.upsert_training_plan(db, current_user.id, updated_plan)

    # Merge RAG retrieval sources into the result.
    # rag_sources contains the full metadata for all retrieved chunks;
    # use them as the authoritative sources list when RAG context was retrieved.
    if rag_sources:
        result["sources"] = rag_sources

    return schemas.AskTrainerResponse.model_validate(result)


@router.post("/rate-workout", response_model=schemas.RateWorkoutResponse)
async def rate_workout(
    body: schemas.RateWorkoutRequest,
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RateWorkoutResponse:
    profile = _user_to_profile_dict(current_user)
    feedback = await ai_service.rate_completed_workout(
        body.day.model_dump(by_alias=True),
        profile,
        provider=_provider(current_user),
    )
    return schemas.RateWorkoutResponse(feedback=feedback)
