"""Activity identity helpers shared by import and dashboard persistence code."""

from __future__ import annotations

import re


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


def near_duplicate_fingerprints(
    *,
    activity_date: str,
    sport_type: str | None,
    activity_name: str | None,
    activity_start_datetime: str | None,
    duration_seconds: int | float | None,
) -> set[str]:
    duration_min = rounded_duration_minutes(duration_seconds)
    if duration_min is None:
        return set()

    family = activity_family(sport_type)
    name = normalize_activity_text(activity_name)
    start_minute = normalized_start_minute(activity_start_datetime)
    keys: set[str] = set()
    if name:
        keys.add(f"name:{family}|{activity_date}|{name}|{duration_min}")
    if start_minute:
        keys.add(f"start:{family}|{activity_date}|{start_minute}|{duration_min}")
    return keys
