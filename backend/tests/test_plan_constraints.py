"""Unit tests for services/plan_constraints.py."""

from __future__ import annotations

import pytest

from services.plan_constraints import (
    blocked_dates,
    day_violates_constraint,
    describe_constraint_overrides,
    filter_plan_updates_for_constraints,
    sanitize_plan_for_constraints,
)


def _constraint(date: str) -> dict:
    return {"constraintType": "no_training", "constraintDate": date}


def _day(date: str, workout_type: str = "intervals", duration: int = 60) -> dict:
    return {"date": date, "workoutType": workout_type, "durationMinutes": duration}


# ---------------------------------------------------------------------------
# day_violates_constraint
# ---------------------------------------------------------------------------


def test_training_day_violates_constraint():
    assert day_violates_constraint(_day("2026-06-26"), [_constraint("2026-06-26")])


def test_rest_day_does_not_violate():
    assert not day_violates_constraint(
        {"date": "2026-06-26", "workoutType": "rest", "durationMinutes": 0},
        [_constraint("2026-06-26")],
    )


def test_different_date_does_not_violate():
    assert not day_violates_constraint(_day("2026-06-27"), [_constraint("2026-06-26")])


def test_empty_constraints_never_violates():
    assert not day_violates_constraint(_day("2026-06-26"), [])


def test_day_without_date_does_not_violate():
    assert not day_violates_constraint({"workoutType": "intervals"}, [_constraint("2026-06-26")])


def test_zero_duration_rest_does_not_violate():
    assert not day_violates_constraint(
        {"date": "2026-06-26", "workoutType": "rest", "durationMinutes": 0},
        [_constraint("2026-06-26")],
    )


def test_non_no_training_constraint_type_is_ignored():
    c = {"constraintType": "other_type", "constraintDate": "2026-06-26"}
    assert not day_violates_constraint(_day("2026-06-26"), [c])


# ---------------------------------------------------------------------------
# sanitize_plan_for_constraints
# ---------------------------------------------------------------------------


def test_sanitize_converts_violating_day_to_rest():
    plan = [_day("2026-06-26"), _day("2026-06-27")]
    result = sanitize_plan_for_constraints(plan, [_constraint("2026-06-26")])

    assert result[0]["workoutType"] == "rest"
    assert result[0]["durationMinutes"] == 0
    assert result[0]["title"] == "Unavailable"
    assert result[0]["date"] == "2026-06-26"
    assert result[1]["workoutType"] == "intervals"


def test_sanitize_preserves_non_violating_days():
    plan = [_day("2026-06-25"), _day("2026-06-27")]
    result = sanitize_plan_for_constraints(plan, [_constraint("2026-06-26")])
    assert result == plan


def test_sanitize_empty_constraints_returns_plan_unchanged():
    plan = [_day("2026-06-26")]
    assert sanitize_plan_for_constraints(plan, []) is plan


def test_sanitize_preserves_date_field_on_rest_day():
    plan = [_day("2026-06-26")]
    result = sanitize_plan_for_constraints(plan, [_constraint("2026-06-26")])
    assert result[0]["date"] == "2026-06-26"


# ---------------------------------------------------------------------------
# filter_plan_updates_for_constraints
# ---------------------------------------------------------------------------


def test_filter_removes_violating_updates():
    updates = [_day("2026-06-26"), _day("2026-06-27")]
    result = filter_plan_updates_for_constraints(updates, [_constraint("2026-06-26")])
    assert len(result) == 1
    assert result[0]["date"] == "2026-06-27"


def test_filter_empty_constraints_returns_all_updates():
    updates = [_day("2026-06-26"), _day("2026-06-27")]
    assert filter_plan_updates_for_constraints(updates, []) == updates


def test_filter_all_violating_returns_empty():
    updates = [_day("2026-06-26")]
    assert filter_plan_updates_for_constraints(updates, [_constraint("2026-06-26")]) == []


# ---------------------------------------------------------------------------
# required_workout (positive) constraints
# ---------------------------------------------------------------------------


def _required(date: str, workout_type: str = "endurance", min_minutes: int = 120) -> dict:
    return {
        "constraintType": "required_workout",
        "constraintDate": date,
        "requiredWorkout": {
            "workoutType": workout_type,
            "minDurationMinutes": min_minutes,
        },
    }


def test_sanitize_coerces_rest_day_to_required_workout():
    plan = [{"date": "2026-07-04", "workoutType": "rest", "durationMinutes": 0}]
    result = sanitize_plan_for_constraints(plan, [_required("2026-07-04")])
    assert result[0]["workoutType"] == "endurance"
    assert result[0]["durationMinutes"] == 120


def test_sanitize_bumps_too_short_required_day():
    plan = [{"date": "2026-07-04", "workoutType": "endurance", "durationMinutes": 60}]
    result = sanitize_plan_for_constraints(plan, [_required("2026-07-04")])
    assert result[0]["workoutType"] == "endurance"
    assert result[0]["durationMinutes"] == 120


def test_sanitize_overrides_wrong_type_but_keeps_longer_duration():
    plan = [{"date": "2026-07-04", "workoutType": "intervals", "durationMinutes": 180}]
    result = sanitize_plan_for_constraints(plan, [_required("2026-07-04")])
    assert result[0]["workoutType"] == "endurance"
    assert result[0]["durationMinutes"] == 180  # already exceeds the floor


def test_sanitize_leaves_satisfying_required_day_untouched():
    day = {
        "date": "2026-07-04",
        "workoutType": "endurance",
        "durationMinutes": 150,
        "title": "Long ride",
    }
    result = sanitize_plan_for_constraints([day], [_required("2026-07-04")])
    assert result[0] == day


def test_no_training_takes_precedence_over_required_on_same_day():
    plan = [{"date": "2026-07-04", "workoutType": "endurance", "durationMinutes": 30}]
    constraints = [_constraint("2026-07-04"), _required("2026-07-04")]
    result = sanitize_plan_for_constraints(plan, constraints)
    assert result[0]["workoutType"] == "rest"


# ---------------------------------------------------------------------------
# describe_constraint_overrides (#414 coach honesty)
# ---------------------------------------------------------------------------


def _required_wd(date: str, weekday: str) -> dict:
    return {**_required(date), "weekday": weekday}


def test_override_reports_rest_request_on_required_day():
    # The #412 case: coach asked to rest a required-session day.
    updates = [{"date": "2026-07-17", "workoutType": "rest", "durationMinutes": 0}]
    overrides = describe_constraint_overrides(
        updates, [_required_wd("2026-07-17", "friday")]
    )
    assert len(overrides) == 1
    assert overrides[0]["constraintType"] == "required_workout"
    assert overrides[0]["weekday"] == "friday"
    assert overrides[0]["requiredType"] == "endurance"


def test_override_reports_training_on_unavailable_day():
    updates = [_day("2026-06-26")]
    overrides = describe_constraint_overrides(
        updates, [{**_constraint("2026-06-26"), "weekday": "friday"}]
    )
    assert len(overrides) == 1
    assert overrides[0]["constraintType"] == "no_training"


def test_override_ignores_compliant_updates():
    # A request that already satisfies the required session is not an override.
    updates = [{"date": "2026-07-17", "workoutType": "endurance", "durationMinutes": 150}]
    assert describe_constraint_overrides(updates, [_required("2026-07-17")]) == []
    # A request on an unconstrained day is fine.
    assert describe_constraint_overrides([_day("2026-07-20")], [_required("2026-07-17")]) == []


def test_override_empty_when_no_constraints():
    assert describe_constraint_overrides([_day("2026-07-17")], []) == []


def test_override_deduplicates_per_date():
    updates = [
        {"date": "2026-07-17", "workoutType": "rest", "durationMinutes": 0},
        {"date": "2026-07-17", "workoutType": "recovery", "durationMinutes": 30},
    ]
    overrides = describe_constraint_overrides(updates, [_required("2026-07-17")])
    assert len(overrides) == 1


# ---------------------------------------------------------------------------
# blocked_dates, and the stale-twin removal on write (ai-trainer-ops#29, #30)
# ---------------------------------------------------------------------------


def test_blocked_dates_collects_only_no_training_entries():
    constraints = [
        _constraint("2026-06-26"),
        {
            "constraintType": "required_workout",
            "constraintDate": "2026-06-27",
            "requiredWorkout": {"workoutType": "endurance"},
        },
    ]
    assert blocked_dates(constraints) == {"2026-06-26"}


def test_blocked_dates_is_empty_without_constraints():
    assert blocked_dates([]) == set()


def test_blocked_dates_ignores_entries_without_a_date():
    assert blocked_dates([{"constraintType": "no_training"}]) == set()


def test_blanking_removes_the_stale_snake_case_twin():
    """The gate's verdict must be the only thing the day says about itself.

    Leaving ``duration_minutes: 90`` next to the written ``durationMinutes: 0``
    is what let the module's own reader see 90 and call the blanked day a
    violation of the constraint it had just enforced.
    """
    plan = [{"date": "2026-06-26", "workout_type": "intervals", "duration_minutes": 90}]
    (out,) = sanitize_plan_for_constraints(plan, [_constraint("2026-06-26")])
    assert out["workoutType"] == "rest"
    assert out["durationMinutes"] == 0
    assert "workout_type" not in out
    assert "duration_minutes" not in out
    assert not day_violates_constraint(out, [_constraint("2026-06-26")])


def test_coercion_keeps_a_snake_case_session_intact():
    """``was_rest_or_empty`` read camelCase alone, so this day counted as empty.

    The day is short of the floor, so it *is* coerced and the duration goes up.
    What must not happen is the rest-day treatment: before the fix this day read
    as empty and had its title, description and intervals replaced, losing a real
    planned session to a generic "Required endurance session".
    """
    plan = [
        {
            "date": "2026-06-26",
            "workout_type": "endurance",
            "duration_minutes": 30,
            "title": "Long ride with the club",
            "description": "Bakery stop at the halfway point",
            "intervals": [{"minutes": 30}],
        }
    ]
    constraints = [
        {
            "constraintType": "required_workout",
            "constraintDate": "2026-06-26",
            "requiredWorkout": {"workoutType": "endurance", "minDurationMinutes": 60},
        }
    ]
    (out,) = sanitize_plan_for_constraints(plan, constraints)
    assert out["durationMinutes"] == 60  # raised to the floor
    assert "duration_minutes" not in out  # and the stale twin is gone
    assert out["title"] == "Long ride with the club"
    assert out["description"] == "Bakery stop at the halfway point"
    assert out["intervals"] == [{"minutes": 30}]


def test_coercion_still_labels_a_genuinely_empty_day():
    """The rest-day treatment is right when the day really is empty."""
    plan = [{"date": "2026-06-26", "workout_type": "rest", "duration_minutes": 0}]
    constraints = [
        {
            "constraintType": "required_workout",
            "constraintDate": "2026-06-26",
            "requiredWorkout": {"workoutType": "endurance", "minDurationMinutes": 60},
        }
    ]
    (out,) = sanitize_plan_for_constraints(plan, constraints)
    assert out["title"] == "Required endurance session"
    assert out["workoutType"] == "endurance"
    assert out["durationMinutes"] == 60
    assert "workout_type" not in out


def test_a_blocked_day_stays_blocked_across_repeated_passes():
    """Precedence has to be a property of the gate, not of its first application."""
    plan = [{"date": "2026-06-26", "workoutType": "endurance", "durationMinutes": 90}]
    constraints = [
        _constraint("2026-06-26"),
        {
            "constraintType": "required_workout",
            "constraintDate": "2026-06-26",
            "requiredWorkout": {"workoutType": "endurance", "minDurationMinutes": 60},
        },
    ]
    current = plan
    for _ in range(3):
        current = sanitize_plan_for_constraints(current, constraints)
        assert current[0]["workoutType"] == "rest"
        assert current[0]["durationMinutes"] == 0
