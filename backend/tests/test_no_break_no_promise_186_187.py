"""No invented break, no promised result (ai-trainer-ops#186, #187)."""

from unittest.mock import patch

import pytest

from services import ai_service
from services.ai_service import _promised_result
from services.prompts import (
    COACH_PERSONA,
    RUNNING_COACH_PERSONA,
    generate_plan_system,
    opening_message_system,
    update_memory_system,
)
from tests.test_coach_rewrite import PLAN, PROFILE, _coach


@pytest.mark.parametrize("persona", [COACH_PERSONA, RUNNING_COACH_PERSONA])
def test_every_coach_is_told_an_empty_history_is_no_break(persona):
    assert "never speak of a break, a comeback, a return" in persona
    assert "Never promise a result" in persona


def test_the_plan_the_opening_and_the_memory_are_told_too():
    assert "never speak of a break" in generate_plan_system()
    assert "not back from a break" in opening_message_system("English", "cycling")
    assert "Never record a training break" in update_memory_system()


@pytest.mark.parametrize(
    ("text", "promise"),
    [
        ("Der Ötztaler ist absolut machbar, und du stehst topfit am Start.", "absolut machbar"),
        ("Im August stehst du topfit am Start.", "topfit am Start"),
        ("Das ist garantiert zu schaffen.", "garantiert"),
        ("You will definitely make it.", "You will definitely"),
        ("That's absolutely doable for you.", "absolutely doable"),
    ],
)
def test_a_promised_result_is_found(text, promise):
    assert _promised_result(text) == promise


@pytest.mark.parametrize(
    "text",
    [
        "Garantieren kann ich das nicht.",
        "There is no guarantee, but here is what it depends on.",
        "Ob es reicht, hängt von deinem Gewicht und den Zeitlimits ab.",
        "Das ist nicht garantiert.",
    ],
)
def test_no_promise(text):
    assert _promised_result(text) is None


@pytest.mark.asyncio
async def test_a_promise_is_written_again():
    chat, calls = _coach(
        {"response": "Der Ötztaler ist absolut machbar für dich.", "planUpdates": []},
        {"response": "Ob er realistisch ist, hängt von deinem Gewicht und den Zeitlimits ab. Was wiegst du?", "planUpdates": []},
    )

    with patch.object(ai_service, "_chat_history", side_effect=chat):
        result = await ai_service.ask_trainer("Ist der Ötztaler realistisch für mich?", PLAN, PROFILE)

    assert len(calls) == 2
    assert 'your reply promises a result ("absolut machbar")' in calls[1][-1]["content"]
    assert result["response"].startswith("Ob er realistisch ist")
    assert "Hinweis" not in result["response"]


@pytest.mark.asyncio
async def test_a_promise_that_stays_gets_a_note():
    reply = {"response": "You will definitely make it.", "planUpdates": []}
    chat, calls = _coach(reply, reply)

    with patch.object(ai_service, "_chat_history", side_effect=chat):
        result = await ai_service.ask_trainer("Can I finish the Etape?", PLAN, PROFILE)

    assert len(calls) == 2
    assert result["response"].endswith(
        "Note: no one can promise a result; it depends on how the training goes and how your body responds."
    )
