"""Heart-rate and pace intervals are scored against those targets (ai-trainer-ops#133).

Since #117 a plan writes its intervals in heart rate or pace for an athlete
without a power meter and for runners. The compliance score read only watts, so
such a session was judged on the clock alone: a runner who held every pace
interval scored like one who jogged. The average heart rate a session carries
was computed for its load and then thrown away.
"""

from __future__ import annotations

from functools import partial

from types import SimpleNamespace

import pytest

import crud
import models
from services import analysis
from domain import plan_compliance
from services.prompts import compliance_badge_phrase
from tests.conftest import TestSessionLocal
from tests.factories import create_user_row, ROAD_RIDER


def _ride(**kwargs) -> SimpleNamespace:
    fields = {
        "duration_seconds": 3600,
        "normalized_power_w": None,
        "avg_power_w": None,
        "intensity_factor": None,
        "tss": None,
        "avg_heart_rate_bpm": None,
        "distance_km": None,
    }
    fields.update(kwargs)
    return SimpleNamespace(**fields)


HR_PLAN = {
    "date": "2026-10-20",
    "title": "Threshold intervals by heart rate",
    "workoutType": "intervals",
    "durationMinutes": 60,
    "intervals": [
        {"duration": 600, "heartRate": 165, "rest": 180},
        {"duration": 600, "heartRate": 168, "rest": 180},
    ],
}

PACE_PLAN = {
    "date": "2026-10-21",
    "title": "Tempo run intervals",
    "workoutType": "intervals",
    "durationMinutes": 50,
    "intervals": [
        {"duration": 480, "pace": "4:30/km", "rest": 120},
        {"duration": 480, "pace": "4:20/km", "rest": 120},
    ],
}


# --- The targets are read -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("4:30/km", 270), ("4:30", 270), (" 5:05 / km ", 305), ("12:00/km", 720)],
)
def test_a_pace_reads_as_seconds_per_kilometre(text, seconds):
    assert plan_compliance.parse_pace_seconds_per_km(text) == seconds


@pytest.mark.parametrize("text", ["fast", "4:75/km", "4:30/mi", "", None, 270])
def test_anything_else_is_no_pace(text):
    assert plan_compliance.parse_pace_seconds_per_km(text) is None


def test_the_kind_of_target_follows_the_intervals():
    assert plan_compliance.interval_target_kind(HR_PLAN) == "heart rate"
    assert plan_compliance.interval_target_kind(PACE_PLAN) == "pace"
    powered = {**HR_PLAN, "intervals": [{"duration": 600, "power": 280}]}
    assert plan_compliance.interval_target_kind(powered) == "power"
    assert plan_compliance.interval_target_kind({**HR_PLAN, "intervals": []}) is None


# --- Heart rate ------------------------------------------------------------


def test_a_session_held_at_the_heart_rate_target_scores_high():
    score = plan_compliance.compute_match_score(_ride(avg_heart_rate_bpm=166), HR_PLAN)
    assert score is not None and score >= 90


def test_an_easy_session_on_a_heart_rate_day_scores_low():
    held = plan_compliance.compute_match_score(_ride(avg_heart_rate_bpm=158), HR_PLAN)
    easy = plan_compliance.compute_match_score(_ride(avg_heart_rate_bpm=120), HR_PLAN)
    assert held is not None and easy is not None
    # The average sits below the target because of warm-up and recoveries:
    # within 90 % of it the session counts as done.
    assert held >= 85
    assert easy < 60


def test_heart_rate_intervals_are_not_scored_on_watts_the_plan_did_not_ask_for():
    """A rider with a power meter on a heart-rate day used to get the 70-point
    'no interval target' guess for power, whatever their heart rate did."""
    easy_with_power = plan_compliance.compute_match_score(
        _ride(avg_heart_rate_bpm=120, normalized_power_w=150, avg_power_w=150, tss=40),
        HR_PLAN,
    )
    assert easy_with_power is not None and easy_with_power < 60


def test_without_a_heart_rate_the_clock_is_all_there_is():
    score = plan_compliance.compute_match_score(_ride(), HR_PLAN)
    assert score == 100  # 60 of 60 planned minutes, and nothing else to judge
    basis = plan_compliance.grading_basis(HR_PLAN, _ride())
    assert basis.startswith("duration only")
    assert "no heart rate to compare" in basis


def test_with_a_heart_rate_the_basis_names_it():
    assert (
        plan_compliance.grading_basis(HR_PLAN, _ride(avg_heart_rate_bpm=160))
        == "interval heart rate, plus duration"
    )


# --- Pace -------------------------------------------------------------------


def test_a_run_that_held_its_pace_beats_one_that_jogged():
    # 50 min at an average 4:50/km over the whole session, recoveries included.
    held = plan_compliance.compute_match_score(
        _ride(duration_seconds=3000, distance_km=3000 / 290), PACE_PLAN
    )
    # Same time, 6:30/km: a jog.
    jogged = plan_compliance.compute_match_score(
        _ride(duration_seconds=3000, distance_km=3000 / 390), PACE_PLAN
    )
    assert held is not None and jogged is not None
    assert held >= 85
    assert jogged < 60
    assert held - jogged >= 30


def test_a_run_without_a_distance_has_no_pace_to_compare():
    assert (
        plan_compliance.score_structured_interval_effort(
            _ride(duration_seconds=3000), PACE_PLAN
        )
        is None
    )
    assert "no pace to compare" in plan_compliance.grading_basis(PACE_PLAN, _ride())


def test_the_coach_line_explains_the_badge_from_what_was_measured():
    metric = _ride(
        avg_heart_rate_bpm=None,
        match_score=100,
        match_label="Perfect",
        matched_plan_snapshot=HR_PLAN,
        label_override=None,
    )
    phrase = compliance_badge_phrase(metric)
    assert phrase is not None and "no heart rate to compare" in phrase


# --- The heart rate is kept --------------------------------------------------


def test_the_metrics_chain_keeps_the_sessions_average_heart_rate():
    run = {
        "strava_activity_id": 77,
        "activity_date": "2026-10-20",
        "sport_type": "Run",
        "duration_seconds": 3000,
        "distance_km": 10.0,
        "streams": {},
        "_summary_avg_hr_bpm": 158.4,
    }
    metrics = analysis.build_ride_metrics_chain(
        [run], ftp=250.0, max_heart_rate=190, resting_heart_rate=50
    )
    assert metrics[0]["avg_heart_rate_bpm"] == 158


def test_without_any_heart_rate_none_is_kept():
    run = {
        "strava_activity_id": 78,
        "activity_date": "2026-10-20",
        "sport_type": "Run",
        "duration_seconds": 3000,
        "streams": {},
    }
    metrics = analysis.build_ride_metrics_chain([run], ftp=250.0)
    assert metrics[0]["avg_heart_rate_bpm"] is None


# The users row this module tests with; tests/factories.py writes it (#289).
_create_user = partial(
    create_user_row, is_onboarded=True, **{**ROAD_RIDER, "current_ftp": 320}
)


@pytest.mark.asyncio
async def test_an_imported_session_is_stored_and_matched_on_its_heart_rate():
    user_id = await _create_user("hr-intervals@example.com")
    async with TestSessionLocal() as db:
        ride = await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=9133,
            activity_date="2026-10-20",
            sport_type="Ride",
            duration_seconds=3600,
            avg_heart_rate_bpm=166,
        )
        await crud.update_ride_match(
            db,
            ride,
            status="auto_matched",
            matched_plan_date="2026-10-20",
            matched_plan_snapshot=HR_PLAN,
        )
        await db.commit()
        stored = await db.get(models.RideMetric, ride.id)
        assert stored.avg_heart_rate_bpm == 166
        assert stored.match_score is not None and stored.match_score >= 90
