"""AI routes."""

import asyncio
import json
import logging
from contextlib import AbstractAsyncContextManager, suppress
from datetime import date as _date
from datetime import datetime, timedelta, timezone

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
from services import athlete_model_inference
from services import freshness_allocation
from services import motivation_inference
from services import coach_summary
from services import plan_pipeline
from services import roi_recommendation
from services import status_pipeline
from services import rider_identity
from services import summary_pipeline
from services import workout_curiosity
from services.activity_imports import ImportedActivity
from services.activity_identity import are_near_duplicate_activities
from services.ai_service import (
    MAX_CONVERSATION_HISTORY,
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
    _project_training_load,
    project_training_load_from_seed,
    build_ride_metrics_chain,
    build_ride_analysis,
)
from services.prompts import ride_metrics_context_section
from services.dates import app_today, app_today_iso, request_timezone
from services.availability import (
    extract_availability_constraints,
    extract_constraint_lift,
)
from services.plan_constraints import (
    describe_constraint_overrides,
    filter_plan_updates_for_constraints,
)
from services.intervals_service import apply_summary_fallback, intervals_activity_id
from services.embeddings import embedding_provider_is_configured
from services.knowledge_topics import topics_for_limiter
from services.rag import (
    knowledge_corpus_is_populated,
    reset_corpus_cache,
    retrieve_cycling_context,
)
from services.ride_matching import (
    apply_ride_plan_matches,
    mark_matched_days_completed,
    resolve_manual_match,
    review_matched_ride_and_adapt,
)
from services.strava_service import ensure_fresh_strava_token, fetch_activity_streams
from services import home_location, weather_preference
from services.weather_service import (
    clear_forecast_cache,
    enrich_activity_weather,
    home_coordinates_for_user,
    training_weather_context_for_user,
)
from services import llm as llm_service
from services.llm import resolve_user_provider
from services.token_accounting import track_llm_usage, track_llm_usage_detached


async def _set_ai_key(
    current_user: models.User = Depends(auth.get_current_user),
) -> None:
    """Router-level dependency: inject per-user API keys into the LLM ContextVar."""
    keys = {
        "openai": current_user.user_openai_api_key,
        "gemini": current_user.user_gemini_api_key,
    }
    token = llm_service.set_user_ai_keys(keys)
    try:
        yield
    finally:
        llm_service.reset_user_ai_keys(token)


router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(_set_ai_key)])

logger = logging.getLogger(__name__)


def _optional_int(value: object) -> int | None:
    """Round a provider number to an int, or ``None`` if it is not one."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(value)

_RATE_LIMIT_DETAIL = (
    "The AI service is temporarily unavailable due to rate limiting. "
    "Please try again in a few minutes."
)
_AI_RESPONSE_FORMAT_DETAIL = (
    "The AI service returned an empty response. Please try again."
)
# How far back the coach re-reads its own past ride notes (#513).  The history
# window stays at 30 rides so trend questions keep their numbers; only the prose
# tail is cut, and the coach's own notes were 45 % of that section in production
# (2,278 of 5,063 tokens, re-read on every message).  Seven rides is about a week
# for this athlete — the 30 rides span 33 days — so "how did last week go" is
# still answered from full notes, while the coach stops quoting itself from six
# weeks ago.
RIDE_NOTE_PROSE_WINDOW = 7


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


async def _race_events_for_prompt(db: AsyncSession, user_id: str) -> list[dict]:
    events = await crud.get_race_events(db, user_id)
    return [
        schemas.RaceEventResponse.model_validate(
            event, from_attributes=True
        ).model_dump(by_alias=True)
        for event in events
    ]


async def _upcoming_race_count(db: AsyncSession, user_id: str, timezone_name: str | None) -> int:
    """How many races the athlete still has ahead of them (#564).

    Race specificity is worth nothing with an empty calendar, so the utility
    scorer needs this to stop weighting race-ready options for an athlete who
    has stopped racing.
    """
    today = app_today_iso(timezone_name=timezone_name)
    events = await crud.get_race_events(db, user_id)
    return sum(1 for event in events if (event.date or "") >= today)


async def _active_availability_constraints_for_prompt(
    db: AsyncSession, user_id: str, timezone_name: str | None
) -> list[dict]:
    today = app_today_iso(timezone_name=timezone_name)
    await crud.deactivate_expired_availability_constraints(
        db, user_id, today=today
    )
    constraints = await crud.list_active_availability_constraints(
        db, user_id, today=today
    )
    return [
        schemas.AthleteAvailabilityConstraintSchema.model_validate(
            constraint, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for constraint in constraints
    ]


async def _capture_availability_constraints_from_message(
    db: AsyncSession,
    user_id: str,
    message: str,
    timezone_name: str | None,
) -> tuple[list[dict], list[dict]]:
    """Sync availability constraints with the athlete's message.

    Lifts requested constraints first so a same-message re-proposed edit sees the
    post-lift state, then captures any newly stated constraints. Returns the
    active constraints for the prompt and the constraints that were lifted (#437).
    """
    today = app_today(timezone_name=timezone_name)

    # Lift before capture: honour "lift that constraint" so the coach's edit to a
    # previously blocked day can finally land in the same turn (#437).
    lift = extract_constraint_lift(message, today=today)
    lifted_rows: list[models.AthleteAvailabilityConstraint] = []
    if lift["lift"]:
        dates = lift["dates"] or await crud.get_last_flagged_constraint_dates(
            db, user_id
        )
        if dates:
            lifted_rows = await crud.deactivate_availability_constraints_for_dates(
                db, user_id, dates
            )
    lifted = [
        schemas.AthleteAvailabilityConstraintSchema.model_validate(
            row, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for row in lifted_rows
    ]

    extracted = extract_availability_constraints(message, today=today)
    for item in extracted:
        await crud.upsert_availability_constraint(db, user_id, **item)
    constraints = await _active_availability_constraints_for_prompt(
        db, user_id, timezone_name
    )
    return constraints, lifted


async def _capture_self_reported_signals_from_message(
    db: AsyncSession,
    user_id: str,
    message: str,
) -> str | None:
    """Sync the athlete's self-reports from their message (#495, #563).

    Three independent captures, all deterministic and all best-effort:

    * a stated training location ("I mostly train near Freiburg now") becomes the
      ``user_set`` home-location attribute, which then outranks every inference
      pass and re-anchors all weather lookups;
    * a stated weather preference ("I actually love the rain") becomes evidence on
      the corresponding weather-preference belief, reinforcing the same row the
      ride data writes;
    * a stated motivation ("I don't care about races", "I want more trail time")
      becomes evidence on the athlete's motivation model — what the training is
      actually for.

    Returns a deterministic confirmation note when the location moved — the honest
    record of what the backend actually did, independent of the model's prose (the
    same contract as the availability-constraint lift note, #437).
    """
    try:
        await weather_preference.capture_weather_preferences_from_message(
            db, user_id, message
        )
    except Exception:
        logger.warning("Weather-preference capture failed", exc_info=True)

    # What the athlete says they are training for (#563). Same contract as the
    # captures around it: deterministic, best-effort, and unable to overwrite an
    # objective the athlete set by hand.
    try:
        await motivation_inference.capture_motivation_from_message(
            db, user_id, message
        )
    except Exception:
        logger.warning("Motivation capture failed", exc_info=True)

    try:
        row = await home_location.capture_home_location_from_message(
            db, user_id, message
        )
    except Exception:
        logger.warning("Home-location capture failed", exc_info=True)
        return None
    if row is None:
        return None

    # A new base invalidates the cached forecast for the old one.
    clear_forecast_cache()
    where = row.label or f"{row.latitude:.2f}, {row.longitude:.2f}"
    return (
        f"(Note: I've set your usual training location to {where}, so your weather "
        f"outlook and any weather-driven plan changes now use that.)"
    )


def _profile_with_availability_constraints(
    profile: dict, constraints: list[dict]
) -> dict:
    if not constraints:
        return profile
    return {**profile, "availabilityConstraints": constraints}


def _filter_plan_updates_for_availability_constraints(
    updates: list[dict], constraints: list[dict]
) -> list[dict]:
    return filter_plan_updates_for_constraints(updates, constraints)


def _day_label(override: dict) -> str:
    """Human-friendly day label for a constraint override note."""
    weekday = override.get("weekday")
    if weekday:
        return str(weekday).capitalize()
    return str(override.get("date") or "that day")


def _format_constraint_override_note(overrides: list[dict]) -> str:
    """Compose an honest note about coach-requested changes a constraint blocked.

    The plan pipeline enforces hard availability constraints deterministically,
    so these requests never land as asked. Without this, the coach would falsely
    confirm the change (#414).
    """
    required = [o for o in overrides if o.get("constraintType") == "required_workout"]
    unavailable = [o for o in overrides if o.get("constraintType") == "no_training"]
    sentences: list[str] = []
    if required:
        days = ", ".join(_day_label(o) for o in required)
        sentences.append(
            f"I couldn't change {days}: it's pinned as a required session by one of "
            f"your availability constraints, so the plan keeps it as-is. Let me know "
            f"if you'd like to lift that constraint."
        )
    if unavailable:
        days = ", ".join(_day_label(o) for o in unavailable)
        sentences.append(
            f"I couldn't schedule training on {days}: it's marked unavailable by one "
            f"of your availability constraints."
        )
    return "(Note: " + " ".join(sentences) + ")"


def _format_constraint_lift_note(lifted: list[dict]) -> str:
    """Confirm, deterministically, which availability constraints were lifted.

    The lift is applied by the router before the plan pipeline runs, so this is
    the honest record of what happened — independent of whatever the model says
    in its prose (#437).
    """
    days = ", ".join(_day_label(c) for c in lifted)
    plural = "constraints" if len(lifted) > 1 else "constraint"
    return (
        f"(Note: I lifted the availability {plural} on {days}, so the plan can now "
        f"be changed there.)"
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
            logger.info(
                "Coach memory update abandoned after repeated concurrent edits"
            )
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
        weather_section = await training_weather_context_for_user(db, user.id)
        availability_constraints = await _active_availability_constraints_for_prompt(
            db, user.id, timezone_name
        )
        profile = schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True)
        profile = _profile_with_availability_constraints(profile, availability_constraints)
        updated_plan = await ai_service.adapt_training_plan(
            plan,
            [feedback_entry],
            profile,
            provider=provider,
            rider_assessment=rider_assessment,
            race_events=race_events,
            weather_context_section=weather_section,
            timezone_name=timezone_name,
        )
        commit = await plan_pipeline.commit_plan(
            db, user, updated_plan, base_plan=plan, source="auto_adapt",
            timezone_name=timezone_name,
        )
        await coach_summary.narrate_plan_changes(
            db, user, batch_id=commit.batch_id, source="auto_adapt",
            applied_changes=commit.applied_changes, profile=profile,
            rider_assessment=rider_assessment,
        )
    except Exception:
        logger.warning("Auto-adaptation after flagged workout failed", exc_info=True)


@router.post("/analyse-activities", response_model=schemas.AnalyseActivitiesResponse)
async def analyse_activities(
    request: Request,
    body: schemas.AnalyseActivitiesRequest,
    db: AsyncSession = Depends(get_db),
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
                or (int(current_user.current_ftp) if current_user.current_ftp else None),
                timezone_name=_request_timezone(request),
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
                streams=streams_by_id.get(str(activity.id), {}),
                weather=weather_by_id.get(activity.id, {}),
                summary_avg_power_w=a_dict.get("average_watts"),
                summary_normalized_power_w=a_dict.get("weighted_average_watts"),
                # The only intensity figure a stream-less activity carries (#579).
                summary_avg_hr_bpm=_optional_int(
                    a_dict.get("average_heartrate")
                    or a_dict.get("averageHeartrate")
                ),
                metadata={f"{body.source}_activity_id": external_key},
                legacy_activity_id=legacy_activity_id,
            )
            rides_input.append(imported_activity.to_ride_input())
        if rides_input:
            metrics_chain = build_ride_metrics_chain(
                rides_input,
                ftp_for_chain,
                seed_ctl,
                seed_atl,
                max_heart_rate=current_user.max_heart_rate,
                resting_heart_rate=current_user.resting_heart_rate,
            )
            ride_meta_by_id = {r["strava_activity_id"]: r for r in rides_input}
            for metric in metrics_chain:
                meta = ride_meta_by_id.get(metric["strava_activity_id"], {})
                metric["activity_name"] = meta.get("activity_name")
                metric["activity_start_datetime"] = meta.get("activity_start_datetime")
                if body.source == "intervals":
                    apply_summary_fallback(metric, meta)
            for m in metrics_chain:
                await crud.upsert_ride_metric(db, current_user.id, **m)
            if body.source == "intervals":
                logger.info(
                    "Intervals analysis persisted ride metrics user=%s rides_input=%s metrics=%s metric_ids=%s",
                    current_user.id,
                    len(rides_input),
                    len(metrics_chain),
                    [metric["strava_activity_id"] for metric in metrics_chain[:10]],
                )
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
                    provider=resolve_user_provider(current_user),
                    streams=streams_by_activity_id.get(ride.strava_activity_id),
                )

        elif body.source == "intervals":
            logger.warning(
                "Intervals analysis produced no ride inputs user=%s activity_count=%s sample=%s",
                current_user.id,
                len(analysis_activities),
                _analysis_activity_log_sample(analysis_activities),
            )

    await db.flush()

    assessment_schema = schemas.RiderAssessmentSchema.model_validate(result)
    raw_updates = result.get("planUpdates") or []
    plan_updates = (
        [schemas.PlanDayUpdateSchema.model_validate(u) for u in raw_updates]
        if raw_updates
        else None
    )
    # Persist the plan adaptations here, through the shared constraint- and
    # pin-respecting pipeline (source="ride_review", respect_pins=True, completed
    # days skipped). Previously these were returned unpersisted and the client
    # PUT its whole in-memory plan back via /users/me/plan (source="user_edit"),
    # which reverted concurrent edits and bypassed pin/completed-day protection —
    # a stale-snapshot clobber that rewrote pinned/completed days (#399).
    if raw_updates:
        plan_row = await crud.get_training_plan(db, current_user.id)
        base_plan = plan_row.plan if plan_row is not None else []
        commit = await plan_pipeline.commit_plan_updates(
            db,
            current_user,
            raw_updates,
            base_plan=base_plan,
            source="ride_review",
        )
        await coach_summary.narrate_plan_changes(
            db,
            current_user,
            batch_id=commit.batch_id,
            source="ride_review",
            applied_changes=commit.applied_changes,
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
    weather_section = await training_weather_context_for_user(db, current_user.id)
    race_events = await _race_events_for_prompt(db, current_user.id)
    availability_constraints = await _active_availability_constraints_for_prompt(
        db, current_user.id, timezone_name
    )
    profile = _profile_with_availability_constraints(
        profile, availability_constraints
    )
    # Who this athlete is and what their freshness is for (#602) — the same block
    # the nightly regen builds, so the two triggers plan for the same person.
    athlete_model_section = (
        await freshness_allocation.athlete_model_section_for_user(
            db, current_user, timezone_name=timezone_name
        )
    )
    async with _token_usage_scope(db, current_user, source="api:generate-plan"):
        try:
            plan = await ai_service.generate_training_plan(
                profile,
                provider=resolve_user_provider(current_user),
                rider_assessment=rider_assessment,
                metrics_history_section=metrics_section,
                weather_context_section=weather_section,
                race_events=race_events,
                timezone_name=timezone_name,
                athlete_model_section=athlete_model_section,
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
            )
        except AIResponseFormatError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=_AI_RESPONSE_FORMAT_DETAIL,
            )
    existing_plan = await crud.get_training_plan(db, current_user.id)
    base_plan = existing_plan.plan if existing_plan is not None else []
    commit = await plan_pipeline.commit_plan(
        db, current_user, plan, base_plan=base_plan, source="generate",
        timezone_name=timezone_name,
    )
    return commit.plan


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
    (
        availability_constraints,
        lifted_constraints,
    ) = await _capture_availability_constraints_from_message(
        db, current_user.id, body.question, timezone_name
    )
    # Capture weather self-reports before the forecast is read, so a location the
    # athlete states in this very message anchors this turn's outlook (#495).
    home_location_note = await _capture_self_reported_signals_from_message(
        db, current_user.id, body.question
    )
    profile = _profile_with_availability_constraints(
        profile, availability_constraints
    )
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    memory_enabled = current_user.memory_updates_enabled
    coach_memory_row = await crud.get_coach_memory(db, current_user.id)
    coach_memory = (coach_memory_row.memory if coach_memory_row is not None else "") if memory_enabled else ""
    athlete_context_row = await crud.get_athlete_context(db, current_user.id)
    athlete_context = (
        schemas.AthleteContextSchema.model_validate(
            athlete_context_row, from_attributes=True
        ).model_dump(by_alias=True)
        if (athlete_context_row is not None and memory_enabled)
        else None
    )
    # What the athlete is training for (#562). Gated on the same memory switch
    # as the rest of the durable profile: an athlete who turned memory off does
    # not want their objective recalled either.
    motivation_row = await crud.get_athlete_motivation_model(db, current_user.id)
    motivation_model = (
        crud.motivation_model_as_dict(motivation_row)
        if (motivation_row is not None and memory_enabled)
        else None
    )
    athlete_model_row = await crud.get_athlete_model(db, current_user.id)
    athlete_model = (
        schemas.AthleteModelSchema.model_validate(
            athlete_model_row, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        if (athlete_model_row is not None and memory_enabled)
        else None
    )
    # What is worth being curious about in what the athlete just said (#593).
    # Deterministic and best-effort, like the captures above it, and gated on
    # memory for the same reason the motivation model is: it both reads and
    # writes durable beliefs about the athlete.
    workout_curiosity_context: dict | None = None
    if memory_enabled:
        try:
            workout_curiosity_context = await workout_curiosity.curiosity_for_message(
                db,
                current_user.id,
                body.question,
                weights=(motivation_model or {}).get("weights"),
            )
        except Exception:
            logger.warning("Workout-curiosity pass failed", exc_info=True)
    athlete_memory_fact_rows = (
        await crud.get_prompt_athlete_memory_facts(db, current_user.id)
        if memory_enabled
        else []
    )
    athlete_memory_facts = [
        schemas.AthleteMemoryFactSchema.model_validate(
            fact, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for fact in athlete_memory_fact_rows
    ]
    # Capped, not the full list (#512) — see get_prompt_athlete_open_questions.
    open_question_rows = (
        await crud.get_prompt_athlete_open_questions(db, current_user.id)
        if memory_enabled
        else []
    )
    open_questions = [
        schemas.AthleteOpenQuestionSchema.model_validate(
            question, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for question in open_question_rows
    ]
    # Questions already pinned to the athlete (#506) — passed so the coach does
    # not ask them a second time in prose while the pin is still waiting.
    pending_inquiry_rows = (
        await crud.list_athlete_inquiries(db, current_user.id)
        if memory_enabled
        else []
    )
    pending_inquiries = [
        schemas.AthleteInquirySchema.model_validate(
            inquiry, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for inquiry in pending_inquiry_rows
    ]

    # Deterministic Athlete Performance Model (#476/#477), its ROI recommendation
    # (#478) and the active testable hypotheses (#479) — the structured substrate
    # the coach drills into to explain WHY on demand and to surface a higher-return
    # emphasis proactively (#480).
    performance_model: dict | None = None
    performance_recommendation: dict | None = None
    hypotheses: list[dict] = []
    if memory_enabled:
        perf_model_row = await crud.get_athlete_performance_model(db, current_user.id)
        if perf_model_row is not None:
            performance_model = schemas.AthletePerformanceModelSchema.model_validate(
                perf_model_row, from_attributes=True
            ).model_dump(by_alias=False, mode="json")
            performance_recommendation = roi_recommendation.recommend_training_roi(
                perf_model_row.attributes,
                perf_model_row.limiters,
                motivation=motivation_model,
                upcoming_races=await _upcoming_race_count(
                    db, current_user.id, timezone_name
                ),
            )
        # Capped, not the full list (#512) — see get_prompt_athlete_hypotheses.
        hypothesis_rows = await crud.get_prompt_athlete_hypotheses(
            db, current_user.id
        )
        hypotheses = [
            schemas.AthleteHypothesisSchema.model_validate(
                hypothesis, from_attributes=True
            ).model_dump(by_alias=False, mode="json")
            for hypothesis in hypothesis_rows
        ]
    # Who this athlete is as a rider (#597) — read after the performance model,
    # since the style half comes off it. Gated on memory like everything durable,
    # and best-effort: it explains a recommendation, it never makes one.
    rider_identity_context: dict | None = None
    if memory_enabled:
        try:
            rider_identity_context = await rider_identity.identity_for_prompt(
                db, current_user.id, performance_model=performance_model
            )
        except Exception:
            logger.warning("Rider-identity read failed", exc_info=True)

    chat_messages = await crud.get_chat_messages(db, current_user.id)
    conversation_history = [
        {"role": msg.role, "content": msg.content}
        for msg in chat_messages[-MAX_CONVERSATION_HISTORY:]
    ]

    # --- Fetch ride metrics history for structured LLM context ---
    # Start question classification in parallel with the DB fetch (it's a pure LLM call)
    async with _token_usage_scope(db, current_user, source="api:ask-trainer"):
        # Classification exists to decide whether to retrieve science context, so
        # it is only worth an LLM call when there is a corpus to retrieve from.
        # With none ingested it used to spend one call per chat message to gate a
        # path that could only ever return "" (#515). The check is a cached
        # SELECT EXISTS, so this costs nothing once the corpus is there.
        science_available = await knowledge_corpus_is_populated(db)
        classify_task = (
            # Start it in parallel with the DB fetch (it's a pure LLM call).
            asyncio.ensure_future(
                ai_service.classify_question(
                    body.question, provider=resolve_user_provider(current_user)
                )
            )
            if science_available
            else None
        )
        try:
            recent_metrics = await crud.get_ride_metrics_history(db, current_user.id, limit=30)
            # Keep the full 30-ride window — trend questions are answered from the
            # metrics lines — but stop replaying the coach's own older notes back
            # to it on every message (#513).
            metrics_section = ride_metrics_context_section(
                recent_metrics,
                timezone_name=timezone_name,
                prose_window=RIDE_NOTE_PROSE_WINDOW,
            )
            race_events = await _race_events_for_prompt(db, current_user.id)
            # The upcoming outlook near the athlete's training location plus their
            # learned tolerances — the coach chat is where "should I ride tomorrow?"
            # actually gets asked (#495). Cached per location, so this is cheap.
            weather_section = await training_weather_context_for_user(
                db, current_user.id
            )

            # Await classification (likely already done), then conditionally retrieve RAG context.
            classification = await classify_task if classify_task else {}
            science_context = ""
            rag_sources: list = []
            if classification.get("needs_science_rag", False):
                # Retrieve for *this* athlete, not for the question in the
                # abstract (#627): the performance model above already named the
                # limiter, so the corpus can be ranked by it. Empty whenever no
                # limiter is believed, which leaves retrieval exactly as it was.
                science_context, rag_sources = await retrieve_cycling_context(
                    db,
                    body.question,
                    focus_topics=topics_for_limiter(
                        (performance_model or {}).get("likely_limiter")
                    ),
                )

            result = await ai_service.ask_trainer(
                body.question,
                plan,
                profile,
                provider=resolve_user_provider(current_user),
                rider_assessment=rider_assessment,
                coach_memory=coach_memory,
                conversation_history=conversation_history,
                context_workout=body.context_workout,
                science_context=science_context,
                classification=classification,
                metrics_history_section=metrics_section,
                race_events=race_events,
                athlete_context=athlete_context,
                athlete_memory_facts=athlete_memory_facts,
                athlete_model=athlete_model,
                motivation_model=motivation_model,
                open_questions=open_questions,
                pending_inquiries=pending_inquiries,
                performance_model=performance_model,
                performance_recommendation=performance_recommendation,
                hypotheses=hypotheses,
                weather_context_section=weather_section,
                training_status_badge=_training_status_badge(current_user),
                timezone_name=timezone_name,
                workout_curiosity=workout_curiosity_context,
                rider_identity=rider_identity_context,
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
            )
        except AIResponseFormatError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=_AI_RESPONSE_FORMAT_DETAIL,
            )
        finally:
            # Never leave the classification task pending/unawaited (#450): if we
            # bailed out before awaiting it above, cancel and drain it here.
            if classify_task is not None and not classify_task.done():
                classify_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await classify_task

    user_message_time = datetime.now(timezone.utc)
    assistant_message_time = user_message_time + timedelta(microseconds=1)
    requested_updates = result.get("plan_updates") or []
    plan_updates = _filter_plan_updates_for_availability_constraints(
        requested_updates,
        availability_constraints,
    )
    persisted_updated_plan: list[dict] | None = None

    # Coach honesty: ``commit_plan_updates`` only patches days already present in
    # the plan, so an update for a date the plan no longer contains (e.g. a past
    # day that rolled off the window) is a silent no-op. Don't let the model claim
    # success for a change that was never saved — append a correction to the reply
    # (also stored on the assistant message below) and drop the dead update. Only
    # do this when a plan exists; with no plan at all there is no window to reason
    # about. (#364)
    if plan_updates and plan:
        plan_dates = {d.get("date") for d in plan if isinstance(d, dict)}
        unresolved_dates = sorted(
            {u.get("date") for u in plan_updates if u.get("date")} - plan_dates
        )
        if unresolved_dates:
            joined = ", ".join(unresolved_dates)
            result["response"] = (
                f"{result['response']}\n\n"
                f"(Note: I could not update {joined} — that day is not part of your "
                f"current training plan, so no change was saved.)"
            )
            plan_updates = [u for u in plan_updates if u.get("date") in plan_dates]

    # Coach honesty: hard availability constraints are enforced deterministically
    # by the plan pipeline, so a requested change to a constrained day never lands
    # as asked (a rest day on a required-session day is coerced back; training on
    # an unavailable day is dropped). Without a note the coach falsely confirms the
    # change (#414). Scope to days in the current plan window so dead dates stay
    # owned by the note above.
    flagged_constraint_dates: list[str] = []
    if plan and availability_constraints:
        plan_dates = {d.get("date") for d in plan if isinstance(d, dict)}
        overrides = describe_constraint_overrides(
            [u for u in requested_updates if u.get("date") in plan_dates],
            availability_constraints,
        )
        if overrides:
            result["response"] = (
                f"{result['response']}\n\n{_format_constraint_override_note(overrides)}"
            )
            # Remember which constraints blocked this turn so a follow-up "lift
            # that constraint" can resolve "that" to them (#437).
            flagged_constraint_dates = [
                o["date"] for o in overrides if o.get("date")
            ]

    # Coach honesty: confirm a lift deterministically, independent of the model's
    # prose, so "pls lift that constraint" is not silently ignored (#437).
    if lifted_constraints:
        result["response"] = (
            f"{result['response']}\n\n{_format_constraint_lift_note(lifted_constraints)}"
        )

    # Coach honesty: the training location was moved by the router, before the model
    # replied, so confirm it deterministically rather than hoping the prose does (#495).
    if home_location_note:
        result["response"] = f"{result['response']}\n\n{home_location_note}"

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

    # --- Phase 7b: Persist AI-issued label corrections ---
    ride_label_update = result.pop("ride_label_update", None)
    ride_label_updates: list[dict] = []
    if ride_label_update and isinstance(ride_label_update, dict):
        label_date = ride_label_update.get("activity_date")
        label_text = ride_label_update.get("label")
        if label_date and label_text:
            target_metrics = await crud.get_ride_metrics_by_date(
                db, current_user.id, label_date
            )
            if len(target_metrics) == 1:
                await crud.update_ride_metric_notes(
                    db,
                    current_user.id,
                    target_metrics[0].strava_activity_id,
                    label_override=label_text,
                )
                ride_label_updates = [
                    {
                        "stravaActivityId": target_metrics[0].strava_activity_id,
                        "labelOverride": label_text,
                    }
                ]

    # Persist user and assistant chat messages
    await crud.create_chat_message(
        db,
        current_user.id,
        role="user",
        content=body.question,
        timestamp=user_message_time.isoformat(),
    )
    await crud.create_chat_message(
        db,
        current_user.id,
        role="assistant",
        content=result["response"],
        timestamp=assistant_message_time.isoformat(),
        plan_update_count=len(plan_updates) if plan_updates else None,
        flagged_constraint_dates=flagged_constraint_dates or None,
    )

    # Update coach memory in the background only when user has not disabled it
    if current_user.memory_updates_enabled:
        background_tasks.add_task(
            _update_memory_bg,
            current_user.id,
            body.question,
            result["response"],
            resolve_user_provider(current_user),
        )

    # Apply plan updates if any — through the shared constraint-respecting pipeline.
    if plan_updates:
        persisted_updated_plan = (
            await plan_pipeline.commit_plan_updates(
                db,
                current_user,
                plan_updates,
                base_plan=plan,
                source="coach_chat",
                timezone_name=timezone_name,
            )
        ).plan

    # Merge RAG retrieval sources into the result.
    # rag_sources contains the full metadata for all retrieved chunks;
    # use them as the authoritative sources list when RAG context was retrieved.
    if rag_sources:
        result["sources"] = rag_sources

    if ride_label_updates:
        result["ride_label_updates"] = ride_label_updates

    if persisted_updated_plan is not None:
        result["updated_plan"] = persisted_updated_plan

    # Close this request's transaction before the response — and therefore before
    # the background tasks queued above — instead of leaving it to the get_db
    # teardown, which FastAPI runs *after* them (#522).  _update_memory_bg opens
    # its own session on purpose, so that it re-reads memory the athlete may have
    # edited while the model was generating (#346); an open transaction here is
    # a writer it has to wait behind.  Under postgres the two never touch the
    # same rows and coexist, but sqlite locks the whole file, so under test the
    # memory write blocked for the full 5 s busy timeout and then failed —
    # silently, because _update_memory_bg is deliberately fail-safe.  This must
    # stay the last statement of the handler: every write above it, including
    # commit_plan_updates, has to be inside the transaction it closes.
    await db.commit()

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
    availability_constraints = await _active_availability_constraints_for_prompt(
        db, current_user.id, timezone_name
    )
    profile = _profile_with_availability_constraints(
        profile, availability_constraints
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
                resolve_user_provider(current_user),
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
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
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
    db: AsyncSession = Depends(get_db),
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
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
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
    db: AsyncSession = Depends(get_db),
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
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
            try:
                assignments = await ai_service.match_observations_to_recommendations(
                    recommendations,
                    observations,
                    provider=resolve_user_provider(current_user),
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


async def _run_knowledge_refresh() -> None:
    """Background task: run the cycling science knowledge base ingestion."""
    try:
        # Import is deferred to avoid loading heavy ingestion dependencies
        # (httpx, tiktoken, etc.) at server startup for all requests.
        from scripts.ingest_cycling_science import main as ingest_main  # noqa: PLC0415

        await ingest_main()
        # The corpus-presence answer is cached per process; without this the
        # worker that just filled the corpus keeps skipping classification and
        # retrieval until it restarts.
        reset_corpus_cache()
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
    background task and returns immediately.  Requires an embedding provider
    key; returns HTTP 503 if none is configured.
    """
    # Used to demand OPENAI_API_KEY specifically, which made this endpoint
    # permanently unavailable on the Gemini-only production config (#515).
    if not embedding_provider_is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No embedding provider is configured (set GEMINI_API_KEY or "
                "OPENAI_API_KEY); cannot refresh knowledge base"
            ),
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
    request: Request,
    db: AsyncSession = Depends(get_db),
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
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
            )

    return schemas.RefreshLoginSummaryResponse(login_summary=login_summary)


@router.post("/refresh-training-status", response_model=schemas.TrainingStatusResponse)
async def refresh_training_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
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

    async with _token_usage_scope(db, current_user, source="api:refresh-training-status"):
        try:
            label, tone, rationale = await status_pipeline.regenerate(
                db,
                current_user,
                provider=resolve_user_provider(current_user),
                timezone_name=_request_timezone(request),
            )
        except AIRateLimitError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_RATE_LIMIT_DETAIL
            )

    return schemas.TrainingStatusResponse(
        label=label, tone=tone, rationale=rationale
    )


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
    availability_constraints = await _active_availability_constraints_for_prompt(
        db, current_user.id, timezone_name
    )
    profile = _profile_with_availability_constraints(
        profile, availability_constraints
    )

    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)

    memory_enabled = current_user.memory_updates_enabled
    coach_memory_row = await crud.get_coach_memory(db, current_user.id)
    coach_memory = (coach_memory_row.memory if coach_memory_row is not None else "") if memory_enabled else ""
    athlete_context_row = await crud.get_athlete_context(db, current_user.id)
    athlete_context = (
        schemas.AthleteContextSchema.model_validate(
            athlete_context_row, from_attributes=True
        ).model_dump(by_alias=True)
        if (athlete_context_row is not None and memory_enabled)
        else None
    )
    # What the athlete is training for (#562). Gated on the same memory switch
    # as the rest of the durable profile: an athlete who turned memory off does
    # not want their objective recalled either.
    motivation_row = await crud.get_athlete_motivation_model(db, current_user.id)
    motivation_model = (
        crud.motivation_model_as_dict(motivation_row)
        if (motivation_row is not None and memory_enabled)
        else None
    )
    athlete_memory_fact_rows = (
        await crud.get_prompt_athlete_memory_facts(db, current_user.id)
        if memory_enabled
        else []
    )
    athlete_memory_facts = [
        schemas.AthleteMemoryFactSchema.model_validate(
            fact, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for fact in athlete_memory_fact_rows
    ]

    # --- ROI recommendation from the deterministic performance model (#478) ---
    performance_recommendation: dict | None = None
    perf_model_row = await crud.get_athlete_performance_model(db, current_user.id)
    if perf_model_row is not None:
        performance_recommendation = roi_recommendation.recommend_training_roi(
            perf_model_row.attributes,
            perf_model_row.limiters,
            motivation=motivation_model,
            upcoming_races=await _upcoming_race_count(
                db, current_user.id, timezone_name
            ),
        )

    # --- Resolve the ride(s) to use for the recommendation ---
    if body.strava_activity_id is not None:
        target_ride = await crud.get_ride_metric_by_identity(
            db,
            current_user.id,
            body.strava_activity_id,
            body.external_activity_id,
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

    async with _token_usage_scope(db, current_user, source="api:next-ride-recommendation"):
        try:
            result = await ai_service.recommend_next_session(
                rides=rides,
                plan=plan,
                profile=profile,
                provider=resolve_user_provider(current_user),
                rider_assessment=rider_assessment,
                coach_memory=coach_memory,
                athlete_context=athlete_context,
                athlete_memory_facts=athlete_memory_facts,
                motivation_model=motivation_model,
                performance_recommendation=performance_recommendation,
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
    plan_updates = _filter_plan_updates_for_availability_constraints(
        result.get("plan_updates") or [],
        availability_constraints,
    )
    if plan_updates:
        await plan_pipeline.commit_plan_updates(
            db,
            current_user,
            plan_updates,
            base_plan=plan,
            source="next_ride",
            timezone_name=timezone_name,
        )

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

    async with _token_usage_scope(db, current_user, source="api:process-pending-feedbacks"):
        try:
            login_summary = await ai_service.generate_summary_from_ride_feedbacks(
                rides=rides,
                assessment=assessment_dict,
                training_plan=training_plan or None,
                provider=resolve_user_provider(current_user),
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
