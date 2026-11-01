"""AI routes."""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
    contextmanager,
    suppress,
)
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
from services import activity_analysis
from services import ai_service
from services import assessment_pipeline
from services import athlete_model_inference
from services import metrics_service
from services import plan_inputs
from services import chat_capture, chat_context, coach_chat
from services import (
    goal_progress,
    plan_context,
    season_plan,
    week_goals,
)
from services.athlete_language import interface_language
from services import learning_pipeline
from services import plan_maintenance
from services import plan_generation
from services import ride_ingest
from services import plan_translations
from services import single_flight
from services import status_pipeline
from services import summary_pipeline
from services.activity_imports import (
    ImportedActivity,
    reported_distance_km,
    reported_elevation_m,
    reported_int,
)
from domain.activity_identity import SPORT_CYCLING, are_near_duplicate_activities
from services.ai_service import (
    AIRateLimitError,
    AIResponseFormatError,
)
from services.analysis import (
    compare_planned_vs_actual,
    compute_readiness_score,
    build_readiness_recommendations,
    keyword_observation_assignments,
    attach_observation_assignments,
    finalize_recommendations,
    compute_training_load,
    project_training_load_from_seed,
)
from domain.fitness_ledger import ledger_from_metric, ledger_from_row
from services.prompts import ride_metrics_context_section
from services.dates import app_today, app_today_iso, request_timezone
from services.intervals_service import intervals_activity_id
from services.knowledge_topics import topics_for_limiter
from services.rag import (
    knowledge_corpus_is_populated,
    retrieve_cycling_context,
)
from services.ride_matching import (
    mark_matched_days_completed,
    resolve_manual_match,
    review_matched_ride_and_adapt,
)
from services.strava_service import ensure_fresh_strava_token, fetch_activity_streams
from services.weather_service import (
    enrich_activity_weather,
    home_coordinates_for_user,
)
from routers.dependencies import enforce_ai_rate_limit, set_user_ai_keys
from services.llm import resolve_user_provider
from services.token_accounting import track_llm_usage, track_llm_usage_detached


router = APIRouter(
    prefix="/ai",
    tags=["ai"],
    # ``set_user_ai_keys`` moved to routers/dependencies.py when the .fit
    # uploads turned out to need it too (#693) — they were spending the global
    # key with BYOK-only enabled, because no context meant the scheduler branch.
    dependencies=[Depends(set_user_ai_keys), Depends(enforce_ai_rate_limit)],
)

logger = logging.getLogger(__name__)


_RATE_LIMIT_DETAIL = (
    "The AI service is temporarily unavailable due to rate limiting. "
    "Please try again in a few minutes."
)
_AI_RESPONSE_FORMAT_DETAIL = (
    "The AI service returned an empty response. Please try again."
)


def _analysis_activity_log_sample(
    activities: list[schemas.StravaActivitySchema], limit: int = 10
) -> list[dict[str, object]]:
    sample: list[dict[str, object]] = []
    for activity in activities[:limit]:
        sample.append(
            {
                "id": activity.id,
                "name": activity.name,
                "type": activity.type,
                "sport_type": activity.sport_type,
                "start": activity.start_date_local or activity.start_date,
            }
        )
    return sample


def _activity_date_for_analysis(activity: schemas.StravaActivitySchema) -> str:
    activity_date_source = activity.start_date_local or activity.start_date or ""
    return activity_date_source[:10] if activity_date_source else ""


def _activity_duration_for_analysis(activity: schemas.StravaActivitySchema) -> int:
    return int(activity.moving_time or activity.elapsed_time or 0)


def _dedupe_analysis_activities(
    activities: list[schemas.StravaActivitySchema],
) -> list[schemas.StravaActivitySchema]:
    seen_ids: set[int] = set()
    deduped: list[schemas.StravaActivitySchema] = []
    for activity in activities:
        if activity.id in seen_ids:
            continue
        if any(
            are_near_duplicate_activities(
                activity_date=_activity_date_for_analysis(activity),
                sport_type=activity.sport_type or activity.type,
                activity_name=activity.name,
                activity_start_datetime=activity.start_date_local
                or activity.start_date,
                duration_seconds=_activity_duration_for_analysis(activity),
                other_activity_date=_activity_date_for_analysis(existing),
                other_sport_type=existing.sport_type or existing.type,
                other_activity_name=existing.name,
                other_activity_start_datetime=existing.start_date_local
                or existing.start_date,
                other_duration_seconds=_activity_duration_for_analysis(existing),
            )
            for existing in deduped
        ):
            continue
        deduped.append(activity)
        seen_ids.add(activity.id)
    return deduped


# The prompt inputs the chat shares with plan generation live in plan_context
# (ai-trainer-ops#246).
_race_events_for_prompt = plan_context.race_events_for_prompt
_active_availability_constraints_for_prompt = (
    plan_context.active_availability_constraints
)
_profile_with_availability_constraints = (
    plan_context.profile_with_availability_constraints
)


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


def _training_status_badge(
    user: models.User,
) -> tuple[str | None, str | None, str | None] | None:
    """The dashboard status badge the athlete is currently looking at (#499).

    Handed to the coach so a question about the badge is answered from the badge
    the coach itself wrote, rather than from a guess about what it might mean.
    """
    assessment = user.rider_assessment
    if assessment is None or not assessment.training_status_label:
        return None
    return (
        assessment.training_status_label,
        assessment.training_status_tone,
        assessment.training_status_rationale,
    )


def _token_usage_scope(
    db: AsyncSession, user: models.User, *, source: str
) -> AbstractAsyncContextManager[None]:
    """Collect and persist provider token usage for one endpoint.

    A thin alias for :func:`services.token_accounting.track_llm_usage`. The
    ``api:`` prefix used to be added here; call sites now pass the whole label,
    so the string in the code is the string in Grafana and grepping for one
    finds the other (#549).
    """
    return track_llm_usage(db, user, source=source)


_MEMORY_UPDATE_MAX_ATTEMPTS = 3


async def _update_memory_bg(
    user_id: str,
    question: str,
    response: str,
    provider: str,
) -> None:
    """Background task: update coach memory after the chat response is sent.

    Re-reads the current memory as the base for each attempt and writes it back
    with an atomic compare-and-set, so a coach-memory edit the athlete makes
    while the model is still generating is never clobbered by a stale
    request-time snapshot (#346). On a detected concurrent edit it retries
    against the fresh memory, up to a small bound.

    The whole retry loop shares one usage scope, so the ~3,000 tokens this costs
    per coach question are billed to the athlete once, however many attempts it
    took. The scope has to be opened here rather than inherited from
    ``ask_trainer``: background tasks run after the response, by which time that
    scope is long closed and the call was attributed to nobody (#537).
    """
    async with track_llm_usage_detached(
        async_session_maker, user_id, source="bg:update-coach-memory"
    ):
        try:
            for _ in range(_MEMORY_UPDATE_MAX_ATTEMPTS):
                async with async_session_maker() as session:
                    row = await crud.get_coach_memory(session, user_id)
                    base_memory = row.memory if row is not None else ""

                updated_memory = await ai_service.update_coach_memory(
                    base_memory, question, response, provider=provider
                )
                if not updated_memory or updated_memory == base_memory:
                    return

                async with async_session_maker() as session:
                    applied = await crud.update_coach_memory_if_unchanged(
                        session, user_id, expected=base_memory, new=updated_memory
                    )
                    if applied:
                        await session.commit()
                        return
                # The memory changed under us while generating — retry on the
                # fresh base.
            logger.info("Coach memory update abandoned after repeated concurrent edits")
        except AIRateLimitError:
            logger.info("Coach memory update skipped due to AI rate limit")
        except Exception:
            logger.warning("Coach memory background update failed", exc_info=True)


def _offers_for_prompt(
    race_offer: dict | None, race_proposal: dict | None, profile_proposal: dict | None
) -> list[str]:
    """What the buttons under the reply offer, so the coach knows none of it is done.

    "Das Rennen wird verschoben" got "bleibt als neuer Zieltermin fest
    eingetragen", while the move was only offered (ai-trainer-ops#195). Parsed
    values only, so no athlete text reaches the prompt this way.
    """
    offers = []
    if race_offer:
        moved = race_offer.get("replaces")
        offers.append(
            f"moving the race on {moved['date']} to {race_offer['date']}"
            if moved
            else f"adding a race on {race_offer['date']} to the calendar"
        )
    if race_proposal:
        offers.append("removing the race from the profile and the calendar")
    proposal = profile_proposal or {}
    if "weekly_hours" in proposal:
        offers.append(f"setting the weekly hours to {proposal['weekly_hours']}")
    if "training_days" in proposal:
        days = f"setting the training days to {', '.join(proposal['training_days'])}"
        # A day the athlete generally prefers, swapped in (ai-trainer-ops#204):
        # the reply says the change holds from now on, not only for this week.
        if proposal.get("standing_day_swap"):
            days += (
                " (a lasting change: once accepted it holds for every coming week and "
                "every later plan, so say it holds from now on, not only this week)"
            )
        offers.append(days)
    if "sports" in proposal:
        offers.append(f"setting the sports to {', '.join(proposal['sports'])}")
    if "ftp" in proposal:
        offers.append(f"setting the FTP to {proposal['ftp']} W")
    if "fitness_level" in proposal:
        offers.append(f"setting the level to {proposal['fitness_level']}")
    if "max_heart_rate" in proposal:
        offers.append(f"setting the max heart rate to {proposal['max_heart_rate']}")
    return offers


@router.post("/analyse-activities", response_model=schemas.AnalyseActivitiesResponse)
async def analyse_activities(
    request: Request,
    body: schemas.AnalyseActivitiesRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AnalyseActivitiesResponse:
    analysis_activities = _dedupe_analysis_activities(body.activities)
    if body.source == "intervals":
        logger.info(
            "Intervals analysis received activities user=%s count=%s sample=%s",
            current_user.id,
            len(analysis_activities),
            _analysis_activity_log_sample(analysis_activities),
        )
    if len(analysis_activities) != len(body.activities):
        logger.info(
            "Activity analysis deduplicated near-identical activities user=%s source=%s received=%s analysed=%s",
            current_user.id,
            body.source,
            len(body.activities),
            len(analysis_activities),
        )

    # Fetch per-second stream data for each activity from Strava
    streams_by_id: dict[str, dict] = {}
    if body.source == "strava" and current_user.strava_token is not None:
        try:
            async with db.begin_nested():
                # Only the refresh: Strava has already revoked the old token, so a
                # later failure must not roll the rotation back (#281).
                access_token = await ensure_fresh_strava_token(
                    current_user.strava_token, db
                )
            for activity in analysis_activities:
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

    activity_payloads: list[dict] = []
    weather_by_id: dict[int, dict] = {}
    # Resolved once for the batch so indoor rides still record conditions (#495).
    home_coordinates = await home_coordinates_for_user(db, current_user.id)
    for activity in analysis_activities:
        activity_dict = activity.model_dump()
        weather_fields = await enrich_activity_weather(
            activity_dict,
            streams=streams_by_id.get(str(activity.id)),
            fallback_coordinates=home_coordinates,
        )
        weather_by_id[activity.id] = weather_fields
        activity_payloads.append({**activity_dict, **weather_fields})

    existing_plan = await crud.get_training_plan(db, current_user.id)
    training_plan = existing_plan.plan if existing_plan is not None else []

    async with _token_usage_scope(db, current_user, source="api:analyse-activities"):
        try:
            result = await ai_service.analyse_strava_activities(
                activity_payloads,
                provider=resolve_user_provider(current_user),
                streams_by_id=streams_by_id,
                max_heart_rate=body.max_heart_rate,
                training_plan=training_plan or None,
                user_ftp=body.current_ftp
                or (
                    int(current_user.current_ftp) if current_user.current_ftp else None
                ),
                timezone_name=_request_timezone(request),
                threshold_pace_seconds_per_km=(
                    await metrics_service.get_effective_threshold_pace(db, current_user)
                ),
                # Their chat words first, the interface next (ai-trainer-ops#121, #258).
                reply_language=await summary_pipeline.athlete_language_of(
                    db, current_user.id
                )
                or interface_language(request),
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
            )

    # Validated before any persistence or the per-ride review calls below (which
    # spend more tokens): a malformed assessment is an unprocessable upstream
    # result (422), caught here so the rollback does not throw away — and a
    # retry does not repeat — that extra spend (ops#132).
    try:
        assessment_schema = activity_analysis.checked_assessment(result)
    except activity_analysis.UnparsableAnalysis as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await activity_analysis.record_assessment(db, current_user, result)

    if body.source == "intervals":
        current_user.intervals_analysis_complete = True
    else:
        current_user.strava_analysis_complete = True
    # Track the most recent activity analysed so the frontend can detect new rides.
    if body.activities:
        newest_id = max(a.id for a in body.activities)
        if body.source == "intervals":
            if (
                current_user.last_intervals_activity_id is None
                or newest_id > current_user.last_intervals_activity_id
            ):
                current_user.last_intervals_activity_id = newest_id
        elif (
            current_user.last_strava_activity_id is None
            or newest_id > current_user.last_strava_activity_id
        ):
            current_user.last_strava_activity_id = newest_id

    # --- Incremental ride-metrics chain ---
    # Always use the user-entered FTP for all ride-metric computations.
    # Store rides even when FTP is not yet set (TSS/IF will be null) so that
    # a later recalculate-metrics call can fill them in.
    ftp_for_chain = float(current_user.current_ftp or body.current_ftp or 0)
    if analysis_activities:
        rides_input = []
        already_stored = 0
        for activity in analysis_activities:
            a_dict = activity.model_dump()
            start_date: str = a_dict.get("startDate") or a_dict.get("start_date") or ""
            start_date_local: str = (
                a_dict.get("startDateLocal") or a_dict.get("start_date_local") or ""
            )
            activity_date_source = start_date_local or start_date
            activity_date = activity_date_source[:10] if activity_date_source else ""
            if not activity_date:
                if body.source == "intervals":
                    logger.warning(
                        "Intervals analysis skipped activity without date user=%s activity_id=%s name=%s",
                        current_user.id,
                        activity.id,
                        activity.name,
                    )
                continue
            sport_type = (
                a_dict.get("sportType")
                or a_dict.get("sport_type")
                or a_dict.get("type")
                or "cycling"
            )
            duration_seconds = int(
                a_dict.get("movingTime")
                or a_dict.get("moving_time")
                or a_dict.get("elapsedTime")
                or a_dict.get("elapsed_time")
                or 0
            )
            # Prefer the raw string external id (intervals ``i166933341``) so the
            # persisted identity survives the float64 round-trip the numeric
            # ``id`` suffers on the JS side, and matches what the sync path stores
            # (#429 Bug B). Strava activities have no external_id and keep the id.
            external_key = activity.external_id or str(activity.id)
            # A stored intervals.icu ride came in with more than this summary:
            # the import and the sync fetch its streams and intervals.icu's own
            # load. Stored again from here, it lost its purpose, its signals and
            # that load (ai-trainer-ops#306). It is not reviewed again either:
            # the sync reviewed its rides, and an import's are history.
            if body.source == "intervals" and (
                await crud.get_ride_metric_by_source(
                    db, current_user.id, "intervals", external_key
                )
                is not None
            ):
                already_stored += 1
                continue
            legacy_activity_id = (
                intervals_activity_id(activity.external_id)
                if activity.external_id is not None
                else activity.id
            )
            imported_activity = ImportedActivity(
                source=body.source,
                external_activity_id=external_key,
                name=a_dict.get("name"),
                start_datetime=start_date_local or start_date or None,
                activity_date=activity_date,
                sport_type=sport_type,
                duration_seconds=duration_seconds,
                distance_km=reported_distance_km(a_dict.get("distance")),
                elevation_gain_m=reported_elevation_m(
                    a_dict.get("total_elevation_gain")
                ),
                streams=streams_by_id.get(str(activity.id), {}),
                weather=weather_by_id.get(activity.id, {}),
                summary_avg_power_w=a_dict.get("average_watts"),
                summary_normalized_power_w=a_dict.get("weighted_average_watts"),
                # The only intensity figure a stream-less activity carries (#579).
                summary_avg_hr_bpm=reported_int(
                    a_dict.get("average_heartrate") or a_dict.get("averageHeartrate")
                ),
                metadata={f"{body.source}_activity_id": external_key},
                legacy_activity_id=legacy_activity_id,
            )
            rides_input.append(imported_activity.to_ride_input())
        if rides_input:
            # Every step a new ride takes, as the sync takes them (#248). The
            # analysis wrote the last-ride feedback itself, above.
            ingested = await ride_ingest.ingest_rides(
                db,
                current_user,
                rides_input,
                ftp=ftp_for_chain,
                policy=ride_ingest.ANALYSIS,
            )
            if ingested.stored:
                # One learning step for the rides it stored, as the sync takes
                # one, once the answer has left (ai-trainer-ops#301).
                background_tasks.add_task(
                    learning_pipeline.learn_in_own_session,
                    async_session_maker,
                    current_user.id,
                )
            if body.source == "intervals":
                logger.info(
                    "Intervals analysis persisted ride metrics user=%s rides_input=%s metrics=%s metric_ids=%s",
                    current_user.id,
                    len(rides_input),
                    ingested.stored,
                    [ride["strava_activity_id"] for ride in rides_input[:10]],
                )

        elif body.source == "intervals" and not already_stored:
            logger.warning(
                "Intervals analysis produced no ride inputs user=%s activity_count=%s sample=%s",
                current_user.id,
                len(analysis_activities),
                _analysis_activity_log_sample(analysis_activities),
            )

    await activity_analysis.record_login_summary(db, current_user, result)

    await db.flush()

    raw_updates = result.get("planUpdates") or []
    plan_updates = (
        [schemas.PlanDayUpdateSchema.model_validate(u) for u in raw_updates]
        if raw_updates
        else None
    )
    # Persisted here, respecting pins and skipping completed days (#399).
    await activity_analysis.commit_plan_updates(
        db, current_user, raw_updates, source="ride_review"
    )
    return schemas.AnalyseActivitiesResponse(
        assessment=assessment_schema, plan_updates=plan_updates
    )


# Each browser tab runs its own ``useStravaSync``, whose in-flight guard is a
# ref scoped to that hook instance — so two tabs (or two devices) can both call
# this at once. On 2026-09-08 two runs landed one second apart and persisted two
# entirely different weeks, the second undoing the first. Coalescing them means
# the LLM call and the plan write happen once and both callers get that one
# result (#664).
_generate_plan_inflight: single_flight.InFlight = {}

_REBUILD_LIMIT_DETAIL = (
    f"You have rebuilt your plan {plan_inputs.DAILY_REBUILDS} times today. "
    "You can rebuild it again tomorrow."
)


async def _generate_plan_for_user(
    db: AsyncSession,
    current_user: models.User,
    timezone_name: str | None,
    language: str | None = None,
) -> list[dict]:
    """Generate the plan, with the domain's errors as the statuses the app reads.

    For an interface the plan is translated into, the new plan's texts are
    translated before the answer leaves, so the plan comes up in the athlete's
    language (ai-trainer-ops#271). The plan is committed first: the translation
    is one more model call, and the plan's row stays locked until the commit.
    """
    try:
        plan = await plan_generation.generate_plan_for_user(
            db, current_user, timezone_name
        )
    except plan_generation.RebuildLimitReached:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_REBUILD_LIMIT_DETAIL
        )
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
        )
    except plan_generation.PlanGenerationFailed:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=_AI_RESPONSE_FORMAT_DETAIL
        )
    if language and language in plan_translations.LANGUAGE_CODES:
        await db.commit()
        await plan_translations.translate_new_plan(
            db,
            current_user,
            language,
            app_today(timezone_name=timezone_name),
            source="api:plan-translations",
        )
    return plan


@router.get("/plan-inputs-changed", response_model=schemas.PlanInputsChangedResponse)
async def plan_inputs_changed(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.PlanInputsChangedResponse:
    """Whether the coach knows enough new about the athlete to rebuild the plan
    (ai-trainer-ops#158). No model call; the dashboard asks on every load."""
    reasons = await plan_inputs.changes_since_generation(db, current_user)
    left = await plan_generation.rebuilds_left(
        db, current_user, _request_timezone(request)
    )
    return schemas.PlanInputsChangedResponse(
        changed=bool(reasons),
        reasons=reasons,
        rebuilds_left=left,
        last_rebuild=plan_inputs.last_rebuild(
            await crud.get_training_plan(db, current_user.id)
        ),
    )


@router.post("/plan-rebuild", response_model=schemas.PlanRebuildResponse)
async def plan_rebuild(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.PlanRebuildResponse:
    """Rebuild the plan by itself when the athlete changed what it was built
    from (ai-trainer-ops#206).

    The app asks after the athlete saved a setting or accepted a chat offer.
    Only their own changes rebuild here; new rides and answered questions wait
    for the nightly run, which batches them. One model call at most, and only
    while today's rebuilds last; done, logged and pinned days stay, as with any
    generation.
    """
    reasons = await plan_inputs.changes_since_generation(db, current_user)
    if not plan_inputs.ATHLETE_REASONS.intersection(reasons):
        return schemas.PlanRebuildResponse(reasons=reasons)
    timezone_name = _request_timezone(request)
    if await plan_generation.rebuilds_left(db, current_user, timezone_name) <= 0:
        return schemas.PlanRebuildResponse(reasons=reasons, limited=True)
    plan = await single_flight.run_single_flight(
        _generate_plan_inflight,
        current_user.id,
        lambda: _generate_plan_for_user(
            db, current_user, timezone_name, interface_language(request)
        ),
    )
    await plan_generation.mark_rebuilt(
        db, current_user, reasons, datetime.now(timezone.utc).isoformat()
    )
    return schemas.PlanRebuildResponse(rebuilt=True, reasons=reasons, plan=plan)


@router.post("/generate-plan")
async def generate_plan(
    body: schemas.GeneratePlanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> list[dict]:
    timezone_name = _request_timezone(request)
    return await single_flight.run_single_flight(
        _generate_plan_inflight,
        current_user.id,
        lambda: _generate_plan_for_user(
            db, current_user, timezone_name, interface_language(request)
        ),
    )


def _season_plan_response(
    weeks: list[season_plan.SeasonWeek], goals: dict
) -> schemas.SeasonPlanResponse:
    return schemas.SeasonPlanResponse(
        weeks=[
            schemas.SeasonWeekOut(
                week_start=week.start.isoformat(),
                phase=week.phase,
                focus=week.focus,
                recovery=week.recovery,
                load_week=week.load_week,
                hours_min=week.hours[0] if week.hours else None,
                hours_max=week.hours[1] if week.hours else None,
                hard_sessions_min=week.hard_sessions[0],
                hard_sessions_max=week.hard_sessions[1],
                key_session=week.key_session,
                next_race=week.next_race.isoformat() if week.next_race else None,
                goal=goals.get(week.start),
            )
            for week in weeks
        ]
    )


@router.get("/season-plan", response_model=schemas.SeasonPlanResponse)
async def get_season_plan(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.SeasonPlanResponse:
    """The weeks ahead, their targets and the goals already written (ai-trainer-ops#98).

    No model call: a week whose goal is missing or stale comes back without one,
    and the page asks ``POST /season-plan/goals`` for it.
    """
    today = app_today(timezone_name=_request_timezone(request))
    weeks = await plan_context.season_weeks_for_user(db, current_user.id, today)
    stored = await crud.get_week_goals(
        db, current_user.id, [week.start.isoformat() for week in weeks]
    )
    return _season_plan_response(weeks, season_plan.current_goals(weeks, stored))


# One goal-writing call per athlete at a time: two tabs opening the dashboard
# would otherwise pay for the same sentences twice.
_week_goals_inflight: single_flight.InFlight = {}


@router.post("/season-plan/goals", response_model=schemas.SeasonPlanResponse)
async def write_season_plan_goals(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.SeasonPlanResponse:
    """Write the goals the weeks are missing, in one model call (ai-trainer-ops#98)."""
    today = app_today(timezone_name=_request_timezone(request))

    async def _write() -> schemas.SeasonPlanResponse:
        weeks = await plan_context.season_weeks_for_user(db, current_user.id, today)
        try:
            goals = await week_goals.ensure_week_goals(
                db,
                current_user,
                weeks,
                today,
                source="api:week-goals",
                language=interface_language(request),
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
            )
        return _season_plan_response(weeks, goals)

    return await single_flight.run_single_flight(
        _week_goals_inflight, current_user.id, _write
    )


@router.get("/plan-translations", response_model=schemas.PlanTranslationsResponse)
async def get_plan_translations(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.PlanTranslationsResponse:
    """The plan's texts already translated into the interface language (ai-trainer-ops#121).

    No model call: when texts are still untranslated, ``missing`` says so and the
    page asks ``POST /plan-translations`` for them. The plan itself stays English.
    """
    code = plan_translations.LANGUAGE_CODES.get(interface_language(request) or "")
    if code is None:
        return schemas.PlanTranslationsResponse()
    today = app_today(timezone_name=_request_timezone(request))
    known, missing = await plan_translations.current_translations(
        db, current_user.id, code, today
    )
    return schemas.PlanTranslationsResponse(
        language=code,
        translations=known,
        missing=bool(missing),
        missing_key=plan_translations.missing_key(missing),
    )


# One translating call per athlete at a time, for the same reason as the goals.
_plan_translations_inflight: single_flight.InFlight = {}


@router.post("/plan-translations", response_model=schemas.PlanTranslationsResponse)
async def write_plan_translations(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.PlanTranslationsResponse:
    """Translate the plan's untranslated texts in one model call (ai-trainer-ops#121)."""
    language = interface_language(request)
    code = plan_translations.LANGUAGE_CODES.get(language or "")
    if language is None or code is None:
        return schemas.PlanTranslationsResponse()
    today = app_today(timezone_name=_request_timezone(request))

    async def _write() -> schemas.PlanTranslationsResponse:
        try:
            translations, missing = await plan_translations.ensure_translations(
                db, current_user, language, today, source="api:plan-translations"
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
            )
        return schemas.PlanTranslationsResponse(
            language=code,
            translations=translations,
            missing=bool(missing),
            missing_key=plan_translations.missing_key(missing),
        )

    return await single_flight.run_single_flight(
        _plan_translations_inflight, current_user.id, _write
    )


@asynccontextmanager
async def _classification(
    db: AsyncSession, user: models.User, question: str
) -> AsyncIterator[asyncio.Future | None]:
    """The question's classification, started beside the reads that follow.

    Classification exists to decide whether to retrieve science context, so
    it is only worth an LLM call when there is a corpus to retrieve from.
    With none ingested it used to spend one call per chat message to gate a
    path that could only ever return "" (#515). The check is a cached
    SELECT EXISTS, so this costs nothing once the corpus is there.
    """
    science_available = await knowledge_corpus_is_populated(db)
    classify_task = (
        # Start it in parallel with the DB fetch (it's a pure LLM call).
        asyncio.ensure_future(
            ai_service.classify_question(question, provider=resolve_user_provider(user))
        )
        if science_available
        else None
    )
    try:
        yield classify_task
    finally:
        # Never leave the classification task pending/unawaited (#450): if we
        # bailed out before awaiting it, cancel and drain it here.
        if classify_task is not None and not classify_task.done():
            classify_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await classify_task


@contextmanager
def _ai_errors_as_http() -> Iterator[None]:
    """The model's rate limit as a 503, and an empty reply as a 502."""
    try:
        yield
    except AIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_RATE_LIMIT_DETAIL,
        )
    except AIResponseFormatError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_AI_RESPONSE_FORMAT_DETAIL,
        )


async def _science_for(
    db: AsyncSession,
    classify_task: asyncio.Future | None,
    question: str,
    performance_model: dict | None,
) -> tuple[dict, str, list]:
    """The classification, and the science context it asks for with its sources."""
    # Await classification (likely already done), then conditionally retrieve RAG context.
    classification = await classify_task if classify_task else {}
    science_context = ""
    rag_sources: list = []
    if classification.get("needs_science_rag", False):
        # Retrieve for *this* athlete, not for the question in the
        # abstract (#627): the performance model already named the
        # limiter, so the corpus can be ranked by it. Empty whenever no
        # limiter is believed, which leaves retrieval exactly as it was.
        science_context, rag_sources = await retrieve_cycling_context(
            db,
            question,
            focus_topics=topics_for_limiter(
                (performance_model or {}).get("likely_limiter")
            ),
        )
    return classification, science_context, rag_sources


def _remember_in_background(
    background_tasks: BackgroundTasks, user: models.User, question: str, result: dict
) -> None:
    """Update coach memory in the background only when user has not disabled it."""
    if user.memory_updates_enabled:
        background_tasks.add_task(
            _update_memory_bg,
            user.id,
            question,
            result["response"],
            resolve_user_provider(user),
        )


def _ask_trainer_response(
    result: dict,
    captured: chat_capture.Captured,
    outcome: coach_chat.ReplyOutcome,
    *,
    rag_sources: list,
    coach_saw: list,
) -> schemas.AskTrainerResponse:
    """The reply as the athlete gets it, built after the commit (ai-trainer-ops#247)."""
    # Merge RAG retrieval sources into the result.
    # rag_sources contains the full metadata for all retrieved chunks;
    # use them as the authoritative sources list when RAG context was retrieved.
    if rag_sources:
        result["sources"] = rag_sources

    if outcome.ride_label_updates:
        result["ride_label_updates"] = outcome.ride_label_updates

    if outcome.updated_plan is not None:
        result["updated_plan"] = outcome.updated_plan

    # What the router captured or proposes, for the athlete to confirm or undo
    # under the reply (ops#65, #71). Built from rows, never from the model.
    result["captured_goals"] = [
        {
            "id": goal.id,
            "label": goal_progress.goal_name(goal.metric, goal.scope, goal.target),
        }
        for goal in captured.captured_goals
    ]
    result["race_proposal"] = captured.race_proposal
    result["race_offer"] = captured.race_offer
    result["profile_proposal"] = captured.profile_proposal
    result["captured_states"] = captured.captured_states
    result["captured_constraints"] = captured.captured_constraints
    result["captured_day_preferences"] = captured.captured_day_preferences
    result["coach_saw"] = coach_saw
    result["notes"] = outcome.notes
    # For the days the plan took, not every day the reply named (#266).
    result["day_summaries"] = outcome.day_summaries
    return schemas.AskTrainerResponse.model_validate(result)


@router.post("/ask-trainer", response_model=schemas.AskTrainerResponse)
async def ask_trainer(
    body: schemas.AskTrainerRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AskTrainerResponse:
    timezone_name = _request_timezone(request)
    # Load state from DB
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(
        by_alias=True
    )
    captured = await chat_capture.capture_from_message(
        db,
        current_user,
        body.question,
        plan=plan,
        profile=profile,
        timezone_name=timezone_name,
    )
    plan, profile = captured.plan, captured.profile
    coach_context = await chat_context.build_coach_context(
        db,
        current_user,
        body.question,
        context_workout=body.context_workout,
        timezone_name=timezone_name,
    )
    conversation = await chat_context.load_conversation(
        db,
        current_user.id,
        body.question,
        fallback_language=interface_language(request),
    )
    notes_in_german = conversation.reply_language == "German"

    async with (
        _token_usage_scope(db, current_user, source="api:ask-trainer"),
        _classification(db, current_user, body.question) as classify_task,
    ):
        with _ai_errors_as_http():
            sections = await chat_context.build_prompt_sections(
                db,
                current_user,
                plan,
                body.question,
                conversation.history,
                timezone_name=timezone_name,
                constraints=captured.availability_constraints,
            )
            classification, science_context, rag_sources = await _science_for(
                db, classify_task, body.question, coach_context.performance_model
            )
            result = await ai_service.ask_trainer(
                body.question,
                plan,
                profile,
                provider=resolve_user_provider(current_user),
                conversation_history=conversation.history,
                context_workout=body.context_workout,
                science_context=science_context,
                classification=classification,
                training_status_badge=_training_status_badge(current_user),
                timezone_name=timezone_name,
                interface_language=interface_language(request),
                reply_language=conversation.reply_language,
                app_offers=_offers_for_prompt(
                    captured.race_offer,
                    captured.race_proposal,
                    captured.profile_proposal,
                ),
                **coach_context.prompt_inputs(),
                **sections.prompt_inputs(),
            )

    outcome = await coach_chat.apply_reply(
        db,
        current_user,
        result,
        captured,
        question=body.question,
        already_in_chat=body.already_in_chat,
        notes_in_german=notes_in_german,
        timezone_name=timezone_name,
    )

    _remember_in_background(background_tasks, current_user, body.question, result)

    # Close this request's transaction before the response — and therefore before
    # the background tasks queued above. It used to be left to the get_db
    # teardown, which FastAPI ran *after* them (#522); since ai-trainer-ops#161
    # that teardown runs before the response too (scope="function"), and this
    # commit keeps the order from hanging on that setting.  _update_memory_bg opens
    # its own session on purpose, so that it re-reads memory the athlete may have
    # edited while the model was generating (#346); an open transaction here is
    # a writer it has to wait behind.  Under postgres the two never touch the
    # same rows and coexist, but sqlite locks the whole file, so under test the
    # memory write blocked for the full 5 s busy timeout and then failed —
    # silently, because _update_memory_bg is deliberately fail-safe.  This must
    # stay the last statement of the handler: every write above it, including
    # commit_plan_updates, has to be inside the transaction it closes.
    await db.commit()

    return _ask_trainer_response(
        result, captured, outcome, rag_sources=rag_sources, coach_saw=sections.coach_saw
    )


@router.post("/race-event-feedback", response_model=schemas.RaceEventFeedbackResponse)
async def race_event_feedback(
    body: schemas.RaceEventFeedbackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RaceEventFeedbackResponse:
    timezone_name = _request_timezone(request)
    existing_plan = await crud.get_training_plan(db, current_user.id)
    plan = existing_plan.plan if existing_plan is not None else []
    profile = schemas.UserProfileSchema.from_user(current_user).model_dump(
        by_alias=True
    )
    availability_constraints = await _active_availability_constraints_for_prompt(
        db, current_user.id, timezone_name
    )
    profile = _profile_with_availability_constraints(profile, availability_constraints)
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
    async with _token_usage_scope(db, current_user, source="api:race-event-feedback"):
        try:
            feedback = await ai_service.race_event_feedback(
                body.event.model_dump(by_alias=True),
                plan,
                profile,
                provider=resolve_user_provider(current_user),
                rider_assessment=rider_assessment,
                race_events=race_events,
                metrics_history_section=metrics_section,
                action=body.action,
                timezone_name=timezone_name,
                # Their chat words first, the interface next (ai-trainer-ops#121, #258).
                reply_language=await summary_pipeline.athlete_language_of(
                    db, current_user.id
                )
                or interface_language(request),
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
            )
    return schemas.RaceEventFeedbackResponse(feedback=feedback)


@router.post("/rate-workout", response_model=schemas.RateWorkoutResponse)
async def rate_workout(
    body: schemas.RateWorkoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
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
            async with db.begin_nested():
                # Only the refresh: Strava has already revoked the old token, so a
                # later failure must not roll the rotation back (#281).
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
                    sport_type=body.day.sport,
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

    async with _token_usage_scope(db, current_user, source="api:rate-workout"):
        try:
            result = await ai_service.rate_completed_workout(
                body.day.model_dump(by_alias=True),
                profile,
                provider=resolve_user_provider(current_user),
                stream_delta=stream_delta,
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
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
            # The router keeps the trigger; the adaptation is the nightly run's
            # own path, with the same context (ai-trainer-ops#246).
            await plan_maintenance.adapt_after_feedback(
                db, current_user, plan, auto_feedback, timezone_name=timezone_name
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
    db: AsyncSession = Depends(get_db, scope="function"),
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

    async with _token_usage_scope(db, current_user, source="api:review-new-rides"):
        try:
            review_text = await ai_service.batch_review_rides(
                unreviewed,
                profile=profile,
                provider=resolve_user_provider(current_user),
                training_plan=training_plan,
                timezone_name=timezone_name,
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
            )

    # Mark rides as reviewed so they are not presented again
    ride_ids = [m.strava_activity_id for m in unreviewed]
    await crud.mark_rides_as_reviewed(db, current_user.id, ride_ids)

    return schemas.BatchReviewRidesResponse(
        review=review_text, ride_count=len(unreviewed)
    )


@router.post(
    "/extract-athlete-facts", response_model=schemas.ExtractAthleteFactsResponse
)
async def extract_athlete_facts(
    body: schemas.ExtractAthleteFactsRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.ExtractAthleteFactsResponse:
    """Extract candidate durable athlete facts from a pasted conversation.

    Candidates are returned for review only — nothing is persisted here. The
    client accepts chosen candidates via ``POST /users/me/athlete-memory-facts``.
    """
    async with _token_usage_scope(db, current_user, source="api:extract-athlete-facts"):
        try:
            candidates = await ai_service.extract_athlete_facts(
                body.transcript,
                provider=resolve_user_provider(current_user),
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
            )

    return schemas.ExtractAthleteFactsResponse(
        candidates=[
            schemas.AthleteFactCandidateSchema.model_validate(candidate)
            for candidate in candidates
        ]
    )


@router.post("/refresh-athlete-model", response_model=schemas.AthleteModelSchema)
async def refresh_athlete_model(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteModelSchema:
    """Re-derive the long-term athlete model from training history on demand (#384).

    Mirrors the weekly background derivation but is triggered by the athlete from
    the settings UI. When the history yields nothing the current model (or an
    empty one) is returned unchanged.
    """
    timezone_name = _request_timezone(request)
    recent_metrics = await crud.get_ride_metrics_history(db, current_user.id, limit=60)
    metrics_section = ride_metrics_context_section(
        recent_metrics, timezone_name=timezone_name
    )

    existing_row = await crud.get_athlete_model(db, current_user.id)
    current_model = (
        schemas.AthleteModelSchema.model_validate(
            existing_row, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        if existing_row is not None
        else None
    )

    async with _token_usage_scope(db, current_user, source="api:refresh-athlete-model"):
        try:
            derived = await ai_service.derive_athlete_model(
                metrics_section,
                current_model=current_model,
                provider=resolve_user_provider(current_user),
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
            )

    if derived is not None:
        model = await crud.upsert_athlete_model(db, current_user.id, **derived)
        return schemas.AthleteModelSchema.model_validate(model, from_attributes=True)

    if existing_row is not None:
        return schemas.AthleteModelSchema.model_validate(
            existing_row, from_attributes=True
        )
    return schemas.AthleteModelSchema()


@router.get(
    "/athlete-performance-model",
    response_model=schemas.AthletePerformanceModelSchema,
)
async def get_athlete_performance_model(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthletePerformanceModelSchema:
    """Return the athlete's deterministic, evidence-backed performance model (#475).

    An athlete with no derived model yet gets an empty model (no attributes) rather
    than a 404, so the frontend can render a clean "not enough data" state.
    """
    row = await crud.get_athlete_performance_model(db, current_user.id)
    if row is None:
        return schemas.AthletePerformanceModelSchema()
    return schemas.AthletePerformanceModelSchema.model_validate(
        row, from_attributes=True
    )


@router.post(
    "/refresh-athlete-performance-model",
    response_model=schemas.AthletePerformanceModelSchema,
)
async def refresh_athlete_performance_model(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthletePerformanceModelSchema:
    """Re-run the deterministic inference engine on demand (#476).

    Mirrors the post-import/weekly refresh but athlete-triggered. Returns the
    (possibly empty) current model when the history yields no usable signals.
    """
    row = await athlete_model_inference.refresh_performance_model(db, current_user)
    if row is None:
        existing = await crud.get_athlete_performance_model(db, current_user.id)
        if existing is None:
            return schemas.AthletePerformanceModelSchema()
        return schemas.AthletePerformanceModelSchema.model_validate(
            existing, from_attributes=True
        )
    return schemas.AthletePerformanceModelSchema.model_validate(
        row, from_attributes=True
    )


@router.post("/resolve-ride-match", response_model=schemas.ResolveRideMatchResponse)
async def resolve_ride_match(
    body: schemas.ResolveRideMatchRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
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
        planned_slot=body.planned_slot,
        external_activity_id=body.external_activity_id,
    )
    if ride is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No ambiguous ride match found for that planned date",
        )

    # The athlete just said they rode this session, so tick it — otherwise resolving
    # the ambiguity left the day looking untouched and an automated regenerate could
    # still drop it (#574). Best-effort, like the import path: naming the session must
    # not fail because the completion write did.
    try:
        async with db.begin_nested():
            await mark_matched_days_completed(
                db, current_user, plan, [ride], source="manual_match"
            )
    except Exception:
        logger.warning("Failed to mark resolved plan day completed", exc_info=True)
    # Re-read afterwards: the review below commits against this list, and handing it
    # the pre-completion snapshot would make its own write look like a concurrent
    # user edit (stale-snapshot clobber, #452).
    refreshed_plan = await crud.get_training_plan(db, current_user.id)
    if refreshed_plan is not None:
        plan = refreshed_plan.plan

    streams = None
    if current_user.strava_token is not None:
        try:
            async with db.begin_nested():
                # Only the refresh: Strava has already revoked the old token, so a
                # later failure must not roll the rotation back (#281).
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

    async with _token_usage_scope(db, current_user, source="api:resolve-ride-match"):
        coach_note, plan_updates = await review_matched_ride_and_adapt(
            db,
            current_user,
            ride,
            plan,
            provider=resolve_user_provider(current_user),
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


# The model's matching of observations to readiness recommendations, per
# athlete, kept while neither side changes (ai-trainer-ops#255). The dashboard
# GETs the score on every load, GETs are exempt from the AI rate limit, and
# each load was a model call. One uvicorn worker, so a dict is the cache.
_READINESS_MATCH_TTL_SECONDS = 24 * 3600
_readiness_matches: dict[str, tuple[str, float, dict[int, list[str]]]] = {}
# The cache's clock, a name of its own so a test can move it without moving
# the event loop's.
_readiness_clock = time.monotonic


def _readiness_match_key(recommendations: list, observations: list) -> str:
    payload = json.dumps([recommendations, observations], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


@router.get("/readiness-score", response_model=schemas.ReadinessScoreResponse)
async def readiness_score(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
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

    # --- Load the coach's personal observations of the athlete ---
    # Only prompt-safe facts, and only when the athlete has memory enabled.
    observation_facts = (
        await crud.get_prompt_athlete_memory_facts(db, current_user.id)
        if current_user.memory_updates_enabled
        else []
    )
    observations = [fact.fact for fact in observation_facts]

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
        # Race readiness is scored against FTP and a cycling plan, so the
        # fitness half has to be the *cycling* rung of the ledger (#713). The
        # row's own ``ctl_after`` is whichever sport was logged last, which for
        # an athlete who ran yesterday would hand the readiness model a running
        # CTL. Fatigue is the aggregate either way — that is the point of it.
        current_ledger = ledger_from_metric(latest_ride)
    else:
        # Fall back to plan simulation when no ride data is available
        plan_to_today = [d for d in plan if d.get("date", "") <= today_str]
        fallback_load = (
            compute_training_load(plan_to_today, ftp)
            if ftp > 0
            else {"atl": 0.0, "ctl_by_sport": {}}
        )
        current_ledger = ledger_from_row(
            fallback_load.get("ctl_by_sport"),
            ctl_after=None,
            atl_after=fallback_load["atl"],
        )

    current_ctl = current_ledger.ctl(SPORT_CYCLING)
    current_atl = current_ledger.atl
    current_tsb = current_ledger.tsb(SPORT_CYCLING)

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
            future_plan_days, ftp, seed_ledger=current_ledger
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

    # --- Build recommendations, then weave in the coach's observations ---
    recommendations = build_readiness_recommendations(
        ctl=current_result["ctl"],
        atl=current_result["atl"],
        tsb=current_result["tsb"],
        score=current_result["score"],
        days_until_race=days_until_race,
    )
    if observations:
        assignments: dict[int, list[str]] | None = None
        if settings.readiness_observation_matching == "llm":
            key = _readiness_match_key(recommendations, observations)
            cached = _readiness_matches.get(current_user.id)
            if (
                cached is not None
                and cached[0] == key
                and _readiness_clock() - cached[1] < _READINESS_MATCH_TTL_SECONDS
            ):
                assignments = cached[2]
            else:
                try:
                    # Billed to the athlete like every other model call; a GET
                    # opened no scope, so this one counted toward nobody.
                    async with _token_usage_scope(
                        db, current_user, source="api:readiness-score"
                    ):
                        assignments = (
                            await ai_service.match_observations_to_recommendations(
                                recommendations,
                                observations,
                                provider=resolve_user_provider(current_user),
                            )
                        )
                    _readiness_matches[current_user.id] = (
                        key,
                        _readiness_clock(),
                        assignments,
                    )
                except Exception:
                    logger.exception(
                        "LLM observation matching failed; falling back to keyword matching"
                    )
                    assignments = None
        if assignments is None:
            assignments = keyword_observation_assignments(recommendations, observations)
        attach_observation_assignments(recommendations, assignments)
    finalize_recommendations(recommendations)

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
        recommendations=recommendations,
    )


@router.post(
    "/refresh-login-summary", response_model=schemas.RefreshLoginSummaryResponse
)
async def refresh_login_summary(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RefreshLoginSummaryResponse:
    """Generate a loginSummary from existing assessment data (no new rides needed).

    Called by the frontend when the user has a ``RiderAssessment`` but
    ``login_summary`` is ``None`` — e.g. because the column was added after
    their last Strava sync.  The summary is persisted and returned.
    """
    if current_user.rider_assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No rider assessment found — please complete a Strava analysis first",
        )

    async with _token_usage_scope(db, current_user, source="api:refresh-login-summary"):
        try:
            login_summary = await summary_pipeline.regenerate(
                db,
                current_user,
                provider=resolve_user_provider(current_user),
                timezone_name=_request_timezone(request),
                interface_language=interface_language(request),
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
            )

    return schemas.RefreshLoginSummaryResponse(login_summary=login_summary)


@router.post("/refresh-training-status", response_model=schemas.TrainingStatusResponse)
async def refresh_training_status(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.TrainingStatusResponse:
    """Regenerate the dashboard's coach-authored training-status badge (#499).

    Called by the frontend when the stored badge is missing — either never
    generated, or invalidated by the status pipeline after a plan or activity
    change. Always returns a renderable badge: the pipeline falls back to a
    deterministic label when the provider cannot produce a usable one.
    """
    if current_user.rider_assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No rider assessment found — please complete a Strava analysis first",
        )

    async with _token_usage_scope(
        db, current_user, source="api:refresh-training-status"
    ):
        try:
            label, tone, rationale = await status_pipeline.regenerate(
                db,
                current_user,
                provider=resolve_user_provider(current_user),
                timezone_name=_request_timezone(request),
                interface_language=interface_language(request),
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
            )

    return schemas.TrainingStatusResponse(label=label, tone=tone, rationale=rationale)


@router.post(
    "/process-pending-feedbacks", response_model=schemas.ProcessPendingFeedbacksResponse
)
async def process_pending_feedbacks(
    body: schemas.ProcessPendingFeedbacksRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
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

    async with _token_usage_scope(
        db, current_user, source="api:process-pending-feedbacks"
    ):
        try:
            login_summary = await ai_service.generate_summary_from_ride_feedbacks(
                rides=rides,
                assessment=assessment_dict,
                training_plan=training_plan or None,
                provider=resolve_user_provider(current_user),
                timezone_name=timezone_name,
                reply_language=await summary_pipeline.athlete_language_of(
                    db, current_user.id
                )
                or interface_language(request),
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_RATE_LIMIT_DETAIL,
            )

    if login_summary and current_user.rider_assessment is not None:
        # The summary node's own output: no notification, which would clear it.
        await assessment_pipeline.record(
            db,
            current_user,
            notify=False,
            estimated_ftp=current_user.rider_assessment.estimated_ftp,
            login_summary=login_summary,
        )

    return schemas.ProcessPendingFeedbacksResponse(login_summary=login_summary)
