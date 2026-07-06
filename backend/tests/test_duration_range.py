"""Tests for planned-activity duration ranges (#368).

Cover the pure range helpers, the persistence normalization applied by the plan
pipeline, and the range-aware ride-match on-target label.
"""

from __future__ import annotations

import pytest

import crud
import models
from auth import hash_password
from services import plan_pipeline
from services.duration_range import (
    duration_on_target,
    duration_range,
    has_range,
    normalize_duration_fields,
    representative_minutes,
)
from services.ride_matching import _duration_mismatch_label, _plan_duration_range
from tests.conftest import TestSessionLocal


# --- Pure helpers ---------------------------------------------------------


def test_duration_range_single_value_reads_as_degenerate_window():
    assert duration_range({"durationMinutes": 90}) == (90, 90)


def test_duration_range_reads_explicit_window_ordered():
    day = {"durationMinutes": 165, "durationMaxMinutes": 150, "durationMinMinutes": 180}
    # Bounds are returned ordered regardless of how they were supplied.
    assert duration_range(day) == (150, 180)


def test_duration_range_fills_missing_bound_from_scalar():
    assert duration_range({"durationMinutes": 120, "durationMaxMinutes": 150}) == (120, 150)


def test_duration_range_rest_day_is_none():
    assert duration_range({"durationMinutes": 0}) == (None, None)
    assert duration_range({}) == (None, None)


def test_duration_range_non_dict_is_none():
    assert duration_range(None) == (None, None)
    assert duration_range("nope") == (None, None)  # type: ignore[arg-type]


def test_representative_minutes_none_without_duration():
    assert representative_minutes({"durationMinutes": 0}) is None


def test_representative_minutes_is_window_midpoint():
    assert representative_minutes({"durationMinMinutes": 150, "durationMaxMinutes": 180}) == 165


def test_has_range_only_true_for_genuine_window():
    assert has_range({"durationMinMinutes": 150, "durationMaxMinutes": 180}) is True
    assert has_range({"durationMinutes": 150}) is False


@pytest.mark.parametrize(
    "actual,expected",
    [(150, True), (165, True), (180, True), (149, False), (181, False), (None, None)],
)
def test_duration_on_target_uses_inclusive_window(actual, expected):
    day = {"durationMinMinutes": 150, "durationMaxMinutes": 180}
    assert duration_on_target(day, actual) is expected


def test_duration_on_target_none_without_plan_duration():
    assert duration_on_target({"durationMinutes": 0}, 120) is None


# --- Normalization --------------------------------------------------------


def test_normalize_leaves_single_value_day_untouched():
    day = {"date": "2026-07-10", "durationMinutes": 90, "title": "Ride"}
    assert normalize_duration_fields(day) == day


def test_normalize_non_dict_is_returned_unchanged():
    assert normalize_duration_fields(None) is None  # type: ignore[arg-type]


def test_normalize_orders_window_and_derives_midpoint_scalar():
    day = {"durationMinMinutes": 180, "durationMaxMinutes": 150}
    result = normalize_duration_fields(day)
    assert result["durationMinMinutes"] == 150
    assert result["durationMaxMinutes"] == 180
    assert result["durationMinutes"] == 165


def test_normalize_fills_bound_from_scalar_and_drops_snake_keys():
    day = {"duration_minutes": 120, "durationMaxMinutes": 150}
    result = normalize_duration_fields(day)
    assert result["durationMinMinutes"] == 120
    assert result["durationMaxMinutes"] == 150
    assert result["durationMinutes"] == 135
    assert "duration_minutes" not in result


# --- Ride-match on-target label ------------------------------------------


def test_mismatch_label_treats_in_window_actual_as_on_target():
    day = {"durationMinutes": 165, "durationMinMinutes": 150, "durationMaxMinutes": 180}
    # 200 min is >1.2x the 165 midpoint but still — with no window it would be
    # fine; with the window we assert an in-window actual is never a mismatch.
    assert _duration_mismatch_label(170, 165, _plan_duration_range(day)) is None


def test_mismatch_label_flags_extreme_deviation_outside_window():
    day = {"durationMinutes": 60}
    # 10 min against a 60 min plan is a ratio of 0.17 (< 0.3) → mismatch.
    assert _duration_mismatch_label(10, 60, _plan_duration_range(day)) == "Mismatch"


# --- Pipeline persistence -------------------------------------------------


def _range_day(date: str) -> dict:
    return {
        "date": date,
        "workoutType": "endurance",
        "title": f"Endurance {date}",
        "description": "Steady aerobic ride",
        "durationMinMinutes": 150,
        "durationMaxMinutes": 180,
    }


async def _create_user(email: str, plan: list[dict]) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, plan)
        await db.commit()
        return user.id


@pytest.mark.asyncio
async def test_commit_plan_normalizes_duration_window_on_write():
    """A proposed range day is persisted with ordered bounds and a midpoint scalar."""
    d = "2026-07-20"
    user_id = await _create_user("range-pipe@example.com", [])
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        merged = await plan_pipeline.commit_plan(
            db, user, [_range_day(d)], base_plan=[], source="user_edit"
        )
        await db.commit()

    day = next(x for x in merged if x["date"] == d)
    assert day["durationMinMinutes"] == 150
    assert day["durationMaxMinutes"] == 180
    assert day["durationMinutes"] == 165
