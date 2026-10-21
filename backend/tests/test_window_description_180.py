"""A window day's description says the window the card shows (ai-trainer-ops#180)."""

import pytest

import schemas
from services.duration_range import describe_window
from services.prompts import ask_trainer_plan_updates_rule


@pytest.mark.parametrize(
    ("text", "lo", "hi", "expected"),
    [
        # The owner's two days, and the length outside the window.
        ("Ride for 105 minutes at 180 W.", 80, 90, "Ride for 80–90 min at 180 W."),
        ("Ride for 2 hours easy.", 80, 90, "Ride for 80–90 min easy."),
        ("1,5 Stunden locker", 60, 80, "60–80 min locker"),
        ("Fahre 1 h 45 ruhig", 60, 80, "Fahre 60–80 min ruhig"),
        ("Fahre 1 h 45.", 60, 80, "Fahre 60–80 min."),
        ("Ride 2 h 4 x 10 min hard", 60, 80, "Ride 60–80 min 4 x 10 min hard"),
        # Inside the window it is one of the lengths the card shows.
        ("Ride for 90 minutes", 80, 90, "Ride for 90 minutes"),
        # A bound and a block are not the session's length.
        ("Keep this ride under 1.5 hours.", 100, 120, "Keep this ride under 1.5 hours."),
        ("Fahre 120 Minuten, davon 4 x 10 min zügig.", 90, 105, "Fahre 90–105 min, davon 4 x 10 min zügig."),
        ("Ride 2.5–3h steady", 100, 120, "Ride 2.5–3h steady"),
        # Review of #996: a part of the ride, or a timing inside it, is no length.
        (
            "10 min easy warm-up, then steady riding, 10 min cool-down.",
            90,
            105,
            "10 min easy warm-up, then steady riding, 10 min cool-down.",
        ),
        ("Eat every 20 minutes.", 90, 105, "Eat every 20 minutes."),
        ("Include 20 min at tempo.", 90, 105, "Include 20 min at tempo."),
        ("Iss alle 30 Minuten etwas.", 90, 105, "Iss alle 30 Minuten etwas."),
        ("After 60 min, ease off.", 70, 90, "After 60 min, ease off."),
        ("Settle in for the first 45 minutes.", 50, 60, "Settle in for the first 45 minutes."),
        # Shorter than the window but long enough to be the ride: still its length.
        ("Ride for 75 minutes.", 90, 105, "Ride for 90–105 min."),
    ],
)
def test_a_length_outside_the_window_becomes_the_window(text, lo, hi, expected):
    assert describe_window(text, lo, hi) == expected


def test_a_single_value_or_no_text_is_left_alone():
    assert describe_window("Ride for 105 minutes", 90, 90) == "Ride for 105 minutes"
    assert describe_window(None, 80, 90) is None


def test_a_stored_window_day_agrees_with_its_description():
    day = schemas.PlanDay.model_validate(
        {
            "date": "2026-10-31",
            "workoutType": "endurance",
            "title": "Weekend ride",
            "description": "Ride for 120 minutes, keeping it conversational.",
            "durationMinMinutes": 90,
            "durationMaxMinutes": 105,
        }
    )
    assert (day.duration_min_minutes, day.duration_max_minutes, day.duration_minutes) == (90, 105, 98)
    assert day.description == "Ride for 90–105 min, keeping it conversational."


def test_a_single_value_day_keeps_its_description():
    day = schemas.PlanDay.model_validate(
        {
            "date": "2026-10-31",
            "workoutType": "endurance",
            "title": "Ride",
            "description": "Ride for 120 minutes.",
            "durationMinutes": 60,
        }
    )
    assert day.description == "Ride for 120 minutes."


def test_the_coach_is_asked_to_name_the_window():
    assert "names that window, never one length" in ask_trainer_plan_updates_rule(None)
