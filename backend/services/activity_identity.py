"""Activity identity helpers shared by import and dashboard persistence code."""

from __future__ import annotations

import re
from datetime import datetime, timezone

CONTAINED_DUPLICATE_MIN_OVERLAP_RATIO = 0.8
CONTAINED_DUPLICATE_TIME_TOLERANCE_SECONDS = 10 * 60

# How far apart two recordings may sit and still be read as one interrupted
# session. Generous enough for a café stop or a battery swap, and far below the
# ~9 h between the two halves of a commute — which is the pair this number
# exists to keep apart (#543).
SPLIT_SESSION_MAX_GAP_SECONDS = 90 * 60


def normalize_activity_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def activity_family(sport_type: str | None) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", normalize_activity_text(sport_type))
    if "ride" in normalized or "cycling" in normalized or "bike" in normalized:
        return "cycling"
    if "weight" in normalized or "strength" in normalized:
        return "strength"
    if "run" in normalized:
        return "running"
    return normalized or "activity"


# Families that the power-based ride classifier is entitled to answer for.
# ``activity_family`` returns ``"activity"`` when the sport type is missing or
# unreadable — that is a genuine unknown, not a statement that it was not a ride.
CYCLING_FAMILY = "cycling"
UNREADABLE_FAMILY = "activity"


def non_cycling_classification(sport_type: str | None) -> tuple[str, str, str] | None:
    """Return ``(ride_purpose, confidence, reason)`` for a non-cycling activity.

    ``None`` when the activity is a bike ride, or when the sport type is missing
    or unreadable — those still belong to the power-based classifier, which is
    entitled to answer ``unknown``.

    The confidence is ``high`` because nothing here is inferred: the provider
    stated the sport. What a gym session leaves unknown is its intensity, not
    its identity — and calling that ``unknown`` conflates "the data was not good
    enough to work this out" with "this was never a bike ride". Only the first
    is a question worth putting to the athlete; the second made the coach ask a
    58-minute strength session what its intervals were (#578).
    """
    family = activity_family(sport_type)
    if family in (CYCLING_FAMILY, UNREADABLE_FAMILY):
        return None
    label = normalize_activity_text(sport_type) or family
    return (
        family,
        "high",
        f"Recorded as {label} — a {family} session, not a power-based bike ride, "
        "so no ride classification applies.",
    )


def rounded_duration_minutes(seconds: int | float | None) -> int | None:
    if seconds is None:
        return None
    try:
        duration = float(seconds)
    except (TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    return int((duration / 60) + 0.5)


def activity_identity_tokens(activity_name: str | None) -> set[str]:
    name = re.sub(r"[^a-z0-9\s]", " ", normalize_activity_text(activity_name))
    generic = {
        "activity",
        "bicycling",
        "bike",
        "biking",
        "cycling",
        "fahrt",
        "mountain",
        "mtb",
        "ride",
        "road",
    }
    return {
        token
        for token in name.split()
        if token and token not in generic and not token.isdigit()
    }


def activity_names_compatible(name: str | None, other_name: str | None) -> bool:
    normalized = normalize_activity_text(name)
    other_normalized = normalize_activity_text(other_name)
    if not normalized or not other_normalized:
        return False
    if normalized == other_normalized:
        return True

    tokens = activity_identity_tokens(normalized)
    other_tokens = activity_identity_tokens(other_normalized)
    return bool(tokens and other_tokens and tokens == other_tokens)


def normalized_start_minute(value: str | None) -> str:
    if not value:
        return ""
    return value[:16]


def _parse_activity_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalized_activity_datetime(value: str | None) -> datetime | None:
    parsed = _parse_activity_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc)
    return parsed.replace(tzinfo=None)


def _activity_interval(
    start_value: str | None,
    duration_seconds: int | float | None,
) -> tuple[datetime, float] | None:
    start = _normalized_activity_datetime(start_value)
    if start is None:
        return None
    try:
        duration = float(duration_seconds or 0)
    except (TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    return start, start.timestamp() + duration


def _activities_are_temporally_contained(
    *,
    activity_start_datetime: str | None,
    duration_seconds: int | float | None,
    other_activity_start_datetime: str | None,
    other_duration_seconds: int | float | None,
) -> bool:
    interval = _activity_interval(activity_start_datetime, duration_seconds)
    other_interval = _activity_interval(
        other_activity_start_datetime,
        other_duration_seconds,
    )
    if interval is None or other_interval is None:
        return False

    start, end = interval
    other_start, other_end = other_interval
    start_seconds = start.timestamp()
    other_start_seconds = other_start.timestamp()
    duration = end - start_seconds
    other_duration = other_end - other_start_seconds
    if duration <= 0 or other_duration <= 0:
        return False

    shorter = min(duration, other_duration)
    if abs(duration - other_duration) <= 15 * 60:
        return False

    overlap = max(0, min(end, other_end) - max(start_seconds, other_start_seconds))
    if overlap / shorter < CONTAINED_DUPLICATE_MIN_OVERLAP_RATIO:
        return False

    if duration <= other_duration:
        latest_allowed_start = (
            other_start_seconds - CONTAINED_DUPLICATE_TIME_TOLERANCE_SECONDS
        )
        latest_allowed_end = other_end + CONTAINED_DUPLICATE_TIME_TOLERANCE_SECONDS
        return (
            start_seconds >= latest_allowed_start
            and end <= latest_allowed_end
        )
    latest_allowed_start = start_seconds - CONTAINED_DUPLICATE_TIME_TOLERANCE_SECONDS
    latest_allowed_end = end + CONTAINED_DUPLICATE_TIME_TOLERANCE_SECONDS
    return (
        other_start_seconds >= latest_allowed_start
        and other_end <= latest_allowed_end
    )


def are_near_duplicate_activities(
    *,
    activity_date: str,
    sport_type: str | None,
    activity_name: str | None,
    activity_start_datetime: str | None,
    duration_seconds: int | float | None,
    other_activity_date: str,
    other_sport_type: str | None,
    other_activity_name: str | None,
    other_activity_start_datetime: str | None,
    other_duration_seconds: int | float | None,
) -> bool:
    if activity_date != other_activity_date:
        return False
    if activity_family(sport_type) != activity_family(other_sport_type):
        return False

    if not activity_names_compatible(activity_name, other_activity_name):
        return False

    duration_min = rounded_duration_minutes(duration_seconds)
    other_duration_min = rounded_duration_minutes(other_duration_seconds)
    if duration_min is None or other_duration_min is None:
        return False
    if abs(duration_min - other_duration_min) > 15:
        return _activities_are_temporally_contained(
            activity_start_datetime=activity_start_datetime,
            duration_seconds=duration_seconds,
            other_activity_start_datetime=other_activity_start_datetime,
            other_duration_seconds=other_duration_seconds,
        )

    start = _parse_activity_datetime(activity_start_datetime)
    other_start = _parse_activity_datetime(other_activity_start_datetime)
    if start is None or other_start is None:
        return True
    if start.tzinfo is not None and other_start.tzinfo is not None:
        start = start.astimezone(timezone.utc)
        other_start = other_start.astimezone(timezone.utc)
    else:
        start = start.replace(tzinfo=None)
        other_start = other_start.replace(tzinfo=None)

    return abs((start - other_start).total_seconds()) <= 30 * 60


def activities_form_one_session(
    activities: list[tuple[str | None, str | None, int | float | None]],
    *,
    max_gap_seconds: int = SPLIT_SESSION_MAX_GAP_SECONDS,
) -> bool:
    """True when these recordings look like one session that got split.

    Each entry is ``(sport_type, activity_start_datetime, duration_seconds)``.

    Distinct from :func:`are_near_duplicate_activities`, which asks whether two
    files describe *the same* ride. This asks whether several files describe
    consecutive *parts* of one — a café stop, a battery swap, an accidental
    stop and restart. They must therefore not overlap much and must be adjacent
    in time, where duplicates overlap almost entirely.

    Callers use it to decide whether adding durations up means anything: two
    halves of one ride sum to the session that was planned, a morning commute
    and an evening ride do not (#543).

    A missing or unparseable start, or a non-positive duration, returns False.
    Adjacency is a claim about a timeline; without one it cannot be made, and
    the caller should fall back to treating the activities separately.
    """
    if len(activities) < 2:
        return False

    families = {activity_family(sport_type) for sport_type, _, _ in activities}
    if len(families) != 1:
        return False

    intervals: list[tuple[float, float]] = []
    for _sport_type, start_value, duration_seconds in activities:
        interval = _activity_interval(start_value, duration_seconds)
        if interval is None:
            return False
        start, end = interval
        intervals.append((start.timestamp(), end))

    intervals.sort()
    for (_previous_start, previous_end), (next_start, _next_end) in zip(
        intervals, intervals[1:]
    ):
        # A negative gap is an overlap, which is a duplicate-detection concern
        # and not this function's business; clamping keeps it from reading as
        # "very adjacent" here.
        gap = max(0.0, next_start - previous_end)
        if gap > max_gap_seconds:
            return False
    return True
