"""The coach is told which language the athlete writes (ai-trainer-ops#78)."""

import pytest

from services import ai_service
from services.athlete_language import athlete_language, in_other_language, written_in
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


def test_a_short_message_that_leans_clearly_wins_over_history():
    english_turns = [
        "Is the plan for tomorrow still the same?",
        "What should I eat before?",
    ]

    assert athlete_language("heute, oder?", english_turns) == "German"


def test_a_word_both_languages_share_is_no_evidence():
    assert athlete_language("was") is None


def test_history_is_read_newest_first_and_stops_once_it_knows():
    earlier = [
        "Ich bin heute echt müde und habe keine Lust.",  # oldest, German
        "Is the plan for tomorrow still the same?",
        "What should I eat before the ride?",  # newest, English
    ]

    assert athlete_language("ok", earlier) == "English"


@pytest.mark.parametrize(
    "message, language",
    [
        # The second scenario run: no word of this request was in the lists.
        ("Bitte mach Freitag zum Ruhetag.", "German"),
        ("Please make Friday a rest day.", "English"),
    ],
)
def test_short_requests(message, language):
    assert athlete_language(message) == language


# --- What the coach wrote, held to the athlete's language (ai-trainer-ops#149) ---

GERMAN_REPLY = (
    "Das passt gut so. Heute steht noch Core and Upper Body Strength auf dem Plan, "
    "und morgen fahren wir eine ruhige Runde, wenn das Wetter mitspielt."
)
ENGLISH_REPLY = (
    "That is fair, good weather and fresh legs are hard to pass up. Let me know "
    "how the weekend looks and we will adjust the plan from there."
)


def test_the_language_a_reply_is_written_in():
    # English plan titles in a German reply do not tip it.
    assert written_in(GERMAN_REPLY) == "German"
    assert written_in(ENGLISH_REPLY) == "English"
    assert written_in("Z2, 175–215 W.") is None
    # Too even to say.
    assert written_in("Das ist the plan und that is it, ja and yes.") is None


def test_a_reply_in_the_other_language_is_caught():
    assert in_other_language(ENGLISH_REPLY, "German")
    assert not in_other_language(GERMAN_REPLY, "German")
    assert in_other_language(GERMAN_REPLY, "English")
    # The athlete's language unknown, or the reply too short to tell.
    assert not in_other_language(ENGLISH_REPLY, None)
    assert not in_other_language("Ok, 75 min.", "German")


def _changes() -> list[dict]:
    old = {"date": "2026-10-18", "workoutType": "endurance", "durationMinutes": 90}
    return [
        {
            "date": "2026-10-18",
            "old_day": old,
            "new_day": {**old, "workoutType": "rest"},
        }
    ]


def _narration(summary: str, reason: str) -> str:
    import json

    return json.dumps(
        {"summary": summary, "days": [{"date": "2026-10-18", "reason": reason}]}
    )


@pytest.mark.asyncio
async def test_a_narration_in_the_wrong_language_is_asked_for_once_more():
    from unittest.mock import patch

    calls: list[str] = []
    replies = iter(
        [
            _narration(
                "I made Sunday a rest day so you start the week fresh.",
                "Rest after a hard week.",
            ),
            _narration(
                "Ich habe den Sonntag zum Ruhetag gemacht, damit du frisch startest.",
                "Ruhe nach der harten Woche.",
            ),
        ]
    )

    async def chat(provider, system_prompt, user_msg, **_kwargs):
        calls.append(user_msg)
        return next(replies)

    with patch.object(ai_service, "_chat", side_effect=chat):
        parsed = await ai_service.summarize_plan_changes(
            _changes(),
            {"currentFTP": 250},
            run_context="your daily overnight routine check of the plan",
            language_samples=["Ich bin heute echt müde, und es regnet."],
        )

    assert parsed["summary"].startswith("Ich habe den Sonntag")
    assert len(calls) == 2
    assert calls[1].endswith("Write 'summary' and every 'reason' in German.")


@pytest.mark.asyncio
async def test_a_failed_second_narration_keeps_the_first():
    from unittest.mock import patch

    from services.llm import AIProviderError

    first = _narration(
        "I made Sunday a rest day so you start the week fresh.",
        "Rest after a hard week.",
    )
    outcomes = iter([first, AIProviderError(key_refused=False, provider="gemini")])

    async def chat(*_args, **_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with patch.object(ai_service, "_chat", side_effect=chat):
        parsed = await ai_service.summarize_plan_changes(
            _changes(),
            {"currentFTP": 250},
            run_context="your daily overnight routine check of the plan",
            language_samples=["Ich bin heute echt müde, und es regnet."],
        )

    assert parsed["summary"].startswith("I made Sunday")


@pytest.mark.asyncio
async def test_a_narration_in_the_athletes_language_is_not_asked_again():
    from unittest.mock import patch

    calls: list[str] = []

    async def chat(provider, system_prompt, user_msg, **_kwargs):
        calls.append(user_msg)
        return _narration(
            "Ich habe den Sonntag zum Ruhetag gemacht, damit du frisch startest.",
            "Ruhe nach der Woche.",
        )

    with patch.object(ai_service, "_chat", side_effect=chat):
        await ai_service.summarize_plan_changes(
            _changes(),
            {"currentFTP": 250},
            run_context="your daily overnight routine check of the plan",
            language_samples=["Ich bin heute echt müde, und es regnet."],
        )
        await ai_service.summarize_plan_changes(
            _changes(), {"currentFTP": 250}, run_context="x", language_samples=None
        )

    assert len(calls) == 2
