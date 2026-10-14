"""Planning priors (ai-trainer-ops#26): reported, never enforced.

The examples pin what each prior reports. The properties pin what makes a
prior a prior rather than a rule or noise: a plan inside every prior gets no
finding, and each departure is reported exactly where it is.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

import models
from services import plan_context, plan_priors
from services.dates import app_today
from services.plan_priors import (
    PRIOR_RAMP,
    PRIOR_STATED_HOURS,
    PRIOR_TAPER_INTENSITY,
    PRIOR_TAPER_VOLUME,
    PRIOR_TRAINING_DAYS,
    WEEKDAYS,
    find_prior_departures,
    plan_priors_section,
)
from tests.conftest import TestSessionLocal

TODAY = date(2026, 6, 15)  # a Monday


def _day(offset: int, minutes: int = 0, workout_type: str | None = None) -> dict:
    kind = workout_type or ("endurance" if minutes else "rest")
    return {
        "date": (TODAY + timedelta(days=offset)).isoformat(),
        "workoutType": kind,
        "durationMinutes": minutes,
    }


def _plan(minutes_by_offset: dict[int, int], days: int = 14) -> list[dict]:
    return [_day(offset, minutes_by_offset.get(offset, 0)) for offset in range(days)]


def _priors(findings) -> list[str]:
    return [f.prior for f in findings]


def test_a_week_over_the_stated_hours_is_reported_with_its_hours():
    plan = _plan({1: 120, 3: 120, 5: 180, 6: 120})  # 9 h in week one
    findings = find_prior_departures(plan, TODAY, weekly_hours=6)
    assert _priors(findings) == [PRIOR_STATED_HOURS]
    assert "9.0 h" in findings[0].statement and "6 h" in findings[0].statement


def test_ten_minutes_over_the_stated_hours_is_rounding_not_a_finding():
    plan = _plan({1: 120, 3: 120, 5: 130})  # 6 h 10 min
    assert find_prior_departures(plan, TODAY, weekly_hours=6) == []


def test_a_session_on_a_day_the_athlete_does_not_train_is_named():
    plan = _plan({0: 60, 1: 60, 5: 90})
    findings = find_prior_departures(plan, TODAY, training_days=["tuesday", "saturday"])
    assert _priors(findings) == [PRIOR_TRAINING_DAYS]
    assert "2026-06-15 (monday)" in findings[0].statement


def test_past_days_are_history_not_a_departure():
    plan = [_day(-1, 60), *_plan({1: 60})]  # Sunday before today
    assert find_prior_departures(plan, TODAY, training_days=["tuesday"]) == []


def test_a_taper_that_barely_cuts_volume_is_reported():
    race = TODAY + timedelta(days=14)
    plan = _plan({**{d: 60 for d in range(7)}, **{d: 55 for d in range(7, 14)}})
    plan[10]["workoutType"] = "intervals"
    findings = find_prior_departures(plan, TODAY, race_dates=[race.isoformat()])
    assert _priors(findings) == [PRIOR_TAPER_VOLUME]
    assert "40-60 %" in findings[0].statement


def test_a_taper_that_drops_all_intensity_is_reported_even_with_volume_cut():
    race = TODAY + timedelta(days=14)
    plan = _plan({**{d: 90 for d in range(7)}, **{d: 30 for d in range(7, 14)}})
    findings = find_prior_departures(plan, TODAY, race_dates=[race.isoformat()])
    assert _priors(findings) == [PRIOR_TAPER_INTENSITY]


def test_a_good_taper_is_silent():
    race = TODAY + timedelta(days=14)
    plan = _plan({**{d: 90 for d in range(7)}, **{d: 40 for d in range(7, 14)}})
    plan[11]["workoutType"] = "intervals"
    assert find_prior_departures(plan, TODAY, race_dates=[race.isoformat()]) == []


def test_a_race_beyond_the_plan_is_not_judged():
    race = TODAY + timedelta(days=60)
    assert find_prior_departures(_plan({1: 60}), TODAY, race_dates=[race]) == []


def test_only_a_wide_ramp_is_reported_and_it_says_heuristic():
    plan = _plan({1: 120, 3: 120, 5: 180, 6: 120})  # 9 h
    assert find_prior_departures(plan, TODAY, recent_weekly_hours=(7, 7, 7, 7)) == []
    findings = find_prior_departures(plan, TODAY, recent_weekly_hours=(5, 5, 5, 5))
    assert _priors(findings) == [PRIOR_RAMP]
    assert findings[0].evidence == plan_priors.EVIDENCE_HEURISTIC


def test_no_history_means_no_ramp_judgement():
    plan = _plan({1: 600})
    assert find_prior_departures(plan, TODAY, recent_weekly_hours=(0, 0, 1, 1)) == []
    assert find_prior_departures(plan, TODAY, recent_weekly_hours=(5, 5)) == []


def test_the_section_leaves_the_decision_to_the_coach():
    section = plan_priors_section(
        find_prior_departures(_plan({0: 600}), TODAY, weekly_hours=4)
    )
    assert "not rules" in section
    assert "keep it and tell the athlete why" in section
    assert plan_priors_section([]) == ""


@pytest.mark.asyncio
async def test_the_athletes_stated_hours_reach_every_plan_writer(client, auth_headers):
    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"weeklyHours": 3, "trainingDays": ["saturday"]},
    )
    today = app_today()
    plan = [
        {
            "date": (today + timedelta(days=offset)).isoformat(),
            "workoutType": "endurance",
            "durationMinutes": 60,
        }
        for offset in range(7)
    ]

    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        context = await plan_context.plan_writer_context(db, user.id, plan)

    assert "the athlete said they can give 3 h a week" in context.coherence
    assert "the athlete said they train on saturday" in context.coherence


@pytest.mark.asyncio
async def test_the_longest_recorded_ride_reaches_every_plan_writer(client, auth_headers):
    today = app_today()
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        db.add(
            models.RideMetric(
                user_id=user.id,
                strava_activity_id=71001,
                activity_source="strava",
                external_activity_id="71001",
                activity_date=(today - timedelta(days=5)).isoformat(),
                activity_name="Saturday loop",
                sport_type="Ride",
                duration_seconds=2 * 3600,
            )
        )
        await db.commit()
        plan = [
            {
                "date": (today + timedelta(days=2)).isoformat(),
                "workoutType": "endurance",
                "durationMinutes": 300,
            }
        ]
        context = await plan_context.plan_writer_context(db, user.id, plan)

    assert "against 2.0 h for the longest ride of the last 8 weeks" in context.coherence


# --- properties --------------------------------------------------------------

_SETTINGS = settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)

minutes_strategy = st.lists(
    st.sampled_from([0, 0, 30, 45, 60, 90, 120]), min_size=14, max_size=14
)
days_strategy = st.lists(st.sampled_from(WEEKDAYS), min_size=1, max_size=7, unique=True)


@_SETTINGS
@given(minutes=minutes_strategy, days=days_strategy)
def test_a_plan_inside_the_athletes_own_priors_gets_no_finding(minutes, days):
    plan = [
        _day(
            offset,
            m if WEEKDAYS[(TODAY + timedelta(days=offset)).weekday()] in days else 0,
        )
        for offset, m in enumerate(minutes)
    ]
    week_hours = (
        max(
            sum(plan_priors.planned_minutes(d) for d in plan[0:7]),
            sum(plan_priors.planned_minutes(d) for d in plan[7:14]),
        )
        / 60
    )
    findings = find_prior_departures(
        plan, TODAY, weekly_hours=max(week_hours, 1), training_days=days
    )
    assert findings == []


@_SETTINGS
@given(minutes=minutes_strategy, days=days_strategy, extra=st.integers(0, 13))
def test_a_session_on_a_non_training_day_is_reported_with_that_date(
    minutes, days, extra
):
    when = TODAY + timedelta(days=extra)
    if WEEKDAYS[when.weekday()] in days:
        return
    plan = [_day(offset, m) for offset, m in enumerate(minutes)]
    plan[extra] = _day(extra, 45)
    findings = [
        f
        for f in find_prior_departures(plan, TODAY, training_days=days)
        if f.prior == PRIOR_TRAINING_DAYS
    ]
    assert len(findings) == 1
    assert when.isoformat() in findings[0].statement


@_SETTINGS
@given(minutes=minutes_strategy, hours=st.integers(1, 20))
def test_stated_hours_findings_are_exactly_the_weeks_over_the_limit(minutes, hours):
    plan = [_day(offset, m) for offset, m in enumerate(minutes)]
    over = [
        sum(minutes[start : start + 7])
        > hours * 60 + plan_priors.STATED_HOURS_SLACK_MINUTES
        for start in (0, 7)
    ]
    findings = [
        f
        for f in find_prior_departures(plan, TODAY, weekly_hours=hours)
        if f.prior == PRIOR_STATED_HOURS
    ]
    assert len(findings) == sum(over)


@_SETTINGS
@given(minutes=minutes_strategy)
def test_without_any_athlete_input_nothing_is_reported(minutes):
    plan = [_day(offset, m) for offset, m in enumerate(minutes)]
    assert find_prior_departures(plan, TODAY) == []


# --- back-to-back hard days --------------------------------------------------


def test_two_hard_days_in_a_row_are_reported_as_a_heuristic():
    plan = _plan({})
    plan[2] = _day(2, 60, "intervals")
    plan[3] = _day(3, 60, "threshold")
    findings = find_prior_departures(plan, TODAY)
    assert _priors(findings) == [plan_priors.PRIOR_BACK_TO_BACK]
    assert findings[0].evidence == plan_priors.EVIDENCE_HEURISTIC
    assert plan[2]["date"] in findings[0].statement
    assert plan[3]["date"] in findings[0].statement


def test_an_easy_day_between_hard_days_is_silent():
    plan = _plan({})
    plan[2] = _day(2, 60, "intervals")
    plan[3] = _day(3, 90, "endurance")
    plan[4] = _day(4, 60, "vo2max")
    assert find_prior_departures(plan, TODAY) == []


def test_hard_days_in_the_past_are_history():
    plan = [_day(-2, 60, "intervals"), _day(-1, 60, "intervals"), *_plan({})]
    assert find_prior_departures(plan, TODAY) == []


def test_a_bogus_far_future_date_is_ignored_not_an_overflow():
    plan = [*_plan({}), {"date": "9999-12-31", "workoutType": "intervals", "durationMinutes": 60}]
    assert find_prior_departures(plan, TODAY) == []


@_SETTINGS
@given(hard=st.lists(st.booleans(), min_size=14, max_size=14))
def test_back_to_back_findings_name_exactly_the_consecutive_hard_days(hard):
    plan = [
        _day(offset, 60, "intervals" if is_hard else "endurance")
        for offset, is_hard in enumerate(hard)
    ]
    findings = [
        f
        for f in find_prior_departures(plan, TODAY)
        if f.prior == plan_priors.PRIOR_BACK_TO_BACK
    ]
    starts = [i for i in range(13) if hard[i] and hard[i + 1]]
    assert len(findings) == (1 if starts else 0)
    for i in starts:
        pair = f"{plan[i]['date']} and {plan[i + 1]['date']}"
        assert pair in findings[0].statement


# --- ride length (ops#81, Q23) -------------------------------------------------

from types import SimpleNamespace  # noqa: E402


def _ride(days_ago: int, minutes: float, sport: str = "Ride"):
    return SimpleNamespace(
        activity_date=TODAY - timedelta(days=days_ago),
        sport_type=sport,
        duration_seconds=minutes * 60,
    )


def test_a_ride_well_beyond_the_longest_recent_one_is_a_heuristic_finding():
    plan = _plan({3: 300, 5: 270})
    findings = find_prior_departures(plan, TODAY, longest_ride=180)
    assert _priors(findings) == [plan_priors.PRIOR_RIDE_LENGTH]
    assert findings[0].evidence == plan_priors.EVIDENCE_HEURISTIC
    assert f"{plan[3]['date']} (5.0 h)" in findings[0].statement
    assert plan[5]["date"] not in findings[0].statement  # 1.5 x 3 h is the line
    assert "3.0 h" in findings[0].statement


def test_short_histories_are_held_to_the_floor_not_the_ratio():
    assert find_prior_departures(_plan({2: 85}), TODAY, longest_ride=40) == []
    assert _priors(find_prior_departures(_plan({2: 100}), TODAY, longest_ride=40)) == [
        plan_priors.PRIOR_RIDE_LENGTH
    ]


def test_a_ride_given_only_as_a_window_is_judged_by_its_upper_bound():
    plan = _plan({})
    plan[2] = {
        "date": plan[2]["date"],
        "workoutType": "endurance",
        "durationMinMinutes": 240,
        "durationMaxMinutes": 300,
    }
    findings = find_prior_departures(plan, TODAY, longest_ride=120)
    assert f"{plan[2]['date']} (5.0 h)" in findings[0].statement


def test_no_ride_in_the_window_means_no_ride_length_judgement():
    assert find_prior_departures(_plan({2: 400}), TODAY, longest_ride=None) == []


def test_a_long_run_is_not_judged_as_a_ride():
    plan = _plan({})
    plan[2] = {**_day(2, 200), "sport": "running"}
    assert find_prior_departures(plan, TODAY, longest_ride=60) == []


def test_the_longest_ride_counts_only_recent_rides_before_today():
    rows = [
        _ride(3, 120),
        _ride(10, 150),
        _ride(5, 300, sport="Run"),  # not a ride
        _ride(60, 400),  # older than eight weeks
        _ride(0, 500),  # today is not history yet
    ]
    assert plan_priors.longest_ride_minutes(rows, TODAY) == 150
    assert plan_priors.longest_ride_minutes([], TODAY) is None


@_SETTINGS
@given(minutes=minutes_strategy, longest=st.integers(10, 240))
def test_ride_length_is_reported_exactly_when_a_ride_passes_the_line(minutes, longest):
    plan = [_day(offset, m) for offset, m in enumerate(minutes)]
    line = max(1.5 * longest, plan_priors.RIDE_LENGTH_FLOOR_MINUTES)
    findings = [
        f
        for f in find_prior_departures(plan, TODAY, longest_ride=longest)
        if f.prior == plan_priors.PRIOR_RIDE_LENGTH
    ]
    assert len(findings) == (1 if any(m > line for m in minutes) else 0)


# --- race eve (ops#81, Q25) ----------------------------------------------------


def _race_eve(findings):
    return [f for f in findings if f.prior == plan_priors.PRIOR_RACE_EVE]


def test_a_long_ride_the_day_before_a_race_is_a_heuristic_finding():
    # ops#76, race-in-four-days: four hours on the eve, a window no weekly taper
    # prior can see.
    race = TODAY + timedelta(days=4)
    plan = _plan({2: 90, 3: 240})
    plan[2]["workoutType"] = "vo2max"
    findings = find_prior_departures(plan, TODAY, race_dates=[race.isoformat()])
    assert _priors(findings) == [plan_priors.PRIOR_RACE_EVE]
    assert findings[0].evidence == plan_priors.EVIDENCE_HEURISTIC
    assert f"{plan[3]['date']} (4.0 h)" in findings[0].statement
    assert plan[2]["date"] not in findings[0].statement  # 90 minutes is the line
    assert race.isoformat() in findings[0].statement


def test_short_openers_before_a_race_are_silent():
    race = TODAY + timedelta(days=4)
    plan = _plan({2: 60, 3: 45})
    plan[3]["workoutType"] = "intervals"
    assert _race_eve(find_prior_departures(plan, TODAY, race_dates=[race])) == []


def test_a_session_given_as_a_window_is_judged_by_its_upper_bound_before_a_race():
    race = TODAY + timedelta(days=4)
    plan = _plan({})
    plan[3] = {
        "date": plan[3]["date"],
        "workoutType": "endurance",
        "durationMinMinutes": 60,
        "durationMaxMinutes": 120,
    }
    findings = _race_eve(find_prior_departures(plan, TODAY, race_dates=[race]))
    assert f"{plan[3]['date']} (2.0 h)" in findings[0].statement


def test_the_first_stage_of_a_stage_race_is_not_the_eve_of_the_second():
    first, second = TODAY + timedelta(days=3), TODAY + timedelta(days=4)
    plan = _plan({3: 180, 4: 180})
    plan[3]["workoutType"] = plan[4]["workoutType"] = "race"
    assert _race_eve(find_prior_departures(plan, TODAY, race_dates=[first, second])) == []


def test_an_eve_already_ridden_is_history():
    race = TODAY + timedelta(days=1)
    plan = [_day(-1, 240), *_plan({0: 45})]
    assert _race_eve(find_prior_departures(plan, TODAY, race_dates=[race])) == []


@_SETTINGS
@given(eve=st.tuples(st.integers(0, 400), st.integers(0, 400)), out=st.integers(0, 400))
def test_race_eve_is_reported_exactly_when_an_eve_session_passes_the_line(eve, out):
    race = TODAY + timedelta(days=6)
    plan = _plan({3: out, 4: eve[0], 5: eve[1]})  # day 3 is outside the eve
    findings = _race_eve(find_prior_departures(plan, TODAY, race_dates=[race]))
    line = plan_priors.RACE_EVE_MAX_MINUTES
    assert len(findings) == (1 if max(eve) > line else 0)
    if findings:
        assert plan[3]["date"] not in findings[0].statement
