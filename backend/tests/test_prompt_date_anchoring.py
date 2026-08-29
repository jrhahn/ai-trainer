"""Guardrail: every plan-consuming LLM prompt must anchor session timing.

The dashboard "next session is today" bug (#462) came from handing the model a
bare training plan with only an ISO date, so it computed weekdays/relative days
from memory. #463 fixed the login summary; this suite locks in the *generic*
fix (#464): every builder that shows the athlete's plan must inject the
authoritative date context AND weekday/dateLabel/relativeDay anchors, so a new
builder cannot silently reintroduce the bug.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from services import prompts
from services.dates import (
    annotate_plan_days,
    plan_day_date_labels,
    plan_window_calendar,
)

TZ = "Europe/Berlin"
# 2026-07-25 is a Saturday; anchored one day after the fixed "today" below.
PLAN = [
    {
        "date": "2026-07-25",
        "title": "Long Aerobic Base Build",
        "workoutType": "endurance",
        "durationMinutes": 180,
        "description": "Steady Zone 2.",
    }
]
FIXED_TODAY = datetime.date(2026, 7, 24)  # Friday


def _ride(**overrides) -> SimpleNamespace:
    base = dict(
        activity_name="Morning ride",
        activity_date="2026-07-23",
        ride_purpose="endurance",
        sport_type="cycling",
        duration_seconds=3600,
        user_note="legs felt good",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _all_plan_builder_messages() -> list[tuple[str, str]]:
    """Build one user message per plan-consuming builder, each given PLAN."""
    return [
        (
            "analyse_activities_user",
            prompts.analyse_activities_user(
                [{"type": "Ride"}],
                "",
                "",
                sport_type="cycling",
                training_plan=PLAN,
                timezone_name=TZ,
            ),
        ),
        (
            "batch_review_user",
            prompts.batch_review_user(
                [_ride()], profile={"estimatedFTP": 250}, training_plan=PLAN, timezone_name=TZ
            ),
        ),
        (
            "next_ride_recommendation_user",
            prompts.next_ride_recommendation_user(
                rides=[_ride()], plan=PLAN, timezone_name=TZ
            ),
        ),
        (
            "process_pending_feedbacks_user",
            prompts.process_pending_feedbacks_user(
                [_ride()], assessment=None, training_plan=PLAN, timezone_name=TZ
            ),
        ),
        (
            "training_status_user",
            prompts.training_status_user(
                {"sessions": [dict(PLAN[0], status="done")]},
                training_plan=PLAN,
                timezone_name=TZ,
            ),
        ),
    ]


@pytest.fixture()
def _fixed_today(monkeypatch):
    """Pin the app clock so relativeDay anchoring is deterministic in tests."""
    monkeypatch.setattr(prompts, "app_today", lambda timezone_name=None: FIXED_TODAY)
    monkeypatch.setattr(
        prompts, "app_today_iso", lambda timezone_name=None: FIXED_TODAY.isoformat()
    )


@pytest.mark.parametrize(
    "name",
    [
        "analyse_activities_user",
        "batch_review_user",
        "next_ride_recommendation_user",
        "process_pending_feedbacks_user",
        "training_status_user",
    ],
)
def test_plan_builders_inject_date_context(name, _fixed_today):
    messages = dict(_all_plan_builder_messages())
    msg = messages[name]
    # Authoritative local-date block from app_date_context().
    assert "Current local date context" in msg
    # Weekday anchor for the plan day (2026-07-25 → Saturday) — never a bare date.
    assert "Saturday" in msg
    # Relative-day anchor so the model never calls a future session "today".
    assert "tomorrow" in msg


# ---------------------------------------------------------------------------
# Prompts that WRITE a plan (#625)
#
# The builders above are handed a plan to read, so annotating it anchors them.
# These two invent the dates, so there is nothing to annotate — they were left
# deriving weekdays from a bare "Today's date" line, and the coach put a "long
# weekend ride" on a Monday.
# ---------------------------------------------------------------------------

# 2026-08-31 is a Monday, so a 14-day window from it spans two full weekends.
MONDAY = "2026-08-31"


def _plan_producer_messages() -> dict[str, str]:
    return {
        "generate_plan_user": prompts.generate_plan_user(
            {"name": "Jonas"}, MONDAY, "", timezone_name=TZ
        ),
        "adapt_plan_user": prompts.adapt_plan_user(
            {"name": "Jonas"},
            MONDAY,
            recent_feedback=[],
            incomplete_days=[dict(PLAN[0], date="2026-09-05")],
            timezone_name=TZ,
        ),
    }


@pytest.mark.parametrize("name", ["generate_plan_user", "adapt_plan_user"])
def test_plan_producers_are_given_the_weekday_of_every_date_they_may_fill(name):
    msg = _plan_producer_messages()[name]

    assert "Current local date context" in msg
    for iso, weekday in [
        ("2026-08-31", "Monday"),
        ("2026-09-03", "Thursday"),
        ("2026-09-13", "Sunday"),
    ]:
        assert f"{iso} ({weekday}" in msg, f"{iso} reaches the model without its weekday"


@pytest.mark.parametrize("name", ["generate_plan_user", "adapt_plan_user"])
def test_plan_producers_are_told_which_days_are_the_weekend(name):
    """"Saturday" only rules out a weekday ride if the model knows it is the weekend."""
    msg = _plan_producer_messages()[name]

    assert "2026-09-05 (Saturday, weekend)" in msg
    assert "2026-09-06 (Sunday, weekend)" in msg
    assert "2026-09-04 (Friday)" in msg
    assert "2026-09-04 (Friday, weekend)" not in msg


@pytest.mark.parametrize("builder", ["generate_plan_system", "adapt_plan_system"])
def test_plan_producers_are_forbidden_from_misnaming_the_week(builder):
    system = getattr(prompts, builder)().lower()

    assert "weekend ride" in system, "the rule does not name the mistake it prevents"
    assert "part of the week it does not fall in" in system


def test_the_adapt_prompt_annotates_the_days_it_rewrites():
    """Rescheduling a stale session is where a weekday gets invented."""
    msg = prompts.adapt_plan_user(
        {"name": "Jonas"},
        MONDAY,
        recent_feedback=[],
        # A Saturday session that was never done, to be moved somewhere.
        incomplete_days=[dict(PLAN[0], date="2026-08-29")],
        timezone_name=TZ,
    )

    assert '"weekday": "Saturday"' in msg


def test_a_malformed_today_costs_the_calendar_not_the_plan():
    """The date arrives preformatted; a bad one must not take the request down."""
    msg = prompts.generate_plan_user({"name": "Jonas"}, "not-a-date", "", timezone_name=TZ)

    assert "The dates you are planning" not in msg
    assert "Generate a 14-day training plan" in msg


def test_the_horizon_is_stated_once():
    """The prompt text, the calendar and the ask must not drift apart."""
    system = prompts.generate_plan_system()
    msg = prompts.generate_plan_user({"name": "Jonas"}, MONDAY, "", timezone_name=TZ)

    assert f"{prompts.PLAN_HORIZON_DAYS}-day training plan" in system
    assert f"{prompts.PLAN_HORIZON_DAYS}-day training plan" in msg
    # One line per day in the window, and no more.
    assert msg.count("\n- 2026-") == prompts.PLAN_HORIZON_DAYS


def test_plan_day_date_labels_relative_days():
    today = FIXED_TODAY
    assert plan_day_date_labels("2026-07-24", today)["relativeDay"] == "today"
    assert plan_day_date_labels("2026-07-25", today)["relativeDay"] == "tomorrow"
    assert plan_day_date_labels("2026-07-23", today)["relativeDay"] == "yesterday"
    # Weekday + label are present even without a reference "today".
    labels = plan_day_date_labels("2026-07-25")
    assert labels["weekday"] == "Saturday"
    assert labels["dateLabel"] == "Saturday, July 25, 2026"
    assert "relativeDay" not in labels
    # Days far from today carry no relativeDay tag.
    assert "relativeDay" not in plan_day_date_labels("2026-08-01", today)


def test_plan_window_calendar_covers_the_window_exactly():
    calendar = plan_window_calendar(datetime.date(2026, 8, 31), 3)

    assert calendar.splitlines()[1:] == [
        "- 2026-08-31 (Monday)",
        "- 2026-09-01 (Tuesday)",
        "- 2026-09-02 (Wednesday)",
    ]


def test_plan_window_calendar_survives_a_zero_or_negative_window():
    assert plan_window_calendar(datetime.date(2026, 8, 31), 0).count("- 2026") == 0
    assert plan_window_calendar(datetime.date(2026, 8, 31), -3).count("- 2026") == 0


def test_plan_day_date_labels_handles_missing_and_bad_dates():
    assert plan_day_date_labels(None) == {}
    assert plan_day_date_labels("") == {}
    assert plan_day_date_labels("not-a-date") == {}


def test_annotate_plan_days_passthrough_and_merge():
    assert annotate_plan_days(None) is None
    assert annotate_plan_days([]) == []
    annotated = annotate_plan_days(PLAN, FIXED_TODAY)
    assert annotated[0]["title"] == "Long Aerobic Base Build"  # original fields kept
    assert annotated[0]["weekday"] == "Saturday"
    assert annotated[0]["relativeDay"] == "tomorrow"
    # Pure: input plan is not mutated.
    assert "weekday" not in PLAN[0]
