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
    "'You have excellent aerobic endurance' not 'The athlete has excellent aerobic endurance'. "
    "Your coaching philosophy: long-term athletic development always overrules short-term gains. "
    "Never sacrifice recovery, health, or sustainable progression for quick wins. "
    "When in doubt, prioritise the athlete's long-term progress over immediate performance."
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

# Average-power threshold that separates endurance from tempo zones (~76 % FTP).
# Below this level a sustained ride is aerobic/endurance; at or above it the
# effort is in the tempo/sweet-spot band.
_TEMPO_THRESHOLD_PCT = 0.76

# Rough proxy used when no algorithmic FTP estimate is available: a cyclist's
# true FTP is typically ~75 % of their raw average power across all recent rides
# (accounting for the mix of easy and hard sessions that make up their history).
_AVG_POWER_TO_FTP_RATIO = 0.75


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


def _detect_intervals(
    watts: list[float],
    time_stream: list[float],
    ftp: float,
    work_threshold_pct: float = 0.85,
    min_interval_secs: float = 30.0,
    recovery_gap_secs: float = 30.0,
) -> list[dict]:
    """Detect interval blocks in a power stream relative to FTP.

    An interval is a contiguous block where average power exceeds
    ``work_threshold_pct × ftp``.  Short recoveries (< ``recovery_gap_secs``)
    between high-power blocks are merged into the preceding interval so noisy
    one-second dips do not split a single effort into many fragments.

    Returns a list of dicts, each with:
        ``start_idx``, ``end_idx``, ``duration_secs``,
        ``avg_power``, ``peak_power``.
    """
    if not watts or not time_stream or len(watts) != len(time_stream) or ftp <= 0:
        return []

    threshold = ftp * work_threshold_pct
    n = len(watts)

    # --- Phase 1: build raw on/off blocks ---
    blocks: list[tuple[int, int]] = []  # (start, end) inclusive
    in_block = False
    block_start = 0

    for i in range(n):
        above = watts[i] >= threshold
        if above and not in_block:
            in_block = True
            block_start = i
        elif not above and in_block:
            blocks.append((block_start, i - 1))
            in_block = False
    if in_block:
        blocks.append((block_start, n - 1))

    # --- Phase 2: merge blocks separated by a short recovery gap ---
    merged: list[tuple[int, int]] = []
    for block in blocks:
        if merged and (time_stream[block[0]] - time_stream[merged[-1][1]]) <= recovery_gap_secs:
            merged[-1] = (merged[-1][0], block[1])
        else:
            merged.append(block)

    # --- Phase 3: filter out blocks shorter than the minimum duration ---
    result: list[dict] = []
    for start, end in merged:
        dur = time_stream[end] - time_stream[start]
        if dur < min_interval_secs:
            continue
        seg_watts = watts[start : end + 1]
        result.append(
            {
                "start_idx": start,
                "end_idx": end,
                "duration_secs": round(dur),
                "avg_power": round(sum(seg_watts) / len(seg_watts)),
                "peak_power": round(max(seg_watts)),
            }
        )
    return result


def _compute_hr_drift(hr_segment: list[float]) -> float | None:
    """Return the linear-regression slope (bpm per sample) of HR over a segment.

    A positive slope indicates cardiac drift (HR rising while effort is
    sustained).  Returns ``None`` when the segment is too short (<= 2 points).
    """
    n = len(hr_segment)
    if n <= 2:
        return None
    xs = list(range(n))
    mean_x = (n - 1) / 2.0
    mean_y = sum(hr_segment) / n
    num = sum((xs[i] - mean_x) * (hr_segment[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    return num / den if den != 0 else 0.0


def _classify_ride_purpose(
    watts: list[float],
    time_stream: list[float],
    ftp: float,
) -> str:
    """Classify the overall purpose/category of a ride.

    Categories (aligned with problem statement):
    - ``recovery``            : avg power < 60 % FTP
    - ``endurance``           : avg power 60–75 % FTP, no hard intervals
    - ``tempo``               : avg power ~76–85 % FTP, no distinct intervals
    - ``interval_sweetspot``  : detected intervals lasting 10–20 min at 88–95 % FTP
    - ``interval_threshold``  : detected intervals ~5–12 min at 95–105 % FTP
    - ``interval_vo2max``     : detected intervals 2–5 min at 106–130 % FTP
    - ``interval_sprints``    : detected intervals < 2 min at > 130 % FTP
    - ``mixed``               : multiple distinct interval types detected
    """
    if not watts or ftp <= 0:
        return "endurance"

    total_time = time_stream[-1] - time_stream[0] if len(time_stream) > 1 else len(watts)
    avg_power = sum(watts) / len(watts)
    avg_pct = avg_power / ftp

    # Detect intervals at 85 % threshold
    intervals = _detect_intervals(watts, time_stream, ftp, work_threshold_pct=0.85)

    if not intervals:
        # No distinct interval blocks — classify by average power
        if avg_pct < 0.60:
            return "recovery"
        if avg_pct < _TEMPO_THRESHOLD_PCT:
            return "endurance"
        return "tempo"

    # Classify each detected interval by its relative power and duration
    interval_types: list[str] = []
    for iv in intervals:
        dur_min = iv["duration_secs"] / 60.0
        pct = iv["avg_power"] / ftp
        if pct > 1.30 and dur_min < 2:
            interval_types.append("sprint")
        elif pct > 1.05 and dur_min <= 5:
            interval_types.append("vo2max")
        elif 0.95 <= pct <= 1.05 and dur_min <= 12:
            interval_types.append("threshold")
        elif 0.88 <= pct < 0.95 and 10 <= dur_min <= 20:
            interval_types.append("sweetspot")
        elif pct > 0.85:
            # Catch-all for other hard efforts
            if dur_min < 2:
                interval_types.append("sprint")
            elif dur_min <= 5:
                interval_types.append("vo2max")
            else:
                interval_types.append("threshold")

    unique_types = set(interval_types)
    if not unique_types:
        # Intervals detected but all fell below the classification thresholds
        return "endurance" if avg_pct < _TEMPO_THRESHOLD_PCT else "tempo"
    if len(unique_types) > 1:
        return "mixed"
    sole_type = next(iter(unique_types))
    return {
        "sprint": "interval_sprints",
        "vo2max": "interval_vo2max",
        "threshold": "interval_threshold",
        "sweetspot": "interval_sweetspot",
    }.get(sole_type, "endurance")


def _build_ride_analysis(
    streams: dict,
    ftp: float,
) -> dict:
    """Compute a structured analysis of a single ride from its stream data.

    Returns a dict suitable for embedding into the AI prompt.
    """
    watts: list[float] = streams.get("watts", {}).get("data", [])
    hr_data: list[float] = streams.get("heartrate", {}).get("data", [])
    time_data: list[float] = streams.get("time", {}).get("data", [])

    if not watts or not time_data:
        return {}

    ride_category = _classify_ride_purpose(watts, time_data, ftp)
    intervals = _detect_intervals(watts, time_data, ftp)

    # Annotate each interval with HR data and drift
    annotated: list[dict] = []
    for iv in intervals:
        s, e = iv["start_idx"], iv["end_idx"]
        annotated_iv = {
            "duration_secs": iv["duration_secs"],
            "avg_power_w": iv["avg_power"],
            "peak_power_w": iv["peak_power"],
            "power_pct_ftp": round(iv["avg_power"] / ftp * 100),
        }
        if hr_data and len(hr_data) == len(watts):
            hr_seg = hr_data[s : e + 1]
            avg_hr = sum(hr_seg) / len(hr_seg)
            annotated_iv["avg_hr_bpm"] = round(avg_hr)
            drift = _compute_hr_drift(hr_seg)
            if drift is not None:
                # Normalise drift to total HR rise across the segment
                total_drift_bpm = drift * len(hr_seg)
                annotated_iv["hr_drift_bpm"] = round(total_drift_bpm, 1)
                annotated_iv["hr_drift_status"] = (
                    "stable" if abs(total_drift_bpm) < 5 else "drifting"
                )
        annotated.append(annotated_iv)

    return {
        "ride_category": ride_category,
        "avg_power_w": round(sum(watts) / len(watts)),
        "intervals_detected": annotated,
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

    system_prompt = (
        f"{COACH_PERSONA} Analyse the provided Strava activities and return a JSON assessment.\n"
        "Return ONLY a valid JSON object with these fields "
        "(all keys double-quoted, numeric values must be plain numbers with no units):\n"
        "- \"estimatedFTP\": integer watts — use the pre-computed value when provided, "
        "otherwise estimate from activity summaries; null if no power data\n"
        "- \"estimatedThresholdHR\": integer bpm — use the pre-computed value when provided, "
        "otherwise estimate from activity summaries; null if no HR data\n"
        "- \"riderType\": one of \"timetrial\", \"sprinter\", \"climber\", \"allrounder\", \"endurance\"\n"
        "- \"notes\": a concise overall assessment addressed directly to the athlete using 'you'. "
        "Mention their strengths, rider type, and key observations from their rides. "
        "Example: 'You show strong sustained power over long efforts, which marks you as a time-trial type rider. "
        "Your aerobic base looks solid…'\n"
        "- \"rideInsights\": a per-ride narrative addressed to the athlete. "
        "For each ride: state its category (recovery/endurance/tempo/sweet-spot/threshold/VO2max/sprints), "
        "comment on the interval quality (power consistency, HR drift if data available), and give one "
        "concrete takeaway. Also include 1-2 specific recommendations for the athlete's next training "
        "session based on what you observed. Be empathetic and personal — reference their specific numbers.\n"
        "- \"planUpdates\": optional array of training day updates for the upcoming plan based on what "
        "you observed in the rides. Only include updates that are genuinely warranted (e.g. add recovery "
        "if athlete shows fatigue/HR drift, increase intensity if athlete is clearly above their current "
        "targets). Each update: {\"date\": \"<ISO date>\", \"workoutType\": \"<type>\", \"title\": "
        "\"<string>\", \"description\": \"<string>\", \"durationMinutes\": <int>}. "
        "If no updates are needed, omit this field or set it to [].\n\n"
        "Ride categories:\n"
        "- recovery: avg power < 60% FTP\n"
        "- endurance: avg power 60–75% FTP, no distinct intervals\n"
        "- tempo: avg power ~76–85% FTP, no distinct intervals\n"
        "- interval_sweetspot: 10–20 min efforts at 88–95% FTP\n"
        "- interval_threshold: ~5–12 min efforts at 95–105% FTP\n"
        "- interval_vo2max: 2–5 min efforts at 106–130% FTP\n"
        "- interval_sprints: < 2 min efforts at > 130% FTP\n\n"
        "Rider-type guidelines:\n"
        "- timetrial: strong sustained power, low variability, long average efforts\n"
        "- sprinter: high max power, shorter efforts, high power variability\n"
        "- climber: high elevation gain per km, longer sustained efforts at moderate power\n"
        "- endurance: long rides, moderate intensity, high volume\n"
        "- allrounder: balanced across metrics"
    )

    # Build the per-ride analysis section for the AI prompt
    ride_analyses_section = ""
    if ride_analyses:
        ride_analyses_str = json.dumps(ride_analyses, indent=2)
        ride_analyses_section = (
            f"\n\nAlgorithmic per-ride analysis (computed from stream data):\n{ride_analyses_str}"
        )

    user_msg = (
        f"Last {len(activities)} Strava rides:\n{json.dumps(activities, indent=2)}"
        f"{computed_section}"
        f"{ride_analyses_section}\n\n"
        "Assess my fitness. When pre-computed FTP/threshold HR values are given, "
        "use them verbatim for estimatedFTP and estimatedThresholdHR. "
        "Use the per-ride analyses above to write accurate rideInsights and appropriate planUpdates."
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
    today = __import__("datetime").datetime.now().date().isoformat()
    user_msg = (
        f"Today's date: {today}\n"
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
    today = __import__("datetime").datetime.now().date().isoformat()
    incomplete_days = [day for day in plan if not day.get("completed")]
    system_prompt = f"{COACH_PERSONA} Adapt the remaining training plan based on recent workout feedback.\nReturn ONLY a valid JSON object with an \"updatedDays\" array. All keys must be double-quoted. All numeric fields must be plain numbers with no units. Keep the same date fields.\nEach updated day must include all required TrainingDay fields: \"date\", \"workoutType\", \"title\", \"description\", \"durationMinutes\"."
    user_msg = f"Today's date: {today}\nProfile: {json.dumps(profile)}\nRecent feedback: {json.dumps(recent_feedback)}\nRemaining plan days: {json.dumps(incomplete_days)}\nAdapt the remaining days based on the feedback. Return the full updated days array."
    raw = await _chat(provider, system_prompt, user_msg, json_mode=True)
    parsed = _parse_ai_json(raw)
    updated_days = {day["date"]: day for day in parsed.get("updatedDays", [])}
    return [day if day.get("completed") else updated_days.get(day["date"], day) for day in plan]


async def ask_trainer(
    question: str,
    plan: list[dict],
    profile: dict,
    provider: str = "openai",
    rider_assessment: dict | None = None,
    coach_memory: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    context_workout: dict | None = None,
) -> dict:
    today = __import__("datetime").datetime.now().date().isoformat()
    last_7_days = [day for day in plan if day.get("date", "") <= today][-7:]
    next_14_days = [day for day in plan if day.get("date", "") >= today][:14]
    memory_section = f"\n\nCoach notes about this athlete (remember these):\n{coach_memory}" if coach_memory else ""

    assessment_section = ""
    if rider_assessment:
        rider_type = rider_assessment.get("riderType", "")
        ftp = rider_assessment.get("estimatedFTP")
        thr = rider_assessment.get("estimatedThresholdHR")
        notes = rider_assessment.get("notes", "")
        assessment_lines = [f"- Rider type: {rider_type}"]
        if ftp:
            assessment_lines.append(f"- Estimated FTP: {ftp} W")
        if thr:
            assessment_lines.append(f"- Estimated threshold HR: {thr} bpm")
        if notes:
            assessment_lines.append(f"- Assessment notes: {notes}")
        assessment_section = (
            "\n\nRider assessment from recent Strava analysis:\n"
            + "\n".join(assessment_lines)
            + "\nUse this to give personalised advice that matches the athlete's strengths and riding style."
        )
    workout_section = (
        f"\n\nThe athlete is currently viewing this specific workout:\n"
        f"{json.dumps(context_workout, indent=2)}"
        "\nWhen the athlete refers to 'this workout', 'today's session', or similar, "
        "they mean the workout above. "
        "IMPORTANT: whenever your coaching response proposes ANY change or adjustment to this "
        "workout (e.g. shorter duration, lower intensity, different structure), you MUST include "
        "the updated workout in planUpdates so the card is immediately refreshed. Do not merely "
        "describe the change in text — always materialise it as a planUpdates entry."
    ) if context_workout else ""

    _intervals_rule = (
        "CRITICAL — intervals array: whenever the athlete changes interval count, duration, "
        "or power you MUST include the full \"intervals\" array in planUpdates. "
        "The array must contain EXACTLY the requested number of objects, one per interval rep. "
        "Each object: {\"duration\": <seconds>, \"power\": <watts>, \"rest\": <seconds>}. "
        "Example — athlete asks for 4×2 min at 370 W with 3 min rest: "
        "\"intervals\": ["
        "{\"duration\": 120, \"power\": 370, \"rest\": 180}, "
        "{\"duration\": 120, \"power\": 370, \"rest\": 180}, "
        "{\"duration\": 120, \"power\": 370, \"rest\": 180}, "
        "{\"duration\": 120, \"power\": 370, \"rest\": 180}]. "
        "Never describe the intervals only in text and omit the array — always materialise "
        "every rep as a separate object in the array."
    )

    if context_workout:
        plan_updates_rule = (
            '- "planUpdates": an array of training day updates. '
            "Include this field in two cases: "
            "(1) The athlete explicitly asks to change, swap, skip, or reschedule any workout. "
            "(2) You propose a coaching change to the currently-viewed workout (see above) — "
            "even if the athlete did not explicitly request a change. "
            'Each update must include "date" (ISO string matching an existing plan date) and any '
            'fields to change: "workoutType", "title", "description", "durationMinutes", '
            '"targetPower", "targetHeartRate", "intervals". '
            "Always include \"title\" and \"description\" so the plan entry stays informative. "
            "For a skipped/rest day set workoutType to \"rest\", durationMinutes to 0. "
            f"{_intervals_rule}"
        )
    else:
        plan_updates_rule = (
            '- "planUpdates": an array of training day updates (optional). Only include this '
            "field when the athlete explicitly asks to change, swap, skip, or reschedule a "
            'workout. Each update must include "date" (ISO string matching an existing plan '
            'date) and any fields to change: "workoutType", "title", "description", '
            '"durationMinutes", "targetPower", "targetHeartRate", "intervals". '
            "Always include \"title\" and \"description\" so the plan entry stays informative. "
            "For a skipped/rest day set workoutType to \"rest\", durationMinutes to 0. "
            f"{_intervals_rule}"
        )

    system_prompt = (
        f"{COACH_PERSONA} Answer the athlete's question concisely and practically.\n"
        f"Today's date: {today}\n"
        f"Athlete profile: {json.dumps(profile)}\n"
        f"Last 7 days of training: {json.dumps(last_7_days)}\n"
        f"Upcoming plan (next 14 days): {json.dumps(next_14_days)}"
        f"{assessment_section}"
        f"{memory_section}"
        f"{workout_section}\n\n"
        "Always take today's date into account when answering — for example when calculating "
        "days until a race, suggesting which workout is next, or referencing past sessions.\n"
        "Whenever the athlete requests a change to the training plan, your response MUST briefly "
        "reflect on whether the change is a good idea: acknowledge their preference warmly, give "
        "an honest assessment of the training impact (e.g. how it affects load, intensity, "
        "progression, or recovery), and explain any trade-offs. "
        "Always be kind, supportive, and encouraging — but never withhold honest coaching "
        "advice. If a change could harm progress or recovery, say so clearly yet tactfully, "
        "and still apply the change if the athlete wants it.\n"
        "ALWAYS respond with a valid JSON object containing exactly these fields:\n"
        '- "response": your natural language answer as a string (required)\n'
        f"{plan_updates_rule}"
    )
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
    system_prompt = (
        f"{COACH_PERSONA} Maintain concise notes about an athlete.\n"
        "Extract any important, actionable information from this conversation exchange and update the notes.\n"
        "Keep notes under 300 words. Focus on: goals, limitations, health issues, preferences, "
        "performance achievements, recurring problems, FTP history (record up to the 5 most recent "
        "FTP estimates with their approximate dates to track progress; drop the oldest when adding a new one), "
        "rider strengths and weaknesses, and personal motivations such as "
        "preferred terrain or event types (e.g. loves hill climbing, prefers long endurance rides).\n"
        "Return ONLY the updated notes as plain text. If nothing new and important was mentioned, return the existing notes unchanged."
    )
    user_msg = (
        f"Existing notes:\n{current_memory or '(none)'}\n\n"
        f"Latest exchange:\nAthlete: {user_message}\nCoach: {coach_response}\n\n"
        "Update the notes with any new important information."
    )
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
