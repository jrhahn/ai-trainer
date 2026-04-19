"""Pure-Python ride analysis algorithms.

These functions operate solely on numeric streams and have no dependency on
any LLM provider, HTTP client, or database.  They are extracted from
ai_service.py to keep algorithmic logic separate from prompt construction
and provider wiring.
"""

from __future__ import annotations

import math

# Fraction of max HR that corresponds to lactate threshold (LTHR).
# 87% is a well-established estimate for trained cyclists.
LTHR_RATIO = 0.87

# Average-power threshold that separates endurance from tempo zones (~76 % FTP).
# Below this level a sustained ride is aerobic/endurance; at or above it the
# effort is in the tempo/sweet-spot band.
TEMPO_THRESHOLD_PCT = 0.76

# Rough proxy used when no algorithmic FTP estimate is available: a cyclist's
# true FTP is typically ~75 % of their raw average power across all recent rides
# (accounting for the mix of easy and hard sessions that make up their history).
AVG_POWER_TO_FTP_RATIO = 0.75


def best_n_min_power(
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


def hr_corrected_ftp(
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
    lthr = max_hr * LTHR_RATIO
    corrected = interval_power * (lthr / interval_hr)
    return round(corrected)


def compute_hr_zones(max_hr: int) -> dict:
    """Compute 5 standard HR training zones based on percentage of max HR."""
    return {
        "zone1": {"low": 0, "high": round(max_hr * 0.60)},
        "zone2": {"low": round(max_hr * 0.60), "high": round(max_hr * 0.70)},
        "zone3": {"low": round(max_hr * 0.70), "high": round(max_hr * 0.80)},
        "zone4": {"low": round(max_hr * 0.80), "high": round(max_hr * 0.90)},
        "zone5": {"low": round(max_hr * 0.90), "high": max_hr},
    }


def detect_intervals(
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


def compute_hr_drift(hr_segment: list[float]) -> float | None:
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


def classify_ride_purpose(
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

    avg_power = sum(watts) / len(watts)
    avg_pct = avg_power / ftp

    # Detect intervals at 85 % threshold
    intervals = detect_intervals(watts, time_stream, ftp, work_threshold_pct=0.85)

    if not intervals:
        # No distinct interval blocks — classify by average power
        if avg_pct < 0.60:
            return "recovery"
        if avg_pct < TEMPO_THRESHOLD_PCT:
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
        return "endurance" if avg_pct < TEMPO_THRESHOLD_PCT else "tempo"
    if len(unique_types) > 1:
        return "mixed"
    sole_type = next(iter(unique_types))
    return {
        "sprint": "interval_sprints",
        "vo2max": "interval_vo2max",
        "threshold": "interval_threshold",
        "sweetspot": "interval_sweetspot",
    }.get(sole_type, "endurance")


def compute_training_load(plan_days: list[dict], ftp: float) -> dict:
    """Compute CTL, ATL, and TSB training load metrics from plan days.

    Uses an approximation of TSS per day from ``durationMinutes`` and
    ``targetPower`` when stream data is not available:

        TSS ≈ (duration_s × NP × IF) / (FTP × 3600) × 100

    where NP is approximated as ``targetPower`` mid-point (or a fraction of
    FTP based on workout type) and IF = NP / FTP.

    CTL — 42-day exponential weighted average of daily TSS (fitness).
    ATL — 7-day exponential weighted average of daily TSS (fatigue).
    TSB — CTL − ATL (form/freshness).

    Returns ``{"ctl": float, "atl": float, "tsb": float, "daily_tss": list[float]}``.
    """
    if ftp <= 0:
        return {"ctl": 0.0, "atl": 0.0, "tsb": 0.0, "daily_tss": []}

    # --- Estimate TSS per day ---
    daily_tss: list[float] = []
    for day in plan_days:
        duration_min = day.get("durationMinutes") or 0
        duration_s = duration_min * 60.0
        if duration_s <= 0:
            daily_tss.append(0.0)
            continue

        # Try to use targetPower mid-point as NP approximation
        target_power = day.get("targetPower")
        if target_power and isinstance(target_power, dict):
            low = target_power.get("low") or 0
            high = target_power.get("high") or 0
            if low > 0 and high > 0:
                np_approx = (low + high) / 2.0
            elif high > 0:
                np_approx = float(high)
            elif low > 0:
                np_approx = float(low)
            else:
                np_approx = None
        else:
            np_approx = None

        # Fall back to workout-type heuristic when no power target is available
        if np_approx is None or np_approx <= 0:
            workout_type = (day.get("workoutType") or "").lower()
            type_pct_map = {
                "rest": 0.0,
                "recovery": 0.50,
                "endurance": 0.68,
                "tempo": 0.80,
                "intervals": 0.90,
                "strength": 0.65,
                "race": 0.95,
            }
            pct = type_pct_map.get(workout_type, 0.65)
            np_approx = ftp * pct

        if np_approx <= 0:
            daily_tss.append(0.0)
            continue

        intensity_factor = np_approx / ftp
        tss = (duration_s * np_approx * intensity_factor) / (ftp * 3600.0) * 100.0
        daily_tss.append(round(tss, 1))

    # --- Compute CTL and ATL via exponential weighted averages ---
    # CTL: 42-day time constant → smoothing factor α = 1 - exp(-1/42)
    # ATL: 7-day time constant  → smoothing factor α = 1 - exp(-1/7)
    alpha_ctl = 1.0 - math.exp(-1.0 / 42.0)
    alpha_atl = 1.0 - math.exp(-1.0 / 7.0)

    ctl = 0.0
    atl = 0.0
    for tss in daily_tss:
        ctl = ctl + alpha_ctl * (tss - ctl)
        atl = atl + alpha_atl * (tss - atl)

    tsb = ctl - atl
    return {
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb": round(tsb, 1),
        "daily_tss": daily_tss,
    }


def compute_readiness_score(
    ctl: float,
    atl: float,
    tsb: float,
    days_until_race: int,
) -> dict:
    """Compute a 0–100 race readiness score from training-load metrics.

    The score blends two components:

    **Form score (65 %)** — based on TSB (Training Stress Balance = CTL − ATL).
    The optimal TSB window for racing is roughly +5 to +15: the athlete has
    shed acute fatigue while preserving their fitness base.

    =====================  ===========
    TSB range              Form score
    =====================  ===========
    ≤ −30 (severe fatigue)       0
    −30 → 0 (tired)        0 → 50 (linear)
    0 → +10 (building)    50 → 100 (linear)
    +10 → +25 (tapering)  100 → 85 (slight drop)
    > +25 (over-tapered)  ≥ 85 → 0 (declining)
    =====================  ===========

    **Fitness score (35 %)** — based on CTL (Chronic Training Load).
    CTL is the 42-day exponential smoothing of daily TSS.  A CTL of 100
    represents strong fitness for a trained cyclist; values are capped at 100.

    Returns a dict with ``score``, ``form_score``, ``fitness_score``,
    ``ctl``, ``atl``, ``tsb``, and ``days_until_race``.
    """
    # --- Form score (TSB-based) ---
    if tsb <= -30.0:
        form_score = 0.0
    elif tsb < 0.0:
        form_score = (tsb + 30.0) / 30.0 * 50.0
    elif tsb <= 10.0:
        form_score = 50.0 + tsb / 10.0 * 50.0
    elif tsb <= 25.0:
        form_score = 100.0 - (tsb - 10.0) / 15.0 * 15.0
    else:
        form_score = max(0.0, 85.0 - (tsb - 25.0) * 2.0)
    form_score = max(0.0, min(100.0, form_score))

    # --- Fitness score (CTL-based, capped at 100) ---
    fitness_score = max(0.0, min(100.0, ctl))

    # --- Weighted combination ---
    score = 0.65 * form_score + 0.35 * fitness_score
    score = max(0.0, min(100.0, score))

    return {
        "score": round(score, 1),
        "form_score": round(form_score, 1),
        "fitness_score": round(fitness_score, 1),
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb": round(tsb, 1),
        "days_until_race": days_until_race,
    }


def _project_training_load(
    plan_days: list[dict],
    ftp: float,
    target_date_str: str,
) -> dict:
    """Forward-project CTL/ATL/TSB to a specific target date.

    Uses only plan days whose ``date`` field is on or before ``target_date_str``
    so we get the projected load at that point in the plan.

    Returns the same shape as :func:`compute_training_load`.
    """
    days_up_to_target = [
        day for day in plan_days
        if day.get("date", "") <= target_date_str
    ]
    return compute_training_load(days_up_to_target, ftp)


def _normalized_power(watts: list[float], time_stream: list[float]) -> float | None:
    """Compute Normalized Power (NP) from a power stream.

    Uses the standard algorithm: compute a 30-second rolling average, raise
    each value to the 4th power, average those values, then take the 4th root.
    Returns ``None`` when there is insufficient data (< 30 seconds of riding).
    """
    if len(watts) < 2 or not time_stream or len(watts) != len(time_stream):
        return None
    total_time = time_stream[-1] - time_stream[0]
    if total_time < 30:
        return None

    rolling_avgs: list[float] = []
    left = 0
    window_sum = 0.0

    for right in range(len(watts)):
        window_sum += watts[right]
        while time_stream[right] - time_stream[left] > 30.0:
            window_sum -= watts[left]
            left += 1
        count = right - left + 1
        rolling_avgs.append(window_sum / count)

    if not rolling_avgs:
        return None
    mean_fourth = sum(v**4 for v in rolling_avgs) / len(rolling_avgs)
    return mean_fourth**0.25


def _time_in_power_zones(
    watts: list[float], time_stream: list[float], ftp: float
) -> dict:
    """Compute time spent (in seconds) in each of the 7 standard power training zones.

    Zone boundaries (as % FTP):
    - Z1: < 55 %  (Active Recovery)
    - Z2: 55–75 % (Endurance)
    - Z3: 75–90 % (Tempo)
    - Z4: 90–105 % (Threshold)
    - Z5: 105–120 % (VO2max)
    - Z6: 120–150 % (Anaerobic Capacity)
    - Z7: > 150 %  (Neuromuscular Power)
    """
    zones: dict[str, float] = {f"z{i}_secs": 0.0 for i in range(1, 8)}
    if not watts or not time_stream or len(watts) != len(time_stream) or ftp <= 0:
        return {k: round(v) for k, v in zones.items()}

    boundaries = [0.55, 0.75, 0.90, 1.05, 1.20, 1.50]

    for i in range(len(watts)):
        pct = watts[i] / ftp
        dt = (time_stream[i + 1] - time_stream[i]) if i < len(watts) - 1 else 1.0
        if pct < boundaries[0]:
            zones["z1_secs"] += dt
        elif pct < boundaries[1]:
            zones["z2_secs"] += dt
        elif pct < boundaries[2]:
            zones["z3_secs"] += dt
        elif pct < boundaries[3]:
            zones["z4_secs"] += dt
        elif pct < boundaries[4]:
            zones["z5_secs"] += dt
        elif pct < boundaries[5]:
            zones["z6_secs"] += dt
        else:
            zones["z7_secs"] += dt

    return {k: round(v) for k, v in zones.items()}


def _detect_intensity_spikes(
    watts: list[float],
    time_stream: list[float],
    target_power: float,
    spike_threshold_pct: float = 10.0,
    window_secs: float = 900.0,
) -> list[dict]:
    """Detect non-overlapping windows where average power exceeded ``target_power``
    by more than ``spike_threshold_pct`` percent.

    Returns a list of dicts with ``start_min``, ``end_min``,
    ``avg_power_w``, and ``pct_over_target``.
    """
    if not watts or not time_stream or target_power <= 0:
        return []
    if len(watts) != len(time_stream):
        return []

    spikes: list[dict] = []
    window_start_idx = 0

    while window_start_idx < len(watts):
        window_end_time = time_stream[window_start_idx] + window_secs
        window_end_idx = window_start_idx
        while window_end_idx < len(watts) and time_stream[window_end_idx] < window_end_time:
            window_end_idx += 1

        if window_end_idx <= window_start_idx:
            break

        seg = watts[window_start_idx:window_end_idx]
        avg = sum(seg) / len(seg)
        pct_over = (avg - target_power) / target_power * 100.0
        if pct_over > spike_threshold_pct:
            start_min = round((time_stream[window_start_idx] - time_stream[0]) / 60.0, 1)
            end_min = round((time_stream[window_end_idx - 1] - time_stream[0]) / 60.0, 1)
            spikes.append(
                {
                    "start_min": start_min,
                    "end_min": end_min,
                    "avg_power_w": round(avg),
                    "pct_over_target": round(pct_over, 1),
                }
            )

        if window_end_idx >= len(watts):
            break
        window_start_idx = window_end_idx

    return spikes


def compare_planned_vs_actual(
    planned: dict,
    streams: dict,
    ftp: float | None = None,
) -> dict:
    """Compare planned workout targets against actual Strava stream data.

    Args:
        planned: A training-day dict with ``targetPower``, ``targetHeartRate``,
            ``durationMinutes``, and ``workoutType`` keys (camelCase).
        streams: The raw Strava streams dict keyed by type (``watts``,
            ``heartrate``, ``time``).
        ftp: The athlete's current FTP in watts (used for time-in-zone
            and zone-relative delta calculations).  When ``None`` the
            time-in-zones field is omitted.

    Returns:
        A dict with the following keys (all optional — only present when the
        relevant stream data is available):

        ``avg_power_w``
            Actual average power from the stream.
        ``normalized_power_w``
            NP calculated from the 30-second rolling average.
        ``target_power_low`` / ``target_power_high``
            Planned power targets from the day schema.
        ``avg_power_delta_pct``
            Percentage deviation of actual avg power from the target midpoint
            (positive = over target, negative = under).
        ``normalized_power_delta_pct``
            Same but using NP instead of avg power.
        ``time_in_zones``
            Dict of ``z1_secs`` … ``z7_secs`` — time spent in each power zone.
        ``avg_hr_bpm``
            Actual average HR from the stream.
        ``target_hr_low`` / ``target_hr_high``
            Planned HR targets from the day schema.
        ``avg_hr_delta_pct``
            Percentage deviation of actual avg HR from the target HR midpoint.
        ``hr_drift_bpm``
            Total HR drift (linear regression slope × n samples) across the
            session.  Positive = HR rising while effort is sustained (cardiac
            drift).
        ``intensity_spikes``
            List of 15-min windows where avg power exceeded the target midpoint
            by more than 10 %.  Each entry has ``start_min``, ``end_min``,
            ``avg_power_w``, ``pct_over_target``.
    """
    watts: list[float] = streams.get("watts", {}).get("data", [])
    hr_data: list[float] = streams.get("heartrate", {}).get("data", [])
    time_data: list[float] = streams.get("time", {}).get("data", [])

    if not watts or not time_data:
        return {}

    result: dict = {}

    # --- Average power ---
    avg_power = sum(watts) / len(watts)
    result["avg_power_w"] = round(avg_power)

    # --- Normalized Power ---
    np_value = _normalized_power(watts, time_data)
    if np_value is not None:
        result["normalized_power_w"] = round(np_value)

    # --- Target power delta ---
    target_power = planned.get("targetPower")
    target_mid: float | None = None
    if target_power and isinstance(target_power, dict):
        low = target_power.get("low") or 0
        high = target_power.get("high") or 0
        if low > 0 and high > 0:
            result["target_power_low"] = low
            result["target_power_high"] = high
            target_mid = (low + high) / 2.0
            result["avg_power_delta_pct"] = round(
                (avg_power - target_mid) / target_mid * 100.0, 1
            )
            np_for_delta = np_value if np_value is not None else avg_power
            result["normalized_power_delta_pct"] = round(
                (np_for_delta - target_mid) / target_mid * 100.0, 1
            )

    # --- Time in power zones ---
    if ftp and ftp > 0:
        result["time_in_zones"] = _time_in_power_zones(watts, time_data, ftp)

    # --- HR metrics ---
    if hr_data and len(hr_data) == len(watts):
        avg_hr = sum(hr_data) / len(hr_data)
        result["avg_hr_bpm"] = round(avg_hr)

        drift = compute_hr_drift(hr_data)
        if drift is not None:
            result["hr_drift_bpm"] = round(drift * len(hr_data), 1)

        target_hr = planned.get("targetHeartRate")
        if target_hr and isinstance(target_hr, dict):
            hr_low = target_hr.get("low") or 0
            hr_high = target_hr.get("high") or 0
            if hr_low > 0 and hr_high > 0:
                result["target_hr_low"] = hr_low
                result["target_hr_high"] = hr_high
                hr_mid = (hr_low + hr_high) / 2.0
                result["avg_hr_delta_pct"] = round(
                    (avg_hr - hr_mid) / hr_mid * 100.0, 1
                )

    # --- Intensity spikes ---
    if target_mid is not None:
        spikes = _detect_intensity_spikes(watts, time_data, target_mid)
        if spikes:
            result["intensity_spikes"] = spikes

    return result


def build_ride_analysis(
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

    ride_category = classify_ride_purpose(watts, time_data, ftp)
    intervals = detect_intervals(watts, time_data, ftp)

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
            drift = compute_hr_drift(hr_seg)
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


def compute_ftp_from_streams(
    streams_by_id: dict[str, dict],
    max_heart_rate: int | None = None,
) -> tuple[int | None, int | None]:
    """Estimate FTP and threshold HR from activity stream data.

    Uses two methods per activity and returns the best (highest) FTP estimate:
    - Method 1: 95 % of best 20-min average power.
    - Method 2: HR-corrected FTP derived from 10-, 15-, and 20-min best
      intervals when heart-rate data and ``max_heart_rate`` are available.

    Returns ``(computed_ftp, computed_threshold_hr)``.  Both are ``None`` when
    insufficient data is available.
    """
    ftp_candidates: list[int] = []
    threshold_hrs: list[int] = []

    for _act_id, streams in streams_by_id.items():
        watts_data: list[float] = streams.get("watts", {}).get("data", [])
        hr_data: list[float] = streams.get("heartrate", {}).get("data", [])
        time_data: list[float] = streams.get("time", {}).get("data", [])

        if not watts_data or not time_data:
            continue

        # --- Method 1: best-20-min power × 0.95 ---
        best20, start20, end20 = best_n_min_power(watts_data, time_data, 20)
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
                best_n, s, e = best_n_min_power(watts_data, time_data, n_min)
                if best_n is not None:
                    seg_hr = hr_data[s : e + 1]
                    if seg_hr:
                        avg_interval_hr = sum(seg_hr) / len(seg_hr)
                        ftp_hr = hr_corrected_ftp(best_n, avg_interval_hr, max_heart_rate)
                        if ftp_hr is not None:
                            ftp_candidates.append(ftp_hr)

    computed_ftp = max(ftp_candidates) if ftp_candidates else None
    computed_threshold_hr = round(sum(threshold_hrs) / len(threshold_hrs)) if threshold_hrs else None
    return computed_ftp, computed_threshold_hr
