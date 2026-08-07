"""Backend AI service.

This is a focused Python port of the frontend ai.ts service. It keeps prompt
content and response shapes aligned with the existing UI so the frontend can
switch to backend fetches without semantic changes.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
from typing import Any

from json_repair import repair_json
from pydantic import ValidationError

import schemas

from . import metrics, token_accounting
from .coach_schema import COACH_REPLY_SCHEMA
from .analysis import (
    AVG_POWER_TO_FTP_RATIO,
    LTHR_RATIO,
    _time_in_power_zones,
    build_ride_analysis,
    compute_ftp_from_streams,
    compute_hr_zones,
    compute_training_load,
)
from .llm import (
    AIRateLimitError,
    TASK_CLASSIFY,
    TASK_COACH,
    TASK_FEEDBACK,
    TASK_PLAN,
    get_provider,
)  # re-exported for backward compat
from .dates import (
    app_date_context,
    app_today,
    app_today_iso,
    app_today_stamp,
    plan_day_date_labels,
)
from .prompts import (
    COACH_PERSONA,
    analyse_activities_computed_section,
    analyse_activities_system,
    analyse_activities_user,
    generate_plan_system,
    generate_plan_user,
    adapt_plan_system,
    adapt_plan_user,
    plan_change_summary_system,
    plan_change_summary_user,
    race_events_context_section,
    ask_trainer_assessment_section,
    ask_trainer_classify_system,
    ask_trainer_classify_user,
    ask_trainer_workout_section,
    ask_trainer_plan_updates_rule,
    ask_trainer_system_sections,
    update_memory_system,
    update_memory_user,
    derive_athlete_model_system,
    derive_athlete_model_user,
    detect_contradictions_system,
    detect_contradictions_user,
    extract_athlete_facts_system,
    extract_athlete_facts_user,
    generate_athlete_hypotheses_system,
    generate_athlete_hypotheses_user,
    generate_athlete_insights_system,
    generate_athlete_insights_user,
    generate_open_questions_system,
    generate_open_questions_user,
    generate_inquiries_system,
    generate_inquiries_user,
    evaluate_inquiry_answer_system,
    evaluate_inquiry_answer_user,
    generate_validation_experiments_system,
    generate_validation_experiments_user,
    generate_athlete_predictions_system,
    generate_athlete_predictions_user,
    evaluate_athlete_predictions_system,
    evaluate_athlete_predictions_user,
    match_observations_system,
    match_observations_user,
    rate_workout_system,
    rate_workout_user,
    refresh_login_summary_system,
    refresh_login_summary_user,
    training_status_system,
    training_status_user,
    TRAINING_STATUS_LABEL_MAX_CHARS,
    TRAINING_STATUS_TONES,
    batch_review_system,
    batch_review_user,
    next_ride_recommendation_system,
    next_ride_recommendation_user,
    process_pending_feedbacks_system,
    process_pending_feedbacks_user,
)

MAX_CONVERSATION_HISTORY = 10
MAX_PLAN_DAYS_PAST = 7
MAX_PLAN_DAYS_AHEAD = 7
MAX_COACH_MEMORY_CHARS = 800

logger = logging.getLogger(__name__)


class AIResponseFormatError(Exception):
    """Raised when the LLM returns a successful response without usable content."""


# Empty/blank coach replies are usually transient provider blips, so retry the
# call a couple of times with exponential backoff before surfacing the failure.
ASK_TRAINER_EMPTY_RESPONSE_RETRIES = 2
ASK_TRAINER_RETRY_BASE_DELAY = 0.5


def _coach_prose_reply(raw: str) -> str | None:
    """The answer inside a coach reply that ignored the JSON contract, if any.

    ``json_mode`` is not a guarantee. When the model answers the athlete in
    plain prose, ``repair_json`` returns an empty string — it repairs a
    *truncated* object happily, so an empty repair means there was no structure
    there to begin with — and the parse then raises (#558).

    Prose is not retried. The same question produced prose on three consecutive
    requests in production, so another attempt mostly buys another 17k input
    tokens; and the prose is already the coach's answer. It is passed through as
    the reply with no plan updates, which is not a loss: there is no structure
    to read updates from either way.

    Returns ``None`` for anything that looks like broken JSON rather than an
    answer, or that carries no word at all — those belong on the retry path.
    """
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    candidate = (fenced.group(1) if fenced else raw).strip()
    if not candidate or candidate.startswith(("{", "[")):
        return None
    # At least one real word, so "..." or "42" is not mistaken for an answer.
    if not re.search(r"[^\W\d_]{3,}", candidate):
        return None
    return candidate


def _coach_result(parsed: dict, response: str) -> dict:
    """Shape one coach reply for the router; *parsed* is empty for prose."""
    return {
        "response": response,
        "plan_updates": parsed.get("planUpdates"),
        "sources": parsed.get("sources") or [],
        "ride_note_update": parsed.get("ride_note_update"),
        "ride_label_update": parsed.get("ride_label_update"),
        "physiology_rationale": _clean_rationale(parsed.get("physiologyRationale")),
        "context_rationale": _clean_rationale(parsed.get("contextRationale")),
        "objective_rationale": _clean_rationale(parsed.get("objectiveRationale")),
    }


def _clean_rationale(value: object) -> str | None:
    """Normalise an optional rationale field to a trimmed string or ``None``."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


_SLIM_PLAN_KEEP = {
    "date",
    "workoutType",
    "workout_type",
    "title",
    "durationMinutes",
    "duration_minutes",
    "durationMinMinutes",
    "durationMaxMinutes",
    "targetPower",
    "target_power",
    "completed",
}


def _plan_date_labels(
    raw_date: object, today_date: datetime.date | None = None
) -> dict:
    """Backward-compatible alias for :func:`services.dates.plan_day_date_labels`.

    The canonical implementation now lives in ``services.dates`` so every
    plan-consuming prompt shares one date-anchoring source; this thin wrapper
    preserves existing call sites and tests.
    """
    return plan_day_date_labels(raw_date, today_date)


def _slim_plan_entry(
    entry: dict, today_date: datetime.date | None = None
) -> dict:
    """Return a compact version of a plan day with only fields needed for chat context."""
    slim = {k: v for k, v in entry.items() if k in _SLIM_PLAN_KEEP and v is not None}
    slim.update(_plan_date_labels(slim.get("date"), today_date))
    return slim


def _event_date(value: dict) -> datetime.date | None:
    raw = value.get("date")
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(str(raw))
    except ValueError:
        return None


def _next_race_date(
    profile: dict,
    race_events: list[dict] | None,
    timezone_name: str | None = None,
) -> datetime.date | None:
    today = app_today(timezone_name=timezone_name)
    candidates: list[datetime.date] = []
    profile_race = profile.get("raceDate")
    if profile_race:
        try:
            candidates.append(datetime.date.fromisoformat(str(profile_race)))
        except ValueError:
            pass
    for event in race_events or []:
        parsed = _event_date(event)
        if parsed is not None:
            candidates.append(parsed)
    upcoming = [date for date in candidates if date >= today]
    return min(upcoming) if upcoming else None


# Models sometimes write a unit into a numeric field — ``"durationMinutes": 180
# minutes`` — which is not JSON. The unit is dropped, but *only* directly after
# the colon that opens a value: an earlier version matched anywhere in the
# document and quietly ate the unit out of the coach's own prose, turning
# "Wir fahren am Samstag 4 Stunden, danach Pause" into "am Samstag 4, danach
# Pause" for every athlete reading it (#516, fixed in #558).
_UNIT_SUFFIXED_NUMBER = re.compile(r":(\s*-?\d+(?:\.\d+)?)\s+[a-zA-Z_]+(?=\s*[,}\]\n])")


def _parse_ai_json(text: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    extracted = fenced.group(1).strip() if fenced else text.strip()
    stripped = _UNIT_SUFFIXED_NUMBER.sub(r":\1", extracted)
    repaired = repair_json(stripped)
    if repaired != stripped:
        # Reported through token_accounting so the line carries the task, model
        # and prompt_sha of the call that produced the malformed JSON — a
        # prompt that keeps needing repair is a prompt to fix (#516).
        token_accounting.note_json_repair(len(stripped), len(repaired))
    return json.loads(repaired)


def _is_complete_login_summary(text: str) -> bool:
    """Return True when *text* looks like a complete login summary.

    A valid summary must have a minimum length and contain at least one
    bullet point ('- '), matching the format the prompt asks for.  This
    guards against LLM truncation artefacts where ``repair_json`` closes
    a partial string and produces something like ``"You had a"``.
    """
    return len(text) >= 60 and "- " in text


def _duration_label(seconds: int | None) -> str:
    if not seconds:
        return ""
    minutes = round(seconds / 60)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins} min"


def _fallback_login_summary_from_rides(rides: list) -> str:
    """Build a deterministic login summary when the LLM returns an unusable one."""
    if not rides:
        return ""

    latest = rides[-1]
    name = getattr(latest, "activity_name", None) or "your latest activity"
    date = getattr(latest, "activity_date", None) or "the latest recorded date"
    sport_type = getattr(latest, "ride_purpose", None) or getattr(
        latest, "sport_type", None
    )
    duration = _duration_label(getattr(latest, "duration_seconds", None))
    tss = getattr(latest, "tss", None)
    ctl = getattr(latest, "ctl_after", None)
    atl = getattr(latest, "atl_after", None)
    tsb = getattr(latest, "tsb_after", None)
    matched_snapshot = getattr(latest, "matched_plan_snapshot", None)

    descriptors = [part for part in [duration, str(sport_type) if sport_type else ""] if part]
    descriptor_text = f" ({', '.join(descriptors)})" if descriptors else ""
    load_bits: list[str] = []
    if tss is not None:
        load_bits.append(f"TSS {round(float(tss))}")
    if ctl is not None:
        load_bits.append(f"CTL {round(float(ctl), 1)}")
    if atl is not None:
        load_bits.append(f"ATL {round(float(atl), 1)}")
    if tsb is not None:
        load_bits.append(f"TSB {round(float(tsb), 1)}")
    load_text = ", ".join(load_bits)

    bullets = [
        f"- Latest activity: Your \"{name}\" on {date}{descriptor_text} is the newest activity in your training log."
    ]
    if load_text:
        bullets.append(
            f"- Training load: The current ride metrics for this activity are {load_text}, so use that load when deciding how hard to go next."
        )
    if isinstance(matched_snapshot, dict):
        planned = matched_snapshot.get("title") or matched_snapshot.get("workoutType")
        if planned:
            bullets.append(
                f"- Plan alignment: This activity is being compared against \"{planned}\", so keep the next session honest if the effort was bigger than planned."
            )
    if len(bullets) < 3:
        bullets.append(
            "- Next step: Keep the next workout easy if your legs feel heavy; otherwise continue with the planned training rhythm."
        )

    return "Here is the latest training update based on your newest recorded activity:\n" + "\n".join(bullets[:3])


def _activity_sport_type(activity: dict) -> str:
    value = (
        activity.get("sportType")
        or activity.get("sport_type")
        or activity.get("type")
        or "cycling"
    )
    return str(value).strip() or "cycling"


def _primary_sport_type(activities: list[dict], fallback: str = "cycling") -> str:
    if not activities:
        return fallback
    types = [_activity_sport_type(activity) for activity in activities]
    normalized = {sport_type.lower() for sport_type in types if sport_type}
    if len(normalized) == 1:
        return types[0]
    return "mixed"


async def _chat(
    provider: str,
    system_prompt: str,
    user_msg: str,
    json_mode: bool = False,
    task: str = TASK_COACH,
) -> str:
    return await get_provider(provider, task=task).chat(
        system_prompt, user_msg, json_mode=json_mode
    )


async def _chat_history(
    provider: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    json_mode: bool = False,
    task: str = TASK_COACH,
    response_schema: dict | None = None,
) -> str:
    return await get_provider(provider, task=task).chat_history(
        system_prompt, messages, json_mode=json_mode, response_schema=response_schema
    )


async def analyse_strava_activities(
    activities: list[dict],
    provider: str = "openai",
    streams_by_id: dict[str, dict] | None = None,
    max_heart_rate: int | None = None,
    sport_type: str = "cycling",
    training_plan: list[dict] | None = None,
    user_ftp: int | None = None,
    timezone_name: str | None = None,
) -> dict:
    sport_type = _primary_sport_type(activities, fallback=sport_type)
    is_running = sport_type.lower() in ("running", "run")

    # --- Algorithmic computation from per-activity stream data ---
    computed_ftp: int | None = None
    computed_hr_zones: dict | None = None
    ride_analyses: dict[str, dict] = {}  # activity_id -> per-activity analysis

    if streams_by_id:
        raw_ftp, _raw_threshold_hr = compute_ftp_from_streams(
            streams_by_id, max_heart_rate
        )
        # Skip power-based FTP for running — no watts stream expected
        if not is_running:
            computed_ftp = raw_ftp

        # --- Per-activity analysis: category + interval detection + HR drift ---
        # Only meaningful for cycling where power streams are available.
        if not is_running:
            ftp_for_analysis = float(computed_ftp) if computed_ftp else None
            if ftp_for_analysis is None:
                # Rough proxy: compute global average power across all activities with power data
                all_avg_watts = [
                    a.get("averageWatts") or a.get("average_watts") for a in activities
                ]
                valid = [w for w in all_avg_watts if w and w > 0]
                if valid:
                    ftp_for_analysis = (
                        float(sum(valid) / len(valid)) * AVG_POWER_TO_FTP_RATIO
                    )
            if ftp_for_analysis and ftp_for_analysis > 0:
                for act_id, streams in streams_by_id.items():
                    analysis = build_ride_analysis(streams, ftp_for_analysis)
                    if analysis:
                        # Find the matching activity name for context
                        act_name = next(
                            (
                                a.get("name", act_id)
                                for a in activities
                                if str(a.get("id")) == act_id
                            ),
                            act_id,
                        )
                        ride_analyses[act_name] = analysis

    if max_heart_rate:
        computed_hr_zones = compute_hr_zones(max_heart_rate)

    # --- Build the contextual section describing computed metrics ---
    # Pass the user-entered FTP and threshold HR directly to the prompt; computed_ftp
    # is used only for per-activity categorisation above, not for the AI assessment output.
    computed_section = analyse_activities_computed_section(
        user_ftp,
        max_heart_rate,
        computed_hr_zones,
    )

    system_prompt = analyse_activities_system(sport_type=sport_type, user_ftp=user_ftp)

    # Build the per-activity analysis section for the AI prompt. Drop the
    # whole-ride ``avg_power_w`` — it is the lossy stream-mean that misled the
    # coach into "averaging 221W"; the authoritative average is now in the
    # labeled power block (#467). Per-interval ``avg_power_w`` inside
    # ``intervals_detected`` is legitimate and left untouched.
    ride_analyses_section = ""
    if ride_analyses:
        sanitized = {
            name: {k: v for k, v in analysis.items() if k != "avg_power_w"}
            for name, analysis in ride_analyses.items()
        }
        ride_analyses_str = json.dumps(sanitized, indent=2)
        ride_analyses_section = f"\n\nAlgorithmic per-activity analysis (computed from stream data):\n{ride_analyses_str}"

    # Per-ride time-in-zone from the authoritative entered FTP, so the coach can
    # ground zone claims in measured time rather than guessing (#468). Keyed by
    # activity id to match the labeled power block.
    time_in_zone_by_id: dict[str, dict] = {}
    if streams_by_id and not is_running and user_ftp and user_ftp > 0:
        for act_id, streams in streams_by_id.items():
            watts = streams.get("watts", {}).get("data", [])
            time_data = streams.get("time", {}).get("data", [])
            tiz = _time_in_power_zones(watts, time_data, float(user_ftp))
            if any(v > 0 for v in tiz.values()):
                time_in_zone_by_id[str(act_id)] = tiz

    user_msg = analyse_activities_user(
        activities,
        computed_section,
        ride_analyses_section,
        sport_type=sport_type,
        training_plan=training_plan,
        timezone_name=timezone_name,
        user_ftp=user_ftp,
        time_in_zone_by_id=time_in_zone_by_id,
    )

    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)

    # FTP is never estimated — always null; user-entered currentFTP is the
    # authoritative value and is used directly from the user profile.
    parsed["estimatedFTP"] = None
    if computed_hr_zones is not None:
        parsed["hrZones"] = computed_hr_zones

    return parsed


async def analyse_fit_activity(
    sport_type: str,
    duration_minutes: int,
    avg_power: int | None,
    avg_hr: int | None,
    max_heart_rate: int | None = None,
    provider: str = "openai",
) -> dict:
    """Analyse a single .fit-imported activity and return a fitness assessment.

    This is a lightweight version of ``analyse_strava_activities`` intended for
    .fit uploads where only summary metrics (avg power/HR) are available rather
    than full per-second streams.

    FTP estimation:
    - Cycling: estimated as ``avg_power × AVG_POWER_TO_FTP_RATIO`` when power
      data is present (a rough proxy since we lack the full stream).
    - Running: FTP is set to ``null``.
    Threshold HR is never estimated — only the user-entered value is used.
    """
    is_running = sport_type.lower() in ("running", "run")

    computed_ftp: int | None = None
    computed_hr_zones: dict | None = None

    if not is_running and avg_power and avg_power > 0:
        computed_ftp = round(avg_power * AVG_POWER_TO_FTP_RATIO)

    if max_heart_rate and max_heart_rate > 0:
        computed_hr_zones = compute_hr_zones(max_heart_rate)

    activity_summary = {
        "sport_type": sport_type,
        "duration_minutes": duration_minutes,
        "average_power": avg_power,
        "average_heart_rate": avg_hr,
    }

    computed_section = analyse_activities_computed_section(
        computed_ftp if not is_running else None,
        max_heart_rate,
        computed_hr_zones,
    )

    system_prompt = analyse_activities_system(sport_type=sport_type)
    user_msg = analyse_activities_user(
        [activity_summary], computed_section, "", sport_type=sport_type
    )

    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)

    # FTP is never estimated — always null.
    parsed["estimatedFTP"] = None
    if computed_hr_zones is not None:
        parsed["hrZones"] = computed_hr_zones

    return parsed


async def generate_training_plan(
    profile: dict,
    provider: str = "openai",
    rider_assessment: dict | None = None,
    metrics_history_section: str = "",
    weather_context_section: str = "",
    race_events: list[dict] | None = None,
    timezone_name: str | None = None,
) -> list[dict]:
    system_prompt = generate_plan_system()
    assessment_section = (
        f"\nRider assessment from recent Strava activities: {json.dumps(rider_assessment)}"
        if rider_assessment
        else ""
    )
    today = app_today_iso(timezone_name=timezone_name)
    user_msg = generate_plan_user(
        profile,
        today,
        assessment_section,
        metrics_history_section=metrics_history_section,
        weather_context_section=weather_context_section,
        race_events_section=race_events_context_section(race_events),
    )
    return await _generate_plan_days(provider, system_prompt, user_msg)


_MAX_PLAN_VALIDATION_ATTEMPTS = 3


def _summarize_validation_error(exc: ValidationError) -> str:
    """A compact, model-friendly summary of why a generated plan was rejected."""
    problems = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        problems.append(f"{loc}: {err.get('msg')}")
    return "; ".join(problems)


async def _generate_plan_days(
    provider: str, system_prompt: str, user_msg: str
) -> list[dict]:
    """Call the model and validate each day as a canonical ``PlanDay``.

    The lenient ``PlanDay`` coercions repair most malformed output silently; this
    loop is the safety net for a hard schema violation. On failure the specific
    error is fed back and the model gets up to ``_MAX_PLAN_VALIDATION_ATTEMPTS``
    tries before we give up with ``AIResponseFormatError`` (#422 follow-up).
    """
    correction = ""
    last_error: ValidationError | None = None
    for attempt in range(1, _MAX_PLAN_VALIDATION_ATTEMPTS + 1):
        raw = await _chat(
            provider, system_prompt, user_msg + correction,
            json_mode=True, task=TASK_PLAN,
        )
        days = _parse_ai_json(raw).get("plan", [])
        try:
            validated = [schemas.PlanDay.model_validate(day) for day in days]
        except ValidationError as exc:
            last_error = exc
            logger.warning(
                "Generated plan failed PlanDay validation (attempt %d/%d): %s",
                attempt, _MAX_PLAN_VALIDATION_ATTEMPTS, exc,
            )
            correction = (
                "\n\nYour previous response was rejected because some plan days "
                f"were invalid ({_summarize_validation_error(exc)}). Return the "
                "corrected plan as JSON with the same schema and field names."
            )
            continue
        return [d.model_dump(by_alias=True, exclude_none=True) for d in validated]
    raise AIResponseFormatError(
        "Generated plan failed validation after "
        f"{_MAX_PLAN_VALIDATION_ATTEMPTS} attempts: {last_error}"
    )


async def adapt_training_plan(
    plan: list[dict],
    recent_feedback: list[dict],
    profile: dict,
    provider: str = "openai",
    rider_assessment: dict | None = None,
    metrics_history_section: str = "",
    weather_context_section: str = "",
    race_events: list[dict] | None = None,
    timezone_name: str | None = None,
) -> list[dict]:
    today_date = app_today(timezone_name=timezone_name)
    today = today_date.isoformat()
    incomplete_days = [
        _slim_plan_entry(day, today_date=today_date)
        for day in plan
        if not day.get("completed")
    ]
    # Always use the user-entered FTP for training load computation.
    ftp = float(profile.get("currentFTP") or 0)
    training_load = compute_training_load(plan, ftp) if ftp > 0 else None

    # Detect taper window: if race is within 14 days, pass the remaining days
    # so the prompt builder can inject explicit taper instructions.
    taper_days_remaining: int | None = None
    next_race = _next_race_date(profile, race_events, timezone_name=timezone_name)
    if next_race is not None:
        days_left = (next_race - app_today(timezone_name=timezone_name)).days
        if 0 <= days_left <= 14:
            taper_days_remaining = days_left

    system_prompt = adapt_plan_system()
    user_msg = adapt_plan_user(
        profile,
        today,
        recent_feedback,
        incomplete_days,
        rider_assessment=rider_assessment,
        training_load=training_load,
        taper_days_remaining=taper_days_remaining,
        metrics_history_section=metrics_history_section,
        weather_context_section=weather_context_section,
        race_events_section=race_events_context_section(race_events),
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)
    updated_days = {day["date"]: day for day in parsed.get("updatedDays", [])}
    return [
        day if day.get("completed") else updated_days.get(day["date"], day)
        for day in plan
    ]


async def summarize_plan_changes(
    changes: list[dict],
    profile: dict,
    *,
    run_context: str,
    provider: str = "openai",
    rider_assessment: dict | None = None,
    training_load_section: str = "",
    weather_context_section: str = "",
) -> dict:
    """Narrate a coach run's applied plan changes for the athlete (#439).

    ``changes`` is the ``applied=True`` subset from the plan pipeline
    (``{"date", "old_day", "new_day"}`` dicts). Returns a dict with a first-person
    ``summary`` string and a ``days`` list of ``{"date", "reason"}`` — the caller
    (``services/coach_summary.py``) persists them and posts the chat message.
    """
    system_prompt = plan_change_summary_system()
    user_msg = plan_change_summary_user(
        changes,
        profile,
        run_context=run_context,
        rider_assessment=rider_assessment,
        training_load_section=training_load_section,
        weather_context_section=weather_context_section,
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)
    return parsed if isinstance(parsed, dict) else {}


async def classify_question(question: str, provider: str = "openai") -> dict:
    """Classify an athlete question to determine routing and RAG need.

    Uses ``provider`` for the classification call so the same backend is used
    as for all other AI requests from the same caller.  Defaults to ``"openai"``
    (typically the cheapest/fastest option for this lightweight task).

    Returns a dict with ``category`` and ``needs_science_rag``.
    On failure returns a safe default (needs_science_rag=False).
    """
    try:
        classify_sys = ask_trainer_classify_system()
        classify_user = ask_trainer_classify_user(question)
        classify_raw = await _chat(
            provider, classify_sys, classify_user, json_mode=True, task=TASK_CLASSIFY
        )
        return _parse_ai_json(classify_raw)
    except Exception:
        logger.warning(
            "Question classification failed; defaulting to no-RAG", exc_info=True
        )
        return {"category": "general_coaching", "needs_science_rag": False}


async def ask_trainer(
    question: str,
    plan: list[dict],
    profile: dict,
    provider: str = "openai",
    rider_assessment: dict | None = None,
    coach_memory: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    context_workout: dict | None = None,
    science_context: str | None = None,
    classification: dict | None = None,
    metrics_history_section: str = "",
    race_events: list[dict] | None = None,
    athlete_context: dict | None = None,
    athlete_memory_facts: list[dict] | None = None,
    athlete_model: dict | None = None,
    motivation_model: dict | None = None,
    open_questions: list[dict] | None = None,
    pending_inquiries: list[dict] | None = None,
    performance_model: dict | None = None,
    performance_recommendation: dict | None = None,
    hypotheses: list[dict] | None = None,
    weather_context_section: str = "",
    training_status_badge: tuple[str | None, str | None, str | None] | None = None,
    timezone_name: str | None = None,
) -> dict:
    today_date = app_today(timezone_name=timezone_name)
    today = today_date.isoformat()
    date_context = app_date_context(timezone_name=timezone_name)
    last_7_days = [
        _slim_plan_entry(day, today_date=today_date)
        for day in plan
        if day.get("date", "") < today
    ][-MAX_PLAN_DAYS_PAST:]
    next_7_days = [
        _slim_plan_entry(day, today_date=today_date)
        for day in plan
        if day.get("date", "") >= today
    ][:MAX_PLAN_DAYS_AHEAD]
    trimmed_memory = (
        (coach_memory or "")[-MAX_COACH_MEMORY_CHARS:] if coach_memory else None
    )
    memory_section = (
        f"\n\nCoach notes about this athlete (remember these):\n{trimmed_memory}"
        if trimmed_memory
        else ""
    )

    assessment_section = ask_trainer_assessment_section(
        rider_assessment, current_ftp=profile.get("currentFTP")
    )
    workout_section = ask_trainer_workout_section(context_workout)
    plan_updates_rule = ask_trainer_plan_updates_rule(context_workout)

    # --- Task 1: Compute training load from the plan ---
    # Always use the user-entered FTP for load computation.
    ftp = float(profile.get("currentFTP") or 0)
    training_load = compute_training_load(plan, ftp) if ftp > 0 and plan else None

    prompt_sections = ask_trainer_system_sections(
        profile,
        today,
        last_7_days,
        next_7_days,
        assessment_section,
        memory_section,
        workout_section,
        plan_updates_rule,
        athlete_context=athlete_context,
        athlete_memory_facts=athlete_memory_facts,
        athlete_model=athlete_model,
        motivation_model=motivation_model,
        open_questions=open_questions,
        pending_inquiries=pending_inquiries,
        performance_model=performance_model,
        performance_recommendation=performance_recommendation,
        hypotheses=hypotheses,
        science_context=science_context or "",
        training_load=training_load,
        classification=classification,
        metrics_history_section=metrics_history_section,
        race_events_section=race_events_context_section(race_events),
        weather_context_section=weather_context_section,
        training_status_badge=training_status_badge,
        date_context=date_context,
    )
    system_prompt = "".join(prompt_sections.values())
    # Which part of the prompt is big is a question about the athlete's real
    # data, so it is answered here rather than by a script rebuilding it (#556).
    token_accounting.log_prompt_sections(system_prompt, prompt_sections)
    history = (conversation_history or [])[-MAX_CONVERSATION_HISTORY:]
    date_stamp = app_today_stamp(timezone_name=timezone_name)
    messages = [*history, {"role": "user", "content": f"{date_stamp}\n{question}"}]

    # Retry empty/blank coach replies with exponential backoff before giving up;
    # rate-limit errors are not retried here and propagate to the caller.
    for attempt in range(ASK_TRAINER_EMPTY_RESPONSE_RETRIES + 1):
        raw = await _chat_history(
            provider,
            system_prompt,
            messages,
            json_mode=True,
            task=TASK_COACH,
            response_schema=COACH_REPLY_SCHEMA,
        )
        # A reply that will not parse must not escape as a JSONDecodeError: the
        # router has no handler for it, so it 500s with a traceback and the
        # athlete's question is lost — a failed request never persists it (#558).
        try:
            parsed = _parse_ai_json(raw)
        except (ValueError, TypeError):
            parsed = None

        if not isinstance(parsed, dict):
            prose = _coach_prose_reply(raw)
            if prose is not None:
                # Shape only. The reply is the athlete's health data (#499).
                logger.warning(
                    "AI coach replied outside the JSON contract; using the prose "
                    "as the answer (chars=%d fenced=%s)",
                    len(raw),
                    "```" in raw,
                )
                metrics.record_coach_reply(contract="prose")
                return _coach_result({}, prose)
            parsed = {}

        # --- Task 2: Strip "thinking" — never expose internal reasoning ---
        parsed.pop("thinking", None)
        response = parsed.get("response")
        if isinstance(response, str) and response.strip():
            metrics.record_coach_reply(contract="json")
            return _coach_result(parsed, response.strip())

        if attempt < ASK_TRAINER_EMPTY_RESPONSE_RETRIES:
            delay = ASK_TRAINER_RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "AI coach returned an empty response (attempt %d/%d); "
                "retrying in %.1fs",
                attempt + 1,
                ASK_TRAINER_EMPTY_RESPONSE_RETRIES + 1,
                delay,
            )
            await asyncio.sleep(delay)

    raise AIResponseFormatError("AI coach returned an empty response")


async def race_event_feedback(
    event: dict,
    plan: list[dict],
    profile: dict,
    provider: str = "openai",
    rider_assessment: dict | None = None,
    race_events: list[dict] | None = None,
    metrics_history_section: str = "",
    action: str = "added",
    timezone_name: str | None = None,
) -> str:
    today_date = app_today(timezone_name=timezone_name)
    today = today_date.isoformat()
    upcoming_plan = [
        _slim_plan_entry(day, today_date=today_date)
        for day in plan
        if day.get("date", "") >= today and not day.get("completed")
    ][:14]
    system_prompt = (
        f"{COACH_PERSONA} Review a calendar race event that was {action}. "
        'Return ONLY a valid JSON object with a "feedback" string. '
        "For added or updated events, the feedback must cover exactly: "
        "(1) how well the event fits the current training plan, "
        "(2) what should generally be adapted in training, such as more sweet spot, climbing, "
        "sprinting, endurance, taper, recovery, or other work. "
        "Do not ask whether the athlete wants the plan adapted; the UI asks that separately. "
        "Do not include planUpdates and do not rewrite the plan yet."
    )
    user_msg = (
        f"Today's date: {today}\n"
        f"Athlete profile: {json.dumps(profile)}\n"
        f"Race event {action}: {json.dumps(event)}\n"
        f"All race events: {json.dumps(race_events or [])}\n"
        f"Upcoming plan: {json.dumps(upcoming_plan)}\n"
        f"Rider assessment: {json.dumps(rider_assessment or {})}\n"
        f"{metrics_history_section}"
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_COACH
    )
    parsed = _parse_ai_json(raw)
    return parsed.get("feedback", "")


async def update_coach_memory(
    current_memory: str,
    user_message: str,
    coach_response: str,
    provider: str = "openai",
) -> str:
    system_prompt = update_memory_system()
    user_msg = update_memory_user(current_memory, user_message, coach_response)
    return await _chat(provider, system_prompt, user_msg, task=TASK_CLASSIFY)


# Cap the transcript so a huge paste cannot blow up the prompt / token budget.
MAX_TRANSCRIPT_CHARS = 24000
MAX_EXTRACTED_FACT_CANDIDATES = 20


def _normalise_fact_candidate(raw: object) -> dict | None:
    """Validate one extracted candidate; return a clean dict or ``None``."""
    if not isinstance(raw, dict):
        return None
    fact = raw.get("fact")
    if not isinstance(fact, str) or not fact.strip():
        return None
    category = raw.get("category")
    category = category.strip() if isinstance(category, str) and category.strip() else "general"
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.35
    confidence = max(0.0, min(0.9, confidence))
    snippet = raw.get("sourceSnippet") or raw.get("source_snippet") or ""
    snippet = snippet.strip()[:240] if isinstance(snippet, str) else ""
    kind_raw = raw.get("kind")
    # A stable stated/measured value is a "fact"; anything else (including a
    # missing or unrecognised label) is treated as an inferred "observation" —
    # the more conservative classification (#386).
    kind = "fact" if isinstance(kind_raw, str) and kind_raw.strip().lower() == "fact" else "observation"
    return {
        "fact": fact.strip(),
        "kind": kind,
        "category": category,
        "confidence": round(confidence, 2),
        "source_snippet": snippet,
    }


async def extract_athlete_facts(
    transcript: str,
    provider: str = "openai",
) -> list[dict]:
    """Extract candidate durable athlete facts from a pasted conversation.

    Returns a list of candidate dicts (``fact``, ``category``, ``confidence``,
    ``source_snippet``). Candidates are NOT persisted — the caller reviews and
    accepts them before storage.
    """
    cleaned = (transcript or "").strip()
    if not cleaned:
        return []
    cleaned = cleaned[:MAX_TRANSCRIPT_CHARS]

    system_prompt = extract_athlete_facts_system()
    user_msg = extract_athlete_facts_user(cleaned)
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY)
    parsed = _parse_ai_json(raw)

    candidates_raw = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(candidates_raw, list):
        return []

    candidates: list[dict] = []
    seen: set[str] = set()
    for item in candidates_raw:
        candidate = _normalise_fact_candidate(item)
        if candidate is None:
            continue
        dedupe_key = candidate["fact"].casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(candidate)
        if len(candidates) >= MAX_EXTRACTED_FACT_CANDIDATES:
            break
    return candidates


MAX_GENERATED_INSIGHTS = 8
MAX_GENERATED_HYPOTHESES = 5
MAX_GENERATED_OPEN_QUESTIONS = 5
MAX_GENERATED_INQUIRIES = 3
MAX_GENERATED_EXPERIMENTS = 5
MAX_GENERATED_PREDICTIONS = 5


async def generate_athlete_insights(
    metrics_section: str,
    existing_facts: list[str] | None = None,
    provider: str = "openai",
) -> list[dict]:
    """Infer durable athlete insights from a training-history context block.

    ``metrics_section`` is a structured-text summary of recent activities (see
    :func:`services.prompts.ride_metrics_context_section`). ``existing_facts`` are
    the athlete's current observations, passed so the model avoids repeating them.

    Returns a list of candidate dicts (``fact``, ``category``, ``confidence``,
    ``source_snippet``), deduplicated and capped. Candidates are NOT persisted —
    the caller decides how to store them.
    """
    if not metrics_section.strip():
        return []

    system_prompt = generate_athlete_insights_system()
    user_msg = generate_athlete_insights_user(metrics_section, existing_facts)
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
    )
    parsed = _parse_ai_json(raw)

    candidates_raw = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(candidates_raw, list):
        return []

    candidates: list[dict] = []
    seen: set[str] = set()
    for item in candidates_raw:
        candidate = _normalise_fact_candidate(item)
        if candidate is None:
            continue
        dedupe_key = candidate["fact"].casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(candidate)
        if len(candidates) >= MAX_GENERATED_INSIGHTS:
            break
    return candidates


# Qualitative single-value athlete-model fields (short free-text descriptors).
_ATHLETE_MODEL_TEXT_FIELDS = (
    "pacing_quality",
    "recovery_ability",
    "threshold_durability",
    "heat_tolerance",
    "preferred_training_style",
    "summary",
)
_ATHLETE_MODEL_LIST_FIELDS = ("strengths", "weaknesses", "risk_factors")
# camelCase JSON key -> snake_case model field.
_ATHLETE_MODEL_KEY_MAP = {
    "ftpWatts": "ftp_watts",
    "vo2max": "vo2max",
    "pacingQuality": "pacing_quality",
    "recoveryAbility": "recovery_ability",
    "thresholdDurability": "threshold_durability",
    "heatTolerance": "heat_tolerance",
    "preferredTrainingStyle": "preferred_training_style",
    "strengths": "strengths",
    "weaknesses": "weaknesses",
    "riskFactors": "risk_factors",
    "summary": "summary",
    "confidence": "confidence",
}
_ATHLETE_MODEL_TEXT_MAX = 200
_ATHLETE_MODEL_LIST_ITEM_MAX = 120
_ATHLETE_MODEL_LIST_MAX_ITEMS = 8


def _normalise_athlete_model(raw: object) -> dict | None:
    """Validate the LLM's derived athlete model into clean snake_case kwargs.

    Returns a dict suitable for ``crud.upsert_athlete_model``, or ``None`` when
    the payload is unusable.
    """
    if not isinstance(raw, dict):
        return None

    source: dict[str, object] = {}
    for camel, snake in _ATHLETE_MODEL_KEY_MAP.items():
        if camel in raw:
            source[snake] = raw[camel]

    result: dict[str, object] = {}

    ftp = source.get("ftp_watts")
    try:
        result["ftp_watts"] = int(ftp) if ftp not in (None, "") else None
    except (TypeError, ValueError):
        result["ftp_watts"] = None

    vo2 = source.get("vo2max")
    try:
        result["vo2max"] = float(vo2) if vo2 not in (None, "") else None
    except (TypeError, ValueError):
        result["vo2max"] = None

    for field in _ATHLETE_MODEL_TEXT_FIELDS:
        value = source.get(field)
        result[field] = (
            str(value).strip()[:_ATHLETE_MODEL_TEXT_MAX]
            if isinstance(value, str)
            else ""
        )

    for field in _ATHLETE_MODEL_LIST_FIELDS:
        value = source.get(field)
        items: list[str] = []
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, str):
                    continue
                cleaned = item.strip()[:_ATHLETE_MODEL_LIST_ITEM_MAX]
                if cleaned:
                    items.append(cleaned)
                if len(items) >= _ATHLETE_MODEL_LIST_MAX_ITEMS:
                    break
        result[field] = items

    confidence = source.get("confidence")
    try:
        result["confidence"] = max(0.0, min(0.9, float(confidence)))
    except (TypeError, ValueError):
        result["confidence"] = 0.0

    return result


async def derive_athlete_model(
    metrics_section: str,
    current_model: dict | None = None,
    provider: str = "openai",
) -> dict | None:
    """Derive/refresh the long-term structured athlete model (#384).

    ``metrics_section`` is a structured-text summary of recent activities (see
    :func:`services.prompts.ride_metrics_context_section`); ``current_model`` is
    the athlete's existing model (camelCase dict) so well-supported values are
    carried forward. Returns snake_case kwargs for
    :func:`crud.upsert_athlete_model`, or ``None`` when the history is too thin.
    """
    if not metrics_section.strip():
        return None

    system_prompt = derive_athlete_model_system()
    user_msg = derive_athlete_model_user(metrics_section, current_model)
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
    )
    parsed = _parse_ai_json(raw)
    return _normalise_athlete_model(parsed)


def _normalise_hypothesis_candidate(raw: object) -> dict | None:
    """Validate one generated hypothesis; return a clean dict or ``None``."""
    if not isinstance(raw, dict):
        return None
    statement = raw.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        return None
    category = raw.get("category")
    category = (
        category.strip()
        if isinstance(category, str) and category.strip()
        else "general"
    )
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.35
    # Hypotheses are unproven ideas, so their confidence is capped low.
    confidence = max(0.0, min(0.6, confidence))
    rationale = raw.get("rationale") or raw.get("source_snippet") or ""
    rationale = rationale.strip()[:240] if isinstance(rationale, str) else ""
    return {
        "statement": statement.strip(),
        "category": category,
        "confidence": round(confidence, 2),
        "rationale": rationale,
    }


async def generate_athlete_hypotheses(
    metrics_section: str,
    existing_facts: list[str] | None = None,
    existing_hypotheses: list[str] | None = None,
    provider: str = "openai",
) -> list[dict]:
    """Form explicit, testable hypotheses from a training-history context block.

    ``metrics_section`` is a structured-text summary of recent activities (see
    :func:`services.prompts.ride_metrics_context_section`). ``existing_facts`` and
    ``existing_hypotheses`` are passed so the model avoids repeating knowledge
    already on file.

    Returns a list of candidate dicts (``statement``, ``category``, ``confidence``,
    ``rationale``), deduplicated and capped. Candidates are NOT persisted — the
    caller decides how to store them.
    """
    if not metrics_section.strip():
        return []

    system_prompt = generate_athlete_hypotheses_system()
    user_msg = generate_athlete_hypotheses_user(
        metrics_section, existing_facts, existing_hypotheses
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
    )
    parsed = _parse_ai_json(raw)

    candidates_raw = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(candidates_raw, list):
        return []

    candidates: list[dict] = []
    seen: set[str] = set()
    for item in candidates_raw:
        candidate = _normalise_hypothesis_candidate(item)
        if candidate is None:
            continue
        dedupe_key = candidate["statement"].casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(candidate)
        if len(candidates) >= MAX_GENERATED_HYPOTHESES:
            break
    return candidates


def _normalise_open_question_candidate(raw: object) -> dict | None:
    """Validate one generated open question (#385); return a clean dict or ``None``."""
    if not isinstance(raw, dict):
        return None
    question = raw.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    category = raw.get("category")
    category = (
        category.strip()
        if isinstance(category, str) and category.strip()
        else "general"
    )

    def _text(key: str) -> str:
        value = raw.get(key)
        return value.strip()[:240] if isinstance(value, str) else ""

    resolved = bool(raw.get("resolved"))
    return {
        "question": question.strip()[:200],
        "category": category,
        "evidence": _text("evidence"),
        "needs": _text("needs"),
        "resolved": resolved,
        "resolution": _text("resolution"),
    }


def _normalise_inquiry_candidate(raw: object) -> dict | None:
    """Validate one generated athlete inquiry (#506); clean dict or ``None``."""
    if not isinstance(raw, dict):
        return None
    question = raw.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    category = raw.get("category")
    category = (
        category.strip()
        if isinstance(category, str) and category.strip()
        else "general"
    )

    def _text(*keys: str, limit: int = 240) -> str:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:limit]
        return ""

    return {
        "question": question.strip()[:200],
        "category": category,
        "why_asking": _text("whyAsking", "why_asking"),
        "settings_hint": _text("settingsHint", "settings_hint", limit=120),
    }


async def generate_athlete_inquiries(
    metrics_section: str,
    existing_facts: list[str] | None = None,
    asked_questions: list[str] | None = None,
    max_candidates: int = MAX_GENERATED_INQUIRIES,
    provider: str = "openai",
) -> list[dict]:
    """Decide what to ask the athlete that data will never reveal (#506).

    ``metrics_section`` is the usual structured training-history summary — here it
    serves the opposite purpose to the other generators: the model reads it to
    work out what the data stream already answers, so it can ask about the gaps
    instead. ``asked_questions`` are every inquiry ever put to this athlete,
    including answered and dismissed ones, so nothing is asked twice.

    Returns candidate dicts (``question``, ``category``, ``why_asking``,
    ``settings_hint``), deduplicated and capped at ``max_candidates``. Candidates
    are NOT persisted — the caller decides how to store them.
    """
    if not metrics_section.strip() or max_candidates <= 0:
        return []

    system_prompt = generate_inquiries_system(max_candidates)
    user_msg = generate_inquiries_user(
        metrics_section, existing_facts, asked_questions
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
    )
    parsed = _parse_ai_json(raw)

    candidates_raw = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(candidates_raw, list):
        return []

    candidates: list[dict] = []
    seen: set[str] = set()
    for item in candidates_raw:
        candidate = _normalise_inquiry_candidate(item)
        if candidate is None:
            continue
        dedupe_key = candidate["question"].casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(candidate)
        if len(candidates) >= max_candidates:
            break
    return candidates


async def evaluate_inquiry_answer(
    question: str,
    answer: str,
    why_asking: str = "",
    settings_hint: str = "",
    is_final_attempt: bool = False,
    provider: str = "openai",
) -> dict:
    """Judge whether an athlete's reply answered the inquiry it was pinned to (#506).

    Returns ``{"answered": bool, "fact": str, "reply": str, "question": str}``:
    ``fact`` is the durable third-person sentence to store when answered, ``reply``
    is what the coach says back, and ``question`` is the rephrased ask when the
    coach gets another attempt (empty on the final one).

    On a malformed or empty model response the answer is treated as **accepted**:
    the athlete did their part, and the failure mode of a broken judge must be a
    stored answer rather than an athlete asked the same thing again.
    """
    cleaned_answer = answer.strip()
    if not cleaned_answer:
        raise ValueError("answer must not be empty")

    fallback = {
        "answered": True,
        "fact": "",
        "reply": "",
        "question": "",
    }

    system_prompt = evaluate_inquiry_answer_system(is_final_attempt)
    user_msg = evaluate_inquiry_answer_user(
        question, cleaned_answer, why_asking, settings_hint
    )
    # The parse is inside the guard on purpose: a model that replies in prose
    # makes _parse_ai_json raise, and an exception here would 500 the request and
    # lose the answer the athlete just typed.
    try:
        raw = await _chat(
            provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
        )
        parsed = _parse_ai_json(raw)
    except Exception:
        logger.warning("Inquiry answer evaluation failed", exc_info=True)
        return fallback

    if not isinstance(parsed, dict) or "answered" not in parsed:
        return fallback

    def _text(key: str, limit: int = 400) -> str:
        value = parsed.get(key)
        return value.strip()[:limit] if isinstance(value, str) else ""

    return {
        "answered": bool(parsed.get("answered")),
        "fact": _text("fact", 240),
        "reply": _text("reply"),
        "question": _text("question", 200),
    }


async def generate_open_questions(
    metrics_section: str,
    existing_facts: list[str] | None = None,
    existing_questions: list[str] | None = None,
    provider: str = "openai",
) -> list[dict]:
    """Maintain the athlete's open-questions list from a training-history block.

    Mirrors :func:`generate_athlete_hypotheses`: ``metrics_section`` is a
    structured summary of recent activities, and ``existing_facts`` /
    ``existing_questions`` are passed so the model avoids repeating what is
    already known or already asked.

    Returns a list of candidate dicts (``question``, ``category``, ``evidence``,
    ``needs``, ``resolved``, ``resolution``), deduplicated and capped. Candidates
    are NOT persisted — the caller decides how to store them.
    """
    if not metrics_section.strip():
        return []

    system_prompt = generate_open_questions_system()
    user_msg = generate_open_questions_user(
        metrics_section, existing_facts, existing_questions
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
    )
    parsed = _parse_ai_json(raw)

    candidates_raw = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(candidates_raw, list):
        return []

    candidates: list[dict] = []
    seen: set[str] = set()
    for item in candidates_raw:
        candidate = _normalise_open_question_candidate(item)
        if candidate is None:
            continue
        dedupe_key = candidate["question"].casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(candidate)
        if len(candidates) >= MAX_GENERATED_OPEN_QUESTIONS:
            break
    return candidates


def _normalise_experiment_candidate(raw: object) -> dict | None:
    """Validate one generated validation experiment; return a clean dict or ``None``."""
    if not isinstance(raw, dict):
        return None
    protocol = raw.get("protocol")
    if not isinstance(protocol, str) or not protocol.strip():
        return None
    question = raw.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    category = raw.get("category")
    category = (
        category.strip()
        if isinstance(category, str) and category.strip()
        else "general"
    )
    rationale = raw.get("rationale") or ""
    rationale = rationale.strip()[:240] if isinstance(rationale, str) else ""
    return {
        "question": question.strip()[:240],
        "protocol": protocol.strip()[:240],
        "rationale": rationale,
        "category": category,
    }


async def generate_validation_experiments(
    uncertainties_section: str,
    existing_experiments: list[str] | None = None,
    provider: str = "openai",
) -> list[dict]:
    """Design validation experiments that resolve an athlete's open questions.

    ``uncertainties_section`` is a structured-text block describing the athlete's
    unproven hypotheses (the uncertainty). ``existing_experiments`` are the
    protocols already suggested, passed so the model avoids repeating them.

    Returns a list of candidate dicts (``question``, ``protocol``, ``rationale``,
    ``category``), deduplicated on the protocol and capped. Candidates are NOT
    persisted — the caller decides how to store them.
    """
    if not uncertainties_section.strip():
        return []

    system_prompt = generate_validation_experiments_system()
    user_msg = generate_validation_experiments_user(
        uncertainties_section, existing_experiments
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
    )
    parsed = _parse_ai_json(raw)

    candidates_raw = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(candidates_raw, list):
        return []

    candidates: list[dict] = []
    seen: set[str] = set()
    for item in candidates_raw:
        candidate = _normalise_experiment_candidate(item)
        if candidate is None:
            continue
        dedupe_key = candidate["protocol"].casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(candidate)
        if len(candidates) >= MAX_GENERATED_EXPERIMENTS:
            break
    return candidates


def _normalise_prediction_candidate(raw: object) -> dict | None:
    """Validate one generated prediction; return a clean dict or ``None``."""
    if not isinstance(raw, dict):
        return None
    prediction = raw.get("prediction")
    if not isinstance(prediction, str) or not prediction.strip():
        return None
    expected = raw.get("expectedOutcome") or raw.get("expected_outcome")
    if not isinstance(expected, str) or not expected.strip():
        return None
    horizon = raw.get("horizon")
    horizon = horizon.strip()[:60] if isinstance(horizon, str) else ""
    category = raw.get("category")
    category = (
        category.strip()
        if isinstance(category, str) and category.strip()
        else "general"
    )
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    return {
        "prediction": prediction.strip()[:240],
        "expected_outcome": expected.strip()[:240],
        "horizon": horizon,
        "confidence": round(confidence, 2),
        "category": category,
    }


async def generate_athlete_predictions(
    metrics_section: str,
    existing_predictions: list[str] | None = None,
    provider: str = "openai",
) -> list[dict]:
    """Make explicit, checkable predictions from a training-history context block.

    ``metrics_section`` is a structured-text summary of recent activities (see
    :func:`services.prompts.ride_metrics_context_section`). ``existing_predictions``
    are the claims already on file, passed so the model avoids repeating them.

    Returns a list of candidate dicts (``prediction``, ``expected_outcome``,
    ``horizon``, ``confidence``, ``category``), deduplicated on the prediction
    text and capped. Candidates are NOT persisted — the caller decides how to
    store them.
    """
    if not metrics_section.strip():
        return []

    system_prompt = generate_athlete_predictions_system()
    user_msg = generate_athlete_predictions_user(
        metrics_section, existing_predictions
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
    )
    parsed = _parse_ai_json(raw)

    candidates_raw = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(candidates_raw, list):
        return []

    candidates: list[dict] = []
    seen: set[str] = set()
    for item in candidates_raw:
        candidate = _normalise_prediction_candidate(item)
        if candidate is None:
            continue
        dedupe_key = candidate["prediction"].casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(candidate)
        if len(candidates) >= MAX_GENERATED_PREDICTIONS:
            break
    return candidates


def _normalise_prediction_evaluation(raw: object) -> dict | None:
    """Validate one prediction verdict; return a clean dict or ``None``.

    Verdicts other than ``correct``/``incorrect`` (e.g. ``unknown``) are dropped
    so an un-scorable prediction is simply left pending.
    """
    if not isinstance(raw, dict):
        return None
    try:
        index = int(raw.get("index"))
    except (TypeError, ValueError):
        return None
    if index < 0:
        return None
    verdict = raw.get("verdict")
    if not isinstance(verdict, str):
        return None
    verdict = verdict.strip().lower()
    if verdict not in ("correct", "incorrect"):
        return None
    actual = raw.get("actualOutcome") or raw.get("actual_outcome") or ""
    actual = actual.strip()[:240] if isinstance(actual, str) else ""
    return {
        "index": index,
        "correct": verdict == "correct",
        "actual_outcome": actual,
    }


async def evaluate_athlete_predictions(
    metrics_section: str,
    predictions: list[dict[str, str]],
    provider: str = "openai",
) -> list[dict]:
    """Score pending predictions against recent training data.

    ``predictions`` is the ordered list of pending predictions (each a dict with
    ``prediction``, ``expected_outcome`` and ``horizon``); the returned verdicts
    reference them by zero-based ``index``. Only predictions the evidence can
    actually settle are returned — each as ``index``, ``correct`` (bool) and
    ``actual_outcome``. Verdicts are NOT persisted; the caller applies them.
    """
    if not metrics_section.strip() or not predictions:
        return []

    system_prompt = evaluate_athlete_predictions_system()
    user_msg = evaluate_athlete_predictions_user(metrics_section, predictions)
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
    )
    parsed = _parse_ai_json(raw)

    evaluations_raw = (
        parsed.get("evaluations") if isinstance(parsed, dict) else None
    )
    if not isinstance(evaluations_raw, list):
        return []

    evaluations: list[dict] = []
    seen: set[int] = set()
    for item in evaluations_raw:
        evaluation = _normalise_prediction_evaluation(item)
        if evaluation is None:
            continue
        if evaluation["index"] >= len(predictions):
            continue
        if evaluation["index"] in seen:
            continue
        seen.add(evaluation["index"])
        evaluations.append(evaluation)
    return evaluations


async def detect_athlete_fact_contradictions(
    metrics_section: str,
    facts: list[str],
    provider: str = "openai",
) -> list[dict]:
    """Find stored athlete facts that recent training data contradicts.

    ``metrics_section`` is a structured-text summary of recent activities (see
    :func:`services.prompts.ride_metrics_context_section`). ``facts`` are the
    athlete's current stored observations, passed in list order; the model
    references each by its zero-based index.

    Returns a list of ``{"factIndex": int, "reason": str}`` dicts — one per fact
    the evidence disagrees with — with indices validated against ``facts`` and
    deduplicated (first reason wins). Returns an empty list when nothing is
    contradicted. Nothing is persisted; the caller decides how to act.
    """
    if not metrics_section.strip() or not facts:
        return []

    system_prompt = detect_contradictions_system()
    user_msg = detect_contradictions_user(metrics_section, facts)
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
    )
    parsed = _parse_ai_json(raw)

    raw_items = parsed.get("contradictions") if isinstance(parsed, dict) else None
    if not isinstance(raw_items, list):
        return []

    contradictions: list[dict] = []
    seen: set[int] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        index = item.get("factIndex")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if not (0 <= index < len(facts)) or index in seen:
            continue
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            continue
        seen.add(index)
        contradictions.append({"factIndex": index, "reason": reason.strip()[:500]})
    return contradictions


async def match_observations_to_recommendations(
    recommendations: list[dict],
    observations: list[str],
    provider: str = "openai",
) -> dict[int, list[str]]:
    """Route athlete observations to recommendations using the LLM.

    Given the day's ``recommendations`` (each with a ``recommendation`` text)
    and the coach's ``observations`` of the athlete, ask the model which
    recommendation each observation best supports.

    Returns ``{recommendation_index: [observation, ...]}`` — the same shape as
    :func:`services.analysis.keyword_observation_assignments` — preserving
    observation order. Observations the model marks irrelevant (or returns
    invalidly) fall back to the primary recommendation (index 0) so the coach's
    knowledge is never silently dropped.
    """
    if not recommendations or not observations:
        return {}

    system_prompt = match_observations_system()
    user_msg = match_observations_user(recommendations, observations)
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_CLASSIFY
    )
    parsed = _parse_ai_json(raw)

    raw_assignments = parsed.get("assignments") if isinstance(parsed, dict) else None
    routed: dict[int, list[str]] = {}
    matched_observations: set[int] = set()
    if isinstance(raw_assignments, list):
        for item in raw_assignments:
            if not isinstance(item, dict):
                continue
            obs_index = item.get("observationIndex")
            rec_index = item.get("recommendationIndex")
            if not isinstance(obs_index, int) or not (
                0 <= obs_index < len(observations)
            ):
                continue
            matched_observations.add(obs_index)
            if not isinstance(rec_index, int) or not (
                0 <= rec_index < len(recommendations)
            ):
                rec_index = 0  # irrelevant/invalid → keep on the primary rec
            routed.setdefault(rec_index, []).append(obs_index)

    # Any observation the model omitted also falls back to the primary rec.
    for obs_index in range(len(observations)):
        if obs_index not in matched_observations:
            routed.setdefault(0, []).append(obs_index)

    # Emit observations in their original order per recommendation.
    return {
        rec_index: [observations[i] for i in sorted(obs_indexes)]
        for rec_index, obs_indexes in routed.items()
    }


async def rate_completed_workout(
    day: dict,
    profile: dict,
    provider: str = "openai",
    stream_delta: dict | None = None,
    ride_analysis: dict | None = None,
) -> dict:
    feedback = day.get("feedback")
    if not feedback and not stream_delta:
        return {"feedback": "", "flag_for_adaptation": False}

    system_prompt = rate_workout_system()
    user_msg = rate_workout_user(
        day,
        feedback,
        profile,
        stream_delta=stream_delta,
        actual_ride_analysis=ride_analysis,
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_FEEDBACK
    )
    parsed = _parse_ai_json(raw)
    return {
        "feedback": parsed.get("feedback", ""),
        "flag_for_adaptation": bool(parsed.get("flag_for_adaptation", False)),
        "needs_athlete_feedback": bool(parsed.get("needs_athlete_feedback", False)),
        "follow_up_question": parsed.get("follow_up_question") or None,
        "suggested_feedback_tags": list(parsed.get("suggested_feedback_tags") or []),
    }


async def generate_login_summary(
    ride_insights: str | None,
    last_ride_feedback: str | None,
    notes: str | None,
    estimated_ftp: int | None,
    training_plan: list[dict] | None = None,
    provider: str = "openai",
    feel_legs: str | None = None,
    timezone_name: str | None = None,
    latest_ride_purpose: str | None = None,
    latest_ride_confidence: str | None = None,
    latest_ride_reason: str | None = None,
) -> str:
    """Generate a loginSummary from existing assessment data (no fresh Strava data needed).

    Used when a user already has a ``RiderAssessment`` but ``login_summary`` is
    ``None`` — e.g. because the column was added after their last Strava sync.
    Returns the summary string, or an empty string on failure.
    """
    # Anchor the plan to today so the model can describe the next session's
    # timing correctly (today/tomorrow/this Saturday/next week) instead of
    # guessing it is "today". Each PlanDay carries an ISO ``date``.
    today = app_today(timezone_name=timezone_name)
    annotated_plan = (
        [{**day, **_plan_date_labels(day.get("date"), today)} for day in training_plan]
        if training_plan
        else training_plan
    )
    system_prompt = refresh_login_summary_system()
    user_msg = refresh_login_summary_user(
        ride_insights=ride_insights,
        last_ride_feedback=last_ride_feedback,
        notes=notes,
        estimated_ftp=estimated_ftp,
        training_plan=annotated_plan,
        feel_legs=feel_legs,
        date_context=app_date_context(timezone_name=timezone_name),
        latest_ride_purpose=latest_ride_purpose,
        latest_ride_confidence=latest_ride_confidence,
        latest_ride_reason=latest_ride_reason,
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)
    summary = parsed.get("loginSummary") or ""
    return summary if _is_complete_login_summary(summary) else ""


async def generate_training_status(
    facts: dict,
    training_plan: list[dict] | None = None,
    provider: str = "openai",
    timezone_name: str | None = None,
) -> tuple[str, str, str] | None:
    """Have the coach write the dashboard status badge from deterministic facts.

    Returns ``(label, tone, rationale)``, or ``None`` when the model produced
    nothing usable — callers fall back to
    :func:`services.training_status.fallback_status` so the chip is never blank.
    """
    system_prompt = training_status_system()
    user_msg = training_status_user(
        facts, training_plan=training_plan, timezone_name=timezone_name
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)

    label = str(parsed.get("label") or "").strip().strip(".")
    rationale = str(parsed.get("rationale") or "").strip()
    if not label or len(label) > TRAINING_STATUS_LABEL_MAX_CHARS or not rationale:
        # A too-long badge wraps and breaks the dashboard's status strip, so an
        # over-length label is rejected outright rather than truncated into
        # something that no longer reads as a phrase.
        logger.warning(
            "Unusable training status from provider (label=%r, rationale len=%d)",
            label,
            len(rationale),
        )
        return None

    tone = str(parsed.get("tone") or "").strip().lower()
    if tone not in TRAINING_STATUS_TONES:
        tone = "steady"
    return label, tone, rationale


async def batch_review_rides(
    rides: list,
    profile: dict,
    provider: str = "openai",
    training_plan: list[dict] | None = None,
    timezone_name: str | None = None,
) -> str:
    """Generate a coach review for a batch of newly imported rides.

    *rides* is a list of RideMetric ORM objects (or duck-typed equivalents).
    Returns the review text string, or an empty string when there are no rides
    or the LLM call fails.
    """
    if not rides:
        return ""
    system_prompt = batch_review_system()
    user_msg = batch_review_user(
        rides,
        profile=profile,
        training_plan=training_plan,
        timezone_name=timezone_name,
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_FEEDBACK
    )
    parsed = _parse_ai_json(raw)
    return parsed.get("review") or ""


async def recommend_next_session(
    rides: list,
    plan: list[dict],
    profile: dict,
    provider: str = "openai",
    rider_assessment: dict | None = None,
    coach_memory: str | None = None,
    athlete_context: dict | None = None,
    athlete_memory_facts: list[dict] | None = None,
    athlete_model: dict | None = None,
    motivation_model: dict | None = None,
    performance_recommendation: dict | None = None,
    ctl: float | None = None,
    atl: float | None = None,
    tsb: float | None = None,
    timezone_name: str | None = None,
) -> dict:
    """Generate a concrete next-ride recommendation based on recent ride(s) and feedback.

    *rides* is a list of RideMetric ORM objects (or duck-typed equivalents).
    Returns a dict with ``response``, ``next_session_recommendation``,
    ``recommendation_type``, and optional ``plan_updates``.
    """
    system_prompt = next_ride_recommendation_system()
    user_msg = next_ride_recommendation_user(
        rides=rides,
        plan=plan,
        profile=profile,
        rider_assessment=rider_assessment,
        coach_memory=coach_memory,
        athlete_context=athlete_context,
        athlete_memory_facts=athlete_memory_facts,
        athlete_model=athlete_model,
        motivation_model=motivation_model,
        performance_recommendation=performance_recommendation,
        ctl=ctl,
        atl=atl,
        tsb=tsb,
        timezone_name=timezone_name,
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_FEEDBACK
    )
    parsed = _parse_ai_json(raw)
    return {
        "response": parsed.get("response", ""),
        "next_session_recommendation": parsed.get("next_session_recommendation", ""),
        "recommendation_type": parsed.get("recommendation_type", "keep_as_planned"),
        "plan_updates": parsed.get("planUpdates") or None,
    }


async def generate_summary_from_ride_feedbacks(
    rides: list,
    assessment: dict | None = None,
    training_plan: list[dict] | None = None,
    provider: str = "openai",
    timezone_name: str | None = None,
) -> str:
    """Generate an updated loginSummary from a batch of rides with fresh athlete feedback.

    *rides* is a list of RideMetric ORM objects (or duck-typed equivalents) sorted
    oldest-first.  Returns the summary string, or an empty string on failure.
    """
    if not rides:
        return ""
    system_prompt = process_pending_feedbacks_system()
    user_msg = process_pending_feedbacks_user(
        rides=rides,
        assessment=assessment,
        training_plan=training_plan,
        timezone_name=timezone_name,
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)
    summary = parsed.get("loginSummary") or ""
    if _is_complete_login_summary(summary):
        return summary
    return _fallback_login_summary_from_rides(rides)
