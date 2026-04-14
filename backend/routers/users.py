"""User profile, plan, workout, chat, and coach-memory routes."""

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import models
import schemas
from database import get_db

router = APIRouter(prefix="/users/me", tags=["users"])


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
        bike_type=user.bike_type,
        training_goal=user.training_goal,
        race_date=user.race_date,
        race_description=user.race_description,
        weekly_hours=user.weekly_hours,
        follows_training_plan=user.follows_training_plan,
        resting_heart_rate=user.resting_heart_rate,
        max_heart_rate=user.max_heart_rate,
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
    plan = await db.scalar(select(models.TrainingPlan).where(models.TrainingPlan.user_id == current_user.id))
    return schemas.PlanResponse(plan=plan.plan if plan is not None else [])


@router.put("/plan", response_model=schemas.PlanResponse)
async def save_plan(
    body: schemas.PlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.PlanResponse:
    plan = await db.scalar(select(models.TrainingPlan).where(models.TrainingPlan.user_id == current_user.id))
    if plan is None:
        plan = models.TrainingPlan(user_id=current_user.id, plan=body.plan)
        db.add(plan)
    else:
        plan.plan = body.plan
    await db.flush()
    return schemas.PlanResponse(plan=plan.plan)


@router.get("/workouts")
async def get_workouts(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict[str, dict]:
    logs = await db.scalars(select(models.WorkoutLog).where(models.WorkoutLog.user_id == current_user.id))
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
    existing = await db.scalar(
        select(models.WorkoutLog).where(
            models.WorkoutLog.user_id == current_user.id,
            models.WorkoutLog.date == date,
        )
    )
    feedback = body.feedback
    if existing is None:
        existing = models.WorkoutLog(
            user_id=current_user.id,
            date=date,
            actual_duration_minutes=feedback.actual_duration_minutes,
            average_power=feedback.average_power,
            average_heart_rate=feedback.average_heart_rate,
            peak_power=feedback.peak_power,
            perceived_effort=feedback.perceived_effort,
            notes=feedback.notes,
            completed_at=feedback.completed_at,
        )
        db.add(existing)
    else:
        existing.actual_duration_minutes = feedback.actual_duration_minutes
        existing.average_power = feedback.average_power
        existing.average_heart_rate = feedback.average_heart_rate
        existing.peak_power = feedback.peak_power
        existing.perceived_effort = feedback.perceived_effort
        existing.notes = feedback.notes
        existing.completed_at = feedback.completed_at
    await db.flush()
    return {"status": "ok"}


@router.get("/chat", response_model=schemas.ChatHistoryResponse)
async def get_chat(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ChatHistoryResponse:
    messages = await db.scalars(
        select(models.ChatMessage)
        .where(models.ChatMessage.user_id == current_user.id)
        .order_by(models.ChatMessage.timestamp)
    )
    return schemas.ChatHistoryResponse(
        messages=[schemas.ChatMessageSchema.model_validate(m, from_attributes=True) for m in messages]
    )


@router.post("/chat", response_model=schemas.ChatMessageSchema)
async def add_chat_message(
    body: schemas.ChatMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ChatMessageSchema:
    message = models.ChatMessage(
        user_id=current_user.id,
        role=body.role,
        content=body.content,
        timestamp=body.timestamp,
        plan_update_count=body.plan_update_count,
    )
    db.add(message)
    await db.flush()
    return schemas.ChatMessageSchema.model_validate(message, from_attributes=True)


@router.delete("/chat")
async def clear_chat(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    await db.execute(delete(models.ChatMessage).where(models.ChatMessage.user_id == current_user.id))
    return {"status": "deleted"}


@router.get("/coach-memory", response_model=schemas.CoachMemoryResponse)
async def get_coach_memory(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.CoachMemoryResponse:
    memory = await db.get(models.CoachMemory, current_user.id)
    return schemas.CoachMemoryResponse(memory=memory.memory if memory is not None else "")


@router.put("/coach-memory", response_model=schemas.CoachMemoryResponse)
async def save_coach_memory(
    body: schemas.CoachMemoryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.CoachMemoryResponse:
    memory = await db.get(models.CoachMemory, current_user.id)
    if memory is None:
        memory = models.CoachMemory(user_id=current_user.id, memory=body.memory)
        db.add(memory)
    else:
        memory.memory = body.memory
    await db.flush()
    return schemas.CoachMemoryResponse(memory=memory.memory)
