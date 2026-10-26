"""What one plan day is, as the planner and the pure rules read it (ai-trainer-ops#304).

plan_priors and day_preference both need a day's kind and its minutes, and
day_preference sits below schemas, which plan_priors imports. So they live
here, below both.
"""

from __future__ import annotations

# The workout types a plan treats as intensity.
INTENSITY_TYPES = frozenset(
    {"intervals", "threshold", "vo2max", "tempo", "sweet_spot", "sweetspot", "race"}
)


def workout_type(day: dict) -> str:
    return str(day.get("workoutType") or day.get("workout_type") or "").strip().lower()


def planned_minutes(day: dict) -> int:
    """Minutes of training the day holds; 0 for rest."""
    if workout_type(day) in ("", "rest", "off"):
        return 0
    raw = day.get("durationMinutes", day.get("duration_minutes"))
    try:
        return max(int(float(raw or 0)), 0)
    except (TypeError, ValueError):
        return 0
