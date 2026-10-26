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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from json_repair import repair_json
from pydantic import ValidationError

import schemas

from . import metrics, output_audit, plan_priors, token_accounting
from domain import untrusted_text
from . import coach_memory as coach_memory_notes
from .athlete_language import athlete_language, in_other_language
from . import reply_notes
from .reply_notes import ReplyNote
from domain.activity_identity import activity_sport_type, power_model_applies
from .plan_priors import PriorDeparture
from .zone_label import NOT_CURRENT_FTP, ftp_figures
from .coach_schema import COACH_REPLY_SCHEMA
from .analysis import (
    AVG_POWER_TO_FTP_RATIO,
    _time_in_power_zones,
    build_ride_analysis,
    compute_ftp_from_streams,
    compute_hr_zones,
    compute_training_load,
)
from .season_plan import GOAL_MAX_CHARS
from .llm import (
    AIProviderError,
    AIRateLimitError,
    AIResponseFormatError,
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
    PLAN_HORIZON_DAYS,
    analyse_activities_computed_section,
    analyse_activities_system,
    analyse_activities_user,
    anchored_activity_date,
    ctl_label,
    tsb_label,
    generate_plan_system,
    generate_plan_user,
    adapt_plan_system,
    adapt_plan_user,
    plan_change_summary_system,
    plan_change_summary_user,
    week_goals_system,
    week_goals_user,
    plan_text_translation_system,
    plan_text_translation_user,
    opening_message_system,
    opening_message_user,
    race_events_context_section,
    ask_trainer_assessment_section,
    ask_trainer_classify_system,
    ask_trainer_classify_user,
    ask_trainer_workout_section,
    ANALYSIS_PROSE_FIELDS,
    ask_trainer_plan_updates_rule,
    race_event_feedback_system,
    race_event_feedback_user,
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
from domain import uncertainty_lifecycle

MAX_CONVERSATION_HISTORY = 10
MAX_PLAN_DAYS_PAST = 7
MAX_PLAN_DAYS_AHEAD = 7

logger = logging.getLogger(__name__)


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


def _plan_updates_of(parsed: dict) -> list[dict] | None:
    """The reply's planUpdates, kept to the objects among them (#283).

    The chat's response schema asks for a list of objects, but a provider that
    ignores the schema can answer a single object or a list of strings, and the
    router's constraint filter then failed on ``.get`` with a 500 after the
    paid call. What is not an object is dropped and logged.
    """
    updates = parsed.get("planUpdates")
    if updates is None:
        return None
    if not isinstance(updates, list):
        logger.warning(
            "Coach reply's planUpdates was a %s, not a list; ignored",
            type(updates).__name__,
        )
        return []
    kept = [update for update in updates if isinstance(update, dict)]
    if len(kept) != len(updates):
        logger.warning(
            "Coach reply carried %d planUpdates that are not objects; ignored",
            len(updates) - len(kept),
        )
    return kept


def _coach_result(parsed: dict, response: str) -> dict:
    """Shape one coach reply for the router; *parsed* is empty for prose."""
    return {
        "response": response,
        "plan_updates": _plan_updates_of(parsed),
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
    # Which sport the session is (#710). Free for a cycling plan: the persist
    # gate omits the default, so the key only exists on days that are not rides —
    # and on those the coach has to know, or it discusses a planned run as if it
    # were one.
    "sport",
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
    entry: dict,
    today_date: datetime.date | None = None,
    done_minutes: int | None = None,
) -> dict:
    """Return a compact version of a plan day with only fields needed for chat context.

    A done day also carries what was done, beside what was planned: shown only
    the plan, the coach quoted it, "nach 75 Minuten" for a ride of 145 (the
    owner's chat, 2026-10-18). ``done_minutes`` comes from the rides matched to
    the day; a session logged by hand keeps its own in its feedback.
    """
    slim = {k: v for k, v in entry.items() if k in _SLIM_PLAN_KEEP and v is not None}
    feedback = entry.get("feedback")
    if done_minutes is None and isinstance(feedback, dict):
        done_minutes = feedback.get("actualDurationMinutes")
    if (
        entry.get("completed")
        and isinstance(done_minutes, (int, float))
        and not isinstance(done_minutes, bool)
    ):
        slim["actualDurationMinutes"] = round(done_minutes)
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


def _audit_prose(
    text: object, system_prompt: str, user_msg: str, *, surface: str
) -> None:
    """Report one athlete-facing text through the output audit (ai-trainer-ops#28).

    ``ask_trainer`` was wired first; these are the other surfaces the athlete
    reads as the coach speaking. The context is the prompt and the user message
    as sent: both are the deterministic model's numbers rendered for the model
    to read, which is what a number in the reply must trace back to.

    Report-only, like ``ask_trainer``: nothing here changes what is returned.
    Empty or non-text output is not counted — it is never shown, and counting it
    would inflate the denominator the findings are read against.
    """
    if isinstance(text, str) and text.strip():
        output_audit.report(text, [system_prompt, user_msg], surface=surface)


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

    descriptors = [
        part for part in [duration, str(sport_type) if sport_type else ""] if part
    ]
    descriptor_text = f" ({', '.join(descriptors)})" if descriptors else ""
    load_bits: list[str] = []
    if tss is not None:
        load_bits.append(f"TSS {round(float(tss))}")
    if ctl is not None:
        # Sport-qualified: this is the latest activity's own sport's fitness,
        # where the ATL beside it is the athlete's aggregate (#713).
        load_bits.append(f"{ctl_label(latest)} {round(float(ctl), 1)}")
    if atl is not None:
        load_bits.append(f"ATL {round(float(atl), 1)}")
    if tsb is not None:
        load_bits.append(f"{tsb_label(latest)} {round(float(tsb), 1)}")
    load_text = ", ".join(load_bits)

    bullets = [
        f'- Latest activity: Your "{name}" on {date}{descriptor_text} is the newest activity in your training log.'
    ]
    if load_text:
        bullets.append(
            f"- Training load: The current ride metrics for this activity are {load_text}, so use that load when deciding how hard to go next."
        )
    if isinstance(matched_snapshot, dict):
        planned = matched_snapshot.get("title") or matched_snapshot.get("workoutType")
        if planned:
            bullets.append(
                f'- Plan alignment: This activity is being compared against "{planned}", so keep the next session honest if the effort was bigger than planned.'
            )
    if len(bullets) < 3:
        bullets.append(
            "- Next step: Keep the next workout easy if your legs feel heavy; otherwise continue with the planned training rhythm."
        )

    return (
        "Here is the latest training update based on your newest recorded activity:\n"
        + "\n".join(bullets[:3])
    )


def _activity_sport_type(activity: dict) -> str:
    """The activity's sport, defaulting to this app's own sport when it has none.

    Which key holds it is ``activity_identity.activity_sport_type``'s business —
    shared so the gates that read a sport cannot drift apart on which spelling
    they trust (#711).
    """
    return activity_sport_type(activity) or "cycling"


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
    threshold_pace_seconds_per_km: float | None = None,
    reply_language: str | None = None,
) -> dict:
    # The batch's primary sport, which is what the prose is written about.
    sport_type = _primary_sport_type(activities, fallback=sport_type)

    # --- Algorithmic computation from per-activity stream data ---
    computed_ftp: int | None = None
    computed_hr_zones: dict | None = None
    ride_analyses: dict[str, dict] = {}  # activity_id -> per-activity analysis

    # Which sport each activity in the batch was. One verdict for the whole
    # batch is the right shape for the prose and the wrong shape for the power
    # maths: a batch of nine rides and one run read as "cycling", so the run's
    # watts joined the FTP envelope — and a batch that was mostly runs read as
    # "running", so its rides got no power analysis at all (#711).
    #
    # Read through ``_activity_sport_type``, so a sport-less activity resolves
    # to the same thing here as it does in ``_primary_sport_type`` above. Not to
    # the batch verdict: that is ``"mixed"`` for a mixed batch, which names no
    # sport and would have withheld power from every activity that did not
    # state its own.
    sport_by_id = {
        str(activity.get("id")): _activity_sport_type(activity)
        for activity in activities
        if activity.get("id") is not None
    }

    if streams_by_id:
        computed_ftp, _raw_threshold_hr = compute_ftp_from_streams(
            streams_by_id, max_heart_rate, sport_by_id=sport_by_id
        )

        # --- Per-activity analysis: category + interval detection + HR drift ---
        ftp_for_analysis = float(computed_ftp) if computed_ftp else None
        if ftp_for_analysis is None:
            # Rough proxy: the average power across the activities the power
            # model applies to. Averaging a run's watts in here moved the proxy
            # threshold every later classification was measured against.
            all_avg_watts = [
                activity.get("averageWatts") or activity.get("average_watts")
                for activity in activities
                if power_model_applies(_activity_sport_type(activity))
            ]
            valid = [w for w in all_avg_watts if w and w > 0]
            if valid:
                ftp_for_analysis = (
                    float(sum(valid) / len(valid)) * AVG_POWER_TO_FTP_RATIO
                )
        if ftp_for_analysis and ftp_for_analysis > 0:
            for act_id, streams in streams_by_id.items():
                analysis = build_ride_analysis(
                    streams, ftp_for_analysis, sport_type=sport_by_id.get(act_id)
                )
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

    system_prompt = analyse_activities_system(
        sport_type=sport_type, user_ftp=user_ftp, reply_language=reply_language
    )

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
    if streams_by_id and user_ftp and user_ftp > 0:
        for act_id, streams in streams_by_id.items():
            # Zones are cut from a cycling FTP, so time in them is a cycling
            # statement — per activity, not per batch (#711).
            if not power_model_applies(sport_by_id.get(act_id)):
                continue
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
        threshold_pace_seconds_per_km=threshold_pace_seconds_per_km,
    )

    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = await _analysis_in_language(
        provider, system_prompt, user_msg, _parse_ai_json(raw), reply_language
    )

    # FTP is never estimated — always null; user-entered currentFTP is the
    # authoritative value and is used directly from the user profile.
    parsed["estimatedFTP"] = None
    if computed_hr_zones is not None:
        parsed["hrZones"] = computed_hr_zones

    # No completeness gate here, unlike the refresh paths: the callers store
    # this summary as it came (routers/ai.py, routers/users.py), so even a
    # truncated one is shown — and what is shown is what gets audited.
    _audit_prose(
        parsed.get("loginSummary"), system_prompt, user_msg, surface="login_summary"
    )
    return parsed


def _analysis_prose(parsed: object) -> str:
    fields = parsed if isinstance(parsed, dict) else {}
    return " ".join(str(fields.get(field) or "") for field in ANALYSIS_PROSE_FIELDS)


async def _analysis_in_language(
    provider: str,
    system_prompt: str,
    user_msg: str,
    parsed: dict,
    reply_language: str | None,
) -> dict:
    """The analysis once more, told plainly, when its texts came back in the other language.

    They are stored as written and are the first an athlete reads after their
    first sync (ai-trainer-ops#258). The first analysis stands when the second
    call fails or comes back in the other language as well.
    """
    if not in_other_language(_analysis_prose(parsed), reply_language):
        return parsed
    metrics.record_coach_rewrite(surface="analyse_activities", reason="language")
    fields = ", ".join(f"'{field}'" for field in ANALYSIS_PROSE_FIELDS)
    try:
        raw = await _chat(
            provider,
            system_prompt,
            f"{user_msg}\n\nWrite {fields} in {reply_language}.",
            json_mode=True,
            task=TASK_PLAN,
        )
        again = _parse_ai_json(raw)
    except (AIRateLimitError, AIResponseFormatError, AIProviderError):
        return parsed
    if isinstance(again, dict) and not in_other_language(
        _analysis_prose(again), reply_language
    ):
        return again
    return parsed


async def analyse_fit_activity(
    sport_type: str,
    duration_minutes: int,
    avg_power: int | None,
    avg_hr: int | None,
    max_heart_rate: int | None = None,
    provider: str = "openai",
    reply_language: str | None = None,
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
    # Not "is it running": a reported watt number from any sport the cycling
    # power model does not describe is not a threshold proxy (#711).
    power_applies = power_model_applies(sport_type)

    computed_ftp: int | None = None
    computed_hr_zones: dict | None = None

    if power_applies and avg_power and avg_power > 0:
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
        computed_ftp,
        max_heart_rate,
        computed_hr_zones,
    )

    system_prompt = analyse_activities_system(
        sport_type=sport_type, reply_language=reply_language
    )
    user_msg = analyse_activities_user(
        [activity_summary], computed_section, "", sport_type=sport_type
    )

    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = await _analysis_in_language(
        provider, system_prompt, user_msg, _parse_ai_json(raw), reply_language
    )

    # FTP is never estimated — always null.
    parsed["estimatedFTP"] = None
    if computed_hr_zones is not None:
        parsed["hrZones"] = computed_hr_zones

    # No completeness gate here, unlike the refresh paths: the callers store
    # this summary as it came (routers/ai.py, routers/users.py), so even a
    # truncated one is shown — and what is shown is what gets audited.
    _audit_prose(
        parsed.get("loginSummary"), system_prompt, user_msg, surface="login_summary"
    )
    return parsed


async def generate_training_plan(
    profile: dict,
    provider: str = "openai",
    rider_assessment: dict | None = None,
    metrics_history_section: str = "",
    weather_context_section: str = "",
    race_events: list[dict] | None = None,
    timezone_name: str | None = None,
    athlete_model_section: str = "",
    prior_check: Callable[[list[dict]], list[PriorDeparture]] | None = None,
) -> list[dict]:
    system_prompt = generate_plan_system()
    assessment_section = (
        f"\nRider assessment from recent activities: {untrusted_text.marked_json(rider_assessment)}"
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
        athlete_model_section=athlete_model_section,
        timezone_name=timezone_name,
    )
    return await _generate_plan_days(
        provider, system_prompt, user_msg, prior_check, today=today
    )


_MAX_PLAN_VALIDATION_ATTEMPTS = 3

# A generated plan is a fortnight from today. A day further out than this, or
# before today, is a calendar the model lost track of (ai-trainer-ops#113).
_PLAN_SLACK_DAYS = 7
# Fewer distinct days than this is no plan, whatever else it holds.
_MIN_PLAN_DAYS = 7
# Ten hours: the longest single session a plan may hold.
_MAX_SESSION_MINUTES = 600


def _plan_days_from(parsed: Any) -> list | None:
    """The day list in a plan reply, read leniently, or None when there is none.

    The prompt asks for ``{"plan": [...]}``. A bare list, or the days under
    another key ("trainingPlan", "days"), is the same answer and is taken as
    such. Before ai-trainer-ops#113 either of those was read as an empty plan,
    which was saved, and onboarding ended with HTTP 200 and nothing to train.
    """
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return None
    days = parsed.get("plan")
    if isinstance(days, list):
        return days
    candidates = [
        value
        for value in parsed.values()
        if isinstance(value, list)
        and value
        and all(isinstance(item, dict) and "date" in item for item in value)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _plan_problems(plan: list[dict], today: str) -> list[str]:
    """What makes a validated plan unusable as a fortnight from ``today``."""
    problems: list[str] = []
    first = datetime.date.fromisoformat(today)
    last = first + datetime.timedelta(days=PLAN_HORIZON_DAYS - 1 + _PLAN_SLACK_DAYS)
    dates: set[datetime.date] = set()
    for day in plan:
        raw = str(day.get("date") or "")
        try:
            when = datetime.date.fromisoformat(raw)
        except ValueError:
            problems.append(f"{raw!r} is not a date in the form YYYY-MM-DD")
            continue
        if not first <= when <= last:
            problems.append(
                f"{raw} is outside {first.isoformat()} to {last.isoformat()}"
            )
            continue
        dates.add(when)
        minutes = int(day.get("durationMinutes") or 0)
        rest = str(day.get("workoutType") or "").strip().lower() in ("rest", "off")
        if not rest and not 0 < minutes <= _MAX_SESSION_MINUTES:
            problems.append(
                f"{raw} has {minutes} minutes; a session needs 1 to "
                f"{_MAX_SESSION_MINUTES} minutes, and a day without one is a rest day"
            )
    if len(dates) < _MIN_PLAN_DAYS:
        problems.append(
            f"it has {len(dates)} days; return one entry for each of the "
            f"{PLAN_HORIZON_DAYS} days from {first.isoformat()}"
        )
    return problems[:8]


def _summarize_validation_error(exc: ValidationError) -> str:
    """A compact, model-friendly summary of why a generated plan was rejected."""
    problems = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        problems.append(f"{loc}: {err.get('msg')}")
    return "; ".join(problems)


def _held_to_priors(surface: str, departures: list[PriorDeparture]) -> str:
    """The correction a plan writer gets when its output breaks a hard prior.

    One rewrite per run (ai-trainer-ops#87, the owner's yes to #81 Q26). What
    survives it is noted on the day by the persist gate rather than blocked.
    """
    metrics.record_coach_rewrite(surface=surface, reason="priors")
    return (
        "\n\nAutomatic check: "
        + plan_priors.rewrite_request(departures)
        + " Return your whole answer again, in the same JSON form."
    )


async def _generate_plan_days(
    provider: str,
    system_prompt: str,
    user_msg: str,
    prior_check: Callable[[list[dict]], list[PriorDeparture]] | None = None,
    *,
    today: str | None = None,
) -> list[dict]:
    """Call the model and validate each day as a canonical ``PlanDay``.

    The lenient ``PlanDay`` coercions repair most malformed output silently; this
    loop is the safety net for a hard schema violation. On failure the specific
    error is fed back and the model gets up to ``_MAX_PLAN_VALIDATION_ATTEMPTS``
    tries before we give up with ``AIResponseFormatError`` (#422 follow-up).

    A reply that is no plan at all (prose, nothing, no days) and a plan that is
    no fortnight from ``today`` (dates out of the window or not dates, sessions
    of no or impossible length, too few days) count as failed attempts too
    (ai-trainer-ops#113). An empty plan is never returned.
    """
    correction = ""
    last_error: Exception | None = None
    held = False
    attempt = 0
    while attempt < _MAX_PLAN_VALIDATION_ATTEMPTS:
        attempt += 1
        try:
            raw = await _chat(
                provider,
                system_prompt,
                user_msg + correction,
                json_mode=True,
                task=TASK_PLAN,
            )
        except AIResponseFormatError as exc:
            # Cut off at its output limit: repaired, it became a shorter plan
            # (ai-trainer-ops#120). Asked again, for a plan that fits.
            last_error = exc
            logger.warning(
                "Generated plan was cut off (attempt %d/%d)",
                attempt,
                _MAX_PLAN_VALIDATION_ATTEMPTS,
            )
            correction = (
                "\n\nYour previous response was cut off before it ended. Return "
                "the whole plan as JSON with the same schema, with shorter descriptions."
            )
            continue
        try:
            days = _plan_days_from(_parse_ai_json(raw))
        except ValueError as exc:  # json.JSONDecodeError is a ValueError
            days, last_error = None, exc
        if not days:
            logger.warning(
                "Generated plan held no days (attempt %d/%d)",
                attempt,
                _MAX_PLAN_VALIDATION_ATTEMPTS,
            )
            last_error = last_error or AIResponseFormatError("no plan days")
            correction = (
                "\n\nYour previous response held no plan. Return only a JSON object "
                '{"plan": [...]} with one entry per day.'
            )
            continue
        try:
            validated = [schemas.PlanDay.model_validate(day) for day in days]
        except ValidationError as exc:
            last_error = exc
            logger.warning(
                "Generated plan failed PlanDay validation (attempt %d/%d): %s",
                attempt,
                _MAX_PLAN_VALIDATION_ATTEMPTS,
                exc,
            )
            correction = (
                "\n\nYour previous response was rejected because some plan days "
                f"were invalid ({_summarize_validation_error(exc)}). Return the "
                "corrected plan as JSON with the same schema and field names."
            )
            continue
        plan = [d.model_dump(by_alias=True, exclude_none=True) for d in validated]
        problems = _plan_problems(plan, today) if today else []
        if problems:
            logger.warning(
                "Generated plan is no fortnight (attempt %d/%d): %s",
                attempt,
                _MAX_PLAN_VALIDATION_ATTEMPTS,
                problems,
            )
            last_error = AIResponseFormatError("; ".join(problems))
            correction = (
                "\n\nYour previous plan was rejected: "
                + "; ".join(problems)
                + ". Return the corrected plan as JSON with the same schema."
            )
            continue
        departures = prior_check(plan) if prior_check is not None and not held else []
        if departures:
            # The rewrite for the priors comes on top of the validation budget.
            held = True
            attempt -= 1
            correction = _held_to_priors("generate_plan", departures)
            continue
        return plan
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
    athlete_model_section: str = "",
    plan_change_history: str = "",
    plan_coherence_warnings: str = "",
    plan_commitments: str = "",
    prior_check: Callable[[list[dict] | None], list[PriorDeparture]] | None = None,
    athlete_words: str = "",
) -> list[dict]:
    """The days this run wants to change or add — not a whole plan (#666).

    It used to return the complete plan, which meant every nightly run handed
    back ~11 days that were already right, each with freshly generated prose:
    309 applied day changes in 30 days of production against the coach's 45. The
    athlete experienced that as their plan quietly moving under them. Callers now
    merge these days into the stored plan, so a day this run did not name stays
    byte-identical.

    An empty list is a valid, common and often correct answer.
    """
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
        athlete_model_section=athlete_model_section,
        plan_change_history=plan_change_history,
        plan_coherence_warnings=plan_coherence_warnings,
        plan_commitments=plan_commitments,
        timezone_name=timezone_name,
        athlete_words=athlete_words,
    )
    # A completed session is history; a run that proposes to rewrite one is
    # simply wrong about it, so it is dropped here rather than relied on being
    # caught downstream.
    completed_dates = {
        day.get("date")
        for day in plan
        if isinstance(day, dict) and day.get("completed")
    }

    async def updates(prompt: str) -> list[dict]:
        raw = await _chat(
            provider, system_prompt, prompt, json_mode=True, task=TASK_PLAN
        )
        return [
            day
            for day in _parse_ai_json(raw).get("updatedDays", [])
            if isinstance(day, dict)
            and day.get("date")
            and day.get("date") not in completed_dates
        ]

    proposed = await updates(user_msg)
    departures = prior_check(proposed) if prior_check is not None else []
    if departures:
        proposed = await updates(user_msg + _held_to_priors("adapt_plan", departures))
    return proposed


async def summarize_plan_changes(
    changes: list[dict],
    profile: dict,
    *,
    run_context: str,
    provider: str = "openai",
    rider_assessment: dict | None = None,
    training_load_section: str = "",
    weather_context_section: str = "",
    language_samples: list[str] | None = None,
    timezone_name: str | None = None,
) -> dict:
    """Narrate a coach run's applied plan changes for the athlete (#439).

    ``changes`` is the ``applied=True`` subset from the plan pipeline
    (``{"date", "old_day", "new_day"}`` dicts). Returns a dict with a first-person
    ``summary`` string and a ``days`` list of ``{"date", "reason"}`` — the caller
    (``services/coach_summary.py``) persists them and posts the chat message.

    ``language_samples`` are the athlete's own recent messages, passed so the
    narration is written in their language rather than in English (#658).
    """
    system_prompt = plan_change_summary_system()
    user_msg = plan_change_summary_user(
        changes,
        profile,
        run_context=run_context,
        rider_assessment=rider_assessment,
        training_load_section=training_load_section,
        weather_context_section=weather_context_section,
        language_samples=language_samples,
        timezone_name=timezone_name,
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)
    parsed = parsed if isinstance(parsed, dict) else {}
    # Told the athlete's language, the narration still came back in English to a
    # German athlete (ai-trainer-ops#149). One more call, told it plainly; if
    # that fails too, the first narration stands.
    expected = (
        athlete_language(language_samples[-1], language_samples[:-1])
        if language_samples
        else None
    )
    if in_other_language(_narration_text(parsed), expected):
        metrics.record_coach_rewrite(surface="plan_change_summary", reason="language")
        try:
            raw = await _chat(
                provider,
                system_prompt,
                f"{user_msg}\n\nWrite 'summary' and every 'reason' in {expected}.",
                json_mode=True,
                task=TASK_PLAN,
            )
            again = _parse_ai_json(raw)
        except (AIRateLimitError, AIResponseFormatError, AIProviderError):
            again = None
        if isinstance(again, dict) and str(again.get("summary") or "").strip():
            parsed = again
    _audit_prose(
        _narration_text(parsed), system_prompt, user_msg, surface="plan_change_summary"
    )
    return parsed


def _narration_text(parsed: dict) -> str:
    """The summary and the day reasons of a narration, as one text."""
    reasons = [
        str(day.get("reason") or "")
        for day in parsed.get("days") or []
        if isinstance(day, dict)
    ]
    return " ".join([str(parsed.get("summary") or ""), *reasons])


async def write_week_goals(
    weeks: list[dict],
    profile: dict,
    *,
    race_events: list[dict] | None = None,
    goals_section: str = "",
    provider: str = "openai",
    language_samples: list[str] | None = None,
    language: str | None = None,
) -> dict[str, str]:
    """One goal sentence per season-plan week, by ISO Monday (ai-trainer-ops#98).

    ``weeks`` are ``{"weekStart", "summary", "nextRace"}`` dicts. A week the
    reply leaves out, or answers with no text, is missing from the result; the
    caller asks again next time.
    """
    system_prompt = week_goals_system()
    user_msg = week_goals_user(
        weeks,
        profile,
        race_events=race_events,
        goals_section=goals_section,
        language_samples=language_samples,
        language=language,
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_COACH
    )
    parsed = _parse_ai_json(raw)
    items = parsed.get("goals") if isinstance(parsed, dict) else None
    wanted = {str(week["weekStart"]) for week in weeks}
    goals: dict[str, str] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        start, goal = str(item.get("weekStart") or ""), item.get("goal")
        if start in wanted and isinstance(goal, str) and goal.strip():
            goals[start] = " ".join(goal.split())[:GOAL_MAX_CHARS]
    _audit_prose(
        " ".join(goals.values()), system_prompt, user_msg, surface="week_goals"
    )
    return goals


async def translate_plan_texts(
    texts: list[str], language: str, provider: str = "openai"
) -> dict[str, str]:
    """``texts`` in ``language``, keyed by the text (ai-trainer-ops#121).

    A text the reply leaves out, or answers with no text, is missing from the
    result and stays English until the next call.
    """
    if not texts:
        return {}
    system_prompt = plan_text_translation_system(language)
    user_msg = plan_text_translation_user(texts)
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_FEEDBACK
    )
    parsed = _parse_ai_json(raw)
    items = parsed.get("translations") if isinstance(parsed, dict) else None
    translated: dict[str, str] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        index, text = item.get("id"), item.get("text")
        if (
            isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index < len(texts)
            and isinstance(text, str)
            and text.strip()
        ):
            translated[texts[index]] = untrusted_text.unmark(text).strip()
    _audit_prose(
        " ".join(translated.values()),
        system_prompt,
        user_msg,
        surface="plan_translation",
    )
    return translated


async def write_opening_message(
    facts: dict, language: str, sport: str, provider: str = "openai"
) -> tuple[str, list]:
    """The coach's first message after onboarding and its starting assumptions
    (ai-trainer-ops#153, #154), as ``(message, assumptions)``, unvalidated.

    A part the reply does not carry comes back empty: ``""`` for the message,
    ``[]`` for the assumptions. The caller then falls back to its fixed text
    and assumptions.
    """
    system_prompt = opening_message_system(language, sport)
    user_msg = opening_message_user(facts)
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_FEEDBACK
    )
    try:
        parsed = _parse_ai_json(raw)
    except ValueError:
        return "", []
    if not isinstance(parsed, dict):
        return "", []
    hypotheses = parsed.get("hypotheses")
    hypotheses = hypotheses if isinstance(hypotheses, list) else []
    message = parsed.get("message")
    if not isinstance(message, str):
        return "", hypotheses
    message = untrusted_text.unmark(message).strip()
    _audit_prose(message, system_prompt, user_msg, surface="opening_message")
    return message, hypotheses


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


# What the athlete reads under a reply that still failed a check after its
# rewrite (ai-trainer-ops#87). Fixed text in the two languages the app's athletes
# write, chosen the way the reply language is.
_PRIOR_NOTE_LABELS = {
    plan_priors.PRIOR_TAPER_VOLUME: (
        "too little volume cut before the race",
        "vor dem Rennen zu wenig Umfang reduziert",
    ),
    plan_priors.PRIOR_TAPER_INTENSITY: (
        "no intensity left before the race",
        "vor dem Rennen keine Intensität mehr",
    ),
    plan_priors.PRIOR_RACE_EVE: (
        "a long session just before the race",
        "eine lange Einheit kurz vor dem Rennen",
    ),
    plan_priors.PRIOR_RACE_WEEK: (
        "a long ride in the race week",
        "eine lange Ausfahrt in der Rennwoche",
    ),
    plan_priors.PRIOR_BACK_TO_BACK: (
        "two hard days in a row",
        "zwei harte Tage hintereinander",
    ),
    plan_priors.PRIOR_RIDE_LENGTH: (
        "a ride well beyond your longest recent one",
        "eine Fahrt weit über deiner längsten der letzten Zeit",
    ),
    plan_priors.PRIOR_STATED_HOURS: (
        "more hours in a week than you said you have",
        "mehr Stunden in einer Woche, als du angegeben hast",
    ),
    plan_priors.PRIOR_ILLNESS: (
        "intensity while you are ill or just after",
        "Intensität während oder kurz nach deiner Krankheit",
    ),
    plan_priors.PRIOR_TIREDNESS: (
        "intensity while you have been tired for days",
        "Intensität, obwohl du seit Tagen müde bist",
    ),
    plan_priors.PRIOR_REDUCED_DAY: (
        "intensity on a day you said you have less to give",
        "Intensität an einem Tag, an dem du weniger geben kannst",
    ),
}


def _with_plan_change(audited_context: list, plan_updates) -> list:
    """The audit's context plus the durations the reply's own plan change sets.

    "Zwei Einheiten von je 75 Minuten" beside a 75-minute update prescribes a
    session and invents nothing about the athlete, yet it got a note
    (ai-trainer-ops#103 Q31, the owner's (a)). Durations only, in minutes and in
    hours: a target power in the same update is no evidence for a watt figure in
    the prose, which may be a claim about the athlete's FTP.
    """
    minutes = []
    for update in plan_updates if isinstance(plan_updates, list) else ():
        if not isinstance(update, dict):
            continue
        for key in ("durationMinutes", "durationMinMinutes", "durationMaxMinutes"):
            value = update.get(key)
            if isinstance(value, bool):
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value > 0:
                minutes.append(value)
    if not minutes:
        return audited_context
    return [*audited_context, minutes, [m / 60 for m in minutes]]


# A reply that says the plan changed while it carries no change: the chat of
# 2026-06-24 ("Saturday is now a complete rest day", and nothing moved) and "Das
# machen wir" in the scenarios (ai-trainer-ops#103 Q33). The forms that report
# a change as made, and the ones that announce it for this reply with the coach
# as subject (``_ANNOUNCED_CHANGE``). "Lass uns … tauschen" is a proposal and
# stays out: in the owner's chat export it matched replies that changed
# nothing and promised nothing.
# "Made", "set" and a bare "gemacht" report progress as often as a change: "Wir
# haben heute einen großen Schritt gemacht". In the owner's chat export no claim
# needed them.
_CHANGED_DE = (
    r"(?:geändert|angepasst|verschoben|getauscht|gestrichen|eingetragen|umgestellt|"
    r"ersetzt|gekürzt|verkürzt|reduziert|umgeplant|aktualisiert|freigehalten|"
    r"herausgenommen|rausgenommen|gelöscht|hinzugefügt|eingeplant|umgewandelt|"
    r"markiert|verlegt|(?:zu[mr]|zu\s+ein(?:em|er))\s+\w+\s+gemacht|frei\s*gemacht)"
)
_CHANGED_EN = (
    r"(?:changed|moved|swapped|updated|adjusted|replaced|removed|added|turned|"
    r"marked|scheduled|rescheduled|cleared|shortened|cut|converted|switched|"
    r"dropped|reduced|trimmed|shifted|pushed|lowered|capped)"
)
# A participle followed by another auxiliary belongs to someone else's clause:
# "dass du die Einheit verschoben hast".
_OWN_PARTICIPLE = r"\b(?!\s+(?:hast|hat|habt|hattest|hatte|hatten|wurde|wurden|worden))"
_CLAIMED_CHANGE = re.compile(
    rf"\b(?:ich|wir)\s+(?:habe|haben|hab)\b(?P<gap>[^.!?\n]{{0,80}}?)\b{_CHANGED_DE}{_OWN_PARTICIPLE}"
    rf"|\b(?:habe|haben|hab)\s+(?:ich|wir)\b(?P<gap2>[^.!?\n]{{0,80}}?)\b{_CHANGED_DE}{_OWN_PARTICIPLE}"
    r"|\b(?:i|we)(?:['’]ve|\s+have)\s+(?:now\s+|just\s+|also\s+|already\s+|gone\s+ahead\s+and\s+)?"
    rf"{_CHANGED_EN}\b"
    r"|\b(?:ist|sind|steht|stehen)\s+(?:jetzt|nun|ab\s+sofort)\b[^.!?\n]{0,50}?"
    r"\b(?:ruhetag|frei|eingetragen|verschoben|gestrichen|im\s+plan)\b"
    r"|\b(?:is|are)\s+now\b[^.!?\n]{0,40}?"
    r"\b(?:rest\s+day|off|in\s+(?:the|your)\s+plan|scheduled|moved|swapped|set)\b"
    r"|(?:^|[,;:—–-]\s*)(?:das|so)\s+machen\s+wir\b(?!\s+am\s+besten)"
    r"|^machen\s+wir\b|\bmachen\s+wir\s+(?:es\s+)?so\b|^erledigt\b|^done\b",
    re.IGNORECASE,
)
# A change the coach announces for this reply, with itself as the subject: "Ich
# trage dir Freitag als Ruhetag ein", "… und mache dir den nächsten Freitag zum
# Ruhetag" (scenario run 1594, with no change), "I'll move your intervals".
# Without the subject "Mache heute …" is advice to the athlete. Verbs that also
# say something else ("Ich stelle fest", "Ich mache mir Sorgen") count only with
# a change after them. In the owner's chat export this matched 1 of 270 replies
# without a plan change, and that one announced a ride-log entry.
# Each verb with the particle it needs to be a change, if any: "Ich mache mir
# Sorgen" and "Wir halten am Plan fest" are none.
_TO_REST = r"zu[mr]|zu\s+ein(?:em|er)|frei"
_ANNOUNCE_SINGULAR = (
    ("mache", _TO_REST),
    ("trage", "ein"),
    ("verschiebe", ""),
    ("streiche", ""),
    ("tausche", ""),
    ("ersetze", ""),
    ("kürze", ""),
    ("ändere", ""),
    ("wandle", "um"),
    ("halte", "frei"),
)
_ANNOUNCE_PLURAL = (
    ("machen", _TO_REST),
    ("tragen", "ein"),
    ("verschieben", ""),
    ("streichen", ""),
    ("tauschen", ""),
    ("ersetzen", ""),
    ("kürzen", ""),
    ("ändern", ""),
    ("halten", "frei"),
)


def _announcements(verbs, subject: str, coordinated: bool = False) -> list[str]:
    """ "ich trage … ein", "trage ich … ein", and "ich …, und trage … ein"."""
    forms = []
    for verb, particle in verbs:
        tail = rf"[^.!?\n]{{0,40}}?\b(?:{particle})\b" if particle else ""
        forms += [rf"\b{subject}\s+{verb}\b{tail}", rf"\b{verb}\s+{subject}\b{tail}"]
        if coordinated:
            forms.append(rf"\b{subject}\b[^.!?\n]{{0,60}}?\bund\s+{verb}\b{tail}")
    return forms


_ANNOUNCED_CHANGE = re.compile(
    "|".join(
        [
            *_announcements(_ANNOUNCE_SINGULAR, "ich", coordinated=True),
            *_announcements(_ANNOUNCE_PLURAL, "wir"),
            r"\b(?:i['’]ll|i\s+will|we['’]ll|we\s+will|i['’]m\s+going\s+to|i\s+am\s+going\s+to)\s+"
            r"(?:now\s+|just\s+|also\s+)?(?:make|move|swap|switch|shift|replace|cut|shorten|drop|"
            r"turn|mark|change|update|adjust|clear|reschedule|convert|push|lower)\b"
            r"(?!\s+(?:sure|you)\b)",
        ]
    ),
    re.IGNORECASE,
)
# An offer, a condition or a negation is no announcement: "Wenn du magst,
# verschiebe ich …", "I can move …", "Ich ändere den Plan für Freitag nicht".
# Only announcements are read this way; a change reported as made stays a claim
# beside a "wenn".
_OFFER = re.compile(
    r"\b(?:wenn|falls|if|would|could|can|kann|kannst|könnte|könnten|würde|würden|möchtest|"
    r"willst|want|shall|should|soll|sollen|sollten|vielleicht|maybe|perhaps"
    r"|nicht|nichts|kein\w*|nie|not|no|never|nothing|won['’]t)\b",
    re.IGNORECASE,
)
# Between the auxiliary and the participle: a negation ("Ich habe nichts
# geändert") or the start of a clause of its own ("Ich habe gesehen, dass …").
_GAP_BREAK = re.compile(
    r"\b(?:nicht|nichts|kein\w*|nie|dass|ob|weil|wenn|falls)\b", re.IGNORECASE
)
# The sentence has to name a day, the plan or a session. "I've updated my
# picture of your recovery" and "We've made good progress this week" change
# nothing the athlete would look for, so the week and "training" do not count.
_PLAN_WORDS = re.compile(
    r"\b(?:heute|morgen|übermorgen|montag|dienstag|mittwoch|donnerstag|freitag|"
    r"samstag|sonntag|today|tomorrow|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|\w*plan|plan\w*|ruhetag\w*|einheit\w*|ausfahrt\w*|"
    r"intervall\w*|session\w*|rest\s+day|day\s+off|rides?|workout\w*|interval\w*)\b",
    re.IGNORECASE,
)


def _claimed_change(text: str) -> str | None:
    """The first sentence of ``text`` that says the plan was or is being changed,
    or ``None``.

    A question is an offer, and a negated change ("Ich habe nichts geändert")
    is none. A sentence naming the FTP is skipped: the chat route sets a stated
    FTP before the coach answers (ai-trainer-ops#70), so "I've updated your FTP"
    can be true without any plan change.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        sentence = sentence.strip(" \t*-•\"'„“»«")
        if not sentence or sentence.endswith("?") or "ftp" in sentence.casefold():
            continue
        if not _PLAN_WORDS.search(sentence):
            continue
        for match in _CLAIMED_CHANGE.finditer(sentence):
            if not _GAP_BREAK.search(match.group("gap") or match.group("gap2") or ""):
                return sentence[:160]
        if _ANNOUNCED_CHANGE.search(sentence) and not _OFFER.search(sentence):
            return sentence[:160]
    return None


# The coach saying it set the athlete's FTP (ai-trainer-ops#129). The chat route
# sets a stated FTP before the reply (#70), so a sentence naming the profile's
# FTP is true; one naming another figure claims a change nobody made, as when
# "I think my FTP is 300 W" is no statement to store.
_FTP_SET = re.compile(
    r"\b(?:updated?|set|changed?|adjusted|saved|entered|stored|recorded|aktualisiert|"
    r"gesetzt|geändert|angepasst|eingetragen|gespeichert|übernommen|hinterlegt)\b",
    re.IGNORECASE,
)


def _profile_watts(current_ftp: object) -> int | None:
    try:
        return round(float(current_ftp)) if current_ftp else None
    except (TypeError, ValueError):
        return None


def _ftp_sentences(text: str):
    """The sentences of ``text`` that name the FTP and state something: a
    question or an offer states nothing."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        sentence = sentence.strip(" \t*-•\"'„“»«")
        if not sentence or sentence.endswith("?") or "ftp" not in sentence.casefold():
            continue
        if _OFFER.search(sentence):
            continue
        yield sentence


def _claimed_ftp_change(text: str, current_ftp: object) -> str | None:
    """The first sentence of ``text`` that says the FTP was set to a figure the
    profile does not hold, or ``None``."""
    current = _profile_watts(current_ftp)
    for sentence in _ftp_sentences(text):
        if not _FTP_SET.search(sentence):
            continue
        watts = ftp_figures(sentence)
        if watts and current not in watts:
            return sentence[:160]
    return None


def _stated_other_ftp(text: str, current_ftp: object, question: str = "") -> str | None:
    """The first sentence of ``text`` that gives the athlete's FTP as a figure
    the profile does not hold, or ``None`` (ai-trainer-ops#147).

    Every target the app computes comes from the profile's FTP, so "your FTP of
    320 W" beside targets worked out from 300 W tells the athlete two different
    things. The coach review found an FTP of 320 W in six actions in three
    weeks, a figure the plan text had carried for months. A sentence that says
    the FTP was set is :func:`_claimed_ftp_change`'s, and a figure the athlete
    gave in ``question`` is theirs to talk about.
    """
    current = _profile_watts(current_ftp)
    if current is None:
        return None
    given = {int(n) for n in re.findall(r"\d{2,4}", question or "")}
    for sentence in _ftp_sentences(text):
        # A set-claim is #129's; a past, a target or a hypothetical FTP is meant
        # as written (reviews of #968).
        if _FTP_SET.search(sentence) or NOT_CURRENT_FTP.search(sentence):
            continue
        watts = ftp_figures(sentence)
        if watts - given and current not in watts:
            return sentence[:160]
    return None


@dataclass(frozen=True)
class _Findings:
    """What a coach reply is held to (ai-trainer-ops#87, #103 Q33)."""

    numbers: list[str]
    # Hard priors broken on the days the reply's plan change names.
    departures: list[PriorDeparture]
    # Race and state priors broken in the coming week, on days the reply leaves alone.
    standing: list[PriorDeparture]
    # The sentence that says the plan changed, in a reply that carries no change.
    claim: str | None
    # The sentence that says the FTP was set to a figure the profile does not hold.
    ftp_claim: str | None = None
    # The sentence that gives the athlete's FTP as another figure (#147), and the
    # profile's, which every target is computed from.
    ftp_stated: str | None = None
    profile_ftp: int | None = None
    # The athlete's language, when the reply is clearly in the other one (#149).
    language: str | None = None
    # Days the reply's ridden changes touch that it never names (#150).
    unmentioned: tuple[datetime.date, ...] = ()
    # A duration the reply gives for a changed day that its update does not
    # hold: (day, minutes said, minutes planned) (#150).
    durations: tuple[tuple[datetime.date, int, int], ...] = ()
    # A result the reply promises: "absolut machbar", "guaranteed" (#187).
    promise: str | None = None

    def __bool__(self) -> bool:
        return bool(
            self.numbers
            or self.departures
            or self.standing
            or self.claim
            or self.ftp_claim
            or self.ftp_stated
            or self.language
            or self.unmentioned
            or self.durations
            or self.promise
        )

    def reason(self) -> str:
        """What the reply was found to contain, for ``coach_rewrites_total``."""
        found = (
            ("numbers", self.numbers),
            ("priors", self.departures),
            ("standing", self.standing),
            ("claim", self.claim),
            ("ftp", self.ftp_claim),
            ("ftp_stated", self.ftp_stated),
            ("language", self.language),
            ("unmentioned", self.unmentioned),
            ("duration", self.durations),
            ("promise", self.promise),
        )
        return "+".join(name for name, value in found if value)


# A result promised in so many words (ai-trainer-ops#187): Werner, 58, in his
# first structured year, asked about the Ötztaler and was told "absolut machbar
# … topfit am Start". The prompt said "Never promise a result" already.
_PROMISE = re.compile(
    r"\b(?:absolut\s+machbar|garantiert|sicher\s+schaffen|"
    r"schaffst\s+du\s+(?:locker|sicher|ganz\s+sicher)|topfit\s+am\s+start|"
    r"guarantee[ds]?|absolutely\s+(?:doable|achievable)|you(?:'ll|\s+will)\s+definitely|"
    r"you(?:'ll|\s+will)\s+(?:easily|surely)\s+make\s+it|no\s+doubt\s+you(?:'ll|\s+will))\b",
    re.IGNORECASE,
)


# A disclaimer is what the rule asks for: "Nobody can guarantee it", "I don't
# guarantee", "ohne Garantie" (review of #1006).
_NEGATION = re.compile(
    r"\b(?:nicht|kein\w*|niemand|ohne|no|not|nobody|without|cannot|can't|"
    r"(?:don|won|doesn|isn|aren|wouldn|couldn|shouldn)'?t|never|nie)\b",
    re.IGNORECASE,
)
_CLAUSE_BREAK = re.compile(r"[,;:.!?]|\s+(?:aber|but|doch|und|and)\s+", re.IGNORECASE)


def _promised_result(text: str) -> str | None:
    """The words of a promised result in ``text``, or ``None``. A negated one
    ("kann ich nicht garantieren", "no guarantee") promises nothing."""
    # "you’ll", "don’t": the curly apostrophe a phone types (review of #1006).
    text = (text or "").replace("\u2019", "'")
    for match in _PROMISE.finditer(text):
        # The clause the promise is in: "Ich weiß nicht, ob es klappt, aber du
        # schaffst es garantiert" negates the doubt, not the promise.
        start = max(
            (m.end() for m in _CLAUSE_BREAK.finditer(text, 0, match.start())), default=0
        )
        ends = [m.start() for m in _CLAUSE_BREAK.finditer(text, match.end())]
        end = min(ends, default=len(text))
        # The promise's own words are not its negation: "no doubt you'll".
        clause = (text[start : match.start()] + " " + text[match.end() : end]).lower()
        if _NEGATION.search(clause):
            continue
        return match.group(0)
    return None


# What a plan update changes that the athlete rides or does; the other fields
# (title, description, purpose, focus points) only say how the day is described.
_RIDDEN_KEYS = (
    "sport",
    "timeOfDay",
    "workoutType",
    "durationMinutes",
    "durationMinMinutes",
    "durationMaxMinutes",
    "targetPower",
    "targetHeartRate",
    "intervals",
    "strengthExercises",
    "completed",
)


# What a title says the athlete does, when it says so (ai-trainer-ops#179). A
# run turned into a walk keeps sport "running" and changes only the title and
# the description; read as a rewording, the saved change was reported as one
# the plan does not contain.
_TITLE_ACTIVITIES = (
    ("ride", r"rides?|spin|bike|cycling|radfahrt|ausfahrt|fahrt|radeln|rolle"),
    ("run", r"runs?|running|jog|jogging|lauf|laufen|dauerlauf"),
    ("walk", r"walks?|walking|hike|hiking|spaziergang|gehen|wanderung|wandern"),
    ("strength", r"strength|gym|weights|kraft|krafttraining"),
    ("mobility", r"mobility|yoga|stretch(?:ing)?|mobilität|mobilisation|dehnen"),
    ("swim", r"swim(?:ming)?|schwimmen"),
    ("rest", r"rest|ruhetag|pause|off"),
)
_TITLE_ACTIVITY_RES = tuple(
    (kind, re.compile(rf"\b(?:{words})\b", re.IGNORECASE))
    for kind, words in _TITLE_ACTIVITIES
)


def _title_activities(title: object) -> frozenset[str]:
    text = str(title or "")
    return frozenset(
        kind for kind, pattern in _TITLE_ACTIVITY_RES if pattern.search(text)
    )


def _changes_the_ride(update: dict, plan: list[dict] | None) -> bool:
    """Whether ``update`` changes what the athlete rides on its day, not only
    how the day is worded (ai-trainer-ops#150).

    On 2026-10-18 the coach answered "you haven't updated the week plan?" with
    "Ich habe den Plan jetzt direkt angepasst", and its update only reworded two
    days its previous reply had already changed. Counted as carried, the update
    let the claim through. A day the plan does not hold is a change, and so is
    every update when there is no plan to compare with.
    """
    if plan is None:
        return True
    try:
        slot = int(update.get("slot") or 0)
    except (TypeError, ValueError):
        return True  # a slot nothing can read is no rewording (review of #973)
    day = next(
        (
            d
            for d in plan
            if isinstance(d, dict)
            and d.get("date") == update.get("date")
            and str(d.get("slot") or 0) == str(slot)
        ),
        None,
    )
    if day is None:
        return True

    def differs(key: str) -> bool:
        # "Not done" and no flag at all are the same day (review of #973).
        if key == "completed":
            return bool(update[key]) != bool(day.get(key))
        return update[key] != day.get(key)

    if any(key in update and differs(key) for key in _RIDDEN_KEYS):
        return True
    # A new activity in the title is a new session, even where no ridden field
    # moved ("Easy Run" to "Easy Walk"); a title that only gains or loses the
    # word ("Endurance Ride" to "Endurance") is still a rewording.
    if "title" in update:
        before, after = (
            _title_activities(day.get("title")),
            _title_activities(update["title"]),
        )
        return bool(before and after and before != after)
    return False


# A day as a reply names it: the weekday in either language, today, tomorrow
# or the day after as a word, or the date itself (ai-trainer-ops#150).
_DAY_NAMES = (
    ("montag", "monday"),
    ("dienstag", "tuesday"),
    ("mittwoch", "wednesday"),
    ("donnerstag", "thursday"),
    ("freitag", "friday"),
    ("samstag", "sonnabend", "saturday"),
    ("sonntag", "sunday"),
)
_RELATIVE_DAYS = {
    0: ("heute", "heutig", "today", "tonight"),
    1: ("morgen", "morgig", "tomorrow"),
    2: ("übermorgen", "day after tomorrow"),
}
_MONTH_NAMES = (
    ("januar", "jan", "january"),
    ("februar", "feb", "february"),
    ("märz", "maerz", "mar", "march"),
    ("april", "apr"),
    ("mai", "may"),
    ("juni", "jun", "june"),
    ("juli", "jul", "july"),
    ("august", "aug"),
    ("september", "sep", "sept"),
    ("oktober", "okt", "oct", "october"),
    ("november", "nov"),
    ("dezember", "dez", "dec", "december"),
)
# Month words that are also ordinary words ("7 may be too much"): only month-first
# ("May 7") names a date with them (review of #975).
_AMBIGUOUS_MONTHS = frozenset({"may", "mar"})
# "heute Morgen", "am Morgen": the morning, not tomorrow (review of #975).
# "Morgen früh" is tomorrow morning and stays (second review of #975).
_MORNING = re.compile(r"\b(?:heute|gestern|diesen|am|jeden|guten)\s+morgen\b")
# Stems that name the day in an adjective ("heutige", "morgige"); every other
# day word must end where the word ends, so "morgens" is not tomorrow.
_DAY_STEMS = frozenset({"heutig", "morgig"})
_ENGLISH_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _mentions_day(text: str, day: datetime.date, today: datetime.date | None) -> bool:
    """Whether ``text`` names ``day``: by its weekday, as today, tomorrow or the
    day after, or by its date ("7.10.", "07.10.", "2026-10-07", "7. Oktober",
    "October 7")."""
    lower = (text or "").casefold()
    words = list(_DAY_NAMES[day.weekday()])
    if today is not None:
        words += _RELATIVE_DAYS.get((day - today).days, ())
    # "heute Morgen" is no tomorrow; taken out before the words are looked for.
    plain = _MORNING.sub(" ", lower)

    def named(word: str) -> bool:
        tail = "" if word in _DAY_STEMS or word in _DAY_NAMES[day.weekday()] else r"\b"
        return re.search(rf"(?<![\w-]){re.escape(word)}{tail}", plain) is not None

    if any(named(word) for word in words):
        return True
    names = _MONTH_NAMES[day.month - 1]
    months = "|".join(re.escape(m) for m in names)
    after_digit = "|".join(re.escape(m) for m in names if m not in _AMBIGUOUS_MONTHS)
    dates = (
        rf"\b0?{day.day}\.\s?0?{day.month}\.",
        re.escape(day.isoformat()),
        rf"\b0?{day.day}\.?\s*(?:{after_digit})\b",
        rf"\b(?:{months})\.?\s+0?{day.day}\b",
    )
    return any(re.search(pattern, lower) for pattern in dates)


def _unmentioned_changes(
    response: str, updates: list[dict], today: datetime.date | None, question: str = ""
) -> list[datetime.date]:
    """The days a reply's ridden changes touch that the reply never names
    (ai-trainer-ops#150). On 2026-10-06 a reply cut Wednesday's ride from 120 to
    105 minutes and changed Friday's exercises, and asked the athlete whether it
    should change anything; the athlete learned of the change from the plan.

    A day the athlete's own question names counts as named: "fahre ich heute
    Rennrad oder MTB?" answered with "ich habe die geplante Ausfahrt auf dem
    MTB eingetragen" leaves no doubt which day changed (ai-trainer-ops#239)."""
    missed: list[datetime.date] = []
    for update in updates:
        try:
            day = datetime.date.fromisoformat(str(update.get("date")))
        except ValueError:
            continue
        # A past day is done or locked; what the athlete needs to hear about is
        # today and what is coming.
        if today is not None and day < today:
            continue
        named = _mentions_day(response, day, today) or _mentions_day(
            question, day, today
        )
        if day not in missed and not named:
            missed.append(day)
    return missed


# A duration as a reply states it: "2 Stunden", "1,5 h", "1:45 h", "1 h 45",
# "anderthalb Stunden", "105 Minuten", "90 min" (ai-trainer-ops#150).
_HOUR_WORDS = {
    "eine": 1.0,
    "einer": 1.0,
    "one": 1.0,
    "an": 1.0,
    "zwei": 2.0,
    "two": 2.0,
    "drei": 3.0,
    "three": 3.0,
    "vier": 4.0,
    "four": 4.0,
    "fünf": 5.0,
    "five": 5.0,
    "anderthalb": 1.5,
    "eineinhalb": 1.5,
    "zweieinhalb": 2.5,
    "dreieinhalb": 3.5,
}
_HOURS = r"(?:stunden?|std\.?|h|hours?|hrs?)\b"
_DURATION = re.compile(
    rf"(?<![\w.,:])(?:(?P<h>\d+(?:[.,]\d+)?)(?::(?P<hm>\d{{2}}))?|(?P<hw>{'|'.join(_HOUR_WORDS)}))\s*{_HOURS}"
    rf"(?:\s*(?:und\s+|and\s+)?(?P<extra>\d{{1,2}})\s*(?:min(?:uten|utes?)?\b|'))?"
    r"|(?<![\w.,:])(?P<m>\d{2,3})\s*(?:min(?:uten|utes?)?\b|')",
    re.IGNORECASE,
)
# "60–90 min", "2-3 Stunden", "bis zu": a range is not a promise of one value.
_RANGE = re.compile(r"\d\s*(?:[–-]|bis|to)\s*\d|bis zu|up to", re.IGNORECASE)
# A sentence about what was ridden, not what is planned.
_PAST = re.compile(
    r"\b(?:gefahren|geradelt|absolviert|geschafft|warst|hattest|gestern|vorgestern|"
    r"rode|ridden|did|yesterday)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])|\n+")


def _stated_minutes(sentence: str) -> list[int]:
    found = []
    for match in _DURATION.finditer(sentence):
        # "4 x 10 min" is a block of the session, not its length.
        if re.search(r"[x×]\s*$", sentence[: match.start()], re.IGNORECASE):
            continue
        if match.group("m"):
            found.append(int(match.group("m")))
            continue
        if match.group("hw"):
            hours = _HOUR_WORDS[match.group("hw").casefold()]
        else:
            hours = float(match.group("h").replace(",", "."))
        minutes = (
            hours * 60 + int(match.group("hm") or 0) + int(match.group("extra") or 0)
        )
        found.append(round(minutes))
    return found


def _planned_minutes(update: dict) -> tuple[int, int] | None:
    """The span of minutes an update sets, or None when it sets none."""

    def number(key):
        value = update.get(key)
        return int(value) if isinstance(value, (int, float)) and value > 0 else None

    exact = number("durationMinutes")
    low, high = number("durationMinMinutes"), number("durationMaxMinutes")
    if low and high:
        return min(low, high), max(low, high)
    if exact:
        return exact, exact
    return None


def _duration_mismatches(
    response: str, updates: list[dict], today: datetime.date | None
) -> list[tuple[datetime.date, int, int]]:
    """Durations a reply gives for a changed day that its update does not set
    (ai-trainer-ops#150). On 2026-10-10 a reply promised two hours and its
    update set 105 minutes.

    Held narrow, because the cost of a wrong finding is a rewrite the reply did
    not need: only a sentence that names this one day and no other, states one
    duration and no range, and does not talk about a ride already ridden; a
    duration within half to double the update's, so a part of the session is
    not read as its length; and a gap of more than 10 minutes and 10 %.
    """
    sentences = [s for s in _SENTENCE.split(response or "") if s.strip()]
    found: list[tuple[datetime.date, int, int]] = []
    for update in updates:
        if update.get("completed"):
            continue
        planned = _planned_minutes(update)
        try:
            day = datetime.date.fromisoformat(str(update.get("date")))
        except ValueError:
            continue
        if planned is None or (today is not None and day < today):
            continue
        others = [day + datetime.timedelta(days=n) for n in range(-6, 7) if n]
        for sentence in sentences:
            if not _mentions_day(sentence, day, today):
                continue
            if any(_mentions_day(sentence, other, today) for other in others):
                continue
            if _RANGE.search(sentence) or _PAST.search(sentence):
                continue
            stated = _stated_minutes(sentence)
            if len(stated) != 1:
                continue
            said = stated[0]
            low, high = planned
            gap = max(low - said, said - high, 0)
            # Far off is a part of the session ("20 min einfahren"), not its length.
            if not low / 2 <= said <= high * 2:
                continue
            if gap > 10 and gap > 0.1 * high:
                found.append((day, said, high if said > high else low))
                break
    return found


def _reply_findings(
    result: dict,
    audited_context: list,
    prior_check,
    current_ftp: object = None,
    question: str = "",
    reply_language: str | None = None,
    plan: list[dict] | None = None,
    today: datetime.date | None = None,
) -> _Findings:
    """The numbers a reply cannot trace, the priors it breaks or leaves broken,
    and a change it claims but does not carry."""
    updates = result.get("plan_updates") or []
    context = _with_plan_change(audited_context, updates)
    numbers = list(
        dict.fromkeys(
            f.detail
            for f in output_audit.unexplained_numbers(result["response"], context)
        )
    )
    held = prior_check(updates) if prior_check else []
    touched = [u.get("date") for u in updates if isinstance(u, dict)]
    departures = plan_priors.hard_departures_on(held, touched, plan_priors.REPLY_PRIORS)
    standing = [d for d in held if d not in departures]
    # A ride's label and note are changes the athlete sees too; an update that
    # only rewords a day is none (#150).
    ridden = [u for u in updates if isinstance(u, dict) and _changes_the_ride(u, plan)]
    carried = (
        ridden or result.get("ride_label_update") or result.get("ride_note_update")
    )
    # Only where the plan is known: without it every update counts as ridden.
    unmentioned = (
        tuple(_unmentioned_changes(result["response"], ridden, today, question))
        if plan is not None
        else ()
    )
    durations = tuple(_duration_mismatches(result["response"], ridden, today))
    claim = None if carried else _claimed_change(result["response"])
    ftp_claim = _claimed_ftp_change(result["response"], current_ftp)
    ftp_stated = _stated_other_ftp(result["response"], current_ftp, question)
    # Named in the prompt since #78, and the model still answered German in
    # English (ai-trainer-ops#149).
    language = (
        reply_language
        if in_other_language(result["response"], reply_language)
        else None
    )
    return _Findings(
        numbers,
        departures,
        standing,
        claim,
        ftp_claim,
        ftp_stated,
        _profile_watts(current_ftp),
        language,
        unmentioned,
        durations,
        _promised_result(result["response"]),
    )


def _standing_request(departures: list[PriorDeparture]) -> str:
    """What the coach is told when it leaves a race or state prior broken in the coming week."""
    lines = [
        "The plan breaks planning priors that this app holds every plan to, in the "
        "coming days, on days your reply does not change:"
    ]
    lines.extend(f"  - [{d.evidence}] {d.statement}" for d in departures)
    lines.append(
        "This is a protective change: make it in planUpdates in this reply, on every "
        "affected day, say what you changed, and say that the athlete only has to "
        "tell you to get the old session back. Keep such a session only for a reason "
        "the athlete gave you, and then say that reason in one sentence."
    )
    return "\n".join(lines)


def _rewrite_request(findings: _Findings) -> str:
    """What the coach is told before it writes the reply a second time."""
    parts = ["Automatic check, not the athlete:"]
    if findings.numbers:
        parts.append(
            "your reply states numbers that appear nowhere in what you were given "
            f"and cannot be computed from it: {', '.join(findings.numbers)}. Every "
            "number you state must come from the context or be computed from it."
        )
    if findings.departures:
        parts.append(plan_priors.rewrite_request(findings.departures))
    if findings.standing:
        parts.append(_standing_request(findings.standing))
    if findings.claim:
        parts.append(
            f'your reply says the plan changed ("{findings.claim}"), but it carries no '
            "change to what the athlete rides (an update that only rewords a day is "
            "none), no ride label and no ride note, so nothing changes for the athlete. "
            "If you meant to make that change, carry it: planUpdates for the plan, "
            "ride_label_update or ride_note_update for a ride. If not, write the reply "
            "so it does not say anything changed."
        )
    if findings.ftp_claim:
        parts.append(
            f'your reply says the FTP was set ("{findings.ftp_claim}"), but nothing in '
            "this turn changed the athlete's FTP. Do not say it changed. An FTP the "
            "athlete states in the chat is offered below your reply and changes only "
            "when they confirm it; they can also set it in Settings."
        )
    if findings.ftp_stated:
        parts.append(
            "your reply gives the athlete's FTP as a figure their profile does not "
            f'hold ("{findings.ftp_stated}"). Their FTP is {findings.profile_ftp} W, '
            "and every target in their plan is computed from it. Name that figure, "
            "or no FTP at all."
        )
    if findings.language:
        parts.append(
            f"your reply is not written in {findings.language}, the language the "
            f"athlete writes to you in. Write the whole reply in {findings.language}."
        )
    if findings.unmentioned:
        days = ", ".join(
            f"{_ENGLISH_WEEKDAYS[d.weekday()]} {d.isoformat()}"
            for d in findings.unmentioned
        )
        parts.append(
            f"your planUpdates change {days}, and your reply does not mention it. Say "
            "in the reply what you changed on each of those days, or leave them as "
            "they were."
        )
    for day, said, planned in findings.durations:
        parts.append(
            f"your reply gives {_ENGLISH_WEEKDAYS[day.weekday()]} {day.isoformat()} "
            f"{said} minutes, and your planUpdates set {planned} minutes for it. Make "
            "the reply and the update say the same duration."
        )
    if findings.promise:
        parts.append(
            f'your reply promises a result ("{findings.promise}"). Nobody can: say what '
            "it depends on and what you still need to know from the athlete."
        )
    parts.append("Write your whole reply again, in the same JSON form.")
    return " ".join(parts)


# Words that only the notes below put into a reply, in both languages. The wave
# report (``scripts/wave_gates_report``) counts the replies that reached an athlete
# carrying them, so a change to the notes must keep these in them.
NUMBER_NOTE_MARKERS = (
    "sich nicht aus deinen Daten ableiten",
    "could not be traced to your data",
)
RULE_NOTE_MARKERS = (
    "weicht von einer Planungsregel ab",
    "departs from a planning rule",
)


def _findings_notes(findings: _Findings, language: str | None) -> list[ReplyNote]:
    """The notes for the athlete about what a rewrite did not fix (#262)."""
    german = language == "German"
    notes: list[ReplyNote] = []
    if findings.numbers:
        listed = ", ".join(findings.numbers)
        notes.append(
            reply_notes.note(
                reply_notes.UNTRACED_NUMBERS,
                f"{listed} in dieser Antwort "
                f"{'ließ' if len(findings.numbers) == 1 else 'ließen'} sich nicht aus deinen "
                "Daten ableiten."
                if german
                else f"{listed} in this reply could not be traced to your data.",
            )
        )
    labels = list(
        dict.fromkeys(_PRIOR_NOTE_LABELS[d.prior][german] for d in findings.departures)
    )
    if labels:
        notes.append(
            reply_notes.note(
                reply_notes.PLAN_CHANGE_PRIORS,
                f"Diese Planänderung weicht von einer Planungsregel ab: {', '.join(labels)}."
                if german
                else f"This plan change departs from a planning rule: {', '.join(labels)}.",
            )
        )
    labels = list(
        dict.fromkeys(_PRIOR_NOTE_LABELS[d.prior][german] for d in findings.standing)
    )
    if labels:
        notes.append(
            reply_notes.note(
                reply_notes.STANDING_PRIORS,
                "Dein Plan für die nächsten Tage weicht von einer Planungsregel "
                f"ab: {', '.join(labels)}."
                if german
                else "Your plan for the coming days departs from a planning rule: "
                f"{', '.join(labels)}.",
            )
        )
    if findings.claim:
        notes.append(
            reply_notes.note(
                reply_notes.UNCARRIED_CLAIM,
                "Diese Antwort nennt eine Planänderung, die dein Plan nicht enthält."
                if german
                else "This reply names a plan change that your plan does not contain.",
            )
        )
    if findings.ftp_claim:
        notes.append(
            reply_notes.note(
                reply_notes.FTP_CLAIM,
                "Diese Antwort sagt, deine FTP sei geändert. Sie wurde nicht "
                "geändert; du kannst sie in den Einstellungen setzen."
                if german
                else "This reply says your FTP was changed. It was not; you can set it "
                "in Settings.",
            )
        )
    if findings.ftp_stated:
        notes.append(
            reply_notes.note(
                reply_notes.FTP_STATED,
                "Diese Antwort nennt eine andere FTP als deine; deine Ziele "
                f"sind aus {findings.profile_ftp} W berechnet."
                if german
                else "This reply names an FTP other than yours; your targets are "
                f"computed from {findings.profile_ftp} W.",
            )
        )
    if findings.language:
        notes.append(
            reply_notes.note(
                reply_notes.LANGUAGE,
                "Diese Antwort ist nicht auf Deutsch geschrieben, obwohl du "
                "Deutsch schreibst."
                if german
                else "This reply is not in English, the language you write in.",
            )
        )
    if findings.promise:
        notes.append(
            reply_notes.note(
                reply_notes.PROMISE,
                "Ein Ergebnis kann niemand versprechen; es hängt davon ab, wie das "
                "Training läuft und was dein Körper mitmacht."
                if german
                else "No one can promise a result; it depends on how the training goes "
                "and how your body responds.",
            )
        )
    # A changed day the reply still does not name gets no note: the plan line
    # under the reply already says the plan changed, and the app's sentence
    # about its own coach read as a machine talking (owner, 2026-10-24,
    # ai-trainer-ops#239: "das kann raus"). The rewrite still asks for it.
    return notes


async def _ask_trainer_reply(
    provider: str, system_prompt: str, messages: list[dict], audited_context: list
) -> tuple[dict, str]:
    """One coach reply and its raw text, retried while it comes back empty.

    Every model reply is counted and audited here, the first attempt of a
    rewritten answer included: its findings are what caused the rewrite.
    ``coach_rewrites_total`` says how many answers took two (ai-trainer-ops#87).
    """
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
                output_audit.report(prose, audited_context, surface="ask_trainer")
                return _coach_result({}, prose), raw
            parsed = {}

        # --- Task 2: Strip "thinking" — never expose internal reasoning ---
        parsed.pop("thinking", None)
        response = parsed.get("response")
        if isinstance(response, str) and response.strip():
            metrics.record_coach_reply(contract="json")
            output_audit.report(
                response,
                _with_plan_change(audited_context, parsed.get("planUpdates")),
                surface="ask_trainer",
            )
            return _coach_result(parsed, response.strip()), raw

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


def strength_in_play(profile: dict, plan: list[dict]) -> bool:
    """Whether the chat needs the rule for prescribing a strength session (#257).

    It does when strength is one of the athlete's sports or a day of the plan is
    a strength session, in the new vocabulary or the old (``workoutType``).
    """
    # ponytail: an athlete with neither who asks for a gym day this turn gets the
    # session without the RIR guidance; read the message too if that shows up.
    if "strength" in (profile.get("sports") or []):
        return True
    # The old vocabulary's workoutType is compared lowercased, as schemas.py
    # reads it; sport is already normalised (normalise_plan_sport).
    return any(
        "strength"
        in (
            str(day.get("sport") or "").lower(),
            str(day.get("workoutType") or "").lower(),
        )
        for day in plan
    )


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
    workout_curiosity: dict | None = None,
    rider_identity: dict | None = None,
    plan_changes_section: str = "",
    plan_coherence_warnings: str = "",
    plan_commitment_notes: str = "",
    prior_check: Callable[[list[dict] | None], list[PriorDeparture]] | None = None,
    interface_language: str | None = None,
    done_minutes: dict[tuple[str, int], int] | None = None,
    app_offers: list[str] | None = None,
    climb_section: str = "",
    reply_language: str | None = None,
) -> dict:
    """One coach reply. ``done_minutes`` holds the minutes ridden per matched
    session, by date and slot (``ride_matching.done_minutes_by_session``).
    ``app_offers`` names what the buttons under the reply offer, none of it
    saved yet (ai-trainer-ops#195). ``climb_section`` is the app's climb watts
    (``climb_power.prompt_section``, ai-trainer-ops#188).
    ``prior_check`` returns the hard planning priors a reply's
    ``planUpdates`` would break (``plan_pipeline.update_prior_check``); with it,
    such a reply is written once more, as is one stating a number the context
    cannot explain (ai-trainer-ops#87). So is one that leaves a race or state
    prior broken in the coming week, when the check was built with ``today``
    (ai-trainer-ops#103 Q33, Q37), and one that says the plan changed while it
    carries no change (Q33)."""
    today_date = app_today(timezone_name=timezone_name)
    today = today_date.isoformat()
    date_context = app_date_context(timezone_name=timezone_name)
    # The router decides it once and writes its own notes in it (#262); a caller
    # that does not is given the same rule here.
    if reply_language is None:
        reply_language = (
            athlete_language(
                question,
                [
                    str(m.get("content") or "")
                    for m in (conversation_history or [])
                    if m.get("role") == "user"
                ],
            )
            or interface_language
        )  # their words first, the interface next (#121)
    done = done_minutes or {}

    def slim(day: dict) -> dict:
        done_here = done.get(schemas.session_key(day))
        return _slim_plan_entry(day, today_date=today_date, done_minutes=done_here)

    last_7_days = [slim(day) for day in plan if day.get("date", "") < today][
        -MAX_PLAN_DAYS_PAST:
    ]
    next_7_days = [slim(day) for day in plan if day.get("date", "") >= today][
        :MAX_PLAN_DAYS_AHEAD
    ]
    # The whole notes, cut at the end only past ``coach_memory.PROMPT_CHARS``
    # (ai-trainer-ops#119).
    marked_memory = untrusted_text.mark(
        coach_memory, limit=coach_memory_notes.PROMPT_CHARS
    )
    memory_section = (
        # Marked: the coach's own notes are written by an earlier model turn out
        # of whatever the athlete and Strava supplied, and they are *durable* —
        # an instruction that lands here is re-read on every future turn rather
        # than once (ai-trainer-ops#33).
        f"\n\nCoach notes about this athlete (remember these):\n{marked_memory}"
        if marked_memory
        else ""
    )

    assessment_section = ask_trainer_assessment_section(
        rider_assessment, current_ftp=profile.get("currentFTP")
    )
    workout_section = ask_trainer_workout_section(context_workout)
    plan_updates_rule = ask_trainer_plan_updates_rule(
        context_workout, strength=strength_in_play(profile, plan)
    )

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
        workout_curiosity=workout_curiosity,
        rider_identity=rider_identity,
        plan_changes_section=plan_changes_section,
        plan_coherence_warnings=plan_coherence_warnings,
        plan_commitment_notes=plan_commitment_notes,
        reply_language=reply_language,
        app_offers=app_offers,
        climb_section=climb_section,
    )
    system_prompt = "".join(prompt_sections.values())
    # Which part of the prompt is big is a question about the athlete's real
    # data, so it is answered here rather than by a script rebuilding it (#556).
    token_accounting.log_prompt_sections(system_prompt, prompt_sections)
    history = (conversation_history or [])[-MAX_CONVERSATION_HISTORY:]
    date_stamp = app_today_stamp(timezone_name=timezone_name)
    messages = [*history, {"role": "user", "content": f"{date_stamp}\n{question}"}]

    # What the coach was given, for the provenance half of the output audit
    # (ai-trainer-ops#28): the prompt is already the deterministic model's
    # numbers rendered for the model to read, so it needs no second assembly.
    # The messages belong in it too — an athlete who writes "my FTP is 290" has
    # supplied that number, and the coach repeating it invented nothing.
    audited_context = [system_prompt, messages]

    result, raw = await _ask_trainer_reply(
        provider, system_prompt, messages, audited_context
    )
    current_ftp = profile.get("currentFTP")
    findings = _reply_findings(
        result,
        audited_context,
        prior_check,
        current_ftp,
        question,
        reply_language,
        plan,
        today_date,
    )
    result["notes"] = []
    if not findings:
        return result

    # Held to the checks, not only reported (ai-trainer-ops#87, the owner's yes to
    # #81 Q26): one rewrite with the findings in the prompt, and whatever survives
    # it reaches the athlete with a note rather than being blocked.
    metrics.record_coach_rewrite(surface="ask_trainer", reason=findings.reason())
    retry_messages = [
        *messages,
        {"role": "assistant", "content": raw},
        {"role": "user", "content": _rewrite_request(findings)},
    ]
    try:
        rewritten, _raw = await _ask_trainer_reply(
            provider, system_prompt, retry_messages, audited_context
        )
    except (AIRateLimitError, AIResponseFormatError, AIProviderError):
        # The first reply is still an answer; the note says what is wrong with
        # it. A 5xx or a timeout on the rewrite threw it away (ai-trainer-ops#120).
        rewritten = result
    else:
        # Judged against the first context: the request names the very numbers it
        # objects to, and a context holding them would explain them away.
        findings = _reply_findings(
            rewritten,
            audited_context,
            prior_check,
            current_ftp,
            question,
            reply_language,
            plan,
            today_date,
        )
    rewritten["rewritten"] = True
    # A departure the coach kept on purpose still gets the note: the owner's
    # choice was a visible note rather than a block, and the reply carries the
    # coach's reason beside it.
    rewritten["notes"] = _findings_notes(findings, reply_language)
    return rewritten


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
    reply_language: str | None = None,
) -> str:
    """The coach's word on a race the athlete added, changed or removed.

    A reply in the other language than ``reply_language`` is asked for once
    more, told plainly; the first stands when the second is no better
    (ai-trainer-ops#258).
    """
    today_date = app_today(timezone_name=timezone_name)
    today = today_date.isoformat()
    upcoming_plan = [
        _slim_plan_entry(day, today_date=today_date)
        for day in plan
        if day.get("date", "") >= today and not day.get("completed")
    ][:14]
    system_prompt = race_event_feedback_system(action, reply_language)
    user_msg = race_event_feedback_user(
        today=today,
        action=action,
        profile=profile,
        event=event,
        race_events=race_events,
        upcoming_plan=upcoming_plan,
        rider_assessment=rider_assessment,
        metrics_history_section=metrics_history_section,
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_COACH
    )
    feedback = _feedback_of(_parse_ai_json(raw))
    if in_other_language(feedback, reply_language):
        metrics.record_coach_rewrite(surface="race_event", reason="language")
        try:
            raw = await _chat(
                provider,
                system_prompt,
                f"{user_msg}\n\nWrite 'feedback' in {reply_language}.",
                json_mode=True,
                task=TASK_COACH,
            )
            again = _feedback_of(_parse_ai_json(raw))
        except (AIRateLimitError, AIResponseFormatError, AIProviderError):
            again = ""
        if again and not in_other_language(again, reply_language):
            feedback = again
    _audit_prose(feedback, system_prompt, user_msg, surface="race_event")
    return feedback


def _feedback_of(parsed: object) -> str:
    return str(parsed.get("feedback") or "") if isinstance(parsed, dict) else ""


async def update_coach_memory(
    current_memory: str,
    user_message: str,
    coach_response: str,
    provider: str = "openai",
) -> str:
    """The notes after this exchange: the writer's reply when it is notes, else
    the notes as they were (ai-trainer-ops#119)."""
    system_prompt = update_memory_system()
    user_msg = update_memory_user(current_memory, user_message, coach_response)
    reply = await _chat(provider, system_prompt, user_msg, task=TASK_CLASSIFY)
    reason = coach_memory_notes.rejection(current_memory, reply)
    if reason is not None:
        # Lengths only: the notes are the athlete's health data.
        logger.warning(
            "Coach memory update rejected (%s): %d characters for %d",
            reason,
            len((reply or "").strip()),
            len((current_memory or "").strip()),
        )
        return current_memory
    # The same notes give the router nothing to write, whitespace aside.
    if reply.strip() == (current_memory or "").strip():
        return current_memory
    return reply.strip()


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
    category = (
        category.strip()
        if isinstance(category, str) and category.strip()
        else "general"
    )
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
    kind = (
        "fact"
        if isinstance(kind_raw, str) and kind_raw.strip().lower() == "fact"
        else "observation"
    )
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
        if len(candidates) >= MAX_EXTRACTED_FACT_CANDIDATES:
            break
    # What survived normalisation is what the athlete can be shown (#254).
    _audit_prose(
        "\n".join(candidate["fact"] for candidate in candidates),
        system_prompt,
        user_msg,
        surface="facts",
    )
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
    # The facts that survived normalisation are what the athlete sees as
    # observations; the dropped ones are never shown, so they are not audited.
    _audit_prose(
        "\n".join(candidate["fact"] for candidate in candidates),
        system_prompt,
        user_msg,
        surface="insights",
    )
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
    # A compound, narrowly conditioned claim cannot recur, and a claim that
    # cannot recur can never be confirmed or refuted — which is how 96 % of
    # stored hypotheses ended up stuck at one observation. The prompt asks for
    # one condition and one outcome; this is where that is actually enforced,
    # because an instruction the pipeline does not check is a suggestion (#581).
    if not uncertainty_lifecycle.is_falsifiable_statement(statement):
        logger.info(
            "Hypothesis candidate dropped as unfalsifiable (%s chars): %s",
            len(" ".join(statement.split())),
            statement[:120],
        )
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
    # What survived normalisation is what the athlete can be shown (#254).
    _audit_prose(
        "\n".join(candidate["statement"] for candidate in candidates),
        system_prompt,
        user_msg,
        surface="hypotheses",
    )
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
    *,
    now: datetime.datetime | None = None,
    timezone_name: str | None = None,
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
        metrics_section,
        existing_facts,
        asked_questions,
        now=now,
        timezone_name=timezone_name,
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
    # What survived normalisation is what the athlete can be shown (#254).
    _audit_prose(
        "\n".join(candidate["question"] for candidate in candidates),
        system_prompt,
        user_msg,
        surface="inquiries",
    )
    return candidates


async def evaluate_inquiry_answer(
    question: str,
    answer: str,
    why_asking: str = "",
    settings_hint: str = "",
    is_final_attempt: bool = False,
    provider: str = "openai",
    earlier: list[str] | None = None,
    interface_language: str | None = None,
) -> dict:
    """Judge whether an athlete's reply answered the inquiry it was pinned to (#506).

    Returns ``{"answered": bool, "fact": str, "reply": str, "question": str,
    "asks_coach": bool}``: ``fact`` is the durable third-person sentence to store
    when answered, ``reply`` is what the coach says back, ``question`` is the
    rephrased ask when the coach gets another attempt (empty on the final one),
    and ``asks_coach`` says the answer also asks for something only the coach
    chat can do, such as a plan change (ai-trainer-ops#103 Q28).

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
        "asks_coach": False,
    }

    # A short answer ("5 h", "Noch müde") names no language on its own, so the
    # athlete's earlier messages are read too, then the interface (#121, #258).
    expected = athlete_language(cleaned_answer, earlier) or interface_language
    system_prompt = evaluate_inquiry_answer_system(
        is_final_attempt, reply_language=expected
    )
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

    def _text(source: dict, key: str, limit: int = 400) -> str:
        value = source.get(key)
        return value.strip()[:limit] if isinstance(value, str) else ""

    def _athlete_text(source: dict) -> str:
        return f"{_text(source, 'reply')} {_text(source, 'question', 200)}"

    # Told the athlete's language, the reply still came back in English to a
    # German answer (ai-trainer-ops#149). One more call, told it plainly. If
    # that is wrong too, the coach says nothing rather than the wrong language:
    # the verdict and the fact stand, and an empty question keeps the one
    # already pinned.
    if in_other_language(_athlete_text(parsed), expected):
        metrics.record_coach_rewrite(surface="inquiry_answer", reason="language")
        try:
            raw = await _chat(
                provider,
                system_prompt,
                f"{user_msg}\n\nWrite 'reply' and 'question' in {expected}.",
                json_mode=True,
                task=TASK_CLASSIFY,
            )
            again = _parse_ai_json(raw)
        except Exception:
            logger.warning("Inquiry answer language retry failed", exc_info=True)
            again = None
        if (
            isinstance(again, dict)
            and "answered" in again
            and not in_other_language(_athlete_text(again), expected)
        ):
            parsed = again
        else:
            parsed = {**parsed, "reply": "", "question": ""}

    _audit_prose(
        _athlete_text(parsed).strip(), system_prompt, user_msg, surface="inquiry_answer"
    )
    return {
        "answered": bool(parsed.get("answered")),
        "fact": _text(parsed, "fact", 240),
        "reply": _text(parsed, "reply"),
        "question": _text(parsed, "question", 200),
        "asks_coach": parsed.get("asksCoach") is True,
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
    # What survived normalisation is what the athlete can be shown (#254).
    _audit_prose(
        "\n".join(candidate["question"] for candidate in candidates),
        system_prompt,
        user_msg,
        surface="open_questions",
    )
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
    # What survived normalisation is what the athlete can be shown (#254).
    _audit_prose(
        "\n".join(candidate["protocol"] for candidate in candidates),
        system_prompt,
        user_msg,
        surface="experiments",
    )
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
    user_msg = generate_athlete_predictions_user(metrics_section, existing_predictions)
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
    # What survived normalisation is what the athlete can be shown (#254).
    _audit_prose(
        "\n".join(candidate["prediction"] for candidate in candidates),
        system_prompt,
        user_msg,
        surface="predictions",
    )
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

    evaluations_raw = parsed.get("evaluations") if isinstance(parsed, dict) else None
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
    _audit_prose(
        f"{parsed.get('feedback') or ''} {parsed.get('follow_up_question') or ''}".strip(),
        system_prompt,
        user_msg,
        surface="rate_workout",
    )
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
    latest_ride_sport_type: str | None = None,
    latest_ride_avg_power_w: int | None = None,
    latest_ride_tss: float | None = None,
    latest_ride_date: str | None = None,
    reply_language: str | None = None,
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
    system_prompt = refresh_login_summary_system(reply_language)
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
        latest_ride_sport_type=latest_ride_sport_type,
        latest_ride_avg_power_w=latest_ride_avg_power_w,
        latest_ride_tss=latest_ride_tss,
        latest_ride_date=(
            anchored_activity_date(latest_ride_date, today)
            if latest_ride_date
            else None
        ),
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    parsed = _parse_ai_json(raw)
    summary = parsed.get("loginSummary") or ""
    if not _is_complete_login_summary(summary):
        return ""
    _audit_prose(summary, system_prompt, user_msg, surface="login_summary")
    return summary


async def generate_training_status(
    facts: dict,
    training_plan: list[dict] | None = None,
    provider: str = "openai",
    timezone_name: str | None = None,
    reply_language: str | None = None,
) -> tuple[str, str, str] | None:
    """Have the coach write the dashboard status badge from deterministic facts.

    Returns ``(label, tone, rationale)``, or ``None`` when the model produced
    nothing usable — callers fall back to
    :func:`services.training_status.fallback_status` so the chip is never blank.

    ``reply_language`` names the athlete's language. A badge still written in
    the other one is asked for once more, told plainly; if that fails as well,
    the fallback stands in, and it speaks their language (ai-trainer-ops#258).
    """
    system_prompt = training_status_system(reply_language)
    user_msg = training_status_user(
        facts, training_plan=training_plan, timezone_name=timezone_name
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True, task=TASK_PLAN)
    status = _usable_training_status(_parse_ai_json(raw))
    if status and in_other_language(f"{status[0]}. {status[2]}", reply_language):
        metrics.record_coach_rewrite(surface="training_status", reason="language")
        raw = await _chat(
            provider,
            system_prompt,
            f"{user_msg}\n\nWrite 'label' and 'rationale' in {reply_language}.",
            json_mode=True,
            task=TASK_PLAN,
        )
        status = _usable_training_status(_parse_ai_json(raw))
        if status and in_other_language(f"{status[0]}. {status[2]}", reply_language):
            status = None
    if status is None:
        return None
    label, _, rationale = status
    _audit_prose(
        f"{label}. {rationale}", system_prompt, user_msg, surface="training_status"
    )
    return status


def _usable_training_status(parsed: object) -> tuple[str, str, str] | None:
    """``(label, tone, rationale)`` from a parsed reply, or None when the chip cannot render it."""
    parsed = parsed if isinstance(parsed, dict) else {}
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
    review = parsed.get("review") or ""
    _audit_prose(review, system_prompt, user_msg, surface="ride_review")
    return review


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
    prior_check: Callable[[list[dict] | None], list[PriorDeparture]] | None = None,
    athlete_words: str = "",
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
        athlete_words=athlete_words,
    )
    raw = await _chat(
        provider, system_prompt, user_msg, json_mode=True, task=TASK_FEEDBACK
    )
    parsed = _parse_ai_json(raw)
    departures = (
        prior_check(parsed.get("planUpdates") or []) if prior_check is not None else []
    )
    if departures:
        raw = await _chat(
            provider,
            system_prompt,
            user_msg + _held_to_priors("next_session", departures),
            json_mode=True,
            task=TASK_FEEDBACK,
        )
        parsed = _parse_ai_json(raw)
    # One audit for the pair: both are shown together as one recommendation, and
    # counting them apart would double the denominator for this surface.
    _audit_prose(
        "\n".join(
            part
            for part in (
                parsed.get("response"),
                parsed.get("next_session_recommendation"),
            )
            if isinstance(part, str)
        ),
        system_prompt,
        user_msg,
        surface="next_session",
    )
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
    reply_language: str | None = None,
) -> str:
    """Generate an updated loginSummary from a batch of rides with fresh athlete feedback.

    *rides* is a list of RideMetric ORM objects (or duck-typed equivalents) sorted
    oldest-first.  Returns the summary string, or an empty string on failure.
    """
    if not rides:
        return ""
    system_prompt = process_pending_feedbacks_system(reply_language)
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
        _audit_prose(summary, system_prompt, user_msg, surface="login_summary")
        return summary
    # The fallback is built from the rides by code, not by the model: there is
    # nothing at the seam to audit, and counting it would dilute the rate.
    return _fallback_login_summary_from_rides(rides)
