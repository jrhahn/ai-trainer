"""Activity identity helpers shared by import and dashboard persistence code."""

from __future__ import annotations

import re
from datetime import datetime, timezone


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

    name = normalize_activity_text(activity_name)
    other_name = normalize_activity_text(other_activity_name)
    if not name or name != other_name:
        return False

    duration_min = rounded_duration_minutes(duration_seconds)
    other_duration_min = rounded_duration_minutes(other_duration_seconds)
    if duration_min is None or other_duration_min is None:
        return False
    if abs(duration_min - other_duration_min) > 15:
        return False

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
