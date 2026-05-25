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

# Power-duration windows used for FTP inference.  The factor converts a maximal
# mean power for that duration to an FTP-like estimate.
FTP_POWER_DURATION_FACTORS: tuple[tuple[float, float], ...] = (
    (10.0, 0.90),
    (12.0, 0.92),
    (20.0, 0.95),
    (30.0, 0.97),
    (40.0, 0.985),
    (60.0, 1.00),
)

# Shorter efforts are useful for fitting a power-duration curve, but are too
# VO2-heavy to convert directly to FTP.
CRITICAL_POWER_DURATIONS: tuple[float, ...] = (5.0, 8.0, 12.0, 20.0, 30.0, 40.0)
FTP_ESTIMATE_DURATIONS: tuple[float, ...] = tuple(
    sorted(
        {minutes for minutes, _factor in FTP_POWER_DURATION_FACTORS}
        | set(CRITICAL_POWER_DURATIONS)
    )
)

# Average-power threshold that separates endurance from tempo zones (~76 % FTP).
# Below this level a sustained ride is aerobic/endurance; at or above it the
# effort is in the tempo/sweet-spot band.
TEMPO_THRESHOLD_PCT = 0.76

# Very short rides should not be treated as meaningful endurance work.  These
# thresholds keep warmups, commutes, short spins, and aborted workouts out of
# the normal endurance bucket.
VERY_SHORT_RIDE_SECS = 10 * 60
MIN_ENDURANCE_RIDE_SECS = 30 * 60
# Minimum duration for endurance rides to be classified with high confidence.
# Rides shorter than this are in the aerobic zone but too brief to confirm
# meaningful adaptation.
MIN_HIGH_CONFIDENCE_ENDURANCE_SECS = 45 * 60

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


def _segment_average(values: list[float], start: int, end: int) -> float | None:
    segment = values[start : end + 1]
    if not segment:
        return None
    return sum(segment) / len(segment)


def _duration_hr_is_hard_enough(
    avg_hr: float | None,
    max_hr: int | None,
    duration_minutes: float,
) -> bool:
    """Return whether HR supports treating a window as FTP evidence.

    HR is deliberately used as a gate, not as a multiplier.  Low-HR windows may
    still be good training, but they are weak evidence for threshold power.
    """
    if avg_hr is None or not max_hr or max_hr <= 0:
        return True
    hr_fraction = avg_hr / max_hr
    if duration_minutes < 20.0:
        return hr_fraction >= 0.84
    if duration_minutes < 40.0:
        return hr_fraction >= 0.82
    return hr_fraction >= 0.80


def _power_window_is_steady_enough(
    watts: list[float] | None,
    start: int,
    end: int,
) -> bool:
    if watts is None or len(watts) <= end:
        return True
    segment = watts[start : end + 1]
    if len(segment) < 2:
        return False
    mean_power = sum(segment) / len(segment)
    if mean_power <= 0:
        return False
    variance = sum((value - mean_power) ** 2 for value in segment) / len(segment)
    return (math.sqrt(variance) / mean_power) <= 0.20


def _best_power_points(
    watts: list[float],
    time_stream: list[float],
    durations_minutes: tuple[float, ...],
) -> dict[float, tuple[float, int, int]]:
    points: dict[float, tuple[float, int, int]] = {}
    for minutes in durations_minutes:
        best, start, end = best_n_min_power(watts, time_stream, minutes)
        if best is not None and best >= 50.0:
            points[minutes] = (best, start, end)
    return points


def _critical_power_from_points(points: dict[float, float]) -> int | None:
    """Fit CP from a maximal mean power curve using work = CP × time + W'."""
    if len(points) < 3:
        return None

    durations = sorted(points)
    if (durations[-1] - durations[0]) < 15.0:
        return None

    shortest_power = points[durations[0]]
    longest_power = points[durations[-1]]
    # A flat curve usually means the ride did not expose maximal short efforts,
    # so a CP fit would overstate threshold.
    if shortest_power < longest_power * 1.05:
        return None

    xs = [minutes * 60.0 for minutes in durations]
    ys = [points[minutes] * minutes * 60.0 for minutes in durations]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    den = sum((x - mean_x) ** 2 for x in xs)
    if den <= 0:
        return None
    cp = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(len(xs))) / den
    if cp <= 0:
        return None

    longest_estimate = longest_power * dict(FTP_POWER_DURATION_FACTORS).get(
        durations[-1], 0.95
    )
    lower_bound = longest_estimate * 0.90
    upper_bound = min(shortest_power * 0.95, longest_estimate * 1.08)
    if not (lower_bound <= cp <= upper_bound):
        return None
    return round(cp)


def _ftp_candidates_from_power_duration_points(
    points: dict[float, tuple[float, int, int]],
    watts: list[float] | None = None,
    hr_data: list[float] | None = None,
    max_heart_rate: int | None = None,
    require_hr_for_short_efforts: bool = True,
) -> list[int]:
    candidates: list[int] = []

    for minutes, factor in FTP_POWER_DURATION_FACTORS:
        point = points.get(minutes)
        if point is None:
            continue
        if (
            require_hr_for_short_efforts
            and minutes < 20.0
            and (hr_data is None or not max_heart_rate)
        ):
            continue
        power, start, end = point
        if not _power_window_is_steady_enough(watts, start, end):
            continue
        avg_hr = (
            _segment_average(hr_data, start, end)
            if hr_data is not None and len(hr_data) > end
            else None
        )
        if _duration_hr_is_hard_enough(avg_hr, max_heart_rate, minutes):
            candidates.append(round(power * factor))

    cp_points = {
        minutes: power
        for minutes, (power, start, end) in points.items()
        if minutes in CRITICAL_POWER_DURATIONS
        and _power_window_is_steady_enough(watts, start, end)
        and (
            not require_hr_for_short_efforts
            or minutes >= 20.0
            or (hr_data is not None and max_heart_rate is not None)
        )
        and _duration_hr_is_hard_enough(
            (
                _segment_average(hr_data, start, end)
                if hr_data is not None and len(hr_data) > end
                else None
            ),
            max_heart_rate,
            minutes,
        )
    }
    cp = _critical_power_from_points(cp_points)
    if cp is not None:
        candidates.append(cp)

    return candidates


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
        if (
            merged
            and (time_stream[block[0]] - time_stream[merged[-1][1]])
            <= recovery_gap_secs
        ):
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


def _stream_duration_seconds(time_stream: list[float]) -> float:
    """Estimate ride duration from a Strava-style time stream."""
    if not time_stream:
        return 0.0
    if len(time_stream) == 1:
        return 1.0

    elapsed = time_stream[-1] - time_stream[0]
    if elapsed < 0:
        return 0.0

    sample_spacing = elapsed / max(1, len(time_stream) - 1)
    if sample_spacing <= 0:
        sample_spacing = 1.0
    return elapsed + sample_spacing


def classify_ride_purpose(
    watts: list[float],
    time_stream: list[float],
    ftp: float,
) -> str:
    """Classify the overall purpose/category of a ride.

    Categories (aligned with problem statement):
    - ``unknown``             : insufficient stream data or FTP
    - ``short_easy_spin``     : short low/medium-intensity ride, not enough aerobic duration
    - ``short_hard_effort``   : short high-intensity ride without clear interval structure
    - ``recovery``            : avg power < 60 % FTP
    - ``endurance``           : avg power 60–75 % FTP, no hard intervals
    - ``tempo``               : avg power ~76–85 % FTP, no distinct intervals
    - ``interval_sweetspot``  : detected intervals lasting 10–20 min at 88–95 % FTP
    - ``interval_threshold``  : detected intervals ~5–12 min at 95–105 % FTP
    - ``interval_vo2max``     : detected intervals 2–5 min at 106–130 % FTP
    - ``interval_sprints``    : detected intervals < 2 min at > 130 % FTP
    - ``mixed``               : multiple distinct interval types detected
    """
    if not watts or not time_stream or len(watts) != len(time_stream) or ftp <= 0:
        return "unknown"

    avg_power = sum(watts) / len(watts)
    avg_pct = avg_power / ftp
    duration_secs = _stream_duration_seconds(time_stream)

    # Detect intervals at 85 % threshold
    intervals = detect_intervals(watts, time_stream, ftp, work_threshold_pct=0.85)

    if not intervals:
        if duration_secs < VERY_SHORT_RIDE_SECS:
            return "unknown"
        if duration_secs < MIN_ENDURANCE_RIDE_SECS:
            return (
                "short_hard_effort"
                if avg_pct >= TEMPO_THRESHOLD_PCT
                else "short_easy_spin"
            )

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
        if duration_secs < MIN_ENDURANCE_RIDE_SECS:
            return (
                "short_hard_effort"
                if avg_pct >= TEMPO_THRESHOLD_PCT
                else "short_easy_spin"
            )
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


def classify_ride_confidence_and_reason(
    ride_category: str,
    duration_seconds: float,
    intervals: list[dict],
) -> tuple[str, str]:
    """Return (confidence, reason) for a ride classification.

    confidence: ``'high'``, ``'medium'``, or ``'low'``.
    reason: one short machine-readable sentence explaining the confidence level.

    Rules:
    - ``unknown`` / short rides → ``low``: not enough data or duration.
    - ``recovery`` / ``endurance`` → ``high`` when duration ≥ MIN_ENDURANCE_RIDE_SECS,
      ``medium`` otherwise.
    - ``tempo`` → ``medium``: no interval structure to confirm intent.
    - Structured interval categories → ``high`` with ≥ 2 intervals, ``medium`` with 1.
    - ``mixed`` → ``medium``: ambiguous multi-type session.
    """
    if ride_category == "unknown":
        return "low", "Insufficient stream data to classify ride reliably."
    if ride_category == "short_easy_spin":
        return "low", "Ride too short for a reliable aerobic classification."
    if ride_category == "short_hard_effort":
        return (
            "low",
            "Short high-intensity effort; may be a warmup or incomplete session.",
        )
    if ride_category == "recovery":
        if duration_seconds >= MIN_ENDURANCE_RIDE_SECS:
            return (
                "high",
                "Average power consistently below recovery threshold for sufficient duration.",
            )
        return (
            "medium",
            "Low average power suggests recovery, but ride duration is limited.",
        )
    if ride_category == "endurance":
        if duration_seconds >= MIN_HIGH_CONFIDENCE_ENDURANCE_SECS:
            return "high", "Sustained aerobic effort across adequate ride duration."
        return "medium", "Average power in aerobic zone, but ride duration is short."
    if ride_category == "tempo":
        return (
            "medium",
            "Average power in tempo band with no distinct interval blocks detected.",
        )
    if ride_category in (
        "interval_sweetspot",
        "interval_threshold",
        "interval_vo2max",
        "interval_sprints",
    ):
        interval_label = ride_category.split("_", 1)[1]
        if len(intervals) >= 2:
            return "high", f"Structured {interval_label} intervals detected."
        return (
            "medium",
            f"Single {interval_label} effort detected; may not be a structured session.",
        )
    if ride_category == "mixed":
        return (
            "medium",
            "Multiple interval types detected; overall training intent is ambiguous.",
        )
    # Fallback for any unknown future categories
    return "medium", "Classification based on available power data."


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


def compute_readiness_recommendations(
    ctl: float,
    atl: float,
    tsb: float,
    score: float,
    days_until_race: int,
) -> list[str]:
    """Generate short, actionable bullet-point recommendations to improve race readiness.

    Recommendations are derived from the athlete's current CTL (fitness), ATL
    (fatigue), TSB (form), overall score, and the time left until race day.

    Returns a list of short strings — each is one bullet point.
    """
    tips: list[str] = []

    # --- TSB / form feedback ---
    if tsb < -20:
        tips.append(
            "You are heavily fatigued — prioritise 2–3 easy recovery rides this week."
        )
    elif tsb < -10:
        tips.append(
            "Fatigue is elevated — include at least one full rest day before intensity work."
        )
    elif tsb < 0:
        tips.append(
            "Slight fatigue: balance training stress with adequate sleep and nutrition."
        )
    elif tsb <= 10:
        tips.append(
            "Form is neutral — good time for quality interval sessions to build fitness."
        )
    elif tsb <= 20:
        tips.append(
            "Form is optimal for racing. Maintain with short openers; avoid heavy loads."
        )
    else:
        tips.append(
            "You are very fresh — consider adding some intensity to avoid detraining."
        )

    # --- CTL / fitness feedback ---
    if ctl < 30:
        tips.append(
            "Build your fitness base with consistent 45–90 min rides 3–4 times per week."
        )
    elif ctl < 60:
        tips.append(
            "Add one longer endurance ride per week (2–3 h) to raise your fitness base."
        )
    elif ctl < 80:
        tips.append(
            "Fitness is solid — focus on quality over quantity; one hard session per week."
        )
    else:
        tips.append(
            "High fitness level — protect your CTL with consistent training and avoid gaps."
        )

    # --- Race-specific advice ---
    if days_until_race > 0:
        if days_until_race > 21:
            tips.append(
                f"{days_until_race} days to race: now is the time to accumulate training load."
            )
        elif days_until_race > 10:
            tips.append(
                f"{days_until_race} days to race: begin tapering — reduce volume by ~20 % while keeping intensity."
            )
        elif days_until_race > 3:
            tips.append(
                f"{days_until_race} days to race: taper fully — short, sharp sessions only; prioritise sleep."
            )
        else:
            tips.append(
                f"{days_until_race} day{'s' if days_until_race != 1 else ''} to race: rest up, eat well, and visualise your race plan."
            )
    elif days_until_race == 0:
        tips.append("Race day! Warm up well and trust your training.")

    # --- Overall score nudge ---
    if score < 40:
        tips.append(
            "Target score ≥ 65 for race day: build fitness now and taper the last 7–10 days."
        )
    elif score < 65:
        tips.append(
            "You are on track — keep consistent training and manage fatigue leading up to race day."
        )

    return tips


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
        day for day in plan_days if day.get("date", "") <= target_date_str
    ]
    return compute_training_load(days_up_to_target, ftp)


def project_training_load_from_seed(
    future_plan_days: list[dict],
    ftp: float,
    seed_ctl: float,
    seed_atl: float,
) -> dict:
    """Forward-project CTL/ATL/TSB starting from real (seeded) CTL/ATL values.

    Unlike :func:`_project_training_load` which simulates the entire plan from
    zero, this function starts from *seed_ctl* / *seed_atl* — i.e. the actual
    CTL and ATL measured from real Strava rides — and then applies the future
    plan days on top of that.

    This produces a more accurate race-day projection because the starting
    point reflects what the athlete has actually done rather than what the plan
    assumed.

    Returns ``{"ctl": float, "atl": float, "tsb": float, "daily_tss": list[float]}``.
    """
    if ftp <= 0:
        return {
            "ctl": seed_ctl,
            "atl": seed_atl,
            "tsb": seed_ctl - seed_atl,
            "daily_tss": [],
        }

    alpha_ctl = 1.0 - math.exp(-1.0 / 42.0)
    alpha_atl = 1.0 - math.exp(-1.0 / 7.0)

    ctl = seed_ctl
    atl = seed_atl
    daily_tss: list[float] = []

    # Re-use TSS estimation logic from compute_training_load
    for day in future_plan_days:
        duration_min = day.get("durationMinutes") or 0
        duration_s = duration_min * 60.0
        if duration_s <= 0:
            tss = 0.0
        else:
            target_power = day.get("targetPower")
            np_approx: float | None = None
            if target_power and isinstance(target_power, dict):
                low = target_power.get("low") or 0
                high = target_power.get("high") or 0
                if low > 0 and high > 0:
                    np_approx = (low + high) / 2.0
                elif high > 0:
                    np_approx = float(high)
                elif low > 0:
                    np_approx = float(low)

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
                np_approx = ftp * type_pct_map.get(workout_type, 0.65)

            if np_approx <= 0:
                tss = 0.0
            else:
                intensity_factor = np_approx / ftp
                tss = (
                    (duration_s * np_approx * intensity_factor) / (ftp * 3600.0) * 100.0
                )

        daily_tss.append(round(tss, 1))
        ctl = ctl + alpha_ctl * (tss - ctl)
        atl = atl + alpha_atl * (tss - atl)

    tsb = ctl - atl
    return {
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb": round(tsb, 1),
        "daily_tss": daily_tss,
    }


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
        while (
            window_end_idx < len(watts)
            and time_stream[window_end_idx] < window_end_time
        ):
            window_end_idx += 1

        if window_end_idx <= window_start_idx:
            break

        seg = watts[window_start_idx:window_end_idx]
        avg = sum(seg) / len(seg)
        pct_over = (avg - target_power) / target_power * 100.0
        if pct_over > spike_threshold_pct:
            start_min = round(
                (time_stream[window_start_idx] - time_stream[0]) / 60.0, 1
            )
            end_min = round(
                (time_stream[window_end_idx - 1] - time_stream[0]) / 60.0, 1
            )
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

    if not time_data:
        return {}

    duration_seconds = round(_stream_duration_seconds(time_data))

    if not watts or len(watts) != len(time_data):
        no_intervals: list[dict] = []
        confidence, reason = classify_ride_confidence_and_reason(
            "unknown", duration_seconds, no_intervals
        )
        return {
            "ride_category": "unknown",
            "classification_confidence": confidence,
            "classification_reason": reason,
            "duration_seconds": duration_seconds,
            "intervals_detected": no_intervals,
        }

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

    confidence, reason = classify_ride_confidence_and_reason(
        ride_category, duration_seconds, annotated
    )

    return {
        "ride_category": ride_category,
        "classification_confidence": confidence,
        "classification_reason": reason,
        "duration_seconds": duration_seconds,
        "avg_power_w": round(sum(watts) / len(watts)),
        "intervals_detected": annotated,
    }


def compute_ftp_from_streams(
    streams_by_id: dict[str, dict],
    max_heart_rate: int | None = None,
) -> tuple[int | None, int | None]:
    """Estimate FTP and threshold HR from activity stream data.

    Uses demonstrated power-duration evidence and returns the best FTP-like
    estimate.  HR, when available, is only used to reject low-intensity windows;
    it is not used to scale power into FTP.

    Returns ``(computed_ftp, computed_threshold_hr)``.  Both are ``None`` when
    insufficient data is available.
    """
    ftp_candidates: list[int] = []
    threshold_hrs: list[int] = []
    envelope_points: dict[float, tuple[float, int, int]] = {}

    for _act_id, streams in streams_by_id.items():
        watts_data: list[float] = streams.get("watts", {}).get("data", [])
        hr_data: list[float] = streams.get("heartrate", {}).get("data", [])
        time_data: list[float] = streams.get("time", {}).get("data", [])

        if not watts_data or not time_data:
            continue

        points = _best_power_points(
            watts_data,
            time_data,
            FTP_ESTIMATE_DURATIONS,
        )
        usable_hr = hr_data if hr_data and len(hr_data) == len(time_data) else None
        ftp_candidates.extend(
            _ftp_candidates_from_power_duration_points(
                points,
                watts_data,
                usable_hr,
                max_heart_rate,
            )
        )
        for minutes, point in points.items():
            if minutes < 20.0 and (usable_hr is None or not max_heart_rate):
                continue
            power, start, end = point
            if not _power_window_is_steady_enough(watts_data, start, end):
                continue
            avg_hr = (
                _segment_average(usable_hr, start, end)
                if usable_hr is not None and len(usable_hr) > end
                else None
            )
            if not _duration_hr_is_hard_enough(avg_hr, max_heart_rate, minutes):
                continue
            current = envelope_points.get(minutes)
            if current is None or power > current[0]:
                envelope_points[minutes] = point

        best20 = points.get(20.0)
        if best20 is not None and usable_hr:
            _, start20, end20 = best20
            avg_hr = _segment_average(usable_hr, start20, end20)
            if avg_hr is not None and _duration_hr_is_hard_enough(
                avg_hr, max_heart_rate, 20.0
            ):
                threshold_hrs.append(round(avg_hr))

    ftp_candidates.extend(
        _ftp_candidates_from_power_duration_points(
            envelope_points,
            require_hr_for_short_efforts=False,
        )
    )
    computed_ftp = max(ftp_candidates) if ftp_candidates else None
    computed_threshold_hr = (
        round(sum(threshold_hrs) / len(threshold_hrs)) if threshold_hrs else None
    )
    return computed_ftp, computed_threshold_hr


# ---------------------------------------------------------------------------
# Ride-metrics chain computation
# ---------------------------------------------------------------------------


def estimate_ftp_over_time(
    rides: list[dict],
    max_heart_rate: int | None = None,
    smoothing_days: int = 21,
) -> list[dict]:
    """Estimate FTP over time from a rolling power-duration envelope.

    Each ride contributes best-power points for standard durations.  FTP is
    estimated from the strongest recent power-duration curve, using direct
    duration factors and an optional critical-power fit.  HR, when present, is
    only used to reject low-intensity windows; it is never used to scale power.

    For each date, ``raw_ftp`` is the estimate from that ride alone and ``ftp``
    is recomputed from the best power-duration points across all rides in the
    preceding ``smoothing_days`` days.  This behaves like a rolling capability
    envelope: strong rides establish recent ability, while easy rides usually
    contribute no FTP evidence instead of dragging the estimate down.

    Args:
        rides: List of dicts each containing:

            - ``activity_date`` (str, ISO date YYYY-MM-DD)
            - ``streams`` (dict of Strava stream objects with ``watts``,
              ``heartrate``, and ``time`` keys)

        max_heart_rate: Athlete's max heart rate in bpm.  Used only to filter
            low-intensity HR windows; power-only estimates require longer
            sustained efforts when not provided.
        smoothing_days: Size of the sliding window (days) used to take the
            recent power-duration envelope.  Default 21 (3 weeks) — long
            enough to smooth noise while still tracking gradual FTP changes.

    Returns:
        List of ``{"date": str, "ftp": int, "raw_ftp": int}`` dicts ordered
        by date.  Returns an empty list when no valid FTP estimates can be
        produced.
    """
    import datetime as _dt

    ride_points: list[
        tuple[str, dict[float, tuple[float, int, int]], list[float], list[float] | None]
    ] = []
    raw_estimates: list[tuple[str, int]] = []

    for ride in rides:
        activity_date = ride.get("activity_date", "")
        if not activity_date:
            continue

        streams = ride.get("streams", {})
        watts: list[float] = streams.get("watts", {}).get("data", [])
        hr_data: list[float] = streams.get("heartrate", {}).get("data", [])
        time_data: list[float] = streams.get("time", {}).get("data", [])

        if not watts or not time_data or len(watts) != len(time_data):
            continue

        points = _best_power_points(watts, time_data, FTP_ESTIMATE_DURATIONS)
        usable_hr = hr_data if hr_data and len(hr_data) == len(watts) else None
        candidates = _ftp_candidates_from_power_duration_points(
            points,
            watts,
            usable_hr,
            max_heart_rate,
        )
        best_ftp_for_ride = max(candidates) if candidates else None

        if best_ftp_for_ride is not None:
            ride_points.append((activity_date, points, watts, usable_hr))
            raw_estimates.append((activity_date, best_ftp_for_ride))

    if not raw_estimates:
        return []

    # Sort raw estimates by date ascending.
    raw_estimates.sort(key=lambda x: x[0])
    ride_points.sort(key=lambda x: x[0])

    window = _dt.timedelta(days=max(1, smoothing_days))

    result: list[dict] = []
    for i, (date_str, raw_ftp) in enumerate(raw_estimates):
        try:
            end_date = _dt.date.fromisoformat(date_str)
        except ValueError:
            end_date = None

        if end_date is None:
            result.append({"date": date_str, "ftp": raw_ftp, "raw_ftp": raw_ftp})
            continue

        start_date = end_date - window
        envelope_points: dict[float, tuple[float, int, int]] = {}
        for d_str, points, point_watts, _hr in ride_points:
            if not _in_window(d_str, start_date, end_date):
                continue
            for minutes, point in points.items():
                if minutes < 20.0 and (_hr is None or not max_heart_rate):
                    continue
                power, point_start, point_end = point
                if not _power_window_is_steady_enough(
                    point_watts, point_start, point_end
                ):
                    continue
                avg_hr = (
                    _segment_average(_hr, point_start, point_end)
                    if _hr is not None and len(_hr) > point_end
                    else None
                )
                if not _duration_hr_is_hard_enough(avg_hr, max_heart_rate, minutes):
                    continue
                current = envelope_points.get(minutes)
                if current is None or power > current[0]:
                    envelope_points[minutes] = point

        envelope_candidates = _ftp_candidates_from_power_duration_points(
            envelope_points,
            require_hr_for_short_efforts=False,
        )
        smoothed_ftp = max(envelope_candidates) if envelope_candidates else raw_ftp
        result.append({"date": date_str, "ftp": smoothed_ftp, "raw_ftp": raw_ftp})

    return result


def _in_window(date_str: str, start: object, end: object) -> bool:
    """Return True when ``date_str`` falls in the closed interval [start, end]."""
    import datetime as _dt

    try:
        d = _dt.date.fromisoformat(date_str)
    except ValueError:
        return False
    return start <= d <= end  # type: ignore[operator]


def compute_ride_tss(
    duration_seconds: float, normalized_power: float, ftp: float
) -> float | None:
    """Compute Training Stress Score for a single ride.

    TSS = (duration_s × NP²) / (FTP² × 3600) × 100

    Returns ``None`` when any input is ≤ 0.
    """
    if duration_seconds <= 0 or normalized_power <= 0 or ftp <= 0:
        return None
    intensity_factor = normalized_power / ftp
    tss = (
        (duration_seconds * normalized_power * intensity_factor)
        / (ftp * 3600.0)
        * 100.0
    )
    return round(tss, 1)


def apply_ctl_atl_decay(
    prev_ctl: float,
    prev_atl: float,
    tss: float,
    gap_days: int = 1,
) -> tuple[float, float]:
    """Advance CTL/ATL by ``gap_days``, applying zero-TSS decay for silent days
    then ``tss`` on the final (ride) day.

    ``gap_days=1`` means the ride is on the very next day — no silent days.
    ``gap_days=3`` means 2 rest days then the ride day.

    Uses standard exponential smoothing constants:
    - CTL: 42-day time constant  → α = 1 − exp(−1/42)
    - ATL: 7-day time constant   → α = 1 − exp(−1/7)
    """
    alpha_ctl = 1.0 - math.exp(-1.0 / 42.0)
    alpha_atl = 1.0 - math.exp(-1.0 / 7.0)

    # Decay through silent days (TSS = 0 each day before the ride)
    silent_days = max(0, gap_days - 1)
    if silent_days > 0:
        # Compounded zero-TSS decay: CTL_n = CTL_0 * (1 - α)^n
        decay_ctl = (1.0 - alpha_ctl) ** silent_days
        decay_atl = (1.0 - alpha_atl) ** silent_days
        prev_ctl = prev_ctl * decay_ctl
        prev_atl = prev_atl * decay_atl

    # Apply ride TSS on the ride day
    new_ctl = prev_ctl + alpha_ctl * (tss - prev_ctl)
    new_atl = prev_atl + alpha_atl * (tss - prev_atl)
    return new_ctl, new_atl


def build_rule_based_summary(
    ride_purpose: str,
    duration_seconds: float,
    normalized_power: float | None,
    tss: float | None,
    intervals: list[dict],
) -> str:
    """Build a short deterministic 1-line ride summary — no LLM required.

    Examples:
    - "Threshold intervals: 3×10 min @ 275 W · TSS 94 · 1h20m"
    - "Endurance: NP 198 W · TSS 61 · 2h05m"
    - "Recovery: 45 min"
    """
    h = int(duration_seconds // 3600)
    m = int((duration_seconds % 3600) // 60)
    duration_str = f"{h}h{m:02d}m" if h > 0 else f"{m}m"

    label_map = {
        "unknown": "Unknown ride",
        "short_easy_spin": "Short easy spin",
        "short_hard_effort": "Short hard effort",
        "recovery": "Recovery",
        "endurance": "Endurance",
        "tempo": "Tempo",
        "interval_sweetspot": "Sweet-spot intervals",
        "interval_threshold": "Threshold intervals",
        "interval_vo2max": "VO2max intervals",
        "interval_sprints": "Sprint intervals",
        "mixed": "Mixed intervals",
    }
    label = label_map.get(ride_purpose, ride_purpose.replace("_", " ").capitalize())

    parts = [label]

    # Interval summary: count × duration @ avg_power
    if intervals and ride_purpose not in ("recovery", "endurance", "tempo"):
        reps = len(intervals)
        avg_dur_min = round(sum(iv["duration_secs"] for iv in intervals) / reps / 60)
        avg_power = round(sum(iv["avg_power"] for iv in intervals) / reps)
        parts[0] = f"{label}: {reps}×{avg_dur_min} min @ {avg_power} W"
    elif normalized_power:
        parts.append(f"NP {round(normalized_power)} W")

    if tss is not None:
        parts.append(f"TSS {round(tss)}")

    parts.append(duration_str)
    return " · ".join(parts)


def build_ride_metrics_chain(
    rides: list[dict],
    ftp: float,
    initial_ctl: float = 0.0,
    initial_atl: float = 0.0,
) -> list[dict]:
    """Compute per-ride metrics and the rolling CTL/ATL/TSB chain.

    Args:
        rides: List of ride dicts, each containing:
            - ``strava_activity_id`` (int)
            - ``activity_date`` (str, ISO date YYYY-MM-DD)
            - ``sport_type`` (str)
            - ``duration_seconds`` (int)
            - ``streams`` (dict of Strava stream objects keyed by type)
        ftp: Current FTP in watts. Used for all rides (single snapshot).
        initial_ctl: Starting CTL value (0.0 for full historical rebuild).
        initial_atl: Starting ATL value (0.0 for full historical rebuild).

    Returns:
        List of metric dicts (same order as input) ready for DB upsert.
        Each dict matches the ``RideMetric`` columns (excluding id/user_id/created_at).
    """
    import datetime as _dt  # local import to avoid circular deps at module level

    if not rides:
        return []

    # Sort by date ascending to build the chain correctly
    sorted_rides = sorted(rides, key=lambda r: r["activity_date"])

    ctl = initial_ctl
    atl = initial_atl
    prev_date_str: str | None = None
    result: list[dict] = []

    for ride in sorted_rides:
        streams = ride.get("streams", {})
        watts: list[float] = streams.get("watts", {}).get("data", [])
        time_data: list[float] = streams.get("time", {}).get("data", [])

        # --- Per-ride metrics ---
        avg_power: int | None = None
        np_value: int | None = None
        intensity_factor: float | None = None
        tss: float | None = None
        ride_purpose: str | None = None
        intervals: list[dict] = []

        if watts and time_data and len(watts) == len(time_data):
            avg_power = round(sum(watts) / len(watts))
            np_raw = _normalized_power(watts, time_data)
            if np_raw is not None:
                np_value = round(np_raw)
                if ftp > 0:
                    intensity_factor = round(np_raw / ftp, 3)
                    tss = compute_ride_tss(ride["duration_seconds"], np_raw, ftp)
            ride_purpose = classify_ride_purpose(watts, time_data, ftp)
            if ftp > 0:
                intervals = detect_intervals(watts, time_data, ftp)
        else:
            # Missing or mismatched streams are not enough evidence for a
            # training-purpose label.
            ride_purpose = "unknown"

        # --- CTL/ATL decay and update ---
        activity_date_str = ride["activity_date"]
        gap_days = 1
        if prev_date_str is not None:
            try:
                prev_date = _dt.date.fromisoformat(prev_date_str)
                curr_date = _dt.date.fromisoformat(activity_date_str)
                gap_days = max(1, (curr_date - prev_date).days)
            except ValueError:
                gap_days = 1

        ride_tss = tss if tss is not None else 0.0
        ctl, atl = apply_ctl_atl_decay(ctl, atl, ride_tss, gap_days=gap_days)
        tsb = ctl - atl
        prev_date_str = activity_date_str

        # --- Rule-based summary ---
        duration_s = ride.get("duration_seconds") or 0
        summary = build_rule_based_summary(
            ride_purpose or ride.get("sport_type", "ride"),
            duration_s,
            float(np_value) if np_value else None,
            tss,
            intervals,
        )

        # --- Classification confidence and reason ---
        classification_confidence, classification_reason = (
            classify_ride_confidence_and_reason(
                ride_purpose or "unknown",
                duration_s,
                intervals,
            )
        )

        result.append(
            {
                "strava_activity_id": ride["strava_activity_id"],
                "activity_name": ride.get("activity_name"),
                "activity_start_datetime": ride.get("activity_start_datetime"),
                "activity_date": activity_date_str,
                "sport_type": ride.get("sport_type", "cycling"),
                "duration_seconds": ride.get("duration_seconds"),
                "start_lat": ride.get("start_lat"),
                "start_lng": ride.get("start_lng"),
                "weather_temperature_c": ride.get("weather_temperature_c"),
                "weather_apparent_temperature_c": ride.get(
                    "weather_apparent_temperature_c"
                ),
                "weather_condition": ride.get("weather_condition"),
                "weather_code": ride.get("weather_code"),
                "weather_wind_speed_kph": ride.get("weather_wind_speed_kph"),
                "weather_precipitation_mm": ride.get("weather_precipitation_mm"),
                "weather_source": ride.get("weather_source"),
                "avg_power_w": avg_power,
                "normalized_power_w": np_value,
                "intensity_factor": intensity_factor,
                "tss": tss,
                "ftp_used": round(ftp) if ftp > 0 else None,
                "ctl_after": round(ctl, 2),
                "atl_after": round(atl, 2),
                "tsb_after": round(tsb, 2),
                "ride_purpose": ride_purpose,
                "classification_confidence": classification_confidence,
                "classification_reason": classification_reason,
                "summary": summary,
            }
        )

    return result
