"""AI routes."""

import asyncio
import json
import logging
from datetime import date as _date
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import async_session_maker, get_db
from services import ai_service
from services.ai_service import MAX_CONVERSATION_HISTORY, AIRateLimitError
from services.analysis import (
    compare_planned_vs_actual,
    compute_readiness_score,
    compute_readiness_recommendations,
    compute_training_load,
    _project_training_load,
    project_training_load_from_seed,
    build_ride_metrics_chain,
    build_ride_analysis,
)
from services.prompts import ride_metrics_context_section
from services.dates import app_today, app_today_iso, request_timezone
from services.rag import retrieve_cycling_context
from services.ride_matching import (
    apply_ride_plan_matches,
    resolve_manual_match,
    review_matched_ride_and_adapt,
)
from services.strava_service import ensure_fresh_strava_token, fetch_activity_streams

router = APIRouter(prefix="/ai", tags=["ai"])

logger = logging.getLogger(__name__)

_RATE_LIMIT_DETAIL = (
    "The AI service is temporarily unavailable due to rate limiting. "
    "Please try again in a few minutes."
)


async def _race_events_for_prompt(db: AsyncSession, user_id: str) -> list[dict]:
    events = await crud.get_race_events(db, user_id)
    return [
        schemas.RaceEventResponse.model_validate(
            event, from_attributes=True
        ).model_dump(by_alias=True)
        for event in events
    ]


def _next_race_date_from_events(
    events: list[dict],
    fallback_race_date: str | None,
    timezone_name: str | None = None,
) -> str | None:
    today = app_today(timezone_name=timezone_name) if timezone_name else app_today()
    candidates: list[_date] = []
    if fallback_race_date:
        try:
            candidates.append(_date.fromisoformat(fallback_race_date))
        except ValueError:
            pass
    for event in events:
        event_date = event.get("date")
        if not event_date:
            continue
        try:
            candidates.append(_date.fromisoformat(str(event_date)))
        except ValueError:
            continue
    upcoming = [candidate for candidate in candidates if candidate >= today]
    return min(upcoming).isoformat() if upcoming else None


def _request_timezone(request: Request) -> str | None:
    return request_timezone(request)


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
            plan_day,
            profile,
            provider=provider,
            stream_delta=stream_delta,
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
    timezone_name: str | None = None,
) -> None:
    """Trigger automatic plan adaptation when a workout signals fatigue/illness."""
    rider_assessment = None
    if user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    try:
        race_events = await _race_events_for_prompt(db, user.id)
        updated_plan = await ai_service.adapt_training_plan(
            plan,
            [feedback_entry],
            schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True),
            provider=provider,
            rider_assessment=rider_assessment,
            race_events=race_events,
            timezone_name=timezone_name,
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
            access_token = await ensure_fresh_strava_token(
                current_user.strava_token, db
            )
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
            user_ftp=body.current_ftp
            or (int(current_user.current_ftp) if current_user.current_ftp else None),
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )

    # Normalise text fields: the LLM may return dicts or lists instead of strings.
    # The DB columns and Pydantic schema both expect plain strings.
    for _field in ("rideInsights", "lastRideFeedback", "loginSummary", "notes"):
        _val = result.get(_field)
        if _val is not None and not isinstance(_val, str):
            result[_field] = json.dumps(_val)

    await crud.upsert_rider_assessment(
        db,
        current_user.id,
        estimated_ftp=result.get("estimatedFTP"),
        rider_type=result.get("riderType"),
        notes=result.get("notes"),
        hr_zones=result.get("hrZones"),
        ride_insights=result.get("rideInsights"),
        last_ride_feedback=result.get("lastRideFeedback"),
        login_summary=result.get("loginSummary"),
    )

    current_user.strava_analysis_complete = True
    # Track the most recent activity analysed so the frontend can detect new rides.
    if body.activities:
        newest_id = max(a.id for a in body.activities)
        if (
            current_user.last_strava_activity_id is None
            or newest_id > current_user.last_strava_activity_id
        ):
            current_user.last_strava_activity_id = newest_id

    # --- Incremental ride-metrics chain ---
    # Always use the user-entered FTP for all ride-metric computations.
    # Store rides even when FTP is not yet set (TSS/IF will be null) so that
    # a later recalculate-metrics call can fill them in.
    ftp_for_chain = float(current_user.current_ftp or body.current_ftp or 0)
    if body.activities:
        latest_metric = await crud.get_latest_ride_metric(db, current_user.id)
        seed_ctl = (
            latest_metric.ctl_after
            if latest_metric and latest_metric.ctl_after
            else 0.0
        )
        seed_atl = (
            latest_metric.atl_after
            if latest_metric and latest_metric.atl_after
            else 0.0
        )
        rides_input = []
        for activity in body.activities:
            a_dict = activity.model_dump()
            start_date: str = a_dict.get("startDate") or a_dict.get("start_date") or ""
            start_date_local: str = (
                a_dict.get("startDateLocal") or a_dict.get("start_date_local") or ""
            )
            activity_date_source = start_date_local or start_date
            activity_date = activity_date_source[:10] if activity_date_source else ""
            if not activity_date:
                continue
            sport_type = (
                a_dict.get("sportType") or a_dict.get("sport_type") or "cycling"
            )
            duration_seconds = int(
                a_dict.get("elapsedTime")
                or a_dict.get("elapsed_time")
                or a_dict.get("movingTime")
                or a_dict.get("moving_time")
                or 0
            )
            rides_input.append(
                {
                    "strava_activity_id": activity.id,
                    "activity_name": a_dict.get("name"),
                    "activity_start_datetime": start_date or None,
                    "activity_date": activity_date,
                    "sport_type": sport_type,
                    "duration_seconds": duration_seconds,
                    "streams": streams_by_id.get(str(activity.id), {}),
                }
            )
        if rides_input:
            metrics_chain = build_ride_metrics_chain(
                rides_input, ftp_for_chain, seed_ctl, seed_atl
            )
            ride_meta_by_id = {r["strava_activity_id"]: r for r in rides_input}
            for metric in metrics_chain:
                meta = ride_meta_by_id.get(metric["strava_activity_id"], {})
                metric["activity_name"] = meta.get("activity_name")
                metric["activity_start_datetime"] = meta.get("activity_start_datetime")
            for m in metrics_chain:
                await crud.upsert_ride_metric(db, current_user.id, **m)
            auto_matched = await apply_ride_plan_matches(
                db,
                current_user.id,
                training_plan,
                [m["strava_activity_id"] for m in metrics_chain],
            )
            streams_by_activity_id = {
                ride["strava_activity_id"]: ride.get("streams") or {}
                for ride in rides_input
            }
            for ride in auto_matched:
                await review_matched_ride_and_adapt(
                    db,
                    current_user,
                    ride,
                    training_plan,
                    provider=_provider(current_user),
                    streams=streams_by_activity_id.get(ride.strava_activity_id),
                )

    await db.flush()

    assessment_schema = schemas.RiderAssessmentSchema.model_validate(result)
    raw_updates = result.get("planUpdates") or []
    plan_updates = (
        [schemas.PlanDayUpdateSchema.model_validate(u) for u in raw_updates]
        if raw_updates
        else None
    )
    return schemas.AnalyseActivitiesResponse(
        assessment=assessment_schema, plan_updates=plan_updates
    )


@router.post("/generate-plan")
async def generate_plan(
    body: schemas.GeneratePlanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> list[dict]:
    timezone_name = _request_timezone(request)
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(
        by_alias=True
    )
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    recent_metrics = await crud.get_ride_metrics_history(db, current_user.id, limit=30)
    metrics_section = ride_metrics_context_section(
        recent_metrics, timezone_name=timezone_name
    )
    race_events = await _race_events_for_prompt(db, current_user.id)
    try:
        plan = await ai_service.generate_training_plan(
            profile,
            provider=_provider(current_user),
            rider_assessment=rider_assessment,
            metrics_history_section=metrics_section,
            race_events=race_events,
            timezone_name=timezone_name,
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> list[dict]:
    timezone_name = _request_timezone(request)
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(
        by_alias=True
    )
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    recent_metrics = await crud.get_ride_metrics_history(db, current_user.id, limit=30)
    metrics_section = ride_metrics_context_section(
        recent_metrics, timezone_name=timezone_name
    )
    race_events = await _race_events_for_prompt(db, current_user.id)
    try:
        updated_plan = await ai_service.adapt_training_plan(
            plan,
            [feedback.model_dump(by_alias=True) for feedback in body.recent_feedback],
            profile,
            provider=_provider(current_user),
            rider_assessment=rider_assessment,
            metrics_history_section=metrics_section,
            race_events=race_events,
            timezone_name=timezone_name,
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AskTrainerResponse:
    timezone_name = _request_timezone(request)
    # Load state from DB
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(
        by_alias=True
    )
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
    metrics_section = ride_metrics_context_section(
        recent_metrics, timezone_name=timezone_name
    )
    race_events = await _race_events_for_prompt(db, current_user.id)

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
            race_events=race_events,
            timezone_name=timezone_name,
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
            target_metrics = await crud.get_ride_metrics_by_date(
                db, current_user.id, note_date
            )
            if len(target_metrics) == 1:
                target_metric = target_metrics[0]
                await crud.update_ride_metric_notes(
                    db,
                    current_user.id,
                    target_metric.strava_activity_id,
                    user_note=note_text,
                )

    # Persist user and assistant chat messages
    await crud.create_chat_message(
        db,
        current_user.id,
        role="user",
        content=body.question,
        timestamp=now,
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
            (
                {
                    **day,
                    **{
                        k: v
                        for k, v in updates_by_date[day["date"]].items()
                        if v is not None
                    },
                }
                if day.get("date") in updates_by_date
                else day
            )
            for day in plan
        ]
        await crud.upsert_training_plan(db, current_user.id, updated_plan)

    # Merge RAG retrieval sources into the result.
    # rag_sources contains the full metadata for all retrieved chunks;
    # use them as the authoritative sources list when RAG context was retrieved.
    if rag_sources:
        result["sources"] = rag_sources

    return schemas.AskTrainerResponse.model_validate(result)


@router.post("/race-event-feedback", response_model=schemas.RaceEventFeedbackResponse)
async def race_event_feedback(
    body: schemas.RaceEventFeedbackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RaceEventFeedbackResponse:
    timezone_name = _request_timezone(request)
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(
        by_alias=True
    )
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    recent_metrics = await crud.get_ride_metrics_history(db, current_user.id, limit=30)
    metrics_section = ride_metrics_context_section(
        recent_metrics, timezone_name=timezone_name
    )
    race_events = await _race_events_for_prompt(db, current_user.id)
    try:
        feedback = await ai_service.race_event_feedback(
            body.event.model_dump(by_alias=True),
            plan,
            profile,
            provider=_provider(current_user),
            rider_assessment=rider_assessment,
            race_events=race_events,
            metrics_history_section=metrics_section,
            action=body.action,
            timezone_name=timezone_name,
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )
    return schemas.RaceEventFeedbackResponse(feedback=feedback)


@router.post("/rate-workout", response_model=schemas.RateWorkoutResponse)
async def rate_workout(
    body: schemas.RateWorkoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RateWorkoutResponse:
    timezone_name = _request_timezone(request)
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(
        by_alias=True
    )

    # Fetch Strava streams and compute planned-vs-actual delta when an activity ID is provided
    stream_delta: dict | None = None
    if body.strava_activity_id is not None and current_user.strava_token is not None:
        try:
            access_token = await ensure_fresh_strava_token(
                current_user.strava_token, db
            )
            streams = await fetch_activity_streams(
                access_token, body.strava_activity_id
            )
            if streams:
                ftp = (
                    float(
                        (
                            current_user.rider_assessment
                            and current_user.rider_assessment.estimated_ftp
                        )
                        or current_user.current_ftp
                        or 0
                    )
                    or None
                )
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
            "notes": day_feedback.get(
                "notes", "Auto-triggered due to workout feedback"
            ),
            "completedAt": day_feedback.get("completedAt"),
        }
        await _auto_adapt_plan(
            db,
            current_user,
            plan,
            auto_feedback,
            _provider(current_user),
            timezone_name=timezone_name,
        )

    return schemas.RateWorkoutResponse(
        feedback=result.get("feedback", ""),
        flag_for_adaptation=result.get("flag_for_adaptation", False),
        needs_athlete_feedback=result.get("needs_athlete_feedback", False),
        follow_up_question=result.get("follow_up_question"),
        suggested_feedback_tags=result.get("suggested_feedback_tags", []),
    )


@router.post("/review-new-rides", response_model=schemas.BatchReviewRidesResponse)
async def review_new_rides(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.BatchReviewRidesResponse:
    """Review all rides that have not yet received a batch coach review.

    - Fetches every ride with ``coach_reviewed_at IS NULL`` for the current user.
    - If there are no unreviewed rides, returns an empty review with ``ride_count=0``.
    - Calls the AI to produce a batch review treating the rides as a training block.
    - Marks all included rides as reviewed so they are not re-presented on the
      next request.
    """
    unreviewed = await crud.get_unreviewed_ride_metrics(db, current_user.id)
    if not unreviewed:
        return schemas.BatchReviewRidesResponse(review="", ride_count=0)
    timezone_name = _request_timezone(request)

    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(
        by_alias=True
    )
    existing_plan = await crud.get_training_plan(db, current_user.id)
    training_plan = existing_plan.plan if existing_plan is not None else None

    try:
        review_text = await ai_service.batch_review_rides(
            unreviewed,
            profile=profile,
            provider=_provider(current_user),
            training_plan=training_plan,
            timezone_name=timezone_name,
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )

    # Mark rides as reviewed so they are not presented again
    ride_ids = [m.strava_activity_id for m in unreviewed]
    await crud.mark_rides_as_reviewed(db, current_user.id, ride_ids)

    return schemas.BatchReviewRidesResponse(
        review=review_text, ride_count=len(unreviewed)
    )


@router.post("/resolve-ride-match", response_model=schemas.ResolveRideMatchResponse)
async def resolve_ride_match(
    body: schemas.ResolveRideMatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ResolveRideMatchResponse:
    """Resolve which same-day Strava ride was the scheduled training ride."""
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []
    ride = await resolve_manual_match(
        db,
        current_user.id,
        planned_date=body.planned_date,
        strava_activity_id=body.strava_activity_id,
        plan=plan,
    )
    if ride is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No ambiguous ride match found for that planned date",
        )

    streams = None
    if current_user.strava_token is not None:
        try:
            access_token = await ensure_fresh_strava_token(
                current_user.strava_token, db
            )
            streams = await fetch_activity_streams(
                access_token, body.strava_activity_id
            )
        except Exception:
            logger.warning(
                "Could not fetch Strava streams while resolving ride match",
                exc_info=True,
            )

    coach_note, plan_updates = await review_matched_ride_and_adapt(
        db,
        current_user,
        ride,
        plan,
        provider=_provider(current_user),
        streams=streams,
    )

    validated_updates = (
        [schemas.PlanDayUpdateSchema.model_validate(u) for u in plan_updates]
        if plan_updates
        else None
    )
    return schemas.ResolveRideMatchResponse(
        ride=schemas.RideMetricSchema.model_validate(ride, from_attributes=True),
        coach_note=coach_note,
        plan_updates=validated_updates,
    )


@router.get("/readiness-score", response_model=schemas.ReadinessScoreResponse)
async def readiness_score(
    request: Request,
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

    timezone_name = _request_timezone(request)
    today_str = app_today_iso(timezone_name=timezone_name)

    # --- Compute current CTL/ATL/TSB from actual ride metrics ---
    latest_ride = await crud.get_latest_ride_metric(db, current_user.id)
    if latest_ride is not None and latest_ride.ctl_after is not None:
        current_ctl = float(latest_ride.ctl_after)
        current_atl = float(latest_ride.atl_after or 0.0)
        current_tsb = float(
            latest_ride.tsb_after
            if latest_ride.tsb_after is not None
            else current_ctl - current_atl
        )
    else:
        # Fall back to plan simulation when no ride data is available
        plan_to_today = [d for d in plan if d.get("date", "") <= today_str]
        fallback_load = (
            compute_training_load(plan_to_today, ftp)
            if ftp > 0
            else {"ctl": 0.0, "atl": 0.0, "tsb": 0.0}
        )
        current_ctl = fallback_load["ctl"]
        current_atl = fallback_load["atl"]
        current_tsb = fallback_load["tsb"]

    # --- Compute days until race ---
    days_until_race = 0
    race_events = await _race_events_for_prompt(db, current_user.id)
    race_date_str = _next_race_date_from_events(
        race_events,
        current_user.race_date,
        timezone_name=timezone_name,
    )
    if race_date_str:
        try:
            rd = _date.fromisoformat(race_date_str)
            days_until_race = max(0, (rd - app_today(timezone_name=timezone_name)).days)
        except ValueError:
            race_date_str = None

    # --- Current readiness score ---
    current_result = compute_readiness_score(
        ctl=current_ctl,
        atl=current_atl,
        tsb=current_tsb,
        days_until_race=days_until_race,
    )

    # --- Forward-projection to race day seeded from actual CTL/ATL ---
    projected_score = None
    projected_ctl = None
    projected_atl = None
    projected_tsb = None
    if race_date_str and days_until_race > 0:
        future_plan_days = [d for d in plan if d.get("date", "") > today_str]
        projected_load = project_training_load_from_seed(
            future_plan_days, ftp, seed_ctl=current_ctl, seed_atl=current_atl
        )
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


@router.post(
    "/refresh-login-summary", response_model=schemas.RefreshLoginSummaryResponse
)
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
            login_summary=login_summary,
        )

    return schemas.RefreshLoginSummaryResponse(login_summary=login_summary)


@router.post(
    "/next-ride-recommendation", response_model=schemas.NextRideRecommendationResponse
)
async def next_ride_recommendation(
    body: schemas.NextRideRecommendationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.NextRideRecommendationResponse:
    """Generate a concrete next-session recommendation after ride feedback.

    Accepts an optional ``strava_activity_id``.  When provided the recommendation
    is based on that specific ride; when omitted the most recently recorded ride
    metric is used.  In either case the endpoint also incorporates the current
    training plan, coach memory, rider assessment, and CTL/ATL/TSB values to
    produce a context-aware recommendation.

    If the recommendation includes ``planUpdates`` the next planned session is
    automatically updated in the database (same logic as ``ask-trainer``).
    """
    timezone_name = _request_timezone(request)
    # --- Load plan, profile, assessment, coach memory ---
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(
        by_alias=True
    )

    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)

    coach_memory_row = await crud.get_coach_memory(db, current_user.id)
    coach_memory = coach_memory_row.memory if coach_memory_row is not None else ""

    # --- Resolve the ride(s) to use for the recommendation ---
    if body.strava_activity_id is not None:
        target_ride = await crud.get_ride_metric_by_strava_id(
            db, current_user.id, body.strava_activity_id
        )
        rides = [target_ride] if target_ride is not None else []
    else:
        # Use the most recently recorded ride metric
        latest = await crud.get_latest_ride_metric(db, current_user.id)
        rides = [latest] if latest is not None else []

    # --- Extract CTL/ATL/TSB from the most recent ride metric ---
    ctl: float | None = None
    atl: float | None = None
    tsb: float | None = None
    if rides:
        last = rides[-1]
        ctl = float(last.ctl_after) if last.ctl_after is not None else None
        atl = float(last.atl_after) if last.atl_after is not None else None
        tsb = float(last.tsb_after) if last.tsb_after is not None else None
    else:
        # Fall back to the global latest ride metric for training-load context
        latest_metric = await crud.get_latest_ride_metric(db, current_user.id)
        if latest_metric is not None:
            ctl = (
                float(latest_metric.ctl_after)
                if latest_metric.ctl_after is not None
                else None
            )
            atl = (
                float(latest_metric.atl_after)
                if latest_metric.atl_after is not None
                else None
            )
            tsb = (
                float(latest_metric.tsb_after)
                if latest_metric.tsb_after is not None
                else None
            )

    try:
        result = await ai_service.recommend_next_session(
            rides=rides,
            plan=plan,
            profile=profile,
            provider=_provider(current_user),
            rider_assessment=rider_assessment,
            coach_memory=coach_memory,
            ctl=ctl,
            atl=atl,
            tsb=tsb,
            timezone_name=timezone_name,
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )

    # --- Apply plan updates using the same logic as ask-trainer ---
    plan_updates = result.get("plan_updates") or []
    if plan_updates:
        updates_by_date = {u["date"]: u for u in plan_updates}
        updated_plan = [
            (
                {
                    **day,
                    **{
                        k: v
                        for k, v in updates_by_date[day["date"]].items()
                        if v is not None
                    },
                }
                if day.get("date") in updates_by_date
                else day
            )
            for day in plan
        ]
        await crud.upsert_training_plan(db, current_user.id, updated_plan)

    validated_updates = (
        [schemas.PlanDayUpdateSchema.model_validate(u) for u in plan_updates]
        if plan_updates
        else None
    )
    return schemas.NextRideRecommendationResponse(
        response=result["response"],
        next_session_recommendation=result["next_session_recommendation"],
        recommendation_type=result.get("recommendation_type", "keep_as_planned"),
        plan_updates=validated_updates,
    )


@router.post(
    "/process-pending-feedbacks", response_model=schemas.ProcessPendingFeedbacksResponse
)
async def process_pending_feedbacks(
    body: schemas.ProcessPendingFeedbacksRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ProcessPendingFeedbacksResponse:
    """Generate an updated training summary from a batch of rides with new athlete feedback.

    Called by the frontend after a 60-second quiet window following one or more
    ride-feedback submissions.  Processes the rides in chronological order and
    persists the resulting ``login_summary`` on the rider assessment.
    """
    if not body.activity_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="activity_ids must not be empty",
        )
    timezone_name = _request_timezone(request)

    rides = await crud.get_ride_metrics_by_activity_ids(
        db, current_user.id, body.activity_ids
    )
    # rides are already sorted oldest-first by the crud helper

    existing_plan = await crud.get_training_plan(db, current_user.id)
    training_plan = existing_plan.plan if existing_plan is not None else None

    assessment_dict: dict | None = None
    if current_user.rider_assessment is not None:
        assessment_dict = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)

    try:
        login_summary = await ai_service.generate_summary_from_ride_feedbacks(
            rides=rides,
            assessment=assessment_dict,
            training_plan=training_plan or None,
            provider=_provider(current_user),
            timezone_name=timezone_name,
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )

    if login_summary and current_user.rider_assessment is not None:
        await crud.upsert_rider_assessment(
            db,
            current_user.id,
            estimated_ftp=current_user.rider_assessment.estimated_ftp,
            login_summary=login_summary,
        )

    return schemas.ProcessPendingFeedbacksResponse(login_summary=login_summary)
