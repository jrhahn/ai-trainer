"""Running-specific exposure and the ceiling it puts on volume (#717).

The failure this feature exists to prevent is specific: a fit cyclist's CTL says
their aerobic system can sustain a great deal of running and says nothing about
whether their legs can, so a planner reasoning from fitness to running volume
prescribes a week that would hurt them. These tests pin all three parts of the
answer — what the exposure reader reads, what the ceiling refuses to be a
function of, and what the gate does about a write that goes over it.
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import crud
import models
from auth import hash_password
from services import plan_context, plan_pipeline, run_durability
from services.dates import app_today
from services.prompts import run_durability_section
from tests.conftest import TestSessionLocal

TODAY = date(2026, 10, 5)

# Distinguishes "the helper was not asked about this" from "the row genuinely
# carries no duration", which is a case under test.
_UNSET = object()


def _run_row(
    days_ago: int,
    minutes: float = 40.0,
    *,
    sport: str = "Run",
    distance_m: float | None = 8000.0,
    today: date = TODAY,
    duration_seconds: object = _UNSET,
    perf_signals: object = _UNSET,
) -> SimpleNamespace:
    """A ``RideMetric``-shaped row. ``run_exposure`` is pure, so a stub is enough."""
    signals = perf_signals
    if signals is _UNSET:
        signals = {"sport": "running"}
        if distance_m is not None:
            signals["distance_m"] = distance_m
    seconds = minutes * 60.0 if duration_seconds is _UNSET else duration_seconds
    return SimpleNamespace(
        sport_type=sport,
        activity_date=(today - timedelta(days=days_ago)).isoformat(),
        duration_seconds=seconds,
        perf_signals=signals,
    )


def _plan_run(
    when: str, minutes: int = 40, title: str = "Easy run", **extra
) -> dict:
    return {
        "date": when,
        "sport": "running",
        "workoutType": "endurance",
        "title": title,
        "description": "Run",
        "durationMinutes": minutes,
        **extra,
    }


def _plan_ride(when: str, minutes: int = 120) -> dict:
    return {
        "date": when,
        "sport": "cycling",
        "workoutType": "endurance",
        "title": "Endurance ride",
        "description": "Ride",
        "durationMinutes": minutes,
    }


def _in(days: int, today: date = TODAY) -> str:
    return (today + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# What the exposure reader reads
# ---------------------------------------------------------------------------


def test_an_athlete_with_no_activities_has_no_running_exposure():
    exposure = run_durability.run_exposure([], TODAY)
    assert exposure.has_history is False
    assert exposure.total_minutes == 0
    assert exposure.chronic_weekly_minutes == 0
    assert exposure.days_since_last_run is None


def test_a_cyclist_who_never_runs_has_no_running_exposure():
    rows = [_run_row(day, sport="Ride") for day in range(1, 20)]
    exposure = run_durability.run_exposure(rows, TODAY)
    assert exposure.run_count == 0
    assert exposure.has_history is False


@pytest.mark.parametrize("spelling", ["Run", "TrailRun", "VirtualRun", "run"])
def test_every_provider_spelling_of_a_run_is_exposure(spelling: str):
    exposure = run_durability.run_exposure([_run_row(2, sport=spelling)], TODAY)
    assert exposure.run_count == 1


@pytest.mark.parametrize("spelling", ["Hike", "Walk", "Ride", "WeightTraining"])
def test_walking_and_hiking_are_not_running_exposure(spelling: str):
    """Both carry some eccentric load; neither carries it at running's rate.

    Folding them in would hand a running allowance to an athlete who walks to
    work, which is the direction that errs dangerously.
    """
    exposure = run_durability.run_exposure([_run_row(2, sport=spelling)], TODAY)
    assert exposure.run_count == 0


def test_two_runs_on_one_date_both_count():
    rows = [_run_row(3, 30.0), _run_row(3, 20.0)]
    exposure = run_durability.run_exposure(rows, TODAY)
    assert exposure.run_count == 2
    assert exposure.total_minutes == pytest.approx(50.0)
    assert exposure.longest_run_minutes == pytest.approx(30.0)


def test_a_run_older_than_the_window_is_not_current_exposure():
    inside = _run_row(run_durability.RUN_EXPOSURE_WINDOW_DAYS - 1, 40.0)
    outside = _run_row(run_durability.RUN_EXPOSURE_WINDOW_DAYS, 40.0)
    assert run_durability.run_exposure([inside], TODAY).run_count == 1
    assert run_durability.run_exposure([outside], TODAY).run_count == 0


def test_a_future_dated_run_is_not_exposure_already_absorbed():
    """A run dated tomorrow is a timezone artefact or a bad import.

    Reading it as exposure would hand out an allowance for a run that has not
    happened, so it is dropped rather than counted.
    """
    exposure = run_durability.run_exposure([_run_row(-1, 90.0)], TODAY)
    assert exposure.run_count == 0


def test_the_duration_falls_back_to_the_stored_run_signals():
    row = _run_row(
        2,
        duration_seconds=None,
        perf_signals={"sport": "running", "moving_duration_s": 1800},
    )
    exposure = run_durability.run_exposure([row], TODAY)
    assert exposure.total_minutes == pytest.approx(30.0)

    row = _run_row(
        2, duration_seconds=None, perf_signals={"sport": "running", "duration_s": 2400}
    )
    exposure = run_durability.run_exposure([row], TODAY)
    assert exposure.total_minutes == pytest.approx(40.0)


def test_a_run_nobody_can_time_is_not_counted():
    row = _run_row(2, duration_seconds=None, perf_signals={"sport": "running"})
    exposure = run_durability.run_exposure([row], TODAY)
    assert exposure.run_count == 0
    assert exposure.has_history is False


def test_distance_is_the_raw_distance_not_the_grade_adjusted_one():
    """GAP distance prices the load (#716); mileage is what the athlete's watch says.

    Quoting a hill-inflated figure back at them would be a number they cannot
    check.
    """
    row = _run_row(
        2,
        perf_signals={"sport": "running", "distance_m": 8000, "gap_distance_m": 9500},
    )
    exposure = run_durability.run_exposure([row], TODAY)
    assert exposure.total_distance_km == pytest.approx(8.0)


def test_distance_is_absent_rather_than_zero_when_no_run_measured_one():
    rows = [_run_row(2, distance_m=None), _run_row(4, distance_m=None)]
    exposure = run_durability.run_exposure(rows, TODAY)
    assert exposure.total_distance_km is None
    assert exposure.runs_with_distance == 0
    assert exposure.run_count == 2


def test_partial_distance_coverage_is_reported_as_partial():
    rows = [_run_row(2, distance_m=8000.0), _run_row(4, distance_m=None)]
    exposure = run_durability.run_exposure(rows, TODAY)
    assert exposure.total_distance_km == pytest.approx(8.0)
    assert exposure.runs_with_distance == 1
    assert exposure.run_count == 2
    ceiling = run_durability.run_volume_ceiling(exposure)
    assert "(measured)" in run_durability.ceiling_statement(exposure, ceiling)


def test_the_peak_rolling_window_finds_the_heaviest_seven_days():
    """Three runs inside one week, and a quiet fortnight either side of it."""
    rows = [
        _run_row(20, 60.0),
        _run_row(18, 60.0),
        _run_row(16, 60.0),
        _run_row(3, 30.0),
    ]
    exposure = run_durability.run_exposure(rows, TODAY)
    assert exposure.peak_rolling_minutes == pytest.approx(180.0)
    assert exposure.days_since_last_run == 3


def test_the_chronic_figure_is_the_four_week_mean():
    rows = [_run_row(day, 40.0) for day in (2, 5, 9, 12, 16, 19, 23, 26)]
    exposure = run_durability.run_exposure(rows, TODAY)
    assert exposure.total_minutes == pytest.approx(320.0)
    assert exposure.chronic_weekly_minutes == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# What the ceiling is, and what it refuses to be a function of
# ---------------------------------------------------------------------------


def test_the_ceiling_cannot_read_a_fitness_figure():
    """The structural half of "the ceiling is not a function of CTL".

    Not a style check: the failure #717 exists to prevent is a planner reasoning
    from aerobic fitness to running volume, and a ceiling that *could* be handed
    a fitness number would eventually be handed one.
    """
    params = inspect.signature(run_durability.run_volume_ceiling).parameters
    assert list(params) == ["exposure"]
    fields = run_durability.RunExposure.__dataclass_fields__
    for forbidden in ("ctl", "ftp", "atl", "tsb", "fitness"):
        assert not any(forbidden in name for name in fields)


def test_the_exposure_reader_insists_on_being_told_today():
    """An absent reference date is not the safe direction it looks like.

    ``run_exposure`` answers an empty exposure, which hands a 200 km-a-month
    runner the beginner ceiling and makes the gate revert perfectly good writes.
    A caller that forgot should fail loudly instead.
    """
    params = inspect.signature(plan_context.athlete_run_exposure).parameters
    assert params["today"].default is inspect.Parameter.empty


def test_a_fit_cyclist_who_has_never_run_is_capped_like_a_beginner():
    """The acceptance criterion, and the whole point of the module.

    The athlete is deliberately given a long cycling history, because a shared
    aerobic ceiling would read it as fitness. The ceiling sees running and
    nothing else, so a CTL of 90 changes nothing here.
    """
    rows = [_run_row(day, 180.0, sport="Ride") for day in range(1, 28)]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    assert ceiling.basis == run_durability.BASIS_NO_HISTORY
    assert ceiling.weekly_minutes == run_durability.BEGINNER_WEEKLY_MINUTES
    assert ceiling.longest_run_minutes == run_durability.BEGINNER_LONGEST_RUN_MINUTES


def test_the_ceiling_is_a_step_on_the_four_week_mean():
    rows = [_run_row(day, 50.0) for day in (2, 5, 9, 12, 16, 19, 23, 26)]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    chronic = exposure.chronic_weekly_minutes
    assert chronic == pytest.approx(100.0)
    assert ceiling.weekly_minutes == pytest.approx(
        chronic * run_durability.MAX_ACUTE_CHRONIC_RATIO
    )
    assert ceiling.basis == run_durability.BASIS_RECENT_EXPOSURE


def test_one_big_week_is_a_spike_not_a_base():
    """The mean, not the peak — the distinction the module exists to make.

    Four hours of running in one week followed by three empty ones is not a base
    the tissue is adapted to. The ceiling that comes out is barely above the
    beginner allowance, which is the honest reading.
    """
    rows = [_run_row(day, 60.0) for day in (22, 24, 26, 27)]
    exposure = run_durability.run_exposure(rows, TODAY)
    assert exposure.peak_rolling_minutes == pytest.approx(240.0)
    ceiling = run_durability.run_volume_ceiling(exposure)
    assert ceiling.weekly_minutes < exposure.peak_rolling_minutes / 2


def test_a_lapse_lowers_the_ceiling_without_a_decay_rule():
    """Three weeks off, and the window counts the empty weeks as the zeros they are."""
    steady = [_run_row(day, 60.0) for day in (2, 4, 6, 9, 11, 13, 16, 18, 20, 23, 25, 27)]
    lapsed = [_run_row(day, 60.0) for day in (23, 25, 27)]
    fit = run_durability.run_volume_ceiling(run_durability.run_exposure(steady, TODAY))
    rusty = run_durability.run_volume_ceiling(run_durability.run_exposure(lapsed, TODAY))
    assert rusty.weekly_minutes < fit.weekly_minutes / 3


def test_the_beginner_allowance_is_what_makes_a_small_volume_progressable():
    """30 % of 20 min/week is six minutes, which is not a progression.

    A separate floor on the weekly step would be the obvious fix and is not
    there: the beginner allowance already sits above everything the proportional
    step fails to deliver, so the second constant would be inside its shadow —
    see the comment on ``MAX_WEEKLY_STEP_MINUTES``.
    """
    rows = [_run_row(day, 20.0) for day in (3, 10, 17, 24)]
    exposure = run_durability.run_exposure(rows, TODAY)
    chronic = exposure.chronic_weekly_minutes
    assert chronic == pytest.approx(20.0)
    ceiling = run_durability.run_volume_ceiling(exposure)
    assert ceiling.weekly_minutes > chronic * run_durability.MAX_ACUTE_CHRONIC_RATIO
    assert ceiling.weekly_minutes == run_durability.BEGINNER_WEEKLY_MINUTES


def test_the_step_has_a_cap_so_a_big_volume_does_not_get_a_bigger_licence():
    """At 600 min/week the ratio alone would permit three further hours of impact."""
    rows = [_run_row(day, 150.0) for day in range(1, 17)]
    exposure = run_durability.run_exposure(rows, TODAY)
    chronic = exposure.chronic_weekly_minutes
    assert chronic > 500
    ceiling = run_durability.run_volume_ceiling(exposure)
    assert ceiling.weekly_minutes == pytest.approx(
        chronic + run_durability.MAX_WEEKLY_STEP_MINUTES
    )
    assert ceiling.weekly_minutes < chronic * run_durability.MAX_ACUTE_CHRONIC_RATIO


def test_having_run_once_cannot_make_an_athlete_more_fragile_than_never_running():
    """A single 20-minute run gives a chronic figure of 5 min/week.

    A purely proportional ceiling would conclude this athlete may run less than
    one who has never run at all, so the beginner allowance is a floor under
    everyone rather than a separate case.
    """
    exposure = run_durability.run_exposure([_run_row(10, 20.0)], TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    assert ceiling.weekly_minutes == run_durability.BEGINNER_WEEKLY_MINUTES


def test_the_long_run_ceiling_steps_from_the_longest_recent_run():
    rows = [_run_row(day, 60.0) for day in (3, 6, 10, 13, 17, 20, 24)]
    rows.append(_run_row(27, 90.0))
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    assert exposure.longest_run_minutes == pytest.approx(90.0)
    assert ceiling.longest_run_minutes == pytest.approx(
        90.0 * run_durability.MAX_ACUTE_CHRONIC_RATIO
    )


def test_the_long_run_step_has_a_floor_of_its_own():
    """Thirty per cent of a twenty-minute run is six minutes.

    An athlete who runs often but never long would be told their next longest run
    may be twenty-six minutes, which is not a step anybody can execute — a run is
    not timed that precisely.
    """
    rows = [_run_row(day, 20.0) for day in range(1, 21)]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    assert exposure.longest_run_minutes == pytest.approx(20.0)
    assert ceiling.weekly_minutes > 30.0, "the weekly clamp must not be what binds"
    assert ceiling.longest_run_minutes == pytest.approx(
        20.0 + run_durability.MIN_LONG_RUN_STEP_MINUTES
    )


def test_the_long_run_step_is_capped_tighter_than_the_weekly_one():
    """Thirty minutes added to one session is not thirty spread over three days."""
    rows = [_run_row(day, 180.0) for day in range(1, 15)]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    assert ceiling.longest_run_minutes == pytest.approx(
        180.0 + run_durability.MAX_LONG_RUN_STEP_MINUTES
    )


def test_a_single_run_is_never_allowed_to_exceed_the_whole_weeks_ceiling():
    """A lapsed runner whose one long run is most of their four-week total.

    The arithmetic can otherwise prescribe a long run longer than the week it
    sits in, which is not a coherent plan.
    """
    exposure = run_durability.run_exposure([_run_row(24, 200.0)], TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    assert ceiling.longest_run_minutes <= ceiling.weekly_minutes


# ---------------------------------------------------------------------------
# Reading the plan
# ---------------------------------------------------------------------------


def test_a_duration_window_is_read_at_its_upper_bound():
    """A 60–90 min session is one the athlete may legitimately run for 90.

    Checking the midpoint would permit a week that exceeds the ceiling, and the
    athlete who took the plan at its word is the one who gets hurt.
    """
    day = _plan_run(_in(2), 75, durationMinMinutes=60, durationMaxMinutes=90)
    assert run_durability.planned_run_minutes(day) == pytest.approx(90.0)


@pytest.mark.parametrize(
    "day",
    [
        _plan_ride(_in(2)),
        {"date": _in(2), "sport": "running", "workoutType": "rest", "title": "Rest"},
        {"date": _in(2), "sport": "strength", "workoutType": "strength", "title": "Gym",
         "durationMinutes": 60},
        {"date": _in(2), "sport": "running", "workoutType": "endurance", "title": "Run"},
    ],
    ids=["a ride", "a rest day", "gym work", "a run with no duration"],
)
def test_these_prescribe_no_running_minutes(day: dict):
    assert run_durability.planned_run_minutes(day) is None


def test_a_malformed_plan_day_is_skipped_rather_than_crashing_the_gate():
    """Plan days reach here unvalidated, and one bad day must not block a write.

    ``plan_pipeline._to_canonical_day`` exists because malformed legacy and LLM
    days are real, and this guard runs inside the same write.
    """
    exposure, ceiling = _beginner()
    plan = [
        "not a day",
        {"sport": "running", "workoutType": "endurance", "durationMinutes": 90},
        {"date": "the fifth", "sport": "running", "durationMinutes": 90},
        _plan_run(_in(2), 90, title="Real run"),
    ]
    assert run_durability.planned_run_minutes("not a day") is None
    findings = run_durability.find_run_overload(plan, exposure, ceiling, TODAY)
    assert findings
    assert all(
        session["title"] == "Real run"
        for finding in findings
        for session in finding["sessions"]
    )


def test_a_run_with_no_stored_signals_still_counts_as_exposure():
    """Only its distance is unknown; a run nobody measured is still a run."""
    row = _run_row(3, 40.0, perf_signals=None)
    exposure = run_durability.run_exposure([row], TODAY)
    assert exposure.total_minutes == pytest.approx(40.0)
    assert exposure.total_distance_km is None


def test_an_unreadable_reference_date_produces_no_answer_at_all():
    """There is deliberately no fallback to ``date.today()``.

    The athlete's today is ``app_today``, which applies their timezone and can be
    a different calendar day — so a quiet fallback would answer with a window
    shifted by a day rather than admitting it had no reference.
    """
    rows = [_run_row(day, 60.0) for day in (2, 5, 9)]
    exposure = run_durability.run_exposure(rows, "not-a-date")
    assert exposure.has_history is False
    assert exposure.days_since_last_run is None

    real, ceiling = _beginner()
    plan = [_plan_run(_in(1), 180)]
    assert run_durability.plan_has_running(plan, "not-a-date") is False
    assert run_durability.find_run_volume_excess(plan, real, ceiling, None) == []
    assert run_durability.find_long_run_excess(plan, ceiling, None) == []


def test_plan_has_running_ignores_the_past():
    past = [_plan_run(_in(-3))]
    future = [_plan_run(_in(3))]
    assert run_durability.plan_has_running(past, TODAY) is False
    assert run_durability.plan_has_running(future, TODAY) is True
    assert run_durability.plan_has_running([_plan_ride(_in(3))], TODAY) is False


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _beginner() -> tuple[run_durability.RunExposure, run_durability.RunCeiling]:
    exposure = run_durability.run_exposure([], TODAY)
    return exposure, run_durability.run_volume_ceiling(exposure)


def test_a_week_inside_the_ceiling_produces_nothing():
    exposure, ceiling = _beginner()
    plan = [_plan_run(_in(1), 20), _plan_run(_in(4), 20), _plan_run(_in(6), 20)]
    assert run_durability.find_run_overload(plan, exposure, ceiling, TODAY) == []


def test_a_sharp_step_up_in_weekly_volume_is_flagged():
    """The acceptance criterion: a plan that steps volume up sharply is caught."""
    rows = [_run_row(day, 30.0) for day in (3, 10, 17, 24)]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    plan = [_plan_run(_in(1), 60), _plan_run(_in(3), 60), _plan_run(_in(5), 60)]
    findings = run_durability.find_run_overload(plan, exposure, ceiling, TODAY)
    weekly = [
        f for f in findings if f["rule"] == run_durability.RULE_WEEKLY_RUN_VOLUME
    ]
    assert weekly
    assert weekly[0]["excess_minutes"] > 0
    assert "eccentric contraction" in run_durability.finding_statement(weekly[0])


def test_a_block_across_a_calendar_week_boundary_is_still_one_week_of_loading():
    """Sunday and Monday is one week to the tissue and two to a calendar.

    A Monday-to-Sunday check sees 50 min in one week and 50 in the next, both
    comfortably under the beginner ceiling, and waves through the only
    arrangement that needed catching. The rolling window does not — the test
    asserts the calendar reading first, so it cannot pass for the wrong reason.
    """
    exposure, ceiling = _beginner()
    sunday = next(
        TODAY + timedelta(days=offset)
        for offset in range(1, 9)
        if (TODAY + timedelta(days=offset)).weekday() == 6
    )
    plan = [
        _plan_run(sunday.isoformat(), 50),
        _plan_run((sunday + timedelta(days=1)).isoformat(), 50),
    ]
    by_calendar_week: dict[tuple[int, int], float] = {}
    for day in plan:
        week = date.fromisoformat(day["date"]).isocalendar()[:2]
        by_calendar_week[week] = by_calendar_week.get(week, 0.0) + 50.0
    assert len(by_calendar_week) == 2
    assert max(by_calendar_week.values()) <= ceiling.weekly_minutes

    findings = run_durability.find_run_volume_excess(plan, exposure, ceiling, TODAY)
    assert findings
    assert any(f["planned_minutes"] == pytest.approx(100.0) for f in findings)


def test_running_already_done_this_week_counts_against_the_ceiling():
    """The window spans today, because the legs do not start counting at midnight."""
    rows = [_run_row(2, 60.0), _run_row(1, 60.0)] + [
        _run_row(day, 60.0) for day in (8, 10, 15, 17, 22, 24)
    ]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    plan = [_plan_run(_in(1), 60), _plan_run(_in(3), 60)]

    spanning = [
        f
        for f in run_durability.find_run_volume_excess(plan, exposure, ceiling, TODAY)
        if f["completed_minutes"] > 0
    ]
    assert spanning, "a window spanning today must see the runs already completed"
    assert max(f["completed_minutes"] for f in spanning) == pytest.approx(120.0)
    # One window holds both completed runs and both planned ones.
    assert max(f["planned_minutes"] for f in spanning) == pytest.approx(240.0)


def test_an_ad_hoc_run_done_today_counts_against_the_ceiling():
    """Today is read as whichever of history and plan is larger.

    Reading today from the plan alone loses a run the athlete went out and did
    this morning with nothing prescribed — exposure their legs have taken that
    the window would not see.
    """
    rows = [_run_row(0, 70.0)]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    plan = [_plan_run(_in(2), 40)]
    findings = run_durability.find_run_volume_excess(plan, exposure, ceiling, TODAY)
    assert findings, "70 min this morning plus 40 planned is over a 60 min ceiling"
    assert max(f["completed_minutes"] for f in findings) == pytest.approx(70.0)


def test_a_run_matching_todays_plan_is_not_counted_twice():
    """The ordinary case: the run in today's history *is* the run on today's plan.

    Summing them would flag the athlete for training exactly as instructed.
    """
    rows = [_run_row(0, 50.0)]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    # A second run later in the week, so a window genuinely exceeds the ceiling
    # and the assertion is about the total rather than about an empty list.
    plan = [_plan_run(TODAY.isoformat(), 50), _plan_run(_in(3), 40)]
    findings = run_durability.find_run_volume_excess(plan, exposure, ceiling, TODAY)
    assert findings
    assert max(f["planned_minutes"] for f in findings) == pytest.approx(90.0), (
        "double counting today would read 140"
    )


def test_overshooting_todays_session_counts_what_was_actually_run():
    """Prescribed 40, ran 90 — the legs took 90."""
    rows = [_run_row(0, 90.0)]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    plan = [_plan_run(TODAY.isoformat(), 40)]
    findings = run_durability.find_run_volume_excess(plan, exposure, ceiling, TODAY)
    assert findings
    assert max(f["planned_minutes"] for f in findings) == pytest.approx(90.0)


def test_a_window_before_today_with_no_completed_running_is_not_reported_twice():
    """It is a shifted view of the window that starts today — same sessions, same total."""
    exposure, ceiling = _beginner()
    plan = [_plan_run(_in(1), 90)]
    findings = run_durability.find_run_volume_excess(plan, exposure, ceiling, TODAY)
    assert all(
        date.fromisoformat(f["window_start"]) >= TODAY for f in findings
    )


def test_a_long_run_is_caught_even_inside_a_week_that_is_under_its_total():
    """120 minutes in one run is not the same exposure as 120 across four days.

    The history is deliberately all a week or more old, so no rolling window
    spanning today carries completed minutes and the only thing over the line is
    the single session.
    """
    rows = [_run_row(day, 60.0) for day in (7, 10, 14, 17, 21, 24, 27)]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    plan = [_plan_run(_in(2), 120, title="Long run")]
    assert sum(d["durationMinutes"] for d in plan) < ceiling.weekly_minutes

    findings = run_durability.find_run_overload(plan, exposure, ceiling, TODAY)
    assert [f["rule"] for f in findings] == [run_durability.RULE_LONG_RUN_STEP]
    assert "Long run" in run_durability.finding_statement(findings[0])


def test_findings_beyond_the_horizon_are_left_to_a_later_week():
    exposure, ceiling = _beginner()
    plan = [_plan_run(_in(90), 180)]
    assert run_durability.find_run_overload(plan, exposure, ceiling, TODAY) == []


def test_the_largest_excess_is_stated_first():
    exposure, ceiling = _beginner()
    plan = [_plan_run(_in(1), 45), _plan_run(_in(9), 120, title="Very long")]
    findings = run_durability.find_run_overload(plan, exposure, ceiling, TODAY)
    excesses = [f["excess_minutes"] for f in findings]
    assert excesses == sorted(excesses, reverse=True)


def test_lengthening_an_already_over_week_does_not_create_a_new_finding():
    """The gate's before/after comparison depends on this.

    A write that adds five minutes to a week that was already over has not
    created the overload, and reverting it would punish that write for a block it
    did not build.
    """
    exposure, ceiling = _beginner()
    before = [_plan_run(_in(1), 90)]
    after = [_plan_run(_in(1), 95)]
    assert set(
        run_durability.run_overload_keys(before, exposure, ceiling, TODAY)
    ) == set(run_durability.run_overload_keys(after, exposure, ceiling, TODAY))


def test_a_cycling_week_of_any_size_produces_nothing():
    exposure, ceiling = _beginner()
    plan = [_plan_ride(_in(offset), 300) for offset in range(1, 8)]
    assert run_durability.find_run_overload(plan, exposure, ceiling, TODAY) == []


# ---------------------------------------------------------------------------
# What the coach is told
# ---------------------------------------------------------------------------


def test_the_section_is_empty_without_an_exposure_to_state():
    assert run_durability_section(None, None, [], TODAY) == ""


def test_the_no_history_block_says_aerobic_fitness_is_not_running_fitness():
    exposure, ceiling = _beginner()
    block = run_durability_section(exposure, ceiling, [], TODAY)
    assert "aerobic fitness is not running fitness" in block.lower()
    assert "60 min" in block and "20 min" in block
    assert "beginner" in block


def test_the_block_states_the_ceiling_even_when_nothing_is_wrong():
    """It is a constraint to plan inside, not a defect to report.

    Stating it only on violation would mean the first running week is written
    blind and corrected afterwards — and the correction is what #717 is trying to
    avoid needing.
    """
    exposure, ceiling = _beginner()
    block = run_durability_section(exposure, ceiling, [], TODAY)
    assert "Nothing in the plan exceeds it" in block
    assert "never stops being a beginner" in block


def test_the_block_quotes_the_athletes_own_figures_and_admits_the_uncertainty():
    rows = [_run_row(day, 45.0) for day in (2, 6, 9, 13, 16, 20, 23, 27)]
    exposure = run_durability.run_exposure(rows, TODAY)
    ceiling = run_durability.run_volume_ceiling(exposure)
    block = run_durability_section(exposure, ceiling, [], TODAY)
    assert "8 runs" in block
    assert "360 min total" in block
    assert "convention this app has chosen" in block
    assert "10 % rule included, is weak" in block


def test_overlapping_windows_are_collapsed_for_the_coach():
    """Seven starting days describing one build-up is six lines of noise.

    The count survives, because "4 further windows are also over" is the sentence
    that says this is a block rather than one bad day.
    """
    exposure, ceiling = _beginner()
    plan = [_plan_run(_in(offset), 60) for offset in range(1, 6)]
    findings = run_durability.find_run_overload(plan, exposure, ceiling, TODAY)
    weekly = [
        f for f in findings if f["rule"] == run_durability.RULE_WEEKLY_RUN_VOLUME
    ]
    assert len(weekly) > run_durability.MAX_REPORTED_WINDOWS
    block = run_durability_section(exposure, ceiling, findings, TODAY)
    stated = sum(1 for line in block.splitlines() if " to " in line and "min of running" in line)
    assert stated == run_durability.MAX_REPORTED_WINDOWS
    assert "further overlapping 7-day window" in block


def test_the_coach_is_told_not_to_cut_cycling_to_make_room():
    """The ceiling is about running tissue, and says nothing about the bike.

    A coach that reads "reduce load" and trims the long ride has taken away the
    training the athlete can actually absorb.
    """
    exposure, ceiling = _beginner()
    plan = [_plan_run(_in(1), 120)]
    findings = run_durability.find_run_overload(plan, exposure, ceiling, TODAY)
    block = run_durability_section(exposure, ceiling, findings, TODAY)
    assert "Cycling volume is not limited by this" in block
    assert "their decision to make" in block


def test_the_window_dates_are_weekday_anchored_for_the_planner():
    """A bare "2026-10-11 to 2026-10-17" says nothing about where the weekend is.

    #462's convention: every prompt that shows or writes the plan anchors its
    weekdays, because a writer that has to derive them gets them wrong.
    """
    exposure, ceiling = _beginner()
    plan = [_plan_run(_in(1), 120, title="Long run")]
    findings = run_durability.find_run_overload(plan, exposure, ceiling, TODAY)
    block = run_durability_section(exposure, ceiling, findings, TODAY)
    assert f"({(TODAY + timedelta(days=1)).strftime('%A')})" in block


def test_the_section_takes_an_iso_today_as_well_as_a_date():
    """Callers pair a string-taking detector with a date-taking label helper."""
    exposure, ceiling = _beginner()
    plan = [_plan_run(_in(1), 120)]
    findings = run_durability.find_run_overload(plan, exposure, ceiling, TODAY)
    block = run_durability_section(exposure, ceiling, findings, TODAY.isoformat())
    assert f"({(TODAY + timedelta(days=1)).strftime('%A')})" in block


def test_the_recorded_rationale_carries_plain_dates():
    """The same statement lands in ``plan_day_history``, which has a date column.

    So the weekday label is optional rather than baked in: it is anchoring for a
    prompt and noise in a row that already says which day it is about.
    """
    exposure, ceiling = _beginner()
    plan = [_plan_run(_in(1), 120)]
    finding = run_durability.find_run_overload(plan, exposure, ceiling, TODAY)[0]
    statement = run_durability.finding_statement(finding)
    assert _in(1) in statement
    assert "(" + (TODAY + timedelta(days=1)).strftime("%A") + ")" not in statement


def test_many_over_length_runs_are_summarised_rather_than_listed():
    exposure, ceiling = _beginner()
    plan = [_plan_run(_in(offset * 2), 40) for offset in range(1, 7)]
    findings = run_durability.find_run_overload(plan, exposure, ceiling, TODAY)
    block = run_durability_section(exposure, ceiling, findings, TODAY)
    assert "further planned runs also exceed the single-run ceiling" in block


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _ride_metric(user_id: str, index: int, when: str, minutes: float) -> models.RideMetric:
    return models.RideMetric(
        user_id=user_id,
        strava_activity_id=900000 + index,
        activity_source="strava",
        external_activity_id=f"run-{index}",
        activity_date=when,
        sport_type="Run",
        duration_seconds=int(minutes * 60),
        perf_signals={"sport": "running", "distance_m": minutes * 180},
    )


async def _create_athlete(
    email: str, plan: list[dict], run_days: list[int], minutes: float = 40.0
) -> str:
    """An athlete with ``plan`` and one run ``d`` days ago for each ``d``."""
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Runner",
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
        for index, days_ago in enumerate(run_days):
            when = (app_today() - timedelta(days=days_ago)).isoformat()
            db.add(_ride_metric(user.id, index, when, minutes))
        await crud.upsert_training_plan(db, user.id, plan)
        await db.commit()
        return user.id


async def _commit(user_id: str, plan: list[dict], source: str):
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = await crud.get_training_plan(db, user_id)
        base = current.plan if current is not None else []
        result = await plan_pipeline.commit_plan(
            db, user, plan, base_plan=base, source=source
        )
        await db.commit()
        return result


def _ahead(days: int) -> str:
    return (app_today() + timedelta(days=days)).isoformat()


def _minutes_on(plan: list[dict], when: str) -> int | None:
    return next(
        (d.get("durationMinutes") for d in plan if d.get("date") == when), None
    )


@pytest.mark.asyncio
async def test_an_automated_write_that_lengthens_a_run_past_the_ceiling_is_undone():
    base = [_plan_run(_ahead(2), 30), _plan_run(_ahead(5), 30)]
    user_id = await _create_athlete(
        "durability-revert@example.com", base, run_days=[3, 10, 17, 24], minutes=30.0
    )
    proposal = [_plan_run(_ahead(2), 30), _plan_run(_ahead(5), 150)]
    result = await _commit(user_id, proposal, "nightly_maintenance")
    assert _minutes_on(result.plan, _ahead(5)) == 30


@pytest.mark.asyncio
async def test_the_revert_records_the_rule_it_applied():
    base = [_plan_run(_ahead(2), 30)]
    user_id = await _create_athlete(
        "durability-reason@example.com", base, run_days=[3, 10, 17, 24], minutes=30.0
    )
    await _commit(user_id, [_plan_run(_ahead(2), 160)], "nightly_maintenance")
    async with TestSessionLocal() as db:
        rows = list(await crud.list_plan_day_history(db, user_id, date=_ahead(2)))
    blocked = [row for row in rows if not row.applied and row.reason]
    assert blocked, "a guard revert must be attributable from the history alone"
    reason = blocked[0].reason
    assert "ceiling" in reason
    assert "reverted to the duration the plan held" in reason


@pytest.mark.asyncio
async def test_an_athlete_who_asks_for_the_bigger_week_gets_it():
    """The ceiling brakes the automated writer, not the athlete.

    User-driven triggers skip every guard at this gate, and that is the property
    that keeps a stated ceiling from becoming a silent clamp: the reasoning is in
    front of the athlete, and the decision stays theirs.
    """
    base = [_plan_run(_ahead(2), 30)]
    user_id = await _create_athlete(
        "durability-user@example.com", base, run_days=[3, 10, 17, 24], minutes=30.0
    )
    result = await _commit(user_id, [_plan_run(_ahead(2), 160)], "coach_chat")
    assert _minutes_on(result.plan, _ahead(2)) == 160


@pytest.mark.asyncio
async def test_a_week_that_was_already_over_the_ceiling_is_left_alone():
    """Not this write's doing, so not this write's to pay for."""
    base = [_plan_run(_ahead(2), 150), _plan_run(_ahead(4), 60)]
    user_id = await _create_athlete(
        "durability-preexisting@example.com", base, run_days=[3, 10], minutes=30.0
    )
    proposal = [
        _plan_run(_ahead(2), 150),
        _plan_run(_ahead(4), 60, title="Retitled run"),
    ]
    result = await _commit(user_id, proposal, "nightly_maintenance")
    titles = {d["date"]: d["title"] for d in result.plan}
    assert titles[_ahead(4)] == "Retitled run"


@pytest.mark.asyncio
async def test_the_first_plan_an_athlete_ever_gets_is_not_reverted():
    """There is no previous version of anything, so nothing can be handed back.

    The same early return the interference guard makes, and for the same reason:
    a revert restores stored content, and here there is none.
    """
    user_id = await _create_athlete(
        "durability-firstplan@example.com", [], run_days=[], minutes=30.0
    )
    proposal = [_plan_run(_ahead(1), 120), _plan_run(_ahead(3), 120)]
    result = await _commit(user_id, proposal, "nightly_maintenance")
    assert _minutes_on(result.plan, _ahead(1)) == 120
    assert _minutes_on(result.plan, _ahead(3)) == 120


@pytest.mark.asyncio
async def test_an_appended_run_is_reported_rather_than_deleted():
    """There is nothing to give back, and deleting it would be #651.

    Shortening it instead would be the silent clamp #717 rules out, so the
    session stands and the coach is told.
    """
    base = [_plan_ride(_ahead(2))]
    user_id = await _create_athlete(
        "durability-appended@example.com", base, run_days=[], minutes=30.0
    )
    proposal = [_plan_ride(_ahead(2)), _plan_run(_ahead(4), 150, title="Debut run")]
    result = await _commit(user_id, proposal, "nightly_maintenance")
    assert _minutes_on(result.plan, _ahead(4)) == 150

    async with TestSessionLocal() as db:
        exposure = await plan_context.athlete_run_exposure(db, user_id, app_today())
    ceiling = run_durability.run_volume_ceiling(exposure)
    findings = run_durability.find_run_overload(
        result.plan, exposure, ceiling, app_today()
    )
    assert findings, "the overload the gate could not fix must reach the coach"


@pytest.mark.asyncio
async def test_the_guard_hands_back_the_run_the_write_lengthened_not_the_one_it_shortened():
    """Reverting a shortened run would hand back the longer version.

    The guard would then have raised the volume it fired on, which is the one
    outcome worse than not firing at all.
    """
    base = [_plan_run(_ahead(2), 60), _plan_run(_ahead(4), 30)]
    user_id = await _create_athlete(
        "durability-direction@example.com", base, run_days=[3, 10, 17, 24], minutes=30.0
    )
    proposal = [_plan_run(_ahead(2), 40), _plan_run(_ahead(4), 150)]
    result = await _commit(user_id, proposal, "nightly_maintenance")
    assert _minutes_on(result.plan, _ahead(2)) == 40
    assert _minutes_on(result.plan, _ahead(4)) == 30


@pytest.mark.asyncio
async def test_a_cycling_only_write_does_not_read_the_run_history(monkeypatch):
    """Every finding names a planned run, so with no run there is nothing to find.

    The query is not free — it reads 200 rows and dedupes them — and the nightly
    job runs for every athlete. A guard that cannot change the outcome should not
    be asked.
    """
    base = [_plan_ride(_ahead(2), 120)]
    user_id = await _create_athlete(
        "durability-noquery@example.com", base, run_days=[3, 10], minutes=30.0
    )

    async def _refuse(*args, **kwargs):
        raise AssertionError("run history must not be read for a cycling-only week")

    monkeypatch.setattr(plan_context, "athlete_run_exposure", _refuse)
    result = await _commit(user_id, [_plan_ride(_ahead(2), 180)], "nightly_maintenance")
    assert _minutes_on(result.plan, _ahead(2)) == 180


@pytest.mark.asyncio
async def test_a_cycling_write_is_never_touched_by_the_running_ceiling():
    base = [_plan_ride(_ahead(2), 120)]
    user_id = await _create_athlete(
        "durability-cycling@example.com", base, run_days=[3, 10, 17, 24], minutes=30.0
    )
    result = await _commit(user_id, [_plan_ride(_ahead(2), 360)], "nightly_maintenance")
    assert _minutes_on(result.plan, _ahead(2)) == 360


# ---------------------------------------------------------------------------
# Wiring: what reaches a plan writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pure_cyclist_carries_no_paragraph_about_tibias():
    plan = [_plan_ride(_ahead(2))]
    user_id = await _create_athlete(
        "durability-quiet@example.com", plan, run_days=[]
    )
    async with TestSessionLocal() as db:
        context = await plan_context.plan_writer_context(db, user_id, plan)
    assert "Running durability" not in context.coherence


@pytest.mark.asyncio
async def test_a_cyclist_with_a_run_in_the_plan_is_given_the_beginner_ceiling():
    """The acceptance criterion, end to end through the prompt the planner reads."""
    plan = [_plan_ride(_ahead(1)), _plan_run(_ahead(3), 90)]
    user_id = await _create_athlete(
        "durability-firstrun@example.com", plan, run_days=[]
    )
    async with TestSessionLocal() as db:
        context = await plan_context.plan_writer_context(db, user_id, plan)
    assert "Running durability" in context.coherence
    assert "beginner" in context.coherence
    assert f"{int(run_durability.BEGINNER_WEEKLY_MINUTES)} min" in context.coherence


@pytest.mark.asyncio
async def test_an_athlete_with_run_history_is_given_their_own_figures():
    plan = [_plan_ride(_ahead(2))]
    user_id = await _create_athlete(
        "durability-history@example.com", plan, run_days=[2, 6, 9, 13], minutes=45.0
    )
    async with TestSessionLocal() as db:
        context = await plan_context.plan_writer_context(db, user_id, plan)
    assert "Running exposure, last 28 days: 4 runs" in context.coherence
