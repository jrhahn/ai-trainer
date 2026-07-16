"""Shared plan constraint enforcement — importable from both routers and services."""

from __future__ import annotations


def day_violates_constraint(day: dict, constraints: list[dict]) -> bool:
    date_value = day.get("date")
    if not date_value:
        return False
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "").lower()
    duration = day.get("durationMinutes") or day.get("duration_minutes") or 0
    is_training = workout_type not in {"", "rest"} or int(duration or 0) > 0
    if not is_training:
        return False
    for constraint in constraints:
        if constraint.get("constraintType") != "no_training":
            continue
        if constraint.get("constraintDate") == date_value:
            return True
    return False


def _required_workout_for_day(day: dict, constraints: list[dict]) -> dict | None:
    """Return the required-workout spec pinned to ``day``'s date, if any."""
    date_value = day.get("date")
    if not date_value:
        return None
    for constraint in constraints:
        if constraint.get("constraintType") != "required_workout":
            continue
        if constraint.get("constraintDate") == date_value:
            return constraint.get("requiredWorkout") or {}
    return None


def day_satisfies_required_workout(day: dict, spec: dict) -> bool:
    """Whether ``day`` already meets the required workout type and duration floor."""
    required_type = str(spec.get("workoutType") or "").lower()
    if required_type:
        day_type = str(day.get("workoutType") or day.get("workout_type") or "").lower()
        if day_type != required_type:
            return False
    min_minutes = spec.get("minDurationMinutes")
    if min_minutes:
        duration = day.get("durationMinutes") or day.get("duration_minutes") or 0
        if int(duration or 0) < int(min_minutes):
            return False
    return True


def _coerce_day_to_required(day: dict, spec: dict) -> dict:
    """Lift ``day`` up to the required workout, preserving the optimizer's extras.

    Enforcement is "ensure minimum": the day's type is set to the required type
    and its duration is raised to the required floor, but any richer detail the
    planner added (description, intervals, power) is left intact when compatible.
    """
    required_type = spec.get("workoutType")
    min_minutes = spec.get("minDurationMinutes")
    new_day = {**day}
    was_rest_or_empty = str(day.get("workoutType") or "").lower() in {"", "rest"}
    if required_type:
        new_day["workoutType"] = required_type
    if min_minutes:
        current = day.get("durationMinutes") or day.get("duration_minutes") or 0
        new_day["durationMinutes"] = max(int(current or 0), int(min_minutes))
    if spec.get("targetPower") is not None:
        new_day["targetPower"] = spec.get("targetPower")
    if was_rest_or_empty:
        label = required_type or "endurance"
        new_day["title"] = f"Required {label} session"
        new_day["description"] = (
            "Protected required session from an athlete availability constraint."
        )
        new_day["intervals"] = None
        new_day["workoutPurpose"] = (
            "Protects a required-session constraint from being dropped by training optimization."
        )
    return new_day


def sanitize_plan_for_constraints(plan: list[dict], constraints: list[dict]) -> list[dict]:
    if not constraints:
        return plan
    result: list[dict] = []
    for day in plan:
        if day_violates_constraint(day, constraints):
            result.append(
                {
                    **day,
                    "workoutType": "rest",
                    "title": "Unavailable",
                    "description": "No training scheduled because of an athlete availability constraint.",
                    "durationMinutes": 0,
                    "targetPower": None,
                    "targetHeartRate": None,
                    "intervals": None,
                    "workoutPurpose": "Protects a hard availability constraint from being overwritten by training optimization.",
                    "keyFocusPoints": [
                        "Keep the day free from training",
                        "Move any missed stimulus to an available day",
                        "Use the time for recovery or life commitments",
                    ],
                }
            )
            continue
        # Positive constraints: ensure a pinned required session is present and
        # meets its minimum, but only coerce when the planned day falls short.
        spec = _required_workout_for_day(day, constraints)
        if spec and not day_satisfies_required_workout(day, spec):
            result.append(_coerce_day_to_required(day, spec))
        else:
            result.append(day)
    return result


def filter_plan_updates_for_constraints(
    updates: list[dict], constraints: list[dict]
) -> list[dict]:
    if not constraints:
        return updates
    return [u for u in updates if not day_violates_constraint(u, constraints)]


def _constraint_for_date(
    constraints: list[dict], date_value: str, constraint_type: str
) -> dict | None:
    for constraint in constraints:
        if (
            constraint.get("constraintType") == constraint_type
            and constraint.get("constraintDate") == date_value
        ):
            return constraint
    return None


def describe_constraint_overrides(
    updates: list[dict], constraints: list[dict]
) -> list[dict]:
    """Describe requested day-updates that hard constraints would override.

    Constraint enforcement is deterministic and non-negotiable (see
    ``sanitize_plan_for_constraints``), so a coach-requested update to a
    constrained day never lands as asked. Callers use this to tell the athlete
    the truth instead of falsely confirming the change (#414).

    Returns one record per overridden update, in input order and deduplicated by
    date. Each record carries ``date``, ``weekday`` (may be ``None``),
    ``constraintType`` and — for required sessions — ``requiredType``:

    - ``no_training``: the update schedules training on an unavailable day, which
      the pipeline drops.
    - ``required_workout``: the update does not meet a pinned required session,
      which the pipeline coerces the day back to.

    Updates that comply with every active constraint are not reported.
    """
    if not constraints:
        return []
    seen: set[str] = set()
    overrides: list[dict] = []
    for day in updates:
        date_value = day.get("date")
        if not date_value or date_value in seen:
            continue
        if day_violates_constraint(day, constraints):
            constraint = _constraint_for_date(constraints, date_value, "no_training")
            seen.add(date_value)
            overrides.append(
                {
                    "date": date_value,
                    "weekday": (constraint or {}).get("weekday"),
                    "constraintType": "no_training",
                    "requiredType": None,
                }
            )
            continue
        spec = _required_workout_for_day(day, constraints)
        if spec and not day_satisfies_required_workout(day, spec):
            constraint = _constraint_for_date(
                constraints, date_value, "required_workout"
            )
            seen.add(date_value)
            overrides.append(
                {
                    "date": date_value,
                    "weekday": (constraint or {}).get("weekday"),
                    "constraintType": "required_workout",
                    "requiredType": spec.get("workoutType"),
                }
            )
    return overrides
