"""AI routes."""

import logging
import os
from datetime import date as _date
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import get_db
from routers.strava import ensure_fresh_strava_token, fetch_activity_streams
from services import ai_service
from services.ai_service import MAX_CONVERSATION_HISTORY
from services.analysis import compare_planned_vs_actual, compute_readiness_score, compute_training_load, _project_training_load
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

    existing_plan = await crud.get_training_plan(db, current_user.id)
    training_plan = existing_plan.plan if existing_plan is not None else []

    result = await ai_service.analyse_strava_activities(
        [activity.model_dump() for activity in body.activities],
        provider=_provider(current_user),
        streams_by_id=streams_by_id,
        max_heart_rate=body.max_heart_rate,
        training_plan=training_plan or None,
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
        login_summary=result.get("loginSummary"),
    )

    # Record a time-series metric snapshot so the athlete can track progression
    ftp_value = result.get("estimatedFTP")
    threshold_hr_value = result.get("estimatedThresholdHR")
    if ftp_value is not None or threshold_hr_value is not None:
        plan_days = training_plan
        ftp_for_load = ftp_value or current_user.current_ftp or 0
        ctl: float | None = None
        atl: float | None = None
        tsb: float | None = None
        if ftp_for_load > 0 and plan_days:
            training_load = compute_training_load(plan_days, ftp_for_load)
            ctl = training_load.get("ctl")
            atl = training_load.get("atl")
            tsb = training_load.get("tsb")
        await crud.create_athlete_metric_snapshot(
            db,
            current_user.id,
            ftp=ftp_value,
            threshold_hr=threshold_hr_value,
            ctl=ctl,
            atl=atl,
            tsb=tsb,
            source="strava_analysis",
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
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    updated_plan = await ai_service.adapt_training_plan(
        plan,
        [feedback.model_dump(by_alias=True) for feedback in body.recent_feedback],
        profile,
        provider=_provider(current_user),
        rider_assessment=rider_assessment,
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

    # --- Task 5: Classify question first, then conditionally retrieve RAG context ---
    classification = await ai_service.classify_question(body.question, provider=_provider(current_user))
    science_context = ""
    rag_sources: list = []
    if classification.get("needs_science_rag", False):
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
        classification=classification,
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
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RateWorkoutResponse:
    profile = _user_to_profile_dict(current_user)

    # Fetch Strava streams and compute planned-vs-actual delta when an activity ID is provided
    stream_delta: dict | None = None
    if body.strava_activity_id is not None and current_user.strava_token is not None:
        try:
            access_token = await ensure_fresh_strava_token(current_user.strava_token, db)
            streams = await fetch_activity_streams(access_token, body.strava_activity_id)
            if streams:
                ftp = float(
                    (
                        current_user.rider_assessment
                        and current_user.rider_assessment.estimated_ftp
                    )
                    or current_user.current_ftp
                    or 0
                ) or None
                stream_delta = compare_planned_vs_actual(
                    body.day.model_dump(by_alias=True),
                    streams,
                    ftp=ftp,
                )
        except Exception:
            logger.warning(
                "Failed to fetch Strava streams for workout rating; continuing without stream data",
                exc_info=True,
            )

    result = await ai_service.rate_completed_workout(
        body.day.model_dump(by_alias=True),
        profile,
        provider=_provider(current_user),
        stream_delta=stream_delta,
    )

    if result.get("flag_for_adaptation"):
        # Auto-adapt the plan when the workout signals accumulated fatigue/illness/pain
        rider_assessment = None
        if current_user.rider_assessment is not None:
            rider_assessment = schemas.RiderAssessmentSchema.model_validate(
                current_user.rider_assessment, from_attributes=True
            ).model_dump(by_alias=True)
        existing_plan = await crud.get_training_plan(db, current_user.id)
        plan = existing_plan.plan if existing_plan is not None else []
        # Build a feedback entry from the completed day so the adapter has context
        day_feedback = body.day.model_dump(by_alias=True).get("feedback", {}) or {}
        auto_feedback = [
            {
                "actualDurationMinutes": day_feedback.get("actualDurationMinutes"),
                "perceivedEffort": day_feedback.get("perceivedEffort"),
                "notes": day_feedback.get("notes", "Auto-triggered due to workout feedback"),
                "completedAt": day_feedback.get("completedAt"),
            }
        ]
        try:
            updated_plan = await ai_service.adapt_training_plan(
                plan,
                auto_feedback,
                profile,
                provider=_provider(current_user),
                rider_assessment=rider_assessment,
            )
            await crud.upsert_training_plan(db, current_user.id, updated_plan)
        except Exception:
            logger.warning("Auto-adaptation after flagged workout failed", exc_info=True)

    return schemas.RateWorkoutResponse(
        feedback=result.get("feedback", ""),
        flag_for_adaptation=result.get("flag_for_adaptation", False),
    )


@router.get("/readiness-score", response_model=schemas.ReadinessScoreResponse)
async def readiness_score(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ReadinessScoreResponse:
    """Return the current race-day readiness score for the authenticated user.

    Computes CTL/ATL/TSB from the user's training plan and their FTP, then
    derives a 0–100 readiness score.  When a ``race_date`` is set on the user
    profile the response also includes the number of days until the race and a
    forward-projection of the score at race day based on future plan days.
    """
    # --- Load training plan ---
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []

    # --- Resolve FTP (rider assessment takes precedence over profile) ---
    ftp = 0.0
    if current_user.rider_assessment is not None:
        assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
        ftp = float(assessment.get("estimatedFTP") or 0)
    if ftp <= 0:
        ftp = float(current_user.current_ftp or 0)

    # --- Compute current CTL/ATL/TSB (plan days up to today) ---
    today_str = _date.today().isoformat()
    plan_to_today = [d for d in plan if d.get("date", "") <= today_str]
    current_load = compute_training_load(plan_to_today, ftp) if ftp > 0 else {
        "ctl": 0.0, "atl": 0.0, "tsb": 0.0
    }

    # --- Compute days until race ---
    days_until_race = 0
    race_date_str = current_user.race_date
    if race_date_str:
        try:
            rd = _date.fromisoformat(race_date_str)
            days_until_race = max(0, (rd - _date.today()).days)
        except ValueError:
            race_date_str = None

    # --- Current readiness score ---
    current_result = compute_readiness_score(
        ctl=current_load["ctl"],
        atl=current_load["atl"],
        tsb=current_load["tsb"],
        days_until_race=days_until_race,
    )

    # --- Forward-projection to race day (when race_date is set and in the future) ---
    projected_score = None
    projected_ctl = None
    projected_atl = None
    projected_tsb = None
    if race_date_str and days_until_race > 0 and ftp > 0:
        projected_load = _project_training_load(plan, ftp, race_date_str)
        projected_result = compute_readiness_score(
            ctl=projected_load["ctl"],
            atl=projected_load["atl"],
            tsb=projected_load["tsb"],
            days_until_race=0,
        )
        projected_score = projected_result["score"]
        projected_ctl = projected_load["ctl"]
        projected_atl = projected_load["atl"]
        projected_tsb = projected_load["tsb"]

    return schemas.ReadinessScoreResponse(
        score=current_result["score"],
        form_score=current_result["form_score"],
        fitness_score=current_result["fitness_score"],
        ctl=current_result["ctl"],
        atl=current_result["atl"],
        tsb=current_result["tsb"],
        days_until_race=days_until_race,
        race_date=race_date_str,
        projected_score=projected_score,
        projected_ctl=projected_ctl,
        projected_atl=projected_atl,
        projected_tsb=projected_tsb,
    )


async def _run_knowledge_refresh() -> None:
    """Background task: run the cycling science knowledge base ingestion."""
    try:
        # Import is deferred to avoid loading heavy ingestion dependencies
        # (httpx, tiktoken, etc.) at server startup for all requests.
        from scripts.ingest_cycling_science import main as ingest_main  # noqa: PLC0415

        await ingest_main()
        logger.info("Knowledge base refresh completed successfully")
    except Exception:
        logger.exception("Knowledge base refresh failed")


@router.post("/refresh-knowledge", response_model=schemas.RefreshKnowledgeResponse)
async def refresh_knowledge(
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RefreshKnowledgeResponse:
    """Queue a background refresh of the cycling science knowledge base.

    Runs the ingestion script (seed corpus + Semantic Scholar API) as a
    background task and returns immediately.  Requires ``OPENAI_API_KEY`` to
    be set in the environment; returns HTTP 503 if it is absent.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPENAI_API_KEY is not configured; cannot refresh knowledge base",
        )
    background_tasks.add_task(_run_knowledge_refresh)
    return schemas.RefreshKnowledgeResponse(
        status="started",
        message="Knowledge base refresh has been queued and will run in the background",
    )
