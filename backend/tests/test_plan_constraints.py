"""Unit tests for services/plan_constraints.py."""

from __future__ import annotations

import pytest

from services.plan_constraints import (
    day_violates_constraint,
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
