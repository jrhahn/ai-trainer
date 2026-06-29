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


def test_long_session_duration_in_minutes_is_parsed():
    result = extract_availability_constraints(
        "long ride on saturday, 90 min", today=TODAY
    )
    required = [r for r in result if r["constraint_type"] == "required_workout"]
    assert required and required[0]["required_workout"]["minDurationMinutes"] == 90


def test_plain_training_mention_creates_no_required_constraint():
    # "training" alone is not a long-session phrase.
    result = extract_availability_constraints("I love training on saturday", today=TODAY)
    assert all(r["constraint_type"] != "required_workout" for r in result)


def test_german_phrasing_supported():
    result = extract_availability_constraints(
        "Ich habe keine Zeit für training am freitag", today=TODAY
    )
    assert any(r["weekday"] == "friday" for r in result)
