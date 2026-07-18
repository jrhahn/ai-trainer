"""Tests for the canonical ``PlanDay`` model (schemas.py).

``PlanDay`` is the single place that guarantees a persisted training-plan day is
coherent: it reconciles the duration scalar against the min/max window, is
lenient on input (missing fields, snake_case, scalar-only / window-only
duration, a bare target-power number), strict and canonical on output, and
preserves unknown keys so typing never silently drops stored data.
"""

from __future__ import annotations

from schemas import PlanDay


def _dump(day: dict) -> dict:
    return PlanDay.model_validate(day).model_dump(by_alias=True, exclude_none=True)


def test_scalar_only_day_leaves_duration_untouched():
    out = _dump({"date": "2026-07-18", "durationMinutes": 120})
    assert out["durationMinutes"] == 120
    assert "durationMinMinutes" not in out
    assert "durationMaxMinutes" not in out


def test_window_reconciles_scalar_to_midpoint():
    out = _dump(
        {"date": "2026-07-18", "durationMinutes": 120,
         "durationMinMinutes": 45, "durationMaxMinutes": 60}
    )
    assert out["durationMinMinutes"] == 45
    assert out["durationMaxMinutes"] == 60
    assert out["durationMinutes"] == round((45 + 60) / 2)  # 52 — coherent


def test_window_only_derives_scalar():
    out = _dump(
        {"date": "2026-07-18", "durationMinMinutes": 180, "durationMaxMinutes": 240}
    )
    assert out["durationMinutes"] == round((180 + 240) / 2)  # 210


def test_unordered_window_is_ordered():
    out = _dump(
        {"date": "2026-07-18", "durationMinMinutes": 240, "durationMaxMinutes": 180}
    )
    assert out["durationMinMinutes"] == 180
    assert out["durationMaxMinutes"] == 240


def test_rest_day_stays_zero_without_window():
    out = _dump({"date": "2026-07-18", "workoutType": "rest", "durationMinutes": 0})
    assert out["durationMinutes"] == 0
    assert "durationMinMinutes" not in out


def test_snake_case_input_accepted():
    out = _dump({"date": "2026-07-18", "duration_minutes": 90, "workout_type": "endurance"})
    assert out["durationMinutes"] == 90
    assert out["workoutType"] == "endurance"


def test_unknown_extra_key_is_preserved():
    out = _dump({"date": "2026-07-18", "durationMinutes": 60, "someFutureField": "keep-me"})
    assert out["someFutureField"] == "keep-me"


def test_bare_target_power_number_coerced_to_range():
    out = _dump({"date": "2026-07-18", "durationMinutes": 60, "targetPower": 240})
    assert out["targetPower"] == {"low": 240, "high": 240}


def test_partial_target_power_range_filled():
    out = _dump({"date": "2026-07-18", "durationMinutes": 60, "targetPower": {"low": 200}})
    assert out["targetPower"] == {"low": 200, "high": 200}


def test_normalization_is_idempotent():
    raw = {"date": "2026-07-18", "durationMinutes": 999,
           "durationMinMinutes": 45, "durationMaxMinutes": 60, "targetPower": 240}
    once = _dump(raw)
    twice = _dump(once)
    assert once == twice
