"""The canonical either-spelling reader, and the falsy values it exists for.

``day_field`` replaced the ``day.get("camel") or day.get("snake") or default``
idiom across the plan gates. The whole difference between the two is what
happens when the canonical value is falsy, so that is what these pin down — the
old idiom passes every other test in this file.

A ``durationMinutes: 0`` that read back as the stale ``duration_minutes: 90`` is
what let a blanked day stay a 90-minute session and a gate judge its own output
in violation of the constraint it had just applied (ai-trainer-ops#29).
"""

from __future__ import annotations

import pytest

from schemas import PlanDay, day_field

CAMEL = "durationMinutes"
SNAKE = "duration_minutes"


# --------------------------------------------------------------------------
# The reason this function exists: falsy values are answers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("falsy", [0, "", False, 0.0, [], {}])
def test_a_falsy_first_value_wins_over_a_truthy_second(falsy):
    """``0 or 90`` is the bug. Presence decides, so the blanked value stands."""
    assert day_field({CAMEL: falsy, SNAKE: 90}, CAMEL, SNAKE) == falsy


def test_the_old_or_idiom_would_disagree_here():
    """Spelled out so the regression is unmistakable rather than implied."""
    day = {CAMEL: 0, SNAKE: 90}
    assert (day.get(CAMEL) or day.get(SNAKE) or 0) == 90  # what it used to do
    assert day_field(day, CAMEL, SNAKE, default=0) == 0  # what it does now


# --------------------------------------------------------------------------
# Resolution order, and None as the one absent marker
# --------------------------------------------------------------------------


def test_first_named_spelling_wins_when_both_are_present():
    assert day_field({CAMEL: 45, SNAKE: 90}, CAMEL, SNAKE) == 45


def test_falls_through_to_the_later_spelling_when_the_first_is_absent():
    assert day_field({SNAKE: 90}, CAMEL, SNAKE) == 90


def test_none_counts_as_absent_and_falls_through():
    """A key explicitly set to None carries no answer, so the next one is tried."""
    assert day_field({CAMEL: None, SNAKE: 90}, CAMEL, SNAKE) == 90


def test_none_everywhere_yields_the_default():
    assert day_field({CAMEL: None, SNAKE: None}, CAMEL, SNAKE, default=0) == 0


def test_default_is_returned_when_no_spelling_is_present():
    assert day_field({"date": "2026-06-01"}, CAMEL, SNAKE, default=0) == 0


def test_default_defaults_to_none():
    assert day_field({}, CAMEL, SNAKE) is None


def test_a_single_name_is_allowed():
    """Fields with no alias — ``title``, ``date`` — go through the same reader."""
    assert day_field({"title": ""}, "title", default="x") == ""
    assert day_field({}, "title", default="x") == "x"


def test_no_names_yields_the_default():
    assert day_field({CAMEL: 90}, default=7) == 7


# --------------------------------------------------------------------------
# Shape: dicts only, on purpose
# --------------------------------------------------------------------------


def test_a_plan_day_model_is_not_a_dict_and_yields_the_default():
    """Deliberate: spelling ambiguity is a property of raw dicts.

    ``PlanDay`` has no ambiguity by construction, so a caller holding the model
    should read the attribute. Accepting it here would mean a second resolution
    path that no caller exercises and nothing keeps in step with this one.
    """
    model = PlanDay(date="2026-06-01", durationMinutes=90)
    assert model.duration_minutes == 90
    assert day_field(model, CAMEL, SNAKE, default=0) == 0


@pytest.mark.parametrize("not_a_dict", [None, 0, "", [], "a string", object()])
def test_non_dict_input_yields_the_default_rather_than_raising(not_a_dict):
    """The plan gates walk lists that may hold anything an LLM emitted."""
    assert day_field(not_a_dict, CAMEL, SNAKE, default=0) == 0
