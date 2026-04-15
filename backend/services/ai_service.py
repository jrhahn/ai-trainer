"""Backend AI service.

This is a focused Python port of the frontend ai.ts service. It keeps prompt
content and response shapes aligned with the existing UI so the frontend can
switch to backend fetches without semantic changes.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from google import genai
from google.genai import types
from json_repair import repair_json
from openai import AsyncOpenAI

COACH_PERSONA = (
    "You are a professional cycling coach with extensive experience in "
    "competitive road, track, and endurance cycling. "
    "Always address the athlete directly using 'you' — for example, "
    "'You have excellent aerobic endurance' not 'The athlete has excellent aerobic endurance'."
)
MAX_CONVERSATION_HISTORY = 20
OPENAI_MODEL = "gpt-4o-mini"
GEMINI_MODEL = "gemini-2.0-flash"


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


def _best_n_min_power(
    watts: list[float], time_stream: list[float], n_minutes: float
) -> tuple[float | None, int, int]:
    """Find the best average power over a contiguous n-minute window.

    Uses a sliding-window over the time stream (cumulative seconds from start).
    Returns (best_avg_power, start_index, end_index).  Both indices are
    inclusive.  Returns (None, 0, 0) when there is insufficient data.
    """
    target_secs = n_minutes * 60.0
    n = len(watts)
    if n == 0 or len(time_stream) != n:
        return None, 0, 0

    best_power = 0.0
    best_start = 0
    best_end = 0
    left = 0
    window_sum = 0.0

    for right in range(n):
        window_sum += watts[right]
        # Shrink the window from the left until it fits within the target duration
        while time_stream[right] - time_stream[left] > target_secs:
            window_sum -= watts[left]
            left += 1
        actual_dur = time_stream[right] - time_stream[left]
        # Only accept windows that cover at least 90% of the target duration.
        # Shorter windows (e.g. due to GPS gaps at the start or end of a ride)
        # would inflate the average power and produce an unreliable FTP estimate.
        if actual_dur >= target_secs * 0.9:
            count = right - left + 1
            if count > 0:
                avg = window_sum / count
                if avg > best_power:
                    best_power = avg
                    best_start = left
                    best_end = right

    return (best_power if best_power > 0 else None), best_start, best_end


# Fraction of max HR that corresponds to lactate threshold (LTHR).
# 87% is a well-established estimate for trained cyclists.
_LTHR_RATIO = 0.87


def _hr_corrected_ftp(
    interval_power: float,
    interval_hr: float,
    max_hr: int,
) -> int | None:
    """Estimate FTP by scaling interval power based on heart-rate headroom.

    Principle: at threshold, a cyclist should be at approximately LTHR
    (= 87% of max HR).  If the rider held `interval_power` W while their HR
    was `interval_hr`, we scale the power proportionally so it corresponds
    to the threshold HR.

    FTP_hr ≈ interval_power × (LTHR / interval_hr)

    This corrects for under- and over-pacing relative to threshold:
    - If interval HR < LTHR the rider had headroom → actual threshold is higher.
    - If interval HR > LTHR the rider was above threshold → scale down.
    We only apply the correction when the interval HR is between 70% and
    100% of max HR; outside that range the correction is unreliable.
    """
    if interval_hr <= 0 or max_hr <= 0:
        return None
    hr_fraction = interval_hr / max_hr
    if hr_fraction < 0.70 or hr_fraction > 1.0:
        return None
    lthr = max_hr * _LTHR_RATIO
    corrected = interval_power * (lthr / interval_hr)
    return round(corrected)


def _compute_hr_zones(max_hr: int) -> dict:
    """Compute 5 standard HR training zones based on percentage of max HR."""
    return {
        "zone1": {"low": 0, "high": round(max_hr * 0.60)},
        "zone2": {"low": round(max_hr * 0.60), "high": round(max_hr * 0.70)},
        "zone3": {"low": round(max_hr * 0.70), "high": round(max_hr * 0.80)},
        "zone4": {"low": round(max_hr * 0.80), "high": round(max_hr * 0.90)},
        "zone5": {"low": round(max_hr * 0.90), "high": max_hr},
    }


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

    if streams_by_id:
        # Candidate FTP values from different methods; we take the best (highest).
        ftp_candidates: list[int] = []
        threshold_hrs: list[int] = []

        for streams in streams_by_id.values():
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

    system_prompt = (
        f"{COACH_PERSONA} Analyse the provided Strava activities and return a JSON assessment.\n"
        "Return ONLY a valid JSON object with these fields "
        "(all keys double-quoted, numeric values must be plain numbers with no units):\n"
        "- \"estimatedFTP\": integer watts — use the pre-computed value when provided, "
        "otherwise estimate from activity summaries; null if no power data\n"
        "- \"estimatedThresholdHR\": integer bpm — use the pre-computed value when provided, "
        "otherwise estimate from activity summaries; null if no HR data\n"
        "- \"riderType\": one of \"timetrial\", \"sprinter\", \"climber\", \"allrounder\", \"endurance\"\n"
        "- \"notes\": a concise assessment addressed directly to the athlete using 'you'. "
        "Mention their strengths, rider type, and key observations from their rides. "
        "Example: 'You show strong sustained power over long efforts, which marks you as a time-trial type rider. "
        "Your aerobic base looks solid…'\n\n"
        "Rider-type guidelines:\n"
        "- timetrial: strong sustained power, low variability, long average efforts\n"
        "- sprinter: high max power, shorter efforts, high power variability\n"
        "- climber: high elevation gain per km, longer sustained efforts at moderate power\n"
        "- endurance: long rides, moderate intensity, high volume\n"
        "- allrounder: balanced across metrics"
    )

    user_msg = (
        f"Last {len(activities)} Strava rides:\n{json.dumps(activities, indent=2)}"
        f"{computed_section}\n\n"
        "Assess my fitness. When pre-computed FTP/threshold HR values are given, "
        "use them verbatim for estimatedFTP and estimatedThresholdHR."
    )

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


TRAINING_PLAN_PRINCIPLES = """
Training plan scheduling rules (ALWAYS follow these):
- Schedule long endurance and base rides on Saturday and Sunday.
- Keep weekday sessions short (60-90 min maximum) to fit around work.
- Do not rely on a 'weeklyHours' field; derive realistic weekly volume from the athlete's fitness level:
  * beginner: ~3-5 hours/week, no session longer than 90 min
  * intermediate: ~5-8 hours/week, weekend rides up to 2.5 h
  * advanced: ~8-12 hours/week, weekend rides up to 3.5 h
- Make intensity/volume realistic for the athlete's current fitness level and stated goal.
- For race-prep goals: taper in the final week before the race date (reduce volume by ~40%, keep intensity).
- Progressive overload: gradually increase load week-over-week, but include a recovery day after every hard session.
- Never schedule two hard days back-to-back.
- If a rider assessment (FTP/threshold HR) is available, use it to set precise power/HR targets for every workout.
"""


async def generate_training_plan(
    profile: dict, provider: str = "openai", rider_assessment: dict | None = None
) -> list[dict]:
    system_prompt = (
        f"{COACH_PERSONA} Generate a 14-day training plan as JSON.\n"
        "Return ONLY a valid JSON object with a \"plan\" array of training days. "
        "All keys must be double-quoted. All numeric fields must be plain numbers with no units.\n"
        "Each day must have: \"date\" (ISO date string starting from today), "
        "\"workoutType\" (one of: \"rest\",\"endurance\",\"intervals\",\"tempo\",\"race\","
        "\"recovery\",\"strength\"), \"title\" (string), \"description\" (string), "
        "\"durationMinutes\" (integer).\n"
        "Optional fields: \"targetPower\" (object with \"low\" and \"high\" integer fields in watts), "
        "\"targetHeartRate\" (object with \"low\" and \"high\" integer fields in bpm), "
        "\"intervals\" (array of objects with \"duration\" (integer seconds), "
        "\"power\" (integer watts), \"rest\" (integer seconds)).\n"
        f"{TRAINING_PLAN_PRINCIPLES}"
        "Workout type guidance:\n"
        "- For FTP improvement: include threshold and VO2max work\n"
        "- For race prep: include race-specific workouts and a taper week\n"
        "- For weight loss: emphasise longer aerobic sessions\n"
        "- Tailor workout types to the rider type "
        "(e.g. more sprints for sprinters, more climbs for climbers, sustained tempo for TT riders)"
    )
    assessment_section = (
        f"\nRider assessment from recent Strava rides: {json.dumps(rider_assessment)}"
        if rider_assessment
        else ""
    )
    user_msg = (
        f"Profile: {json.dumps(profile)}{assessment_section}\n"
        "Generate a 14-day training plan starting from today that reflects both the athlete's "
        "goals and their actual fitness level from recent rides."
    )
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True)
    parsed = _parse_ai_json(raw)
    return parsed.get("plan", [])


async def adapt_training_plan(
    plan: list[dict], recent_feedback: list[dict], profile: dict, provider: str = "openai"
) -> list[dict]:
    incomplete_days = [day for day in plan if not day.get("completed")]
    system_prompt = f"{COACH_PERSONA} Adapt the remaining training plan based on recent workout feedback.\nReturn ONLY a valid JSON object with an \"updatedDays\" array. All keys must be double-quoted. All numeric fields must be plain numbers with no units. Keep the same date fields.\nEach updated day must include all required TrainingDay fields: \"date\", \"workoutType\", \"title\", \"description\", \"durationMinutes\"."
    user_msg = f"Profile: {json.dumps(profile)}\nRecent feedback: {json.dumps(recent_feedback)}\nRemaining plan days: {json.dumps(incomplete_days)}\nAdapt the remaining days based on the feedback. Return the full updated days array."
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True)
    parsed = _parse_ai_json(raw)
    updated_days = {day["date"]: day for day in parsed.get("updatedDays", [])}
    return [day if day.get("completed") else updated_days.get(day["date"], day) for day in plan]


async def ask_trainer(
    question: str,
    plan: list[dict],
    profile: dict,
    provider: str = "openai",
    coach_memory: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict:
    today = __import__("datetime").datetime.now().date().isoformat()
    last_7_days = [day for day in plan if day.get("date", "") <= today][-7:]
    next_14_days = [day for day in plan if day.get("date", "") >= today][:14]
    memory_section = f"\n\nCoach notes about this athlete (remember these):\n{coach_memory}" if coach_memory else ""
    system_prompt = f"{COACH_PERSONA} Answer the athlete's question concisely and practically.\nAthlete profile: {json.dumps(profile)}\nLast 7 days of training: {json.dumps(last_7_days)}\nUpcoming plan (next 14 days): {json.dumps(next_14_days)}{memory_section}\n\nALWAYS respond with a valid JSON object containing exactly these fields:\n- \"response\": your natural language answer as a string (required)\n- \"planUpdates\": an array of training day updates (optional). Only include this field when the athlete explicitly asks to change, swap, skip, or reschedule a workout. Each update must include \"date\" (ISO string matching an existing plan date) and any fields to change: \"workoutType\", \"title\", \"description\", \"durationMinutes\", \"targetPower\", \"targetHeartRate\". When modifying a day, always include \"title\" and \"description\" so the plan entry stays informative. For a skipped/rest day set workoutType to \"rest\", durationMinutes to 0."
    history = (conversation_history or [])[-MAX_CONVERSATION_HISTORY:]
    messages = [*history, {"role": "user", "content": question}]
    raw = await _chat_history(provider, system_prompt, messages, json_mode=True)
    parsed = _parse_ai_json(raw)
    return {
        "response": parsed.get("response", ""),
        "plan_updates": parsed.get("planUpdates"),
    }


async def update_coach_memory(
    current_memory: str, user_message: str, coach_response: str, provider: str = "openai"
) -> str:
    system_prompt = f"{COACH_PERSONA} Maintain concise notes about an athlete.\nExtract any important, actionable information from this conversation exchange and update the notes.\nKeep notes under 300 words. Focus on: goals, limitations, health issues, preferences, performance achievements, recurring problems.\nReturn ONLY the updated notes as plain text. If nothing new and important was mentioned, return the existing notes unchanged."
    user_msg = f"Existing notes:\n{current_memory or '(none)'}\n\nLatest exchange:\nAthlete: {user_message}\nCoach: {coach_response}\n\nUpdate the notes with any new important information."
    return await _chat(provider, system_prompt, user_msg)


async def rate_completed_workout(day: dict, profile: dict, provider: str = "openai") -> str:
    feedback = day.get("feedback")
    if not feedback:
        return ""

    system_prompt = f"{COACH_PERSONA} Review a completed training session. Compare the actual workout against the planned one and provide brief, encouraging feedback in 2-4 sentences. Note how well the athlete followed the plan, highlight any significant deviations, and explain what it means for their training progress."

    planned_power = ""
    if day.get("targetPower"):
        planned_power = f"\n- Target power: {day['targetPower']['low']}–{day['targetPower']['high']}W"
    planned_hr = ""
    if day.get("targetHeartRate"):
        planned_hr = f"\n- Target HR: {day['targetHeartRate']['low']}–{day['targetHeartRate']['high']} bpm"
    actual_power = f"\n- Average power: {feedback['averagePower']}W" if feedback.get("averagePower") else ""
    actual_peak = f"\n- Peak power: {feedback['peakPower']}W" if feedback.get("peakPower") else ""
    actual_hr = f"\n- Average HR: {feedback['averageHeartRate']} bpm" if feedback.get("averageHeartRate") else ""
    notes = f"\n- Notes: {feedback['notes']}" if feedback.get("notes") else ""

    effort_labels = {
        1: "Easy",
        2: "Moderate",
        3: "Hard",
        4: "Very Hard",
        5: "Max",
    }

    user_msg = f"Planned workout:\n- Type: {day['workoutType']}\n- Title: {day['title']}\n- Duration: {day['durationMinutes']} min\n- Description: {day['description']}{planned_power}{planned_hr}\n\nActual workout:\n- Duration: {feedback['actualDurationMinutes']} min\n- Perceived effort: {feedback['perceivedEffort']}/5 ({effort_labels.get(feedback['perceivedEffort'], '')}){actual_power}{actual_peak}{actual_hr}{notes}\n\nAthlete profile: {json.dumps(profile)}\n\nRate how well this workout matched the plan and give brief feedback."
    return await _chat(provider, system_prompt, user_msg)
