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
    return [
        {
            "date": SATURDAY.isoformat(),
            "workoutType": workout_type,
            "title": "Today",
            "durationMinutes": 0,
        }
    ]


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
    user = models.User(
        sports=["cycling"], training_goal="general_fitness", training_days=days
    )

    facts = opening_message.build_facts(user, _today(workout_type), SATURDAY)

    assert facts["first_day_reason"] == reason


def test_the_prompt_states_the_reason_rather_than_picking_one():
    system = prompts.opening_message_system("German", "cycling")

    assert "FACTS.first_day_reason" in system
    assert "or the plan eases in" not in system


@pytest.mark.parametrize("language", ["German", "English"])
def test_the_reason_is_given_as_meaning_and_only_for_a_rest_day(language):
    """The prompt used to give the reason as an English idiom, and the German
    message came back with "eased der Plan sanft ein"; with a session today
    the model still said the plan eases in (real model, 2026-10-27,
    ai-trainer-ops#230)."""
    system = prompts.opening_message_system(language, "cycling")

    assert "eases in" not in system
    assert "Only if FACTS.first_day_is_rest is true" in system
    assert "say nothing about today" in system


@pytest.mark.parametrize("language", ["German", "English"])
def test_the_pinned_line_is_said_as_it_stands(language):
    """The app writes it under the assumptions it lists; the model no longer
    gets it to repeat (ai-trainer-ops#230)."""
    block = opening_message.assumptions_block(["a", "b"], language)

    assert block.endswith(prompts.PINNED_QUESTIONS_LINE[language])
    assert prompts.PINNED_QUESTIONS_LINE[language] not in (
        prompts.opening_message_system(language, "cycling")
    )


def test_no_tipp_for_a_tap_in_the_prompt_or_the_fixed_text():
    # The pinned line comes under the assumptions the app lists, under either
    # message (ai-trainer-ops#230).
    fixed = opening_message.fixed_message({"answers": {}}, "German")
    block = opening_message.assumptions_block(["Eine Annahme"], "German")

    assert prompts.PINNED_QUESTIONS_LINE["German"] in block
    assert "Tipp" not in fixed and "Tipp" not in block
    assert "Tipp" not in prompts.opening_message_system("German", "cycling")


_REASON_SAID = {
    ("German", "not_a_training_day"): "keiner deiner Trainingstage",
    ("German", "eases_in"): "Heute ist deshalb ein Ruhetag",
    (None, "not_a_training_day"): "not one of your training days",
    (None, "eases_in"): "That is why today is a rest day",
}


@pytest.mark.parametrize(("language", "reason"), list(_REASON_SAID))
def test_the_fixed_text_gives_the_reason_the_facts_give(language, reason):
    """The fixed text said "deshalb" whatever the reason, so an athlete on a
    day off heard that the plan was easing in (real model run, 2026-10-27)."""
    facts = {"answers": {}, "first_day_is_rest": True, "first_day_reason": reason}

    text = opening_message.fixed_message(facts, language)

    assert _REASON_SAID[(language, reason)] in text
    other = "eases_in" if reason == "not_a_training_day" else "not_a_training_day"
    assert _REASON_SAID[(language, other)] not in text


@pytest.mark.parametrize(
    ("language", "example"),
    [("German", "„Ja, meistens“"), ("English", '"Yes, mostly"')],
)
def test_answer_labels_are_asked_for_in_words_with_an_example(language, example):
    """Asked for in characters alone, about half the German labels came back
    longer than 40 and the assumption was dropped (real model, 2026-10-27)."""
    system = prompts.opening_message_system(language, "cycling")

    assert "2 to 5 words" in system
    assert example in system
