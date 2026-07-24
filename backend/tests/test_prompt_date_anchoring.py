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
from services.dates import annotate_plan_days, plan_day_date_labels

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
