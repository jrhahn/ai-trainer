"""Unit tests for services/availability.py."""

from __future__ import annotations

from datetime import date

from services.availability import extract_availability_constraints

TODAY = date(2026, 6, 3)  # a Wednesday


def test_empty_text_returns_no_constraints():
    assert extract_availability_constraints("", today=TODAY) == []
    assert extract_availability_constraints("   ", today=TODAY) == []


def test_requires_both_unavailable_and_training_context():
    # training context but no "unavailable" phrasing
    assert extract_availability_constraints("I love training", today=TODAY) == []
    # unavailable phrasing but no training context
    assert extract_availability_constraints("I am unavailable", today=TODAY) == []


def test_weekday_constraint_extracted():
    result = extract_availability_constraints(
        "I am unavailable for training on friday", today=TODAY
    )
    assert len(result) == 1
    assert result[0]["constraint_type"] == "no_training"
    assert result[0]["weekday"] == "friday"
    assert result[0]["constraint_date"] == result[0]["expires_on"]


def test_tomorrow_and_today_constraints():
    tomorrow = extract_availability_constraints(
        "unavailable for training tomorrow", today=TODAY
    )
    assert tomorrow[0]["constraint_date"] == "2026-06-04"

    today = extract_availability_constraints(
        "unavailable for training today", today=TODAY
    )
    assert today[0]["constraint_date"] == "2026-06-03"


def test_constraints_are_deduplicated_per_date():
    result = extract_availability_constraints(
        "unavailable for training monday and today", today=TODAY
    )
    dates = [r["constraint_date"] for r in result]
    assert len(dates) == len(set(dates))


def test_long_session_required_constraint_extracted():
    result = extract_availability_constraints(
        "lass uns eine lange einheit am samstag machen", today=TODAY
    )
    required = [r for r in result if r["constraint_type"] == "required_workout"]
    assert len(required) == 1
    assert required[0]["weekday"] == "saturday"
    assert required[0]["required_workout"]["workoutType"] == "endurance"
    assert required[0]["required_workout"]["minDurationMinutes"] == 120


def test_long_session_duration_is_parsed():
    result = extract_availability_constraints(
        "long ride on saturday, 3 hours", today=TODAY
    )
    required = [r for r in result if r["constraint_type"] == "required_workout"]
    assert required and required[0]["required_workout"]["minDurationMinutes"] == 180


def test_plain_training_mention_creates_no_required_constraint():
    # "training" alone is not a long-session phrase.
    result = extract_availability_constraints("I love training on saturday", today=TODAY)
    assert all(r["constraint_type"] != "required_workout" for r in result)


def test_german_phrasing_supported():
    result = extract_availability_constraints(
        "Ich habe keine Zeit für training am freitag", today=TODAY
    )
    assert any(r["weekday"] == "friday" for r in result)


def test_rest_day_not_pinned_as_required_session():
    # #412: "tomorrow off? saturday and sunday i could do long endurance" must
    # not cross-apply the long-session requirement onto the rest day. The prod
    # bug pinned a required endurance session onto the exact day the athlete
    # asked to take off, so the coach could never clear it.
    result = extract_availability_constraints(
        "and tomorrow off? saturday and sunday i could do long endurance",
        today=TODAY,  # Wednesday -> tomorrow = Thursday 2026-06-04
    )
    required = [r for r in result if r["constraint_type"] == "required_workout"]
    required_dates = {r["constraint_date"] for r in required}
    # The off day (tomorrow) is not required.
    assert "2026-06-04" not in required_dates
    # Saturday and Sunday still get the long endurance requirement.
    assert required_dates == {"2026-06-06", "2026-06-07"}
    assert all(
        r["required_workout"]["workoutType"] == "endurance" for r in required
    )


def test_intent_does_not_leak_across_clauses():
    # A "no training" clause and a separate "long ride" clause each bind only to
    # their own day, even when both appear in one message.
    result = extract_availability_constraints(
        "cannot ride monday. long ride on saturday", today=TODAY
    )
    by_date = {(r["constraint_type"], r["constraint_date"]) for r in result}
    assert ("no_training", "2026-06-08") in by_date  # next monday
    assert ("required_workout", "2026-06-06") in by_date  # saturday
    # Monday is not also required, and saturday is not also a no-training day.
    assert ("required_workout", "2026-06-08") not in by_date
    assert ("no_training", "2026-06-06") not in by_date


def test_same_intent_still_spans_days_joined_by_and():
    # "and" joins same-intent days within one clause; both must be captured.
    result = extract_availability_constraints(
        "unavailable for training monday and today", today=TODAY
    )
    dates = {r["constraint_date"] for r in result}
    assert dates == {"2026-06-08", "2026-06-03"}  # next monday, today
