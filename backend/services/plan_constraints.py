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
        else:
            result.append(day)
    return result


def filter_plan_updates_for_constraints(
    updates: list[dict], constraints: list[dict]
) -> list[dict]:
    if not constraints:
        return updates
    return [u for u in updates if not day_violates_constraint(u, constraints)]
