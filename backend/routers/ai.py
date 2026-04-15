"""AI routes."""

import os

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import models
import schemas
from database import get_db
from routers.strava import ensure_fresh_strava_token, fetch_activity_streams
from services import ai_service

router = APIRouter(prefix="/ai", tags=["ai"])


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


@router.post("/analyse-activities", response_model=schemas.RiderAssessmentSchema)
async def analyse_activities(
    body: schemas.AnalyseActivitiesRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RiderAssessmentSchema:
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
            pass  # streams are optional; fall back to summary-only analysis

    result = await ai_service.analyse_strava_activities(
        [activity.model_dump() for activity in body.activities],
        provider=_provider(current_user),
        streams_by_id=streams_by_id,
        max_heart_rate=body.max_heart_rate,
    )
    assessment = await db.get(models.RiderAssessment, current_user.id)
    if assessment is None:
        assessment = models.RiderAssessment(
            user_id=current_user.id,
            estimated_ftp=result.get("estimatedFTP"),
            estimated_threshold_hr=result.get("estimatedThresholdHR"),
            rider_type=result.get("riderType", "allrounder"),
            notes=result.get("notes", ""),
            hr_zones=result.get("hrZones"),
        )
        db.add(assessment)
    else:
        assessment.estimated_ftp = result.get("estimatedFTP")
        assessment.estimated_threshold_hr = result.get("estimatedThresholdHR")
        assessment.rider_type = result.get("riderType", assessment.rider_type)
        assessment.notes = result.get("notes", assessment.notes)
        assessment.hr_zones = result.get("hrZones")
    current_user.strava_analysis_complete = True
    # Track the most recent activity analysed so the frontend can detect new rides.
    if body.activities:
        newest_id = max(a.id for a in body.activities)
        if current_user.last_strava_activity_id is None or newest_id > current_user.last_strava_activity_id:
            current_user.last_strava_activity_id = newest_id
    await db.flush()
    return schemas.RiderAssessmentSchema.model_validate(result)


@router.post("/generate-plan")
async def generate_plan(
    body: schemas.GeneratePlanRequest,
    current_user: models.User = Depends(auth.get_current_user),
) -> list[dict]:
    return await ai_service.generate_training_plan(
        body.profile.model_dump(by_alias=True),
        provider=_provider(current_user),
        rider_assessment=body.rider_assessment.model_dump(by_alias=True) if body.rider_assessment else None,
    )


@router.post("/adapt-plan")
async def adapt_plan(
    body: schemas.AdaptPlanRequest,
    current_user: models.User = Depends(auth.get_current_user),
) -> list[dict]:
    return await ai_service.adapt_training_plan(
        body.plan,
        [feedback.model_dump(by_alias=True) for feedback in body.recent_feedback],
        body.profile.model_dump(by_alias=True),
        provider=_provider(current_user),
    )


@router.post("/ask-trainer", response_model=schemas.AskTrainerResponse)
async def ask_trainer(
    body: schemas.AskTrainerRequest,
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AskTrainerResponse:
    result = await ai_service.ask_trainer(
        body.question,
        body.plan,
        body.profile.model_dump(by_alias=True),
        provider=_provider(current_user),
        rider_assessment=body.rider_assessment.model_dump(by_alias=True) if body.rider_assessment else None,
        coach_memory=body.coach_memory,
        conversation_history=[msg.model_dump() for msg in (body.conversation_history or [])],
        context_workout=body.context_workout,
    )
    return schemas.AskTrainerResponse.model_validate(result)


@router.post("/update-coach-memory", response_model=schemas.UpdateCoachMemoryResponse)
async def update_coach_memory(
    body: schemas.UpdateCoachMemoryRequest,
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.UpdateCoachMemoryResponse:
    memory = await ai_service.update_coach_memory(
        body.current_memory,
        body.user_message,
        body.coach_response,
        provider=_provider(current_user),
    )
    return schemas.UpdateCoachMemoryResponse(memory=memory)


@router.post("/rate-workout", response_model=schemas.RateWorkoutResponse)
async def rate_workout(
    body: schemas.RateWorkoutRequest,
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RateWorkoutResponse:
    feedback = await ai_service.rate_completed_workout(
        body.day.model_dump(by_alias=True),
        body.profile.model_dump(by_alias=True),
        provider=_provider(current_user),
    )
    return schemas.RateWorkoutResponse(feedback=feedback)
