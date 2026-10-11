"""The coach is told which language the athlete writes (ai-trainer-ops#78)."""

import pytest

from services import ai_service
from services.athlete_language import athlete_language
from services.prompts import ask_trainer_system_sections


@pytest.mark.parametrize(
    "message, language",
    [
        # The scenario questions that got English answers.
        ("Was wäre heute optimal, unabhängig von meiner Präferenz?", "German"),
        (
            "Bin am überlegen doch noch eine kleine Trail Runde. Bock auf Trails aber kein Bock hinzufahren",
            "German",
        ),
        ("heute kraft training oder yoga core?", "German"),
        ("ok", None),  # too little to tell alone
        ("I had two full rest days and my legs feel really fresh.", "English"),
        ("3x12 sweet spot", None),
    ],
)
def test_the_language_of_one_message(message, language):
    assert athlete_language(message) == language


def test_a_short_message_borrows_from_the_athletes_earlier_ones():
    earlier = ["Ich bin heute echt müde, aber das Wetter ist so schön."]

    assert (
        athlete_language("Heute 2-3h Grundlage: Rennrad oder MTB?", earlier) == "German"
    )
    assert (
        athlete_language("ok", ["Is the plan for tomorrow still the same?"])
        == "English"
    )


def _closing(**kwargs) -> str:
    return ask_trainer_system_sections(
        {}, "2026-10-11", [], [], "", "", "", "", **kwargs
    )["closing"]


def test_the_output_contract_names_the_language():
    assert 'The athlete writes German: write "response" in German.' in _closing(
        reply_language="German"
    )
    assert "The athlete writes" not in _closing()


@pytest.mark.asyncio
async def test_ask_trainer_sends_the_named_language(monkeypatch):
    seen = {}

    async def chat_history(provider, system_prompt, messages, **_kwargs):
        seen["system"] = system_prompt
        return '{"thinking": "", "response": "ok"}'

    monkeypatch.setattr(ai_service, "_chat_history", chat_history)

    await ai_service.ask_trainer(
        "Was wäre heute optimal, unabhängig von meiner Präferenz?", [], {}
    )

    assert "The athlete writes German" in seen["system"]
