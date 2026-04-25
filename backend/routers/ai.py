"""AI routes."""

import asyncio
import json
import logging
from datetime import date as _date
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import async_session_maker, get_db
from services import ai_service
from services.ai_service import MAX_CONVERSATION_HISTORY, AIRateLimitError
from services.analysis import compare_planned_vs_actual, compute_readiness_score, compute_readiness_recommendations, compute_training_load, _project_training_load, build_ride_metrics_chain, estimate_ftp_over_time, build_ride_analysis
from services.prompts import ride_metrics_context_section
from services.rag import retrieve_cycling_context
from services.strava_service import ensure_fresh_strava_token, fetch_activity_streams

router = APIRouter(prefix="/ai", tags=["ai"])

logger = logging.getLogger(__name__)

_RATE_LIMIT_DETAIL = (
    "The AI service is temporarily unavailable due to rate limiting. "
    "Please try again in a few minutes."
)


def _default_provider() -> str:
    """Return the best available provider based on configured API keys."""
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


async def _auto_rate_ride(
    plan_day: dict,
    streams: dict,
    ftp: float | None,
    profile: dict,
    provider: str,
) -> str | None:
    """Generate a coach note for a completed ride by comparing it against the plan.

    Returns the coach note string, or None when there is insufficient data.
    """
    try:
        stream_delta = compare_planned_vs_actual(plan_day, streams, ftp=ftp)
        ride_analysis = build_ride_analysis(streams, ftp) if ftp else None
        result = await ai_service.rate_completed_workout(
            plan_day, profile, provider=provider, stream_delta=stream_delta,
            ride_analysis=ride_analysis,
        )
        feedback_text = result.get("feedback", "")
        return feedback_text or None
    except Exception:
        logger.warning("Auto-rate ride failed", exc_info=True)
        return None


async def _update_memory_bg(
    user_id: str,
    question: str,
    response: str,
    current_memory: str,
    provider: str,
) -> None:
    """Background task: update coach memory after the chat response is sent."""
    try:
        updated_memory = await ai_service.update_coach_memory(
            current_memory, question, response, provider=provider
        )
        if updated_memory and updated_memory != current_memory:
            async with async_session_maker() as session:
                await crud.upsert_coach_memory(session, user_id, updated_memory)
                await session.commit()
    except AIRateLimitError:
        logger.info("Coach memory update skipped due to AI rate limit")
    except Exception:
        logger.warning("Coach memory background update failed", exc_info=True)


async def _auto_adapt_plan(
    db: AsyncSession,
    user: models.User,
    plan: list[dict],
    feedback_entry: dict,
    provider: str,
) -> None:
    """Trigger automatic plan adaptation when a workout signals fatigue/illness."""
    rider_assessment = None
    if user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    try:
        updated_plan = await ai_service.adapt_training_plan(
            plan,
            [feedback_entry],
            schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True),
            provider=provider,
            rider_assessment=rider_assessment,
        )
        await crud.upsert_training_plan(db, user.id, updated_plan)
    except Exception:
        logger.warning("Auto-adaptation after flagged workout failed", exc_info=True)


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
        except HTTPException as exc:
            logger.warning(
                "Strava auth failed (status=%s) fetching streams; falling back to summary-only analysis",
                exc.status_code,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "Strava network error fetching streams (%s); falling back to summary-only analysis",
                exc,
            )
        except Exception:
            logger.warning(
                "Unexpected error fetching Strava streams; falling back to summary-only analysis",
                exc_info=True,
            )

    existing_plan = await crud.get_training_plan(db, current_user.id)
    training_plan = existing_plan.plan if existing_plan is not None else []

    try:
        result = await ai_service.analyse_strava_activities(
            [activity.model_dump() for activity in body.activities],
            provider=_provider(current_user),
            streams_by_id=streams_by_id,
            max_heart_rate=body.max_heart_rate,
            training_plan=training_plan or None,
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )

    # Normalise rideInsights: the LLM may return a list of dicts or a string.
    # The DB column and Pydantic schema both expect a plain string.
    _ri = result.get("rideInsights")
    if _ri is not None and not isinstance(_ri, str):
        result["rideInsights"] = json.dumps(_ri)

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

    # --- Incremental ride-metrics chain ---
    ftp_for_chain = float(ftp_value or current_user.current_ftp or 0)
    if body.activities and ftp_for_chain > 0:
        latest_metric = await crud.get_latest_ride_metric(db, current_user.id)
        seed_ctl = latest_metric.ctl_after if latest_metric and latest_metric.ctl_after else 0.0
        seed_atl = latest_metric.atl_after if latest_metric and latest_metric.atl_after else 0.0
        rides_input = []
        for activity in body.activities:
            a_dict = activity.model_dump()
            start_date: str = a_dict.get("startDate") or a_dict.get("start_date") or ""
            activity_date = start_date[:10] if start_date else ""
            if not activity_date:
                continue
            sport_type = a_dict.get("sportType") or a_dict.get("sport_type") or "cycling"
            duration_seconds = int(
                a_dict.get("elapsedTime") or a_dict.get("elapsed_time")
                or a_dict.get("movingTime") or a_dict.get("moving_time") or 0
            )
            rides_input.append({
                "strava_activity_id": activity.id,
                "activity_date": activity_date,
                "sport_type": sport_type,
                "duration_seconds": duration_seconds,
                "streams": streams_by_id.get(str(activity.id), {}),
            })
        if rides_input:
            metrics_chain = build_ride_metrics_chain(rides_input, ftp_for_chain, seed_ctl, seed_atl)
            for m in metrics_chain:
                await crud.upsert_ride_metric(db, current_user.id, **m)

            # --- Phase 5b: Estimate FTP over time from steady intervals ---
            ftp_series = estimate_ftp_over_time(
                rides_input,
                max_heart_rate=current_user.max_heart_rate,
                resting_heart_rate=current_user.resting_heart_rate,
            )
            for point in ftp_series:
                try:
                    ride_dt = datetime.fromisoformat(point["date"]).replace(tzinfo=timezone.utc)
                except ValueError:
                    ride_dt = datetime.now(timezone.utc)
                await crud.create_athlete_metric_snapshot(
                    db,
                    current_user.id,
                    ftp=point["ftp"],
                    threshold_hr=None,
                    source="ftp_estimation",
                    recorded_at=ride_dt,
                )

            # --- Phase 6: Auto-rate rides that have a matching plan day ---
            profile_for_rating = schemas.UserProfileSchema.from_user(current_user).model_dump(by_alias=True)
            for ride_input, metric in zip(rides_input, metrics_chain):
                activity_date = ride_input["activity_date"]
                # Find matching plan day
                matching_plan_day = next(
                    (day for day in training_plan if day.get("date") == activity_date),
                    None,
                )
                if matching_plan_day and ride_input["streams"]:
                    coach_note = await _auto_rate_ride(
                        matching_plan_day,
                        ride_input["streams"],
                        ftp_for_chain,
                        profile_for_rating,
                        _provider(current_user),
                    )
                    if coach_note:
                        await crud.update_ride_metric_notes(
                            db,
                            current_user.id,
                            ride_input["strava_activity_id"],
                            coach_note=coach_note,
                        )

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
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(by_alias=True)
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    recent_metrics = await crud.get_ride_metrics_history(db, current_user.id, limit=30)
    metrics_section = ride_metrics_context_section(recent_metrics)
    try:
        plan = await ai_service.generate_training_plan(
            profile,
            provider=_provider(current_user),
            rider_assessment=rider_assessment,
            metrics_history_section=metrics_section,
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
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
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(by_alias=True)
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    recent_metrics = await crud.get_ride_metrics_history(db, current_user.id, limit=30)
    metrics_section = ride_metrics_context_section(recent_metrics)
    try:
        updated_plan = await ai_service.adapt_training_plan(
            plan,
            [feedback.model_dump(by_alias=True) for feedback in body.recent_feedback],
            profile,
            provider=_provider(current_user),
            rider_assessment=rider_assessment,
            metrics_history_section=metrics_section,
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )
    await crud.upsert_training_plan(db, current_user.id, updated_plan)
    return updated_plan


@router.post("/ask-trainer", response_model=schemas.AskTrainerResponse)
async def ask_trainer(
    body: schemas.AskTrainerRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AskTrainerResponse:
    # Load state from DB
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(by_alias=True)
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

    # --- Fetch ride metrics history for structured LLM context ---
    # Start question classification in parallel with the DB fetch (it's a pure LLM call)
    classify_task = asyncio.ensure_future(
        ai_service.classify_question(body.question, provider=_provider(current_user))
    )
    recent_metrics = await crud.get_ride_metrics_history(db, current_user.id, limit=30)
    metrics_section = ride_metrics_context_section(recent_metrics)

    # --- Task 5: Await classification (likely already done), then conditionally retrieve RAG context ---
    classification = await classify_task
    science_context = ""
    rag_sources: list = []
    if classification.get("needs_science_rag", False):
        science_context, rag_sources = await retrieve_cycling_context(db, body.question)

    try:
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
            metrics_history_section=metrics_section,
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )

    now = datetime.now(timezone.utc).isoformat()
    plan_updates = result.get("plan_updates") or []

    # --- Phase 7: Persist inferred user ride feedback ---
    ride_note_update = result.pop("ride_note_update", None)
    if ride_note_update and isinstance(ride_note_update, dict):
        note_date = ride_note_update.get("activity_date")
        note_text = ride_note_update.get("note")
        if note_date and note_text:
            target_metric = await crud.get_ride_metric_by_date(db, current_user.id, note_date)
            if target_metric is not None:
                await crud.update_ride_metric_notes(
                    db,
                    current_user.id,
                    target_metric.strava_activity_id,
                    user_note=note_text,
                )

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

    # Update coach memory in the background — no need for the user to wait
    background_tasks.add_task(
        _update_memory_bg,
        current_user.id,
        body.question,
        result["response"],
        coach_memory,
        _provider(current_user),
    )

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
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(by_alias=True)

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
        except HTTPException as exc:
            logger.warning(
                "Strava auth error fetching workout streams (status=%s); continuing without stream data",
                exc.status_code,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "Strava network error fetching workout streams (%s); continuing without stream data",
                exc,
            )
        except Exception:
            logger.warning(
                "Unexpected error fetching Strava streams for workout rating; continuing without stream data",
                exc_info=True,
            )

    try:
        result = await ai_service.rate_completed_workout(
            body.day.model_dump(by_alias=True),
            profile,
            provider=_provider(current_user),
            stream_delta=stream_delta,
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )

    if result.get("flag_for_adaptation"):
        # Auto-adapt the plan when the workout signals accumulated fatigue/illness/pain
        existing_plan = await crud.get_training_plan(db, current_user.id)
        plan = existing_plan.plan if existing_plan is not None else []
        day_feedback = body.day.model_dump(by_alias=True).get("feedback", {}) or {}
        auto_feedback = {
            "actualDurationMinutes": day_feedback.get("actualDurationMinutes"),
            "perceivedEffort": day_feedback.get("perceivedEffort"),
            "notes": day_feedback.get("notes", "Auto-triggered due to workout feedback"),
            "completedAt": day_feedback.get("completedAt"),
        }
        await _auto_adapt_plan(db, current_user, plan, auto_feedback, _provider(current_user))

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
        recommendations=compute_readiness_recommendations(
            ctl=current_result["ctl"],
            atl=current_result["atl"],
            tsb=current_result["tsb"],
            score=current_result["score"],
            days_until_race=days_until_race,
        ),
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
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPENAI_API_KEY is not configured; cannot refresh knowledge base",
        )
    background_tasks.add_task(_run_knowledge_refresh)
    return schemas.RefreshKnowledgeResponse(
        status="started",
        message="Knowledge base refresh has been queued and will run in the background",
    )


@router.post("/refresh-login-summary", response_model=schemas.RefreshLoginSummaryResponse)
async def refresh_login_summary(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RefreshLoginSummaryResponse:
    """Generate a loginSummary from existing assessment data (no new rides needed).

    Called by the frontend when the user has a ``RiderAssessment`` but
    ``login_summary`` is ``None`` — e.g. because the column was added after
    their last Strava sync.  The summary is persisted and returned.
    """
    assessment = current_user.rider_assessment
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No rider assessment found — please complete a Strava analysis first",
        )

    existing_plan = await crud.get_training_plan(db, current_user.id)
    training_plan = existing_plan.plan if existing_plan is not None else None

    try:
        login_summary = await ai_service.generate_login_summary(
            ride_insights=assessment.ride_insights,
            last_ride_feedback=assessment.last_ride_feedback,
            notes=assessment.notes,
            estimated_ftp=assessment.estimated_ftp,
            training_plan=training_plan or None,
            provider=_provider(current_user),
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )

    if login_summary:
        await crud.upsert_rider_assessment(
            db,
            current_user.id,
            estimated_ftp=assessment.estimated_ftp,
            estimated_threshold_hr=assessment.estimated_threshold_hr,
            login_summary=login_summary,
        )

    return schemas.RefreshLoginSummaryResponse(login_summary=login_summary)
