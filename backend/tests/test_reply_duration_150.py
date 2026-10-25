"""A reply and the update it carries give the same duration (ai-trainer-ops#150)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from services import ai_service
from services.dates import app_today

TODAY = date(2026, 10, 7)  # a Wednesday
THURSDAY = date(2026, 10, 8)
UPDATE = {"date": THURSDAY.isoformat(), "workoutType": "endurance", "durationMinutes": 105}


def _found(text: str, update: dict = UPDATE) -> list:
    return ai_service._duration_mismatches(text, [update], TODAY)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        # The finding of 2026-10-10, paraphrased.
        ("Morgen fährst du 2 Stunden locker in Z2.", 120),
        ("Am Donnerstag stehen zwei Stunden Grundlage an.", 120),
        ("Thursday is a 2 h endurance ride.", 120),
        ("Morgen: 1:30 h ruhig rollen.", 90),
        ("Morgen fährst du 1 Stunde und 20 min.", 80),
        ("Tomorrow you ride for 80 minutes.", 80),
    ],
)
def test_a_duration_the_update_does_not_hold_is_found(text, said):
    assert _found(text) == [(THURSDAY, said, 105)]


@pytest.mark.parametrize(
    "text",
    [
        "Morgen fährst du 105 Minuten locker.",
        "Morgen fährst du 1:45 h locker.",
        "Morgen fährst du knapp 1,75 Stunden.",
        "Morgen fährst du 100 Minuten.",  # within 10 minutes
        "Morgen fährst du 90–120 min, wie es sich anfühlt.",  # a range
        "Morgen fährst du bis zu 2 Stunden.",
        "Morgen 4 x 10 min an der Schwelle.",  # a block, not the length
        "Morgen fährst du 20 min ein und dann locker.",  # far off: a part
        "Heute bist du 2 Stunden gefahren, morgen wird es ruhiger.",  # two days
        "Nach den 2 Stunden gestern fährst du morgen ruhig.",  # two days
        "Morgen 2 Stunden, davon 30 min Tempo.",  # two durations
        "Am Freitag fährst du 2 Stunden.",  # not the changed day
        "Fahr locker, 2 Stunden.",  # names no day
    ],
)
def test_what_is_no_mismatch_is_left_alone(text):
    assert _found(text) == []


def test_a_span_holds_every_value_inside_it():
    span = {**UPDATE, "durationMinutes": None, "durationMinMinutes": 90, "durationMaxMinutes": 120}

    assert _found("Morgen fährst du 2 Stunden.", span) == []
    assert _found("Morgen fährst du 2,5 Stunden.", span) == [(THURSDAY, 150, 120)]


@pytest.mark.parametrize(
    "update",
    [
        {**UPDATE, "completed": True},
        {**UPDATE, "date": (TODAY - timedelta(days=1)).isoformat()},
        {**UPDATE, "date": "someday"},
        {"date": THURSDAY.isoformat(), "workoutType": "rest"},
    ],
)
def test_a_done_past_unreadable_or_timeless_day_is_skipped(update):
    assert _found("Morgen fährst du 2 Stunden.", update) == []


def test_the_rewrite_request_names_both_durations():
    request = ai_service._rewrite_request(ai_service._Findings([], [], [], None, durations=((THURSDAY, 120, 105),)))

    assert "Thursday 2026-10-08 120 minutes" in request and "planUpdates set 105 minutes" in request


def _coach(*replies):
    calls: list = []

    async def chat(provider, system_prompt, messages, json_mode=False, **kwargs):
        calls.append(messages)
        return json.dumps(replies[len(calls) - 1])

    return chat, calls


@pytest.mark.asyncio
async def test_a_mismatched_reply_is_written_again_without_a_note():
    tomorrow = app_today() + timedelta(days=1)
    day = {"date": tomorrow.isoformat(), "workoutType": "endurance", "title": "Z2", "durationMinutes": 120}
    shorter = {**day, "durationMinutes": 105}
    chat, calls = _coach(
        {"response": "Morgen fährst du 2 Stunden locker.", "planUpdates": [shorter]},
        {"response": "Morgen fährst du 2 Stunden locker.", "planUpdates": [shorter]},
    )

    with patch.object(ai_service, "_chat_history", side_effect=chat):
        result = await ai_service.ask_trainer("Ich bin müde, morgen kürzer?", [day], {"name": "A"})

    assert len(calls) == 2
    assert "120 minutes, and your planUpdates set 105 minutes" in calls[1][-1]["content"]
    # Kept after the rewrite, it reaches the athlete without a note: a wrong
    # finding must not end in a false note (#150).
    assert result["response"] == "Morgen fährst du 2 Stunden locker."


@pytest.mark.parametrize("context_workout", [None, {"date": "2026-10-25", "title": "Endurance"}])
def test_the_duration_rule_holds_with_and_without_a_workout_card(context_workout):
    """"Kürzer bitte" from a workout card is the commonest edit, and the card's
    branch of the rule had lost the duration part (ai-trainer-ops#261)."""
    from services import prompts

    rule = prompts.ask_trainer_plan_updates_rule(context_workout)

    assert "CRITICAL — duration" in rule
    assert '"durationMinutes" (whole minutes)' in rule

