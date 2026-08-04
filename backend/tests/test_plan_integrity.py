"""Regression tests for plan integrity: completed-day protection and constraint enforcement.

Covers:
- _apply_plan_updates skips completed days
- review_matched_ride_and_adapt never modifies the matched ride's own day
- review_matched_ride_and_adapt filters plan_updates against availability constraints
- nightly maintenance enforces availability constraints
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import crud
import models
from auth import hash_password
from services import plan_maintenance
from services.ride_matching import (
    LABEL_MISMATCH,
    _apply_plan_updates,
    apply_ride_plan_matches,
    review_matched_ride_and_adapt,
)
from tests.conftest import TestSessionLocal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _day(date: str, workout_type: str = "intervals", completed: bool = False) -> dict:
    return {
        "date": date,
        "workoutType": workout_type,
        "title": f"Workout {date}",
        "description": "Training session",
        "durationMinutes": 60,
        "completed": completed,
    }


def _constraint_row(date: str, weekday: str = "thursday") -> models.AthleteAvailabilityConstraint:
    row = models.AthleteAvailabilityConstraint()
    row.constraint_type = "no_training"
    row.constraint_date = date
    row.weekday = weekday
    row.reason = "No time"
    row.source = "test"
    row.active = True
    row.expires_on = date
    return row


async def _create_user_with_plan(
    *,
    email: str,
    plan: list[dict],
    constraints: list[models.AthleteAvailabilityConstraint] | None = None,
) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Test Rider",
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
        if constraints:
            for c in constraints:
                c.user_id = user.id
                db.add(c)
        await db.commit()
        return user.id


async def _get_plan(user_id: str) -> list[dict]:
    async with TestSessionLocal() as db:
        row = await crud.get_training_plan(db, user_id)
        assert row is not None
        return row.plan


# ---------------------------------------------------------------------------
# _apply_plan_updates: completed-day protection
# ---------------------------------------------------------------------------


def test_apply_plan_updates_skips_completed_day():
    plan = [
        _day("2026-06-24", "intervals", completed=True),
        _day("2026-06-25", "endurance", completed=False),
    ]
    updates = [
        {"date": "2026-06-24", "workoutType": "rest", "durationMinutes": 0},
        {"date": "2026-06-25", "workoutType": "recovery", "durationMinutes": 30},
    ]
    result = _apply_plan_updates(plan, updates)
    assert result is not None
    # Completed day must be unchanged
    assert result[0]["workoutType"] == "intervals"
    # Non-completed day should be updated
    assert result[1]["workoutType"] == "recovery"


def test_apply_plan_updates_returns_none_when_no_updates():
    plan = [_day("2026-06-24")]
    assert _apply_plan_updates(plan, None) is None
    assert _apply_plan_updates(plan, []) is None


def test_apply_plan_updates_applies_to_incomplete_days():
    plan = [_day("2026-06-25", "endurance", completed=False)]
    updates = [{"date": "2026-06-25", "workoutType": "tempo", "durationMinutes": 45}]
    result = _apply_plan_updates(plan, updates)
    assert result is not None
    assert result[0]["workoutType"] == "tempo"


# ---------------------------------------------------------------------------
# review_matched_ride_and_adapt: matched-date filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_matched_ride_does_not_modify_matched_day(monkeypatch):
    """plan_updates that target the matched ride's own date must be discarded."""
    user_id = await _create_user_with_plan(
        email="matched-day@example.com",
        plan=[_day("2026-06-24", "intervals"), _day("2026-06-25", "endurance")],
    )

    ride = MagicMock(spec=models.RideMetric)
    ride.matched_plan_date = "2026-06-24"
    # Every real RideMetric has one; the review now reads it to find the other
    # recordings of the same session (#545).
    ride.activity_date = "2026-06-24"
    ride.matched_plan_snapshot = _day("2026-06-24", "intervals")
    ride.ctl_after = None
    ride.atl_after = None
    ride.tsb_after = None
    ride.strava_activity_id = 42
    ride.duration_seconds = 3600
    ride.avg_power_w = None
    ride.user_note = None
    ride.coach_note = None

    # AI suggests changing BOTH today (matched day) and tomorrow
    recommend_mock = AsyncMock(
        return_value={
            "response": "Take it easy.",
            "next_session_recommendation": "Rest",
            "recommendation_type": "recovery",
            "plan_updates": [
                {"date": "2026-06-24", "workoutType": "rest", "durationMinutes": 0},
                {"date": "2026-06-25", "workoutType": "recovery", "durationMinutes": 30},
            ],
        }
    )
    monkeypatch.setattr("services.ride_matching.ai_service.recommend_next_session", recommend_mock)
    monkeypatch.setattr(
        "services.ride_matching.ai_service.rate_completed_workout",
        AsyncMock(return_value={"feedback": "Good ride."}),
    )

    plan = [_day("2026-06-24", "intervals"), _day("2026-06-25", "endurance")]
    user_mock = MagicMock(spec=models.User)
    user_mock.id = user_id
    user_mock.rider_assessment = None
    user_mock.current_ftp = 250

    async with TestSessionLocal() as db:
        fresh_user = await crud.get_user_by_id(db, user_id)
        assert fresh_user is not None
        await review_matched_ride_and_adapt(
            db, fresh_user, ride, plan, provider="gemini"
        )
        await db.commit()

    saved = await _get_plan(user_id)
    by_date = {d["date"]: d for d in saved}
    # The matched day (2026-06-24) must stay as "intervals"
    assert by_date["2026-06-24"]["workoutType"] == "intervals"
    # Tomorrow (2026-06-25) may be updated to "recovery"
    assert by_date["2026-06-25"]["workoutType"] == "recovery"


# ---------------------------------------------------------------------------
# review_matched_ride_and_adapt: coach-pinned day survives reload (#339)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_matched_ride_preserves_coach_pinned_day(monkeypatch):
    """A day the coach edited must survive ride-review re-adaptation on reload (#339).

    This is the live reload-revert: on load the dashboard re-runs ride matching and
    ``review_matched_ride_and_adapt``, which proposes changing a day the user set via
    coach chat. That day is pinned (``source == "user"``), so the automated
    ``ride_review`` write must not overwrite it.
    """
    matched_day = "2026-06-24"
    pinned_day = "2026-06-26"
    # The coach set this day to intervals; the pipeline stamped source="user".
    pinned = {**_day(pinned_day, "intervals"), "title": "Coach VO2max", "source": "user"}
    user_id = await _create_user_with_plan(
        email="reload-revert@example.com",
        plan=[_day(matched_day, "intervals"), pinned],
    )

    ride = MagicMock(spec=models.RideMetric)
    ride.matched_plan_date = matched_day
    ride.activity_date = matched_day
    ride.matched_plan_snapshot = _day(matched_day, "intervals")
    ride.ctl_after = None
    ride.atl_after = None
    ride.tsb_after = None
    ride.strava_activity_id = 44
    ride.duration_seconds = 3600
    ride.avg_power_w = None
    ride.user_note = None
    ride.coach_note = None

    # On reload the AI review proposes reverting the coach-pinned day to recovery.
    recommend_mock = AsyncMock(
        return_value={
            "response": "Ease off.",
            "next_session_recommendation": "Recovery",
            "recommendation_type": "recovery",
            "plan_updates": [
                {"date": pinned_day, "workoutType": "recovery", "durationMinutes": 30},
            ],
        }
    )
    monkeypatch.setattr("services.ride_matching.ai_service.recommend_next_session", recommend_mock)
    monkeypatch.setattr(
        "services.ride_matching.ai_service.rate_completed_workout",
        AsyncMock(return_value={"feedback": ""}),
    )

    plan = [_day(matched_day, "intervals"), pinned]
    async with TestSessionLocal() as db:
        fresh_user = await crud.get_user_by_id(db, user_id)
        assert fresh_user is not None
        await review_matched_ride_and_adapt(
            db, fresh_user, ride, plan, provider="gemini"
        )
        await db.commit()

    by_date = {d["date"]: d for d in await _get_plan(user_id)}
    # The coach-pinned day is unchanged; the automated ride-review write was blocked.
    assert by_date[pinned_day]["workoutType"] == "intervals"
    assert by_date[pinned_day]["source"] == "user"


# ---------------------------------------------------------------------------
# review_matched_ride_and_adapt: constraint filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_matched_ride_respects_availability_constraints(monkeypatch):
    """plan_updates that violate constraints must be dropped."""
    # Use dates relative to today so the constraint is active (not expired):
    # review_matched_ride_and_adapt filters constraints against the real today
    # (it takes no injectable `now`), so hard-coded past dates would be skipped.
    matched_day = date.today().isoformat()
    constrained_day = (date.today() + timedelta(days=2)).isoformat()

    user_id = await _create_user_with_plan(
        email="constraint-ride@example.com",
        plan=[_day(matched_day, "intervals"), _day(constrained_day, "endurance")],
        constraints=[_constraint_row(constrained_day, "thursday")],
    )

    ride = MagicMock(spec=models.RideMetric)
    ride.matched_plan_date = matched_day
    ride.activity_date = matched_day
    ride.matched_plan_snapshot = _day(matched_day, "intervals")
    ride.ctl_after = None
    ride.atl_after = None
    ride.tsb_after = None
    ride.strava_activity_id = 43
    ride.duration_seconds = 3600
    ride.avg_power_w = None
    ride.user_note = None
    ride.coach_note = None

    # AI tries to schedule training on the constrained day
    recommend_mock = AsyncMock(
        return_value={
            "response": "Move intensity to the constrained day.",
            "next_session_recommendation": "Intervals on the constrained day",
            "recommendation_type": "move_intensity",
            "plan_updates": [
                {"date": constrained_day, "workoutType": "intervals", "durationMinutes": 60},
            ],
        }
    )
    monkeypatch.setattr("services.ride_matching.ai_service.recommend_next_session", recommend_mock)
    monkeypatch.setattr(
        "services.ride_matching.ai_service.rate_completed_workout",
        AsyncMock(return_value={"feedback": ""}),
    )

    plan = [_day(matched_day, "intervals"), _day(constrained_day, "endurance")]

    async with TestSessionLocal() as db:
        fresh_user = await crud.get_user_by_id(db, user_id)
        assert fresh_user is not None
        await review_matched_ride_and_adapt(
            db, fresh_user, ride, plan, provider="gemini"
        )
        await db.commit()

    saved = await _get_plan(user_id)
    by_date = {d["date"]: d for d in saved}
    # The constrained day must keep its original workout — the AI's update is blocked
    assert by_date[constrained_day]["workoutType"] == "endurance"


# ---------------------------------------------------------------------------
# plan_maintenance: availability constraint enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_respects_availability_constraints(monkeypatch):
    """Nightly maintenance must not schedule training on constrained days."""
    thursday = "2026-06-25"
    user_id = await _create_user_with_plan(
        email="maintenance-constraint@example.com",
        plan=[_day("2026-06-23"), _day(thursday, "intervals")],
        constraints=[_constraint_row(thursday, "thursday")],
    )

    # AI tries to reschedule and returns training on Thursday
    async def fake_adapt(plan, *args, **kwargs):
        return [
            {**plan[0], "date": "2026-06-24"},
            {**plan[1], "date": thursday, "workoutType": "intervals"},
        ]

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", fake_adapt)
    monkeypatch.setattr(
        plan_maintenance, "training_weather_context_for_user", AsyncMock(return_value="")
    )

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 24, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert result.updated == 1
    saved = await _get_plan(user_id)
    by_date = {d["date"]: d for d in saved}
    # Thursday constraint must have been enforced — day converted to rest
    assert by_date[thursday]["workoutType"] == "rest"
    assert by_date[thursday]["title"] == "Unavailable"


@pytest.mark.asyncio
async def test_maintenance_passes_constraints_in_profile(monkeypatch):
    """Availability constraints must be visible to the LLM via the profile."""
    thursday = "2026-06-25"
    user_id = await _create_user_with_plan(
        email="maintenance-profile@example.com",
        plan=[_day("2026-06-23")],
        constraints=[_constraint_row(thursday, "thursday")],
    )

    captured_profile: dict = {}

    async def fake_adapt(plan, feedback, profile, *args, **kwargs):
        captured_profile.update(profile)
        return plan

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", fake_adapt)
    monkeypatch.setattr(
        plan_maintenance, "training_weather_context_for_user", AsyncMock(return_value="")
    )

    await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 6, 24, 2, 5, tzinfo=timezone.utc),
        timezone_name="UTC",
    )

    assert "availabilityConstraints" in captured_profile
    dates = [c["constraintDate"] for c in captured_profile["availabilityConstraints"]]
    assert thursday in dates


# ---------------------------------------------------------------------------
# apply_ride_plan_matches: label stickiness ("Auto live, Feedback sticky")
# ---------------------------------------------------------------------------


async def _add_ride(
    user_id: str,
    *,
    strava_activity_id: int,
    activity_date: str,
    duration_seconds: int,
    label_override: str | None = None,
) -> None:
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=strava_activity_id,
            activity_date=activity_date,
            duration_seconds=duration_seconds,
        )
        if label_override is not None:
            await crud.update_ride_metric_notes(
                db, user_id, strava_activity_id, label_override=label_override
            )
        await db.commit()


async def _ride_label(user_id: str, strava_activity_id: int) -> str | None:
    async with TestSessionLocal() as db:
        rides = await crud.get_ride_metrics_by_activity_ids(
            db, user_id, [strava_activity_id]
        )
        assert rides
        return rides[0].label_override


async def _run_match(user_id: str, strava_activity_id: int) -> None:
    async with TestSessionLocal() as db:
        plan = await crud.get_training_plan(db, user_id)
        await apply_ride_plan_matches(db, user_id, plan.plan, [strava_activity_id])
        await db.commit()


@pytest.mark.asyncio
async def test_matching_preserves_subjective_feedback_label():
    """A rider-feedback label must survive re-matching (Feedback sticky)."""
    plan_date = "2026-06-20"
    user_id = await _create_user_with_plan(
        email="sticky-feedback@example.com",
        plan=[_day(plan_date, "endurance")],
    )
    # 10-min ride against a 60-min plan day -> auto would label it "Mismatch".
    await _add_ride(
        user_id,
        strava_activity_id=70001,
        activity_date=plan_date,
        duration_seconds=600,
        label_override="Solid",  # subjective rider feedback, not an auto label
    )

    await _run_match(user_id, 70001)

    assert await _ride_label(user_id, 70001) == "Solid"


@pytest.mark.asyncio
async def test_matching_keeps_auto_label_live():
    """Auto match labels reflect the current ride/plan and are never frozen."""
    plan_date = "2026-06-20"
    user_id = await _create_user_with_plan(
        email="auto-live@example.com",
        plan=[_day(plan_date, "endurance")],
    )
    # Grossly short ride -> auto label "Mismatch".
    await _add_ride(
        user_id,
        strava_activity_id=70002,
        activity_date=plan_date,
        duration_seconds=600,
    )
    await _run_match(user_id, 70002)
    assert await _ride_label(user_id, 70002) == LABEL_MISMATCH

    # Ride now fits the plan duration -> stale auto label must clear on re-match.
    async with TestSessionLocal() as db:
        await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=70002,
            activity_date=plan_date,
            duration_seconds=3600,
        )
        await db.commit()
    await _run_match(user_id, 70002)
    assert await _ride_label(user_id, 70002) is None
