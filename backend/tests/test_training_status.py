"""The deterministic audit behind the dashboard status badge (#499).

The badge used to be a browser-side ``done / due`` ratio that contradicted both
itself and the coach. These tests pin the three ways it was wrong — ambiguous
two-a-day matches, optional sessions counted as obligations, unplanned work
ignored — plus the production case that surfaced the bug.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

from services import training_status as ts

TODAY = datetime.date(2026, 7, 30)


def _day(date: str, workout_type: str = "intervals", **overrides) -> dict:
    day = {
        "date": date,
        "workoutType": workout_type,
        "title": overrides.pop("title", "VO2 Max Intervals"),
        "durationMinutes": overrides.pop("durationMinutes", 60),
    }
    day.update(overrides)
    return day


def _ride(date: str, **overrides) -> SimpleNamespace:
    base = dict(
        activity_date=date,
        sport_type="cycling",
        plan_match_status="unmatched",
        matched_plan_date=None,
        matched_plan_slot=None,
        activity_name="Ride",
        duration_seconds=3600,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _by_date(facts: dict, date: str, slot: int = 0) -> dict:
    return next(
        s for s in facts["sessions"] if s["date"] == date and s["slot"] == slot
    )


# ---------------------------------------------------------------------------
# Defect 1 — ambiguous matches (two-a-days)
# ---------------------------------------------------------------------------


def test_ambiguous_match_counts_as_done():
    """A two-a-day the matcher could not split is still work that happened.

    ``ambiguous`` means "you trained on this date, but we cannot tell which
    session it was" — evidence of work, not of a miss. The old browser heuristic
    accepted only auto/manual matches, so it dropped the day and simultaneously
    rendered "… completed" and "Slightly behind" about the same session.
    """
    plan = [_day("2026-07-29", "strength", title="Core Strength")]
    rides = [
        _ride(
            "2026-07-29",
            plan_match_status="ambiguous",
            matched_plan_date="2026-07-29",
            activity_name="Krafttraining",
        )
    ]

    facts = ts.build_status_facts(plan, rides, TODAY)

    assert _by_date(facts, "2026-07-29")["status"] == ts.STATUS_DONE
    assert facts["plannedDone"] == 1
    assert facts["missed"] == []


def test_two_sessions_on_one_date_are_credited_separately():
    """Both halves of a two-a-day get their own credit, keyed by ``(date, slot)``."""
    plan = [
        _day("2026-07-29", "strength", slot=0, title="AM Strength"),
        _day("2026-07-29", "endurance", slot=1, title="PM Endurance"),
    ]
    rides = [
        _ride(
            "2026-07-29",
            plan_match_status="auto_matched",
            matched_plan_date="2026-07-29",
            matched_plan_slot=1,
            activity_name="Evening Ride",
        ),
        _ride("2026-07-29", plan_match_status="ambiguous", activity_name="Krafttraining"),
    ]

    facts = ts.build_status_facts(plan, rides, TODAY)

    assert _by_date(facts, "2026-07-29", slot=0)["status"] == ts.STATUS_DONE
    assert _by_date(facts, "2026-07-29", slot=1)["status"] == ts.STATUS_DONE
    assert facts["plannedDue"] == 2
    assert facts["plannedDone"] == 2


def test_one_activity_cannot_cover_two_planned_sessions():
    """Credit is per activity: a single ride does not clear a whole two-a-day."""
    plan = [
        _day("2026-07-29", "strength", slot=0, title="AM Strength"),
        _day("2026-07-29", "endurance", slot=1, title="PM Endurance"),
    ]
    rides = [_ride("2026-07-29", plan_match_status="ambiguous")]

    facts = ts.build_status_facts(plan, rides, TODAY)

    statuses = sorted(s["status"] for s in facts["sessions"])
    assert statuses == [ts.STATUS_DONE, ts.STATUS_MISSED]
    assert facts["plannedDue"] == 2
    assert facts["plannedDone"] == 1


# ---------------------------------------------------------------------------
# Defect 2 — optional sessions
# ---------------------------------------------------------------------------


def test_declined_optional_session_is_not_a_miss():
    """Skipping what the plan called optional is compliance, not a shortfall."""
    plan = [
        _day("2026-07-28", "recovery", title="Optional Easy Recovery Spin"),
        _day("2026-07-29", "intervals", title="VO2 Max Intervals"),
    ]
    rides = [
        _ride(
            "2026-07-29",
            plan_match_status="auto_matched",
            matched_plan_date="2026-07-29",
        )
    ]

    facts = ts.build_status_facts(plan, rides, TODAY)

    assert _by_date(facts, "2026-07-28")["status"] == ts.STATUS_SKIPPED_OPTIONAL
    assert facts["missed"] == []
    # The optional session is reported, but never counted against adherence.
    assert facts["plannedDue"] == 1
    assert facts["adherence"] == 1.0
    assert len(facts["skippedOptional"]) == 1


def test_explicit_optional_flag_beats_the_title():
    plan = [_day("2026-07-28", "endurance", title="Base Ride", optional=True)]

    facts = ts.build_status_facts(plan, [], TODAY)

    assert _by_date(facts, "2026-07-28")["status"] == ts.STATUS_SKIPPED_OPTIONAL


def test_completed_optional_session_still_earns_credit():
    plan = [_day("2026-07-28", "recovery", title="Optional Spin")]
    rides = [
        _ride(
            "2026-07-28",
            plan_match_status="auto_matched",
            matched_plan_date="2026-07-28",
        )
    ]

    facts = ts.build_status_facts(plan, rides, TODAY)

    assert _by_date(facts, "2026-07-28")["status"] == ts.STATUS_DONE
    assert facts["plannedDone"] == 1


# ---------------------------------------------------------------------------
# Defect 3 — unplanned work
# ---------------------------------------------------------------------------


def test_unplanned_activity_is_reported_not_discarded():
    """A ride on a day with no plan entry is work done, and must be visible."""
    plan = [_day("2026-07-29")]
    rides = [
        _ride(
            "2026-07-29",
            plan_match_status="auto_matched",
            matched_plan_date="2026-07-29",
        ),
        _ride("2026-07-25", activity_name="Darmstadt Road Cycling", duration_seconds=13622),
    ]

    facts = ts.build_status_facts(plan, rides, TODAY)

    extra = [e for e in facts["extraActivities"] if e["date"] == "2026-07-25"]
    assert extra and extra[0]["name"] == "Darmstadt Road Cycling"
    assert extra[0]["durationMinutes"] == 227
    assert extra[0]["plannedSession"] is False


# ---------------------------------------------------------------------------
# Window edges
# ---------------------------------------------------------------------------


def test_todays_unfinished_session_is_pending_not_missed():
    """A session still ahead of the athlete today cannot drag the badge down."""
    plan = [_day(TODAY.isoformat(), "intervals")]

    facts = ts.build_status_facts(plan, [], TODAY)

    assert _by_date(facts, TODAY.isoformat())["status"] == ts.STATUS_PENDING
    assert facts["plannedDue"] == 0
    assert facts["adherence"] is None


def test_rest_days_and_zero_duration_days_are_not_sessions():
    plan = [
        _day("2026-07-29", "rest", title="Complete Rest Day", durationMinutes=0),
        _day("2026-07-28", "endurance", durationMinutes=0),
    ]

    facts = ts.build_status_facts(plan, [], TODAY)

    assert facts["sessions"] == []
    assert facts["adherence"] is None


def test_window_excludes_days_outside_the_trailing_week():
    plan = [_day("2026-07-20", "intervals"), _day("2026-07-29", "intervals")]

    facts = ts.build_status_facts(plan, [], TODAY)

    assert facts["windowStart"] == "2026-07-24"
    assert [s["date"] for s in facts["sessions"]] == ["2026-07-29"]


def test_athlete_marked_completion_counts_without_any_activity():
    """Hand-ticked days still count — not every sport syncs an activity."""
    plan = [_day("2026-07-29", "strength", completed=True)]

    facts = ts.build_status_facts(plan, [], TODAY)

    assert _by_date(facts, "2026-07-29")["status"] == ts.STATUS_DONE


# ---------------------------------------------------------------------------
# The production case that surfaced the bug
# ---------------------------------------------------------------------------


def test_production_week_reads_on_track_not_slightly_behind():
    """The exact plan/activity data that rendered a false "Slightly behind".

    Every non-optional session was done — one of them a two-a-day that matched
    ambiguously — and four unplanned sessions were logged on top.
    """
    plan = [
        _day("2026-07-24", "rest", title="Complete Rest Day", durationMinutes=0),
        _day("2026-07-26", "recovery", title="Short MTB Melibokus Flow", durationMinutes=90),
        _day("2026-07-27", "rest", title="Complete Rest Day", durationMinutes=0),
        _day("2026-07-28", "intervals", title="VO2 Max Intervals", durationMinutes=100),
        _day("2026-07-29", "recovery", title="Optional Easy Recovery Spin", durationMinutes=45),
        _day("2026-07-30", "strength", title="Core and Upper Body Strength", durationMinutes=40),
    ]
    rides = [
        _ride("2026-07-24", sport_type="Yoga", activity_name="Yoga", duration_seconds=2384),
        _ride("2026-07-25", activity_name="Darmstadt Road Cycling", duration_seconds=13622),
        _ride(
            "2026-07-26",
            plan_match_status="auto_matched",
            matched_plan_date="2026-07-26",
            activity_name="Darmstadt Mountain Biking",
        ),
        _ride("2026-07-27", sport_type="WeightTraining", activity_name="Krafttraining"),
        _ride(
            "2026-07-28",
            plan_match_status="auto_matched",
            matched_plan_date="2026-07-28",
            activity_name="Darmstadt Mountain Biking",
        ),
        _ride(
            "2026-07-30",
            sport_type="WeightTraining",
            plan_match_status="ambiguous",
            matched_plan_date="2026-07-30",
            activity_name="Krafttraining",
        ),
        _ride(
            "2026-07-30",
            sport_type="MountainBikeRide",
            plan_match_status="ambiguous",
            matched_plan_date="2026-07-30",
            activity_name="Darmstadt Mountain Biking",
        ),
    ]

    facts = ts.build_status_facts(plan, rides, TODAY)

    assert facts["missed"] == []
    assert facts["adherence"] == 1.0
    assert facts["plannedDone"] == facts["plannedDue"] == 3
    assert len(facts["extraActivities"]) == 4

    label, tone, _ = ts.fallback_status(facts)
    assert (label, tone) == ("On track", "positive")


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------


def test_fallback_reports_no_signal_when_nothing_fell_due():
    label, tone, rationale = ts.fallback_status(ts.build_status_facts([], [], TODAY))

    assert label == "No sessions due"
    assert tone == "steady"
    assert "adherence signal" in rationale


def test_fallback_flags_a_genuine_shortfall():
    plan = [_day(f"2026-07-2{d}", "intervals") for d in (6, 7, 8, 9)]

    facts = ts.build_status_facts(plan, [], TODAY)
    label, tone, _ = ts.fallback_status(facts)

    assert facts["plannedDue"] == 4
    assert (label, tone) == ("Behind plan", "caution")
