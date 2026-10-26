"""AI routes."""

import asyncio
import hashlib
import json
import logging
import time
from contextlib import AbstractAsyncContextManager, suppress
from datetime import date as _date
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import async_session_maker, get_db
from services import ai_service
from services import assessment_pipeline
from services import athlete_model_inference
from domain import day_preference
from services import metrics_service
from services import plan_inputs
from services import motivation_inference
from services import coach_summary
from services import plan_commitments
from services import (
    climb_power,
    goal_progress,
    illness_return,
    plan_context,
    profile_intent,
    race_intent,
    season_plan,
    stated_ftp,
    week_goals,
)
from domain import athlete_states
from services.athlete_language import athlete_language, interface_language
from services import plan_maintenance
from services import heart_rate_caution
from services import plan_generation
from services import plan_pipeline
from services import ride_ingest
from services import plan_translations
from services import roi_recommendation
from services import single_flight
from services import status_pipeline
from services import rider_identity
from services import reply_notes, summary_pipeline
from services import workout_curiosity
from services.activity_imports import (
    ImportedActivity,
    reported_distance_km,
    reported_elevation_m,
)
from domain.activity_identity import SPORT_CYCLING, are_near_duplicate_activities
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
    project_training_load_from_seed,
)
from domain.fitness_ledger import ledger_from_metric, ledger_from_row
from services.prompts import ride_metrics_context_section
from services.dates import app_today, app_today_iso, request_timezone
from domain.availability import (
    SESSION_CAP,
    constraint_label,
    extract_availability_constraints,
    extract_constraint_lift,
    weekday_label_for,
)
from services.plan_constraints import (
    day_session_cap,
    describe_constraint_overrides,
    filter_plan_updates_for_constraints,
    has_session_cap,
    over_session_cap,
    session_cap_is_that_days_only,
    session_cap_minutes,
)
from services.intervals_service import intervals_activity_id
from services.embeddings import embedding_provider_is_configured
from services.knowledge_topics import topics_for_limiter
from services.rag import (
    knowledge_corpus_is_populated,
    reset_corpus_cache,
    retrieve_cycling_context,
)
from services.ride_matching import (
    done_minutes_by_session,
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

# How many of the newest chat messages a coach turn loads (#284): ten become
# the history, and the note language reads the last five of the athlete's.
CHAT_CONTEXT_MESSAGES = 5 * MAX_CONVERSATION_HISTORY


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


# The prompt inputs the chat shares with plan generation live in plan_context
# (ai-trainer-ops#246).
_race_events_for_prompt = plan_context.race_events_for_prompt
_active_availability_constraints_for_prompt = (
    plan_context.active_availability_constraints
)
_profile_with_availability_constraints = (
    plan_context.profile_with_availability_constraints
)


async def _upcoming_race_count(
    db: AsyncSession, user_id: str, timezone_name: str | None
) -> int:
    """How many races the athlete still has ahead of them (#564).

    Race specificity is worth nothing with an empty calendar, so the utility
    scorer needs this to stop weighting race-ready options for an athlete who
    has stopped racing.
    """
    today = app_today_iso(timezone_name=timezone_name)
    events = await crud.get_race_events(db, user_id)
    return sum(1 for event in events if (event.date or "") >= today)


async def _capture_availability_constraints_from_message(
    db: AsyncSession,
    user_id: str,
    message: str,
    timezone_name: str | None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Sync availability constraints with the athlete's message.

    Lifts requested constraints first so a same-message re-proposed edit sees the
    post-lift state, then captures any newly stated constraints. Returns the
    active constraints for the prompt, the constraints that were lifted (#437),
    and the ones this message stated, as chips to keep or remove
    (ai-trainer-ops#110).
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
    captured = []
    for item in extracted:
        row = await crud.upsert_availability_constraint(db, user_id, **item)
        captured.append(
            {
                "id": row.id,
                "label": constraint_label(
                    row.constraint_type,
                    row.constraint_date,
                    row.required_workout,
                    row.weekday,
                ),
                "removable": True,
            }
        )
    constraints = await _active_availability_constraints_for_prompt(
        db, user_id, timezone_name
    )
    return constraints, lifted, captured


async def _capture_self_reported_signals_from_message(
    db: AsyncSession,
    user_id: str,
    message: str,
) -> tuple[str | None, list[models.AthleteGoal]]:
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

    Returns the new training location's label when it moved, for the
    deterministic confirmation note — the honest record of what the backend
    actually did, independent of the model's prose (the same contract as the
    availability-constraint lift note, #437).
    """
    try:
        async with db.begin_nested():
            await weather_preference.capture_weather_preferences_from_message(
                db, user_id, message
            )
    except Exception:
        logger.warning("Weather-preference capture failed", exc_info=True)

    # What the athlete says they are training for (#563). Same contract as the
    # captures around it: deterministic, best-effort, and unable to overwrite an
    # objective the athlete set by hand.
    try:
        async with db.begin_nested():
            await motivation_inference.capture_motivation_from_message(
                db, user_id, message
            )
    except Exception:
        logger.warning("Motivation capture failed", exc_info=True)

    # A goal with a number on it ("4000 hm in one ride"), so the coach can say
    # how far away it is rather than only repeat it (ai-trainer-ops#65).
    # One savepoint per goal: a write that fails (say, the one-active-goal index
    # losing a race with a second message) costs that goal only, never the
    # captures after it on the same session.
    captured_goals: list[models.AthleteGoal] = []
    for stated in goal_progress.extract_goals(message):
        try:
            async with db.begin_nested():
                goal = await crud.upsert_active_goal(
                    db,
                    user_id,
                    metric=stated.metric,
                    scope=stated.scope,
                    target=stated.target,
                    statement=stated.statement,
                )
                captured_goals.append(goal)
        except Exception:
            logger.warning("Goal capture failed", exc_info=True)

    try:
        async with db.begin_nested():
            row = await home_location.capture_home_location_from_message(
                db, user_id, message
            )
    except Exception:
        logger.warning("Home-location capture failed", exc_info=True)
        return None, captured_goals
    if row is None:
        return None, captured_goals

    # A new base invalidates the cached forecast for the old one.
    clear_forecast_cache()
    return row.label or f"{row.latitude:.2f}, {row.longitude:.2f}", captured_goals


async def _capture_athlete_states(
    db: AsyncSession, user_id: str, message: str, timezone_name: str | None
) -> tuple[list[dict], models.AthleteState | None]:
    """Store what the athlete says about being ill or tired, for the reply to show (#18).

    Before the prompt is built, so this turn's plan already sees it. Kept as a
    prior, never a guard (#39 Q1): ``plan_priors`` reports, the coach decides.
    Best-effort like the captures above: a failed write costs the state only.
    Returns the chips, and the illness this message ended, if any: its rest
    days come back (ai-trainer-ops#232).
    """
    today = app_today(timezone_name=timezone_name)
    captured: list[dict] = []
    ended = None
    try:
        async with db.begin_nested():
            # "Ich war nicht krank" takes the illness back: deleted, with no easy
            # return days, unlike "wieder gesund" (ai-trainer-ops#115).
            if athlete_states.says_not_ill(message):
                retracted = await crud.retract_illness(db, user_id, today)
                if retracted is not None:
                    captured.append(
                        {
                            "id": retracted,
                            "label": athlete_states.retracted_label(),
                            "removable": False,
                        }
                    )
            elif athlete_states.says_recovered(message):
                ended = await crud.end_illness(db, user_id, today)
                if ended is not None and ended.ends_on:
                    captured.append(
                        {
                            "id": ended.id,
                            "label": athlete_states.recovered_label(
                                _date.fromisoformat(ended.ends_on)
                            ),
                            "removable": False,
                        }
                    )
            for stated in athlete_states.extract_states(message, today):
                row = await crud.record_state(db, user_id, stated, today)
                # "Ich war krank, seit gestern fieberfrei" both ends the stored
                # illness and states it ended: one row, so one chip.
                if any(chip["id"] == row.id for chip in captured):
                    continue
                captured.append(
                    {
                        "id": row.id,
                        "label": athlete_states.label(
                            row.kind,
                            _date.fromisoformat(row.starts_on),
                            _date.fromisoformat(row.ends_on) if row.ends_on else None,
                        ),
                        "removable": True,
                    }
                )
    except Exception:
        logger.warning("Athlete-state capture failed", exc_info=True)
        return [], None
    return captured, ended


async def _restore_illness_days(
    db: AsyncSession,
    user: models.User,
    plan: list[dict],
    ended: models.AthleteState | None,
    timezone_name: str | None,
) -> tuple[list[dict], dict[tuple[str, int], dict], _date | None]:
    """Give back the sessions the illness that just ended turned into rest
    (ai-trainer-ops#232, the owner's (a)): from the plan history, easy inside the
    easy days. Before the prompt is built, so the coach sees the plan they are in.
    Returns the plan as it now stands, the sessions that came back, and the last
    easy day. Best-effort: a failed write costs the restore, not the reply.
    """
    if ended is None or not ended.ends_on or not plan:
        return plan, {}, None
    today = app_today(timezone_name=timezone_name)
    easy_until = _date.fromisoformat(ended.ends_on) + timedelta(
        days=athlete_states.RETURN_EASY_DAYS
    )
    try:
        async with db.begin_nested():
            restored = await illness_return.restored_sessions(
                db, user, ended, plan, today=today, easy_until=easy_until
            )
            if not restored:
                return plan, {}, None
            commit = await plan_pipeline.commit_plan(
                db,
                user,
                [restored.get(schemas.session_key(day), day) for day in plan],
                base_plan=plan,
                source="illness_return",
                timezone_name=timezone_name,
            )
        return commit.plan, restored, easy_until
    except Exception:
        logger.warning("The illness's rest days could not be restored", exc_info=True)
        return plan, {}, None


# The chip under the reply, and the id its Remove sends (ai-trainer-ops#184).
HEART_RATE_CAUTION_ID = "heart-rate-caution"
HEART_RATE_OFF_LABEL = (
    "Heart-rate targets are off: your plan steers by effort and power"
)
HEART_RATE_BACK_LABEL = "Heart-rate targets can come back in new sessions"


async def _capture_heart_rate_caution(
    db: AsyncSession,
    user: models.User,
    plan: list[dict],
    message: str,
    timezone_name: str | None,
) -> tuple[list[dict], list[dict], bool]:
    """A medication that blunts the heart rate, said in chat (ai-trainer-ops#184).

    Applied at once, with a chip that says so and can take it back: a bpm target
    the athlete can no longer reach is a safety point, not a matter of taste,
    and stripping it costs no model call. Before the prompt is built, so this
    turn's coach already sees the plan without them. Returns the chips, the
    plan as it now stands, and whether this stripped it: the reply then carries
    that plan (ai-trainer-ops#226).
    """
    try:
        if heart_rate_caution.says_stopped(message) and user.heart_rate_targets_off:
            user.heart_rate_targets_off = False
            await db.flush()
            return (
                [
                    {
                        "id": HEART_RATE_CAUTION_ID,
                        "label": HEART_RATE_BACK_LABEL,
                        "removable": False,
                    }
                ],
                plan,
                False,
            )
        if not heart_rate_caution.says_blunted(message):
            return [], plan, False
        user.heart_rate_targets_off = True
        await db.flush()
        stripped = False
        if plan and any(heart_rate_caution.sets_heart_rate_target(d) for d in plan):
            # A failed strip must not cost the flag or the session: the gate
            # still keeps every later write free of targets (review of #998).
            try:
                async with db.begin_nested():
                    commit = await plan_pipeline.commit_plan(
                        db,
                        user,
                        plan,
                        base_plan=plan,
                        source="heart_rate_caution",
                        timezone_name=timezone_name,
                    )
                plan = commit.plan
                stripped = True
            except Exception:
                logger.warning(
                    "Heart-rate targets could not be stripped now", exc_info=True
                )
        return (
            [
                {
                    "id": HEART_RATE_CAUTION_ID,
                    "label": HEART_RATE_OFF_LABEL,
                    "removable": True,
                }
            ],
            plan,
            stripped,
        )
    except Exception:
        logger.warning("Heart-rate caution capture failed", exc_info=True)
        return [], plan, False


# The router's own notes under a coach reply, in the athlete's language: an
# English "(Note: …)" under a German reply read as a system message, not as
# the coach (ai-trainer-ops#74 follow-up). Each returns the note's sentence;
# the reply carries it as a note beside its text (ai-trainer-ops#262).
_GERMAN_WEEKDAYS = {
    "monday": "Montag",
    "tuesday": "Dienstag",
    "wednesday": "Mittwoch",
    "thursday": "Donnerstag",
    "friday": "Freitag",
    "saturday": "Samstag",
    "sunday": "Sonntag",
}


def _location_note(where: str, german: bool) -> str:
    if german:
        return (
            f"Ich habe deinen üblichen Trainingsort auf {where} gesetzt. "
            f"Wetter und wetterbedingte Planänderungen richten sich jetzt danach."
        )
    return (
        f"I've set your usual training location to {where}, so your weather "
        f"outlook and any weather-driven plan changes now use that."
    )


def _unresolved_dates_note(joined: str, german: bool) -> str:
    if german:
        return (
            f"{joined} konnte ich nicht ändern — der Tag gehört nicht "
            f"zu deinem aktuellen Trainingsplan, es wurde nichts gespeichert."
        )
    return (
        f"I could not update {joined} — that day is not part of your "
        f"current training plan, so no change was saved."
    )


def _illness_return_note(
    restored: dict[tuple[str, int], dict], easy_until: _date, german: bool
) -> str:
    """Name the sessions an ended illness gave back, and which of them are easy
    (ai-trainer-ops#232). Fixed words: the router wrote them, not the coach."""
    days = []
    for iso in sorted({key[0] for key in restored}):
        label = (
            _day_label({"date": iso, "weekday": weekday_label_for(iso)}, german)
            + f" {iso}"
        )
        if _date.fromisoformat(iso) <= easy_until:
            label += " (locker)" if german else " (easy)"
        days.append(label)
    if german:
        return f"Diese Einheiten sind wieder in deinem Plan: {', '.join(days)}."
    return f"These sessions are back in your plan: {', '.join(days)}."


def _filter_plan_updates_for_availability_constraints(
    updates: list[dict], constraints: list[dict]
) -> list[dict]:
    return filter_plan_updates_for_constraints(updates, constraints)


def _day_label(override: dict, german: bool = False) -> str:
    """Human-friendly day label for a constraint override note."""
    weekday = override.get("weekday")
    if weekday:
        if german:
            return _GERMAN_WEEKDAYS.get(str(weekday).lower(), str(weekday).capitalize())
        return str(weekday).capitalize()
    return str(override.get("date") or ("diesen Tag" if german else "that day"))


def _format_constraint_override_note(
    overrides: list[dict], german: bool = False
) -> str:
    """Compose an honest note about coach-requested changes a constraint blocked.

    The plan pipeline enforces hard availability constraints deterministically,
    so these requests never land as asked. Without this, the coach would falsely
    confirm the change (#414).
    """
    required = [o for o in overrides if o.get("constraintType") == "required_workout"]
    unavailable = [o for o in overrides if o.get("constraintType") == "no_training"]
    capped = [o for o in overrides if o.get("constraintType") == SESSION_CAP]
    sentences: list[str] = []
    # One sentence per limit: a weekday may have its own (ai-trainer-ops#231).
    for cap in dict.fromkeys(o.get("capMinutes") for o in capped):
        days = ", ".join(
            _day_label(o, german) for o in capped if o.get("capMinutes") == cap
        )
        sentences.append(
            f"{days} habe ich auf {cap} min gekürzt: Das ist deine Grenze pro Einheit."
            if german
            else f"I kept {days} to {cap} min: that is your limit per session."
        )
    if required and german:
        days = ", ".join(_day_label(o, german) for o in required)
        sentences.append(
            f"{days} konnte ich nicht ändern: Eine deiner Verfügbarkeitsvorgaben legt "
            f"dort eine Pflichteinheit fest, also bleibt der Plan so. Sag Bescheid, "
            f"wenn ich die Vorgabe aufheben soll."
        )
    elif required:
        days = ", ".join(_day_label(o) for o in required)
        sentences.append(
            f"I couldn't change {days}: it's pinned as a required session by one of "
            f"your availability constraints, so the plan keeps it as-is. Let me know "
            f"if you'd like to lift that constraint."
        )
    if unavailable and german:
        days = ", ".join(_day_label(o, german) for o in unavailable)
        sentences.append(
            f"Für {days} konnte ich kein Training einplanen: Eine deiner "
            f"Verfügbarkeitsvorgaben markiert den Tag als nicht verfügbar."
        )
    elif unavailable:
        days = ", ".join(_day_label(o) for o in unavailable)
        sentences.append(
            f"I couldn't schedule training on {days}: it's marked unavailable by one "
            f"of your availability constraints."
        )
    return " ".join(sentences)


def _format_session_cap_note(
    dates: list[str], constraints: list[dict], german: bool = False
) -> str:
    """Say which stored sessions a per-session limit shortened (#185), one
    sentence per limit: a weekday may have its own (ai-trainer-ops#231), and a
    limit said for one day says so, so the athlete can correct it (#295)."""
    every = session_cap_minutes(constraints)
    groups: dict[tuple[int | None, bool], list[str]] = {}
    for d in dates:
        once = session_cap_is_that_days_only({"date": d}, constraints)
        groups.setdefault((day_session_cap({"date": d}, constraints), once), []).append(
            d
        )
    sentences = []
    for (cap, once), group in groups.items():
        days = ", ".join(
            _day_label({"date": d, "weekday": weekday_label_for(d)}, german) + f" {d}"
            for d in group
        )
        if once:
            if german:
                named = " und ".join(
                    f"am {_day_label({'weekday': weekday_label_for(d)}, True)}, {d[8:10]}.{d[5:7]}."
                    for d in group
                )
                sessions = "die Einheit" if len(group) == 1 else "die Einheiten"
                sentences.append(
                    f"Nur {named} hast du höchstens {cap} min, deshalb habe ich {sessions} gekürzt."
                )
            else:
                named = " and ".join(
                    f"{(weekday_label_for(d) or '').capitalize()} {d}" for d in group
                )
                sessions = "that session" if len(group) == 1 else "those sessions"
                sentences.append(
                    f"Only on {named} you have at most {cap} min, so I shortened {sessions}."
                )
            continue
        if cap == every:
            sentences.append(
                f"Deine Grenze von {cap} min pro Einheit gilt jetzt für jede Einheit, "
                f"deshalb habe ich {days} gekürzt."
                if german
                else f"Your limit of {cap} min per session now applies to every session, "
                f"so I shortened {days}."
            )
            continue
        weekdays = list(dict.fromkeys(weekday_label_for(d) or "" for d in group))
        if german:
            named = " und ".join(
                _GERMAN_WEEKDAYS.get(w, w).lower() + "s" for w in weekdays
            )
            sentences.append(
                f"{named[:1].upper()}{named[1:]} hast du höchstens {cap} min, "
                f"deshalb habe ich {days} gekürzt."
            )
        else:
            named = " and ".join(f"{w.capitalize()}s" for w in weekdays)
            sentences.append(
                f"On {named} you have at most {cap} min, so I shortened {days}."
            )
    return " ".join(sentences)


def _format_constraint_lift_note(lifted: list[dict], german: bool = False) -> str:
    """Confirm, deterministically, which availability constraints were lifted.

    The lift is applied by the router before the plan pipeline runs, so this is
    the honest record of what happened — independent of whatever the model says
    in its prose (#437).
    """
    days = ", ".join(_day_label(c, german) for c in lifted)
    if german:
        return (
            f"Ich habe die Verfügbarkeitsvorgabe für {days} aufgehoben, "
            f"der Plan lässt sich dort jetzt ändern."
        )
    plural = "constraints" if len(lifted) > 1 else "constraint"
    return (
        f"I lifted the availability {plural} on {days}, so the plan can now "
        f"be changed there."
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


async def _race_proposal(
    db: AsyncSession, user: models.User, message: str, timezone_name: str | None
) -> dict | None:
    """The offer to clear the race the athlete says is off, or ``None``.

    The button clears the profile's race and every upcoming calendar race, so it
    is offered only when all of them fall in the period the athlete named: "kein
    Rennen dieses Jahr, aber nächstes" must not delete next year's races
    (ai-trainer-ops#129).
    """
    if not (user.race_date or user.training_goal == "race"):
        return None
    today = app_today_iso(timezone_name=timezone_name)
    scope = race_intent.no_race_scope(message, _date.fromisoformat(today))
    if scope is None:
        return None
    upcoming = [e for e in await crud.get_race_events(db, user.id) if e.date >= today]
    races = [_date.fromisoformat(e.date) for e in upcoming]
    # A race already run is history, not a race the offer would clear
    # (review of #934).
    if user.race_date and str(user.race_date)[:10] >= today:
        races.append(_date.fromisoformat(str(user.race_date)[:10]))
    if not all(scope.covers(race) for race in races):
        return None
    return {
        "race_date": user.race_date,
        "race_description": user.race_description,
        # Clearing also removes upcoming calendar races; the button says so.
        "calendar_races": len(upcoming),
    }


async def _race_offer(
    db: AsyncSession, user: models.User, message: str, timezone_name: str | None
) -> dict | None:
    """A race the athlete says is coming, offered for the calendar (ai-trainer-ops#175).

    Only offered, never saved: the date is resolved here ("in 3 weeks") and
    shown, so the athlete can correct it. A race already on that date, in the
    calendar or on the profile, is not offered again.
    """
    today = app_today(timezone_name=timezone_name)
    day = race_intent.stated_race_date(message, today)
    if day is None:
        return None
    iso = day.isoformat()
    if user.race_date and str(user.race_date)[:10] == iso:
        return None
    events = await crud.get_race_events(db, user.id)
    if any(event.date == iso for event in events):
        return None
    offer: dict = {"date": iso}
    if race_intent.stated_race_move(message, today):
        moved = _race_being_moved(user, events, today.isoformat())
        if moved:
            offer["replaces"] = moved
    return offer


def _race_being_moved(user: models.User, events: list, today: str) -> dict | None:
    """The race "the race is moved" means: the profile's when it is ahead, else
    the one race ahead in the calendar. With several, nobody can tell which, so
    the offer adds one as before (#175, comment 5111)."""
    if user.race_date and str(user.race_date)[:10] >= today:
        return {"date": str(user.race_date)[:10], "kind": "profile"}
    ahead = [event for event in events if (event.date or "") >= today]
    if len(ahead) == 1:
        return {"date": ahead[0].date, "kind": "calendar", "event_id": ahead[0].id}
    return None


def _capture_day_preferences(
    user: models.User,
    message: str,
    profile: dict,
    profile_proposal: dict | None,
) -> tuple[dict, dict | None, list[dict]]:
    """Keep or offer the day preferences ``message`` states (ai-trainer-ops#204).

    Returns the profile the coach sees this turn, the profile proposal, and the
    preferences kept, as chips.
    """
    stated = day_preference.extract(message)
    kept = [p for p in stated if day_preference.honoured([p], user.training_days)]
    if kept:
        user.day_preferences = day_preference.merged(user.day_preferences, kept)
        profile = {
            **profile,
            "dayPreferences": [
                day_preference.label(p)
                for p in day_preference.honoured(
                    user.day_preferences, user.training_days
                )
            ],
        }
    if not (profile_proposal or {}).get("training_days"):
        for item in stated:
            swapped = day_preference.swapped_training_days(user.training_days, item)
            if swapped:
                profile_proposal = {
                    **(profile_proposal or {}),
                    "training_days": swapped,
                    "standing_day_swap": True,
                }
                break
    return (
        profile,
        profile_proposal,
        [{"prefer": p["prefer"], "over": p["over"]} for p in kept],
    )


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


async def _record_plan_commitment(
    db: AsyncSession,
    user: models.User,
    proposed: object,
    timezone_name: str | None,
) -> None:
    """Persist the arrangement the coach attached to a plan change (#667).

    Fail-safe by construction: an unusable window or a missing sentence is
    dropped and a storage failure is swallowed. A commitment makes later writes
    *safer*, so failing to record one must never cost the athlete the plan change
    it accompanied — that would trade a small loss for a large one.
    """
    if not isinstance(proposed, dict):
        return
    text = str(proposed.get("text") or "").strip()
    if not text:
        return
    window = plan_commitments.normalize_window(
        proposed.get("startDate"), proposed.get("endDate")
    )
    if window is None:
        return
    try:
        async with db.begin_nested():
            today = app_today_iso(timezone_name=timezone_name)
            await crud.deactivate_expired_plan_commitments(db, user.id, today=today)
            await crud.create_plan_commitment(
                db,
                user.id,
                start_date=window[0],
                end_date=window[1],
                text=text,
                source="coach_chat",
            )
    except Exception:  # noqa: BLE001 — never let this cost the plan change
        logger.warning("could not record plan commitment", exc_info=True)


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
        async with db.begin_nested():
            race_events = await _race_events_for_prompt(db, user.id)
            weather_section = await training_weather_context_for_user(db, user.id)
            availability_constraints = (
                await _active_availability_constraints_for_prompt(
                    db, user.id, timezone_name
                )
            )
            profile = schemas.UserProfileSchema.from_user(user).model_dump(
                by_alias=True
            )
            profile = _profile_with_availability_constraints(
                profile, availability_constraints
            )
            # The same context the chat coach and the nightly run get, so all three
            # plan for a week they can actually see the history of (#666).
            writer_context = await plan_context.plan_writer_context(
                db, user.id, plan, timezone_name=timezone_name
            )
            plan_updates = await ai_service.adapt_training_plan(
                plan,
                [feedback_entry],
                profile,
                provider=provider,
                rider_assessment=rider_assessment,
                race_events=race_events,
                weather_context_section=weather_section,
                timezone_name=timezone_name,
                plan_change_history=writer_context.change_history,
                plan_coherence_warnings=writer_context.coherence,
                plan_commitments=writer_context.commitments,
                # Held to the hard priors once (ai-trainer-ops#87).
                prior_check=plan_pipeline.update_prior_check(
                    writer_context.prior_check, plan
                ),
                # What the athlete said in the chat (ai-trainer-ops#152).
                athlete_words=await plan_context.athlete_words_for_user(
                    db, user.id, timezone_name=timezone_name
                ),
            )
            proposed_plan = plan_maintenance.apply_maintenance_updates(
                plan, plan_updates, app_today_iso(timezone_name=timezone_name)
            )
            if proposed_plan is None:
                return
            commit = await plan_pipeline.commit_plan(
                db,
                user,
                proposed_plan,
                base_plan=plan,
                source="auto_adapt",
                timezone_name=timezone_name,
            )
        # The narration in a savepoint of its own (#281): if it fails, the plan
        # the athlete's feedback changed still stands, as it did before.
        async with db.begin_nested():
            await coach_summary.narrate_plan_changes(
                db,
                user,
                batch_id=commit.batch_id,
                source="auto_adapt",
                applied_changes=commit.applied_changes,
                profile=profile,
                rider_assessment=rider_assessment,
            )
    except Exception:
        logger.warning("Auto-adaptation after flagged workout failed", exc_info=True)


@router.post("/analyse-activities", response_model=schemas.AnalyseActivitiesResponse)
async def analyse_activities(
    request: Request,
    body: schemas.AnalyseActivitiesRequest,
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

    # Normalise text fields: the LLM may return dicts or lists instead of strings.
    # The DB columns and Pydantic schema both expect plain strings.
    for _field in ("rideInsights", "lastRideFeedback", "loginSummary", "notes"):
        _val = result.get(_field)
        if _val is not None and not isinstance(_val, str):
            result[_field] = json.dumps(_val)

    # Validate the model output before any persistence or the per-ride review
    # calls below (which spend more tokens): a malformed assessment is an
    # unprocessable upstream result (422), caught here so the rollback does not
    # throw away — and a retry does not repeat — that extra spend (ops#132).
    try:
        assessment_schema = schemas.RiderAssessmentSchema.model_validate(result)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The analysis could not be parsed.",
        ) from exc

    # The new inputs clear what was built from the old ones, the status badge
    # included, which this write used to leave as it was (ai-trainer-ops#245).
    await assessment_pipeline.record(
        db,
        current_user,
        estimated_ftp=result.get("estimatedFTP"),
        rider_type=result.get("riderType"),
        notes=result.get("notes"),
        hr_zones=result.get("hrZones"),
        ride_insights=result.get("rideInsights"),
        last_ride_feedback=result.get("lastRideFeedback"),
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
                distance_km=reported_distance_km(a_dict.get("distance")),
                elevation_gain_m=reported_elevation_m(
                    a_dict.get("total_elevation_gain")
                ),
                streams=streams_by_id.get(str(activity.id), {}),
                weather=weather_by_id.get(activity.id, {}),
                summary_avg_power_w=a_dict.get("average_watts"),
                summary_normalized_power_w=a_dict.get("weighted_average_watts"),
                # The only intensity figure a stream-less activity carries (#579).
                summary_avg_hr_bpm=_optional_int(
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
            if body.source == "intervals":
                logger.info(
                    "Intervals analysis persisted ride metrics user=%s rides_input=%s metrics=%s metric_ids=%s",
                    current_user.id,
                    len(rides_input),
                    ingested.stored,
                    [ride["strava_activity_id"] for ride in rides_input[:10]],
                )

        elif body.source == "intervals":
            logger.warning(
                "Intervals analysis produced no ride inputs user=%s activity_count=%s sample=%s",
                current_user.id,
                len(analysis_activities),
                _analysis_activity_log_sample(analysis_activities),
            )

    # The analysis wrote the login summary from these inputs and these rides, so
    # it is stored once both notifications have cleared the old one, and without
    # a notification of its own (ai-trainer-ops#245).
    if result.get("loginSummary"):
        await assessment_pipeline.record(
            db,
            current_user,
            notify=False,
            estimated_ftp=result.get("estimatedFTP"),
            login_summary=result.get("loginSummary"),
        )

    await db.flush()

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
) -> list[dict]:
    """Generate the plan, with the domain's errors as the statuses the app reads."""
    try:
        return await plan_generation.generate_plan_for_user(
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
        lambda: _generate_plan_for_user(db, current_user, timezone_name),
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
        lambda: _generate_plan_for_user(db, current_user, timezone_name),
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
        language=code, translations=known, missing=bool(missing)
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
            language=code, translations=translations, missing=missing
        )

    return await single_flight.run_single_flight(
        _plan_translations_inflight, current_user.id, _write
    )


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
    (
        availability_constraints,
        lifted_constraints,
        captured_constraints,
    ) = await _capture_availability_constraints_from_message(
        db, current_user.id, body.question, timezone_name
    )
    # Capture weather self-reports before the forecast is read, so a location the
    # athlete states in this very message anchors this turn's outlook (#495).
    new_location, captured_goals = await _capture_self_reported_signals_from_message(
        db, current_user.id, body.question
    )
    captured_states, ended_illness = await _capture_athlete_states(
        db, current_user.id, body.question, timezone_name
    )
    plan, illness_restored, illness_easy_until = await _restore_illness_days(
        db, current_user, plan, ended_illness, timezone_name
    )
    heart_rate_chips, plan, heart_rate_stripped = await _capture_heart_rate_caution(
        db, current_user, plan, body.question, timezone_name
    )
    captured_states = [*captured_states, *heart_rate_chips]
    # No max HR to build zones from while a medication blunts it (#184).
    profile = heart_rate_caution.prompt_profile(
        profile, current_user.heart_rate_targets_off
    )
    # Offered, never applied: the athlete confirms in the chat (ops#71, #63 Q21).
    race_proposal = await _race_proposal(db, current_user, body.question, timezone_name)
    # A race said to be coming is offered for the calendar (ai-trainer-ops#175);
    # never in the same reply that offers to clear the races.
    race_offer = (
        None
        if race_proposal
        else await _race_offer(db, current_user, body.question, timezone_name)
    )
    # Hours, days, sports, level or max heart rate said in chat: offered, never
    # applied (ops#43, #39, #202).
    profile_proposal = profile_intent.propose_profile_change(
        body.question,
        weekly_hours=current_user.weekly_hours,
        training_days=current_user.training_days,
        sports=current_user.sports,
        fitness_level=current_user.fitness_level,
        max_heart_rate=current_user.max_heart_rate,
        resting_heart_rate=current_user.resting_heart_rate,
    )
    # An FTP said in chat is offered too, not set (ai-trainer-ops#129, the owner's
    # choice of 2026-10-18). It moves every power target in the plan, so a
    # sentence read wrongly must not change them without a tap; it was set at
    # once with a note before (#70).
    stated_ftp_watts = stated_ftp.extract_stated_ftp(body.question)
    if stated_ftp_watts is not None and stated_ftp_watts != current_user.current_ftp:
        profile_proposal = {**(profile_proposal or {}), "ftp": stated_ftp_watts}
    # A weekday preferred over another (ai-trainer-ops#204). Kept when the plan
    # can act on it, both days being training days; offered as a swap of the
    # training days when it is general ("generally", "meistens") and the
    # preferred day is not one. Shown under the reply either way.
    profile, profile_proposal, captured_day_preferences = _capture_day_preferences(
        current_user, body.question, profile, profile_proposal
    )
    profile = _profile_with_availability_constraints(profile, availability_constraints)
    rider_assessment = None
    if current_user.rider_assessment is not None:
        rider_assessment = schemas.RiderAssessmentSchema.model_validate(
            current_user.rider_assessment, from_attributes=True
        ).model_dump(by_alias=True)
    memory_enabled = current_user.memory_updates_enabled
    coach_memory_row = await crud.get_coach_memory(db, current_user.id)
    coach_memory = (
        (coach_memory_row.memory if coach_memory_row is not None else "")
        if memory_enabled
        else ""
    )
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
            async with db.begin_nested():
                # Only a message about a session (ai-trainer-ops#216).
                if await workout_curiosity.reports_a_session(
                    db,
                    current_user.id,
                    today=app_today(timezone_name=timezone_name),
                    context_workout=body.context_workout,
                ):
                    workout_curiosity_context = (
                        await workout_curiosity.curiosity_for_message(
                            db,
                            current_user.id,
                            body.question,
                            weights=(motivation_model or {}).get("weights"),
                        )
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
        await crud.list_athlete_inquiries(db, current_user.id) if memory_enabled else []
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
        hypothesis_rows = await crud.get_prompt_athlete_hypotheses(db, current_user.id)
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
            async with db.begin_nested():
                rider_identity_context = await rider_identity.identity_for_prompt(
                    db, current_user.id, performance_model=performance_model
                )
        except Exception:
            logger.warning("Rider-identity read failed", exc_info=True)

    # The newest messages only (#284): the history below takes the last ten.
    chat_messages = await crud.get_chat_messages(
        db, current_user.id, limit=CHAT_CONTEXT_MESSAGES
    )
    conversation_history = [
        {"role": msg.role, "content": msg.content}
        for msg in chat_messages[-MAX_CONVERSATION_HISTORY:]
    ]
    # The reply's language, decided once: the coach is told it and the router's
    # own notes are written in it. Until #262 the router read the last five of
    # the athlete's messages and the service the last ten.
    reply_language = athlete_language(
        body.question,
        [m["content"] for m in conversation_history if m["role"] == "user"],
    ) or interface_language(request)
    notes_in_german = reply_language == "German"

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
            recent_metrics = await crud.get_ride_metrics_history(
                db, current_user.id, limit=30
            )
            # Keep the full 30-ride window — trend questions are answered from the
            # metrics lines — but stop replaying the coach's own older notes back
            # to it on every message (#513).
            metrics_section = ride_metrics_context_section(
                recent_metrics,
                timezone_name=timezone_name,
                prose_window=RIDE_NOTE_PROSE_WINDOW,
            )
            # How the plan got to its current state (#652), and which adjacent
            # days already collide (#659). Built by the shared helper so the
            # nightly run and auto-adapt see exactly what the chat coach sees —
            # they used to see neither, which is #666.
            writer_context = await plan_context.plan_writer_context(
                db, current_user.id, plan, timezone_name=timezone_name
            )
            plan_changes_section = writer_context.change_history
            coherence_section = writer_context.coherence
            # The same sections, for the athlete to read under the reply (#17).
            coach_saw = plan_context.coach_saw(
                writer_context.sections, settings.git_sha or "develop"
            )
            race_events = await _race_events_for_prompt(db, current_user.id)
            # Climb watts from the app, which the coach quotes (ai-trainer-ops#188).
            climb_section = climb_power.prompt_section(
                climb_power.known_climbs(
                    [
                        body.question,
                        *[
                            m["content"]
                            for m in reversed(conversation_history)
                            if m["role"] == "user"
                        ],
                    ],
                    current_user.race_date,
                    current_user.race_description,
                    race_events,
                    app_today_iso(timezone_name=timezone_name),
                ),
                current_user.weight_kg,
                current_user.current_ftp,
            )
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
                interface_language=interface_language(request),
                reply_language=reply_language,
                workout_curiosity=workout_curiosity_context,
                rider_identity=rider_identity_context,
                plan_changes_section=plan_changes_section,
                plan_coherence_warnings=coherence_section,
                plan_commitment_notes=writer_context.commitments,
                # What a done day's rides took, beside what was planned.
                done_minutes=done_minutes_by_session(recent_metrics),
                # The hard priors, the stated hours and the states are held after
                # the reply is written, on the days its planUpdates change
                # (ai-trainer-ops#87, #103 Q37), and the race and state priors on
                # any day of the coming week (ai-trainer-ops#103 Q33, Q37).
                prior_check=plan_pipeline.update_prior_check(
                    writer_context.prior_check,
                    plan,
                    today=app_today(timezone_name=timezone_name),
                ),
                app_offers=_offers_for_prompt(
                    race_offer, race_proposal, profile_proposal
                ),
                climb_section=climb_section,
            )
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
        finally:
            # Never leave the classification task pending/unawaited (#450): if we
            # bailed out before awaiting it above, cancel and drain it here.
            if classify_task is not None and not classify_task.done():
                classify_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await classify_task

    # The app's own notes under the reply, never in its text (ai-trainer-ops#262).
    notes: list[reply_notes.ReplyNote] = list(result.get("notes") or [])

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
            notes.append(
                reply_notes.note(
                    reply_notes.UNRESOLVED_DATES,
                    _unresolved_dates_note(joined, notes_in_german),
                )
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
            notes.append(
                reply_notes.note(
                    reply_notes.CONSTRAINT_OVERRIDE,
                    _format_constraint_override_note(overrides, notes_in_german),
                )
            )
            # Remember which constraints blocked this turn so a follow-up "lift
            # that constraint" can resolve "that" to them (#437).
            flagged_constraint_dates = [o["date"] for o in overrides if o.get("date")]

    # A per-session limit holds for every stored session from now on, not only
    # for the days the coach changed: say which ones it shortens, and make sure
    # the write below happens even when the coach changed nothing (#185).
    capped_dates: list[str] = []
    if plan and has_session_cap(availability_constraints):
        updated_dates = {u.get("date") for u in requested_updates}
        # The gate's own test, today included, so the note names exactly the
        # sessions the gate shortens (review 3 of #1000).
        capped_dates = sorted(
            {
                str(d["date"])
                for d in plan
                if isinstance(d, dict)
                and d.get("date") not in updated_dates
                and over_session_cap(d, availability_constraints)
            }
        )
        if capped_dates:
            notes.append(
                reply_notes.note(
                    reply_notes.SESSION_CAP,
                    _format_session_cap_note(
                        capped_dates, availability_constraints, notes_in_german
                    ),
                )
            )

    # The illness's rest days came back before the coach answered; say which, in
    # fixed words (ai-trainer-ops#232).
    if illness_restored and illness_easy_until is not None:
        notes.append(
            reply_notes.note(
                reply_notes.ILLNESS_RETURN,
                _illness_return_note(
                    illness_restored, illness_easy_until, notes_in_german
                ),
            )
        )

    # Coach honesty: confirm a lift deterministically, independent of the model's
    # prose, so "pls lift that constraint" is not silently ignored (#437).
    if lifted_constraints:
        notes.append(
            reply_notes.note(
                reply_notes.CONSTRAINT_LIFT,
                _format_constraint_lift_note(lifted_constraints, notes_in_german),
            )
        )

    # Coach honesty: the training location was moved by the router, before the model
    # replied, so confirm it deterministically rather than hoping the prose does (#495).
    if new_location:
        notes.append(
            reply_notes.note(
                reply_notes.LOCATION, _location_note(new_location, notes_in_german)
            )
        )

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
    if not body.already_in_chat:
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
        notes=notes or None,
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

    # Record the arrangement *before* the plan write, so the guard protecting a
    # committed day is already in force for the very write that agreed it — a
    # coach that reschedules Thursday and commits to it in one turn must not have
    # its own Thursday edit blocked, and commit_plan_updates does not respect
    # pins, so it goes through (#667).
    await _record_plan_commitment(
        db, current_user, result.get("planCommitment"), timezone_name
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
    # The coach changed nothing, or changed nothing that went through, yet the
    # stored plan breaks the athlete's limit: the same gate shortens it (#185).
    # Keyed on the stored plan alone, so a rejected coach edit of an over-limit
    # day is shortened too (review 4 of #1000).
    stored = persisted_updated_plan if persisted_updated_plan is not None else plan
    if any(
        isinstance(d, dict) and over_session_cap(d, availability_constraints)
        for d in stored
    ):
        persisted_updated_plan = (
            await plan_pipeline.commit_plan(
                db,
                current_user,
                stored,
                base_plan=stored,
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

    # The caution above stripped the stored plan before the coach saw it, and
    # neither the coach nor the cap wrote it again: the reply carries it. Without
    # it the screen kept "Puls unter 130 bpm" under a reply that had just set
    # heart rate aside, until the next load (ai-trainer-ops#226).
    if persisted_updated_plan is None and (heart_rate_stripped or illness_restored):
        persisted_updated_plan = plan

    if persisted_updated_plan is not None:
        result["updated_plan"] = persisted_updated_plan

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

    # What the router captured or proposes, for the athlete to confirm or undo
    # under the reply (ops#65, #71). Built from rows, never from the model.
    result["captured_goals"] = [
        {
            "id": goal.id,
            "label": goal_progress.goal_name(goal.metric, goal.scope, goal.target),
        }
        for goal in captured_goals
    ]
    result["race_proposal"] = race_proposal
    result["race_offer"] = race_offer
    result["profile_proposal"] = profile_proposal
    result["captured_states"] = captured_states
    result["captured_constraints"] = captured_constraints
    result["captured_day_preferences"] = captured_day_preferences
    result["coach_saw"] = coach_saw
    result["notes"] = notes
    return schemas.AskTrainerResponse.model_validate(result)


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
