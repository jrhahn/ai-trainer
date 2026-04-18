"""Backend AI service.

This is a focused Python port of the frontend ai.ts service. It keeps prompt
content and response shapes aligned with the existing UI so the frontend can
switch to backend fetches without semantic changes.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from google import genai
from google.genai import types
from json_repair import repair_json
from openai import AsyncOpenAI

from .analysis import (
    _AVG_POWER_TO_FTP_RATIO,
    _best_n_min_power,
    _build_ride_analysis,
    _classify_ride_purpose,
    _compute_hr_drift,
    _compute_hr_zones,
    _compute_training_load,
    _detect_intervals,
    _hr_corrected_ftp,
    _LTHR_RATIO,
    _TEMPO_THRESHOLD_PCT,
)
from .prompts import (
    analyse_activities_system,
    analyse_activities_user,
    generate_plan_system,
    generate_plan_user,
    adapt_plan_system,
    adapt_plan_user,
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
)
MAX_CONVERSATION_HISTORY = 20
OPENAI_MODEL = "gpt-4o-mini"
GEMINI_MODEL = "gemini-2.0-flash"

logger = logging.getLogger(__name__)

def _make_openai() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))


def _make_gemini() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    return genai.Client(api_key=api_key)


async def _openai_chat(system_prompt: str, user_msg: str, json_mode: bool = False) -> str:
    client = _make_openai()
    kwargs: dict[str, Any] = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        **kwargs,
    )
    return response.choices[0].message.content or ""


async def _openai_chat_history(
    system_prompt: str, messages: list[dict[str, str]], json_mode: bool = False
) -> str:
    client = _make_openai()
    kwargs: dict[str, Any] = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": system_prompt}, *messages],
        **kwargs,
    )
    return response.choices[0].message.content or ""


async def _gemini_chat(system_prompt: str, user_msg: str, json_mode: bool = False) -> str:
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json" if json_mode else None,
    )
    async with _make_gemini().aio as client:
        response = await client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_msg,
            config=config,
        )
    return response.text or ""


async def _gemini_chat_history(
    system_prompt: str, messages: list[dict[str, str]], json_mode: bool = False
) -> str:
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json" if json_mode else None,
    )
    contents = [
        types.Content(
            role="model" if msg["role"] == "assistant" else "user",
            parts=[types.Part.from_text(text=msg["content"])],
        )
        for msg in messages
    ]
    async with _make_gemini().aio as client:
        response = await client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )
    return response.text or ""


def _parse_ai_json(text: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    extracted = fenced.group(1).strip() if fenced else text.strip()
    stripped = re.sub(r"(\d+)\s+[a-zA-Z_]+(?=\s*[,}\]\n])", r"\1", extracted)
    repaired = repair_json(stripped)
    return json.loads(repaired)


async def _chat(provider: str, system_prompt: str, user_msg: str, json_mode: bool = False) -> str:
    if provider == "gemini":
        return await _gemini_chat(system_prompt, user_msg, json_mode)
    return await _openai_chat(system_prompt, user_msg, json_mode)


async def _chat_history(
    provider: str, system_prompt: str, messages: list[dict[str, str]], json_mode: bool = False
) -> str:
    if provider == "gemini":
        return await _gemini_chat_history(system_prompt, messages, json_mode)
    return await _openai_chat_history(system_prompt, messages, json_mode)


async def analyse_strava_activities(
    activities: list[dict],
    provider: str = "openai",
    streams_by_id: dict[str, dict] | None = None,
    max_heart_rate: int | None = None,
) -> dict:
    # --- Algorithmic computation from per-activity stream data ---
    computed_ftp: int | None = None
    computed_threshold_hr: int | None = None
    computed_hr_zones: dict | None = None
    ride_analyses: dict[str, dict] = {}  # activity_id → per-ride analysis

    if streams_by_id:
        # Candidate FTP values from different methods; we take the best (highest).
        ftp_candidates: list[int] = []
        threshold_hrs: list[int] = []

        for act_id, streams in streams_by_id.items():
            watts_data: list[float] = streams.get("watts", {}).get("data", [])
            hr_data: list[float] = streams.get("heartrate", {}).get("data", [])
            time_data: list[float] = streams.get("time", {}).get("data", [])

            if watts_data and time_data:
                # --- Method 1: best-20-min power × 0.95 ---
                best20, start20, end20 = _best_n_min_power(watts_data, time_data, 20)
                if best20 is not None:
                    # FTP is conventionally defined as 95% of best 20-min average power.
                    # This scaling factor accounts for the difference between a maximal
                    # 20-min effort and a true 60-min sustainable power output.
                    ftp_candidates.append(round(best20 * 0.95))
                    # Track average HR during that segment for threshold-HR estimation.
                    if hr_data and len(hr_data) == len(time_data):
                        segment_hr = hr_data[start20 : end20 + 1]
                        if segment_hr:
                            threshold_hrs.append(round(sum(segment_hr) / len(segment_hr)))

                # --- Method 2: HR-corrected FTP from 10-min and 20-min best intervals ---
                # For each interval length, if we have both power and HR data, scale the
                # interval power to what it would be at exactly the lactate-threshold HR.
                # This is useful when the rider never executed a maximal 20-min effort but
                # did push hard intervals where HR gives us a physiological reference point.
                if hr_data and len(hr_data) == len(time_data) and max_heart_rate:
                    for n_min in (10, 15, 20):
                        best_n, s, e = _best_n_min_power(watts_data, time_data, n_min)
                        if best_n is not None:
                            seg_hr = hr_data[s : e + 1]
                            if seg_hr:
                                avg_interval_hr = sum(seg_hr) / len(seg_hr)
                                ftp_hr = _hr_corrected_ftp(best_n, avg_interval_hr, max_heart_rate)
                                if ftp_hr is not None:
                                    ftp_candidates.append(ftp_hr)

        if ftp_candidates:
            # The best estimate is the highest plausible value across all methods.
            computed_ftp = max(ftp_candidates)
        if threshold_hrs:
            computed_threshold_hr = round(sum(threshold_hrs) / len(threshold_hrs))

        # --- Per-ride analysis: category + interval detection + HR drift ---
        # Use the best available FTP estimate; fall back to a rough proxy from avg power
        # if no algorithmic estimate is available yet.
        ftp_for_analysis = float(computed_ftp) if computed_ftp else None
        if ftp_for_analysis is None:
            # Rough proxy: compute global average power across all activities with power data
            all_avg_watts = [a.get("averageWatts") or a.get("average_watts") for a in activities]
            valid = [w for w in all_avg_watts if w and w > 0]
            if valid:
                ftp_for_analysis = float(sum(valid) / len(valid)) * _AVG_POWER_TO_FTP_RATIO
        if ftp_for_analysis and ftp_for_analysis > 0:
            for act_id, streams in streams_by_id.items():
                analysis = _build_ride_analysis(streams, ftp_for_analysis)
                if analysis:
                    # Find the matching activity name for context
                    act_name = next(
                        (a.get("name", act_id) for a in activities if str(a.get("id")) == act_id),
                        act_id,
                    )
                    ride_analyses[act_name] = analysis

    if max_heart_rate:
        computed_hr_zones = _compute_hr_zones(max_heart_rate)

    # --- Build the contextual section describing computed metrics ---
    computed_section = ""
    if computed_ftp is not None:
        computed_section += (
            f"\nAlgorithmically estimated FTP from stream data: {computed_ftp} W "
            "(95 % of best 20-min average power)"
        )
    if computed_threshold_hr is not None:
        computed_section += (
            f"\nAlgorithmically estimated threshold HR: {computed_threshold_hr} bpm "
            "(average HR during best 20-min power effort)"
        )
    if max_heart_rate is not None:
        computed_section += f"\nMax heart rate provided by athlete: {max_heart_rate} bpm"
    if computed_hr_zones is not None:
        zones_str = ", ".join(
            f"Zone {i}: {z['low']}–{z['high']} bpm"
            for i, z in enumerate(computed_hr_zones.values(), 1)
        )
        computed_section += f"\nHR training zones: {zones_str}"

    system_prompt = analyse_activities_system()

    # Build the per-ride analysis section for the AI prompt
    ride_analyses_section = ""
    if ride_analyses:
        ride_analyses_str = json.dumps(ride_analyses, indent=2)
        ride_analyses_section = (
            f"\n\nAlgorithmic per-ride analysis (computed from stream data):\n{ride_analyses_str}"
        )

    user_msg = analyse_activities_user(activities, computed_section, ride_analyses_section)

    raw = await _chat(provider, system_prompt, user_msg, json_mode=True)
    parsed = _parse_ai_json(raw)

    # Override with algorithmically derived values so the AI cannot contradict them
    if computed_ftp is not None:
        parsed["estimatedFTP"] = computed_ftp
    if computed_threshold_hr is not None:
        parsed["estimatedThresholdHR"] = computed_threshold_hr
    if computed_hr_zones is not None:
        parsed["hrZones"] = computed_hr_zones

    return parsed


async def generate_training_plan(
    profile: dict, provider: str = "openai", rider_assessment: dict | None = None
) -> list[dict]:
    system_prompt = generate_plan_system()
    assessment_section = (
        f"\nRider assessment from recent Strava rides: {json.dumps(rider_assessment)}"
        if rider_assessment
        else ""
    )
    today = __import__("datetime").datetime.now().date().isoformat()
    user_msg = generate_plan_user(profile, today, assessment_section)
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True)
    parsed = _parse_ai_json(raw)
    return parsed.get("plan", [])


async def adapt_training_plan(
    plan: list[dict],
    recent_feedback: list[dict],
    profile: dict,
    provider: str = "openai",
    rider_assessment: dict | None = None,
) -> list[dict]:
    today = __import__("datetime").datetime.now().date().isoformat()
    incomplete_days = [day for day in plan if not day.get("completed")]
    ftp = float(
        (rider_assessment or {}).get("estimatedFTP")
        or profile.get("currentFTP")
        or 0
    )
    training_load = _compute_training_load(plan, ftp) if ftp > 0 else None
    system_prompt = adapt_plan_system()
    user_msg = adapt_plan_user(
        profile,
        today,
        recent_feedback,
        incomplete_days,
        rider_assessment=rider_assessment,
        training_load=training_load,
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True)
    parsed = _parse_ai_json(raw)
    updated_days = {day["date"]: day for day in parsed.get("updatedDays", [])}
    return [day if day.get("completed") else updated_days.get(day["date"], day) for day in plan]


async def classify_question(question: str) -> dict:
    """Classify an athlete question using a lightweight model (gpt-4o-mini).

    Returns a dict with ``category`` and ``needs_science_rag``.
    On failure returns a safe default (needs_science_rag=False).
    """
    try:
        classify_sys = ask_trainer_classify_system()
        classify_user = ask_trainer_classify_user(question)
        classify_raw = await _openai_chat(classify_sys, classify_user, json_mode=True)
        return _parse_ai_json(classify_raw)
    except Exception:
        logger.warning("Question classification failed; defaulting to no-RAG", exc_info=True)
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
) -> dict:
    today = __import__("datetime").datetime.now().date().isoformat()
    last_7_days = [day for day in plan if day.get("date", "") <= today][-7:]
    next_14_days = [day for day in plan if day.get("date", "") >= today][:14]
    memory_section = f"\n\nCoach notes about this athlete (remember these):\n{coach_memory}" if coach_memory else ""

    assessment_section = ask_trainer_assessment_section(rider_assessment)
    workout_section = ask_trainer_workout_section(context_workout)
    plan_updates_rule = ask_trainer_plan_updates_rule(context_workout)

    # --- Task 1: Compute training load from the plan ---
    ftp = float(
        (rider_assessment or {}).get("estimatedFTP")
        or profile.get("currentFTP")
        or 0
    )
    training_load = _compute_training_load(plan, ftp) if ftp > 0 and plan else None

    system_prompt = ask_trainer_system(
        profile,
        today,
        last_7_days,
        next_14_days,
        assessment_section,
        memory_section,
        workout_section,
        plan_updates_rule,
        science_context=science_context or "",
        training_load=training_load,
        classification=classification,
    )
    history = (conversation_history or [])[-MAX_CONVERSATION_HISTORY:]
    messages = [*history, {"role": "user", "content": question}]
    raw = await _chat_history(provider, system_prompt, messages, json_mode=True)
    parsed = _parse_ai_json(raw)

    # --- Task 2: Strip "thinking" — never expose internal reasoning to the frontend ---
    parsed.pop("thinking", None)

    return {
        "response": parsed.get("response", ""),
        "plan_updates": parsed.get("planUpdates"),
        "sources": parsed.get("sources") or [],
    }


async def update_coach_memory(
    current_memory: str, user_message: str, coach_response: str, provider: str = "openai"
) -> str:
    system_prompt = update_memory_system()
    user_msg = update_memory_user(current_memory, user_message, coach_response)
    return await _chat(provider, system_prompt, user_msg)


async def rate_completed_workout(
    day: dict,
    profile: dict,
    provider: str = "openai",
    stream_delta: dict | None = None,
) -> dict:
    feedback = day.get("feedback")
    if not feedback:
        return {"feedback": "", "flag_for_adaptation": False}

    system_prompt = rate_workout_system()
    user_msg = rate_workout_user(day, feedback, profile, stream_delta=stream_delta)
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True)
    parsed = _parse_ai_json(raw)
    return {
        "feedback": parsed.get("feedback", ""),
        "flag_for_adaptation": bool(parsed.get("flag_for_adaptation", False)),
    }
