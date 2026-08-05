"""Tests for the per-activity compliance badge (#551).

The badge used to be computed only in the browser, so the coach could not see
the label it was being asked to explain. Asked "why is my last ride tagged as
'Needs work'?", it read ``plan match:auto_matched`` off the prompt as a verdict
on execution, called the ride "a successful match", and then explained the only
``Needs work`` in its context — a ride from five weeks earlier.

These cover the two halves of the fix: the scoring itself (including the
recovery ride that produced the wrong badge in the first place), and the badge
reaching the coach prompt as the athlete sees it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import crud
import models
from auth import hash_password
from services import plan_compliance
from services.prompts import ride_metrics_context_section
from tests.conftest import TestSessionLocal


def _ride(**kwargs) -> SimpleNamespace:
    fields = {
        "duration_seconds": None,
        "normalized_power_w": None,
        "avg_power_w": None,
        "intensity_factor": None,
        "tss": None,
    }
    fields.update(kwargs)
    return SimpleNamespace(**fields)


# The plan day and ride that exposed the bug: a recovery spin ridden exactly as
# prescribed (160 W against a stated 130-175 W band, IF 0.50) that ran 26 min
# past the planned 50.
RECOVERY_PLAN = {
    "date": "2026-08-05",
    "title": "Gentle Active Recovery Spin",
    "workoutType": "recovery",
    "durationMinutes": 50,
    "description": "Turn the pedals easy with power strictly between 130 and 175.",
}
RECOVERY_RIDE = _ride(
    duration_seconds=4539,
    normalized_power_w=160,
    avg_power_w=160,
    intensity_factor=0.5,
    tss=31.5,
)


# --- Scoring --------------------------------------------------------------


def test_recovery_ride_that_ran_long_is_not_needs_work():
    """The regression: a well-executed recovery spin scored 0/100.

    ``workoutType: "recovery"`` with a duration failed the rest-day test, so the
    ride went down the generic cycling ladder. Plans state recovery watts in
    prose and leave ``targetPower`` unset, which left the clock as the only
    signal — and 76 minutes against a planned 50 collapses to zero.
    """
    score, label = plan_compliance.score_and_label(RECOVERY_RIDE, RECOVERY_PLAN)

    assert label == "Recovery"
    assert score == 85


def test_recovery_ride_on_plan_scores_full_marks():
    ride = _ride(duration_seconds=50 * 60, intensity_factor=0.52, tss=22)
    assert plan_compliance.score_and_label(ride, RECOVERY_PLAN) == (100, "OK")


def test_recovery_ride_ridden_hard_is_too_much():
    """Intensity, not duration, is what a recovery ride can fail on."""
    ride = _ride(duration_seconds=50 * 60, intensity_factor=0.85, tss=60)
    score, label = plan_compliance.score_and_label(ride, RECOVERY_PLAN)
    assert label == "Too much"
    assert score < 40


def test_recovery_ride_far_past_planned_duration_is_capped():
    """Legs decide when they are flushed, but three hours is not a flush."""
    ride = _ride(duration_seconds=3 * 60 * 60, intensity_factor=0.55, tss=90)
    score, label = plan_compliance.score_and_label(ride, RECOVERY_PLAN)
    assert label == "Warning"
    assert score == plan_compliance.RECOVERY_LONG_OVERRUN_CAP


def test_rest_day_still_grades_on_load():
    """A rest day prescribes no session, so any load taken is the measurement."""
    rest = {"date": "2026-08-06", "workoutType": "rest"}
    assert plan_compliance.score_and_label(_ride(tss=22), rest)[1] == "Recovery"
    assert plan_compliance.score_and_label(_ride(tss=140), rest)[1] == "Too much"


def test_endurance_ride_on_target_is_perfect():
    day = {
        "workoutType": "endurance",
        "durationMinutes": 180,
        "targetPower": {"low": 190, "high": 215},
    }
    ride = _ride(duration_seconds=3 * 60 * 60, normalized_power_w=200, tss=105)
    assert plan_compliance.score_and_label(ride, day) == (100, "Perfect")


def test_endurance_ride_cut_short_loses_the_duration_half():
    day = {
        "workoutType": "endurance",
        "durationMinutes": 180,
        "targetPower": {"low": 190, "high": 215},
    }
    ride = _ride(duration_seconds=90 * 60, normalized_power_w=200, tss=52)
    score, label = plan_compliance.score_and_label(ride, day)
    assert score == 50
    assert label == "Off plan"


def test_duration_window_makes_any_in_range_actual_on_target():
    """A prescribed window is a target, not a tolerance band around a point (#368)."""
    day = {
        "workoutType": "endurance",
        "durationMinMinutes": 180,
        "durationMaxMinutes": 240,
    }
    ride = _ride(duration_seconds=200 * 60)
    assert plan_compliance.compute_match_score(ride, day) == 100


def test_strength_session_grades_on_completion():
    day = {"workoutType": "strength", "durationMinutes": 60}
    assert plan_compliance.score_and_label(_ride(duration_seconds=30 * 60), day) == (
        50,
        "Short",
    )


def test_no_plan_day_yields_no_badge():
    assert plan_compliance.score_and_label(RECOVERY_RIDE, None) == (None, None)


def test_describe_badge_names_what_was_measured():
    """The coach must be able to say *why* without reverse-engineering it."""
    score, label = plan_compliance.score_and_label(RECOVERY_RIDE, RECOVERY_PLAN)
    described = plan_compliance.describe_badge(score, label, RECOVERY_PLAN)
    assert described == (
        '"Recovery" (score 85/100 on how easy it was ridden vs a planned recovery ride)'
    )


# --- Persistence ----------------------------------------------------------


async def _create_user(email: str) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=320,
            ai_provider="gemini",
        )
        db.add(user)
        await db.flush()
        await db.commit()
        return user.id


@pytest.mark.asyncio
async def test_matching_a_ride_persists_its_badge():
    """Every match write goes through ``update_ride_match``, so it scores there."""
    user_id = await _create_user("badge-persist@example.com")
    async with TestSessionLocal() as db:
        ride = models.RideMetric(
            user_id=user_id,
            strava_activity_id=901,
            activity_date="2026-08-05",
            sport_type="cycling",
            duration_seconds=4539,
            normalized_power_w=160,
            avg_power_w=160,
            intensity_factor=0.5,
            tss=31.5,
        )
        db.add(ride)
        await db.flush()

        await crud.update_ride_match(
            db,
            ride,
            status="auto_matched",
            matched_plan_date="2026-08-05",
            matched_plan_snapshot=RECOVERY_PLAN,
        )
        await db.commit()

        assert ride.match_score == 85
        assert ride.match_label == "Recovery"


@pytest.mark.asyncio
async def test_unmatching_a_ride_clears_its_badge():
    """A ride with no plan day has nothing to be compliant with."""
    user_id = await _create_user("badge-clear@example.com")
    async with TestSessionLocal() as db:
        ride = models.RideMetric(
            user_id=user_id,
            strava_activity_id=902,
            activity_date="2026-08-05",
            sport_type="cycling",
            duration_seconds=4539,
            intensity_factor=0.5,
            tss=31.5,
            match_score=85,
            match_label="Recovery",
        )
        db.add(ride)
        await db.flush()

        await crud.update_ride_match(db, ride, status="unmatched")
        await db.commit()

        assert ride.match_score is None
        assert ride.match_label is None


# --- The coach prompt -----------------------------------------------------


def _metric(**kwargs) -> SimpleNamespace:
    fields = {
        "activity_date": "2026-08-05",
        "sport_type": "cycling",
        "ride_purpose": None,
        "classification_confidence": "high",
        "classification_reason": None,
        "tss": 31.5,
        "duration_seconds": 4539,
        "normalized_power_w": 160,
        "summary": None,
        "coach_note": None,
        "user_note": "Legs felt fresh.",
        "feel_legs": None,
        "label_override": None,
        "match_score": 85,
        "match_label": "Recovery",
        "plan_match_status": "auto_matched",
        "matched_plan_date": "2026-08-05",
        "matched_plan_snapshot": RECOVERY_PLAN,
    }
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def test_prompt_carries_the_badge_the_athlete_sees():
    section = ride_metrics_context_section([_metric()])
    assert 'badge:"Recovery" (score 85/100 on how easy it was ridden' in section


def test_prompt_never_states_the_linkage_as_a_verdict():
    """"auto_matched" read as "logged as a successful match" — the #551 bug."""
    section = ride_metrics_context_section([_metric()])
    assert "auto_matched" not in section
    assert "plan linkage:linked to a planned session automatically" in section


def test_prompt_marks_an_explicit_label_as_outranking_the_computed_one():
    section = ride_metrics_context_section(
        [_metric(label_override="Perfect", match_label="Recovery")]
    )
    assert 'badge:"Perfect" (set explicitly, overrides the computed badge)' in section
    assert '"Recovery"' not in section


def test_prompt_tells_the_coach_to_explain_the_badge_it_was_given():
    """The instruction that stops it explaining a different ride's badge."""
    section = ride_metrics_context_section([_metric()])
    assert "never explain a badge using another activity's numbers" in section
    assert "that is the newest activity above" in section


def test_unmatched_ride_carries_no_badge():
    section = ride_metrics_context_section(
        [
            _metric(
                plan_match_status="unmatched",
                matched_plan_date=None,
                matched_plan_snapshot=None,
                match_score=None,
                match_label=None,
            )
        ]
    )
    assert "badge:" not in section
    assert "plan linkage:" not in section


def test_recalculating_metrics_rescores_the_badge():
    """An FTP change moves TSS and IF — the very inputs the badge is scored from.

    Metric recalculation runs outside the matcher, so without an explicit
    rescore the stored badge would keep describing the old numbers while the
    coach reported it as current.
    """
    from services.metrics_service import _recalculate_metric_chain, _rescore_compliance_badges

    ride = models.RideMetric(
        user_id="u",
        strava_activity_id=904,
        activity_date="2026-08-05",
        sport_type="cycling",
        duration_seconds=4539,
        normalized_power_w=160,
        avg_power_w=160,
        matched_plan_snapshot=RECOVERY_PLAN,
    )

    # At 320 W FTP those 160 W are IF 0.50 — a genuine recovery spin.
    _recalculate_metric_chain([ride], 320)
    _rescore_compliance_badges([ride])
    assert ride.match_label == "Recovery"

    # At 200 W FTP the same watts are IF 0.80, which is no longer recovery.
    _recalculate_metric_chain([ride], 200)
    _rescore_compliance_badges([ride])
    assert ride.match_label == "Too much"


def test_rescoring_leaves_unmatched_rides_alone():
    from services.metrics_service import _rescore_compliance_badges

    ride = models.RideMetric(
        user_id="u",
        strava_activity_id=905,
        activity_date="2026-08-05",
        sport_type="cycling",
        duration_seconds=4539,
        matched_plan_snapshot=None,
    )
    _rescore_compliance_badges([ride])
    assert ride.match_label is None
