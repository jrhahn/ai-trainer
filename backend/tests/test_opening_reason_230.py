"""The opening message states the rest day's true reason, and says "tap" in German (ai-trainer-ops#230).

Sam (Mon, Wed, Sat) signed up on a Saturday and read "Today is a rest day
because it is not one of your training days": the prompt let the model pick
the reason. The pinned-questions line came back as "mit einem Tipp
beantwortet", a hint rather than a tap, and the fixed German text had the same
slip.
"""

from __future__ import annotations

from datetime import date

import pytest

import models
from services import opening_message, prompts

SATURDAY = date(2026, 10, 24)


def _today(workout_type: str) -> list[dict]:
    return [{"date": SATURDAY.isoformat(), "workoutType": workout_type, "title": "Today", "durationMinutes": 0}]


@pytest.mark.parametrize(
    ("days", "workout_type", "reason"),
    [
        # Sam: Saturday is one of his days, and the plan opens with rest anyway.
        (["monday", "wednesday", "saturday"], "rest", "eases_in"),
        # Mia: Saturday is not one of hers.
        (["tuesday", "thursday", "sunday"], "rest", "not_a_training_day"),
        # No days given: "not one of your training days" would mean nothing.
        (None, "rest", "eases_in"),
        # A session today needs no reason.
        (["saturday"], "endurance", None),
    ],
)
def test_the_facts_say_which_reason_holds(days, workout_type, reason):
    user = models.User(sports=["cycling"], training_goal="general_fitness", training_days=days)

    facts = opening_message.build_facts(user, _today(workout_type), SATURDAY)

    assert facts["first_day_reason"] == reason


def test_the_prompt_states_the_reason_rather_than_picking_one():
    system = prompts.opening_message_system("German", "cycling")

    assert "FACTS.first_day_reason" in system
    assert "or the plan eases in" not in system


@pytest.mark.parametrize("language", ["German", "English"])
def test_the_pinned_line_is_said_as_it_stands(language):
    system = prompts.opening_message_system(language, "cycling")

    assert f'"{prompts.PINNED_QUESTIONS_LINE[language]}"' in system


def test_no_tipp_for_a_tap_in_the_prompt_or_the_fixed_text():
    fixed = opening_message.fixed_message({"answers": {}, "asks": True}, "German")

    assert "Die Fragen dazu sind oben im Chat angeheftet; antippen genügt." in fixed
    assert "Tipp" not in fixed
    assert "Tipp" not in prompts.opening_message_system("German", "cycling")
