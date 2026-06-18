"""Backend AI service.

This is a focused Python port of the frontend ai.ts service. It keeps prompt
content and response shapes aligned with the existing UI so the frontend can
switch to backend fetches without semantic changes.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any

from json_repair import repair_json

from .analysis import (
    AVG_POWER_TO_FTP_RATIO,
    LTHR_RATIO,
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
from .dates import app_date_context, app_today, app_today_iso
from .prompts import (
    COACH_PERSONA,
    analyse_activities_computed_section,
    analyse_activities_system,
    analyse_activities_user,
    generate_plan_system,
    generate_plan_user,
    adapt_plan_system,
    adapt_plan_user,
    race_events_context_section,
    ask_trainer_assessment_section,
    ask_trainer_classify_system,
    ask_trainer_classify_user,
    ask_trainer_workout_section,
    ask_trainer_plan_updates_rule,
    ask_trainer_system,
    update_memory_system,
    update_memory_user,
    rate_workout_system,
    rate_workout_user,
    refresh_login_summary_system,
    refresh_login_summary_user,
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

_SLIM_PLAN_KEEP = {
    "date",
    "workoutType",
    "workout_type",
    "title",
    "durationMinutes",
    "duration_minutes",
    "targetPower",
    "target_power",
    "completed",
}


def _slim_plan_entry(
    entry: dict, today_date: datetime.date | None = None
) -> dict:
    """Return a compact version of a plan day with only fields needed for chat context."""
    slim = {k: v for k, v in entry.items() if k in _SLIM_PLAN_KEEP and v is not None}
    raw_date = slim.get("date")
    if raw_date:
        try:
            parsed = datetime.date.fromisoformat(str(raw_date))
        except ValueError:
            return slim
        slim["weekday"] = parsed.strftime("%A")
        slim["dateLabel"] = f"{parsed.strftime('%A, %B')} {parsed.day}, {parsed.year}"
        if today_date is not None:
            delta_days = (parsed - today_date).days
            if delta_days == 0:
                slim["relativeDay"] = "today"
            elif delta_days == 1:
                slim["relativeDay"] = "tomorrow"
            elif delta_days == -1:
                slim["relativeDay"] = "yesterday"
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


def _parse_ai_json(text: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    extracted = fenced.group(1).strip() if fenced else text.strip()
    stripped = re.sub(r"(\d+)\s+[a-zA-Z_]+(?=\s*[,}\]\n])", r"\1", extracted)
    repaired = repair_json(stripped)
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
) -> str:
    return await get_provider(provider, task=task).chat_history(
        system_prompt, messages, json_mode=json_mode
    )


async def analyse_strava_activities(
    activities: list[dict],
    provider: str = "openai",
    streams_by_id: dict[str, dict] | None = None,
    max_heart_rate: int | None = None,
    sport_type: str = "cycling",
    training_plan: list[dict] | None = None,
    user_ftp: int | None = None,
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

    # Build the per-activity analysis section for the AI prompt
    ride_analyses_section = ""
    if ride_analyses:
        ride_analyses_str = json.dumps(ride_analyses, indent=2)
        ride_analyses_section = f"\n\nAlgorithmic per-activity analysis (computed from stream data):\n{ride_analyses_str}"

    user_msg = analyse_activities_user(
        activities,
        computed_section,
        ride_analyses_section,
        sport_type=sport_type,
        training_plan=training_plan,
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
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)
    return parsed.get("plan", [])


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
    today = app_today_iso(timezone_name=timezone_name)
    incomplete_days = [day for day in plan if not day.get("completed")]
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
    timezone_name: str | None = None,
) -> dict:
    today_date = app_today(timezone_name=timezone_name)
    today = today_date.isoformat()
    date_context = app_date_context(timezone_name=timezone_name)
    last_7_days = [
        _slim_plan_entry(day, today_date=today_date)
        for day in plan
        if day.get("date", "") <= today
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

    system_prompt = ask_trainer_system(
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
        science_context=science_context or "",
        training_load=training_load,
        classification=classification,
        metrics_history_section=metrics_history_section,
        race_events_section=race_events_context_section(race_events),
        date_context=date_context,
    )
    history = (conversation_history or [])[-MAX_CONVERSATION_HISTORY:]
    messages = [*history, {"role": "user", "content": question}]
    raw = await _chat_history(
        provider, system_prompt, messages, json_mode=True, task=TASK_COACH
    )
    parsed = _parse_ai_json(raw)

    # --- Task 2: Strip "thinking" — never expose internal reasoning to the frontend ---
    parsed.pop("thinking", None)

    return {
        "response": parsed.get("response", ""),
        "plan_updates": parsed.get("planUpdates"),
        "sources": parsed.get("sources") or [],
        "ride_note_update": parsed.get("ride_note_update"),
    }


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
    today = app_today_iso(timezone_name=timezone_name)
    upcoming_plan = [
        _slim_plan_entry(day)
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
) -> str:
    """Generate a loginSummary from existing assessment data (no fresh Strava data needed).

    Used when a user already has a ``RiderAssessment`` but ``login_summary`` is
    ``None`` — e.g. because the column was added after their last Strava sync.
    Returns the summary string, or an empty string on failure.
    """
    system_prompt = refresh_login_summary_system()
    user_msg = refresh_login_summary_user(
        ride_insights=ride_insights,
        last_ride_feedback=last_ride_feedback,
        notes=notes,
        estimated_ftp=estimated_ftp,
        training_plan=training_plan,
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)
    summary = parsed.get("loginSummary") or ""
    return summary if _is_complete_login_summary(summary) else ""


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
