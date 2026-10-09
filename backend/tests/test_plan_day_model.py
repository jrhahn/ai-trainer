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


# ---------------------------------------------------------------------------
# One spelling per field (ai-trainer-ops#38)
# ---------------------------------------------------------------------------


def test_a_day_spelling_a_field_twice_keeps_only_the_camel_one():
    """``extra="allow"`` used to let the losing spelling through as an extra.

    A snake_case spelling of a *modelled* field is not unmodelled data — it is a
    second name for a field this model owns, and when both are present only one
    can win. Pydantic resolves that to the alias, so the other survived saying
    something the day no longer meant, and which one a consumer believed
    depended on the order it read them in. That is the defect #29 found in three
    separate modules, and the reason it kept being findable.
    """
    out = _dump(
        {
            "date": "2026-07-18",
            "workoutType": "rest",
            "workout_type": "endurance",
            "durationMinutes": 0,
            "duration_minutes": 90,
        }
    )

    assert out["workoutType"] == "rest"
    assert out["durationMinutes"] == 0
    assert "workout_type" not in out
    assert "duration_minutes" not in out


def test_camel_wins_because_that_is_what_already_won():
    """Keeping the alias removes a stale key and changes no value.

    If this ever flips, every stored plan written through a day that carried both
    spellings changes meaning with no diff to show for it.
    """
    out = _dump({"date": "2026-07-18", "workoutType": "intervals", "workout_type": "rest"})
    assert out["workoutType"] == "intervals"


def test_a_single_snake_case_spelling_is_still_read():
    """Leniency on input is the other half of the promise and must not regress."""
    out = _dump({"date": "2026-07-18", "workout_type": "endurance", "duration_minutes": 75})
    assert (out["workoutType"], out["durationMinutes"]) == ("endurance", 75)
    assert "workout_type" not in out


def test_an_unmodelled_snake_case_key_survives_a_day_that_has_twins():
    """Only twins of modelled fields go. ``planner_note`` is stored data.

    The twin has to be present in the same day, or the drop never runs and the
    test proves nothing — found by mutating the filter to discard every
    snake_case key, which this test survived while there was no twin to trigger
    it.
    """
    out = _dump(
        {
            "date": "2026-07-18",
            "planner_note": "keep me",
            "workoutType": "rest",
            "workout_type": "endurance",
        }
    )

    assert out["planner_note"] == "keep me"
    assert "workout_type" not in out


def test_an_unmodelled_snake_case_key_survives_a_day_without_twins():
    out = _dump({"date": "2026-07-18", "planner_note": "keep me", "workout_type": "rest"})
    assert out["planner_note"] == "keep me"


def test_a_non_dict_passes_through_the_canonicaliser_unchanged():
    """One malformed day must never abort a whole plan write (#422 follow-up)."""
    from schemas import canonical_plan_day

    assert canonical_plan_day("not a day") == "not a day"


def test_the_canonicaliser_survives_a_day_it_cannot_validate():
    from schemas import canonical_plan_day

    bad = {"date": {"nested": "not a string"}}
    assert canonical_plan_day(bad) == bad
