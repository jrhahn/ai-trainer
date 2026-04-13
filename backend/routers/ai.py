"""AI routes."""

import os

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import models
import schemas
from database import get_db
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
    return user.ai_provider or _default_provider()


@router.post("/analyse-activities", response_model=schemas.RiderAssessmentSchema)
async def analyse_activities(
    body: schemas.AnalyseActivitiesRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RiderAssessmentSchema:
    result = await ai_service.analyse_strava_activities(
        [activity.model_dump() for activity in body.activities],
        provider=_provider(current_user),
    )
    assessment = await db.get(models.RiderAssessment, current_user.id)
    if assessment is None:
        assessment = models.RiderAssessment(
            user_id=current_user.id,
            estimated_ftp=result.get("estimatedFTP"),
            estimated_threshold_hr=result.get("estimatedThresholdHR"),
            rider_type=result.get("riderType", "allrounder"),
            notes=result.get("notes", ""),
        )
        db.add(assessment)
    else:
        assessment.estimated_ftp = result.get("estimatedFTP")
        assessment.estimated_threshold_hr = result.get("estimatedThresholdHR")
        assessment.rider_type = result.get("riderType", assessment.rider_type)
        assessment.notes = result.get("notes", assessment.notes)
    current_user.strava_analysis_complete = True
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
        coach_memory=body.coach_memory,
        conversation_history=[msg.model_dump() for msg in (body.conversation_history or [])],
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
