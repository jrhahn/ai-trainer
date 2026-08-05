"""Compliance scoring for a completed activity against its planned session.

This is the badge the athlete sees on each dashboard activity card — "Perfect",
"Solid", "Needs work", "Too much". It used to be computed only in the browser
(``computeMatchScore``/``matchScoreLabel`` in ``DashboardPage.tsx``), which left
the coach unable to see the very badge it was being asked to explain: the chat
prompt carried ``plan match:auto_matched`` — a *linkage* status — and the coach
read that as a compliance verdict, answering "logged as a successful match"
about a ride the dashboard had flagged red. Asked why, it then explained the
only ``Needs work`` in its context, which belonged to a ride five weeks earlier.

Scoring here, and persisting the result on the ride, makes one number the source
of truth for the card, the coach chat, and every other prompt built from
:func:`services.prompts.ride_metrics_context_section`. This is the per-activity
counterpart to :func:`services.prompts.training_status_section`, which fixed the
same confabulation for the weekly status badge (#499).
"""

from __future__ import annotations

from typing import Any, Protocol

from services.duration_range import duration_range

# Recovery and rest days are graded on how easy the athlete kept it, never on
# how closely they hit the clock. The generic cycling ladder below has only the
# duration ratio to go on when the plan (as LLM-written plans routinely do)
# states its watts in prose instead of in ``targetPower`` — which scored a
# 76-minute spin at IF 0.50 against a planned 50-minute recovery ride 0/100 and
# badged it "Needs work", when the athlete had done exactly what was asked.
LOAD_GRADED_WORKOUT_TYPES = frozenset({"rest", "recovery"})

# Intensity ladder for a planned recovery ride. Recovery is defined by how hard
# it was ridden, so intensity factor is the primary signal; a recovery ride
# carries real TSS by design and must not be punished for it.
RECOVERY_IF_EASY = 0.60
RECOVERY_IF_ACCEPTABLE = 0.68
RECOVERY_IF_BRISK = 0.78

# A recovery ride may run over — legs decide when they are flushed — but past
# some multiple of the planned time it stops being recovery. Overrunning caps
# the score rather than collapsing it.
RECOVERY_OVERRUN_RATIO = 1.5
RECOVERY_LONG_OVERRUN_RATIO = 2.5
RECOVERY_OVERRUN_CAP = 85
RECOVERY_LONG_OVERRUN_CAP = 55

# Load ladder for rest days and for days too unspecified to compare, in TSS.
# Here any load at all is the thing being measured.
REST_TSS_EASY = 30.0
REST_TSS_MODERATE = 65.0


class RideLike(Protocol):
    """The ride fields scoring needs — satisfied by ``models.RideMetric``."""

    duration_seconds: int | None
    normalized_power_w: int | None
    avg_power_w: int | None
    intensity_factor: float | None
    tss: float | None


def _text(day: dict, *keys: str) -> str:
    for key in keys:
        value = day.get(key)
        if value:
            return str(value).lower()
    return ""


def _target_power(day: dict) -> tuple[int, int] | None:
    """The planned power window as ``(low, high)``, or None when unset."""
    target = day.get("targetPower")
    if target is None:
        target = day.get("target_power")
    if not isinstance(target, dict):
        return None
    low, high = target.get("low"), target.get("high")
    try:
        low_i, high_i = int(low), int(high)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if low_i <= 0 or high_i <= 0:
        return None
    return (low_i, high_i) if low_i <= high_i else (high_i, low_i)


def _planned_minutes(day: dict) -> int | None:
    lo, hi = duration_range(day)
    return lo if lo is not None else None


def is_load_graded_plan(day: dict) -> bool:
    """Whether ``day`` is graded on how easy it stayed rather than on execution.

    Rest and recovery days, plus any day so unspecified that there is nothing to
    compare against.
    """
    if _text(day, "workoutType", "workout_type") in LOAD_GRADED_WORKOUT_TYPES:
        return True
    return _planned_minutes(day) is None and _target_power(day) is None


def is_recovery_plan(day: dict) -> bool:
    """A prescribed easy ride — as opposed to a rest day, which prescribes none."""
    return _text(day, "workoutType", "workout_type") == "recovery"


def _score_rest_day(tss: float | None) -> int | None:
    if tss is None:
        return None
    if tss <= REST_TSS_EASY:
        return 80
    if tss <= REST_TSS_MODERATE:
        return 55
    return 20


def _score_recovery_ride(
    intensity_factor: float | None, tss: float | None
) -> int | None:
    """Grade a planned recovery ride on how easy it was actually ridden."""
    if intensity_factor is None:
        # No intensity to judge: fall back to load, which at least separates a
        # spin from a session that turned into real training.
        return _score_rest_day(tss)
    if intensity_factor <= RECOVERY_IF_EASY:
        return 100
    if intensity_factor <= RECOVERY_IF_ACCEPTABLE:
        return 85
    if intensity_factor <= RECOVERY_IF_BRISK:
        return 55
    return 25


def _cap_for_recovery_overrun(score: int, ratio: float | None) -> int:
    if ratio is None:
        return score
    if ratio > RECOVERY_LONG_OVERRUN_RATIO:
        return min(score, RECOVERY_LONG_OVERRUN_CAP)
    if ratio > RECOVERY_OVERRUN_RATIO:
        return min(score, RECOVERY_OVERRUN_CAP)
    return score


def is_strength_plan(day: dict) -> bool:
    """Non-cycling sessions (strength, yoga, swim) with no power target."""
    workout_type = _text(day, "workoutType", "workout_type")
    return workout_type == "strength" and _target_power(day) is None


def is_interval_workout_plan(day: dict) -> bool:
    workout_type = _text(day, "workoutType", "workout_type")
    title = _text(day, "title")
    return workout_type == "intervals" or "interval" in title or "vo2" in title


def effective_planned_minutes(day: dict, actual_seconds: float) -> float:
    """The planned minutes to compare ``actual_seconds`` against (#368).

    An actual inside a prescribed window is on-target, so it scores against
    itself; outside the window it scores against the nearest bound.
    """
    lo, hi = duration_range(day)
    if lo is None or hi is None:
        return 0
    actual_minutes = actual_seconds / 60
    if actual_minutes < lo:
        return lo
    if actual_minutes > hi:
        return hi
    return actual_minutes


def score_duration_match(actual_seconds: float, planned_minutes: float) -> int:
    if not planned_minutes:
        return 0
    ratio = actual_seconds / 60 / planned_minutes
    return max(0, round(100 - abs(1 - ratio) * 200))


def score_target_power(actual_power: float, target: tuple[int, int]) -> int:
    low, high = target
    if low <= actual_power <= high:
        return 100
    edge = low if actual_power < low else high
    deviation = abs(actual_power - edge) / edge
    return max(0, round(100 - deviation * 300))


def score_structured_interval_power(actual_power: float, day: dict) -> int | None:
    """Score interval power, crediting a session ridden just under target.

    An interval set held slightly below the prescribed watts is a completed
    session at a lower intensity, not a missed one, so it floors at 85 rather
    than falling off the linear deviation curve.
    """
    intervals = day.get("intervals") or []
    powers = [
        int(interval["power"])
        for interval in intervals
        if isinstance(interval, dict) and (interval.get("power") or 0) > 0
    ]
    target = _target_power(day)
    if target is None and powers:
        target = (min(powers), max(powers))
    if target is None:
        return None

    target_score = score_target_power(actual_power, target)
    if target_score >= 90:
        return target_score

    target_low, target_high = target
    plausible_session_floor = target_low * 0.75
    if plausible_session_floor <= actual_power <= target_high:
        progress = min(
            1.0,
            (actual_power - plausible_session_floor)
            / (target_low - plausible_session_floor),
        )
        return max(target_score, round(85 + progress * 10))
    return target_score


def compute_match_score(ride: RideLike, day: dict | None) -> int | None:
    """0-100 compliance score, or None when there is too little to compare."""
    if not isinstance(day, dict):
        return None

    duration_seconds = getattr(ride, "duration_seconds", None)
    tss = getattr(ride, "tss", None)
    intensity_factor = getattr(ride, "intensity_factor", None)
    actual_power = getattr(ride, "normalized_power_w", None) or getattr(
        ride, "avg_power_w", None
    )
    planned_minutes = _planned_minutes(day)

    if is_load_graded_plan(day):
        if is_recovery_plan(day):
            score = _score_recovery_ride(intensity_factor, tss)
            if score is None:
                return 90
            ratio = (
                duration_seconds / 60 / planned_minutes
                if duration_seconds and planned_minutes
                else None
            )
            return _cap_for_recovery_overrun(score, ratio)
        score = _score_rest_day(tss)
        if score is not None:
            return score
        if duration_seconds and planned_minutes is not None:
            return score_duration_match(
                duration_seconds, effective_planned_minutes(day, duration_seconds)
            )
        return 90

    if is_strength_plan(day):
        if not duration_seconds or planned_minutes is None:
            return 90
        effective = effective_planned_minutes(day, duration_seconds)
        return min(100, round((duration_seconds / 60 / effective) * 100))

    parts: list[int] = []
    interval_workout = is_interval_workout_plan(day)

    if duration_seconds is not None and planned_minutes is not None:
        planned = effective_planned_minutes(day, duration_seconds)
        duration_score = score_duration_match(duration_seconds, planned)
        if interval_workout and planned:
            ratio = duration_seconds / 60 / planned
            # An interval session that ran long still did the intervals — the
            # warm-up/cool-down around them is not a compliance failure.
            if 1 < ratio <= 2:
                duration_score = max(duration_score, 40)
        parts.append(duration_score)

    if actual_power is not None:
        if interval_workout:
            interval_score = score_structured_interval_power(actual_power, day)
            if interval_score is None:
                interval_score = 88 if (tss is not None and tss >= 70) else 70
            parts.append(interval_score)
        else:
            target = _target_power(day)
            if target is not None:
                parts.append(score_target_power(actual_power, target))

    # For an interval session the watts held are the session; the clock is
    # supporting evidence.
    if interval_workout and len(parts) == 2:
        return round(parts[0] * 0.25 + parts[1] * 0.75)

    return round(sum(parts) / len(parts)) if parts else None


def match_score_label(score: int | None, day: dict | None) -> str | None:
    """The badge text for ``score``, using the ladder that fits the plan type.

    Returns None when there is no score to label. Callers apply any stored
    ``label_override`` themselves — an explicit coach or matcher label outranks
    anything computed here.
    """
    if score is None or not isinstance(day, dict):
        return None

    if is_load_graded_plan(day):
        if score >= 90:
            return "OK"
        if score >= 75:
            return "Recovery"
        if score >= 40:
            return "Warning"
        return "Too much"

    if is_strength_plan(day):
        if score >= 85:
            return "Done"
        if score >= 55:
            return "Partial"
        if score >= 25:
            return "Short"
        return "Skipped"

    if score >= 90:
        return "Perfect"
    if score >= 75:
        return "Solid"
    if score >= 60:
        return "Close"
    if score >= 40:
        return "Off plan"
    return "Needs work"


def score_and_label(
    ride: RideLike, day: dict | None
) -> tuple[int | None, str | None]:
    """The persisted pair: compliance score and the badge text derived from it."""
    score = compute_match_score(ride, day)
    return score, match_score_label(score, day)


def grading_basis(day: dict | Any | None) -> str:
    """What the score for ``day`` was actually measured from, in a few words.

    The coach must be able to say *why* the badge reads the way it does without
    reverse-engineering it from the numbers — that guesswork is what produced
    the fabricated explanations this module exists to prevent. Kept terse: this
    rides along on every activity line in every prompt.
    """
    if not isinstance(day, dict):
        return "plan adherence"
    if is_recovery_plan(day):
        return "how easy it was ridden vs a planned recovery ride"
    if is_load_graded_plan(day):
        return "training load taken on a day with no session planned"
    if is_strength_plan(day):
        return "how much of the planned session was completed"
    if is_interval_workout_plan(day):
        return "interval power, plus duration"
    if _target_power(day) is not None:
        return "duration and power vs plan"
    return "duration vs plan"


def describe_badge(
    score: int | None, label: str | None, day: dict | Any | None
) -> str | None:
    """The badge and its basis, as one compact phrase for a prompt line."""
    if label is None:
        return None
    if score is None:
        return f'"{label}"'
    return f'"{label}" (score {score}/100 on {grading_basis(day)})'
