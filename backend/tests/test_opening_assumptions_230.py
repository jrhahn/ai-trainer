"""The opening message's assumptions, listed by the app (ai-trainer-ops#230).

The model wrote its two starting assumptions into the first message by
translating the English statements on the fly, and garbled them: "auf dem Rad
saust", "Einvoraus-Ermüdung". It is the first text a tester reads. The model
now writes the rest of the message, and the app lists the assumptions under
it: in English as stored, in German as the plan-text translation has them, the
same step every coach text passes (the owner's answer on #258: inside English,
everything shown through one pipeline). So the message and the hypothesis card
read the same words, and a German line the translation cannot write is never
guessed: the fixed assumptions, whose German is written by hand, go out
instead.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

import crud
from auth import decode_token
from services import ai_service, opening_message, plan_translations, starting_hypotheses
from tests.conftest import TestSessionLocal
from tests.test_opening_message_153 import (
    GERMAN_STATEMENTS,
    MODEL_HYPOTHESES,
    _coach_messages,
    _onboard,
)

VOLUME = MODEL_HYPOTHESES[0]["statement"]
RECOVERY = MODEL_HYPOTHESES[1]["statement"]
TODAY = date(2026, 10, 27)


@pytest.fixture
def model(monkeypatch):
    """The model's three parts and its two assumptions; the translations it is asked for."""
    asked: list[list[str]] = []
    reply = {"translations": dict(GERMAN_STATEMENTS)}

    async def write(facts, language, sport, provider="openai"):
        return "Hallo, ich bin dein Coach.", MODEL_HYPOTHESES

    async def translate(texts, language, provider="openai"):
        asked.append(list(texts))
        if isinstance(reply["translations"], Exception):
            raise reply["translations"]
        return {
            t: reply["translations"][t] for t in texts if t in reply["translations"]
        }

    monkeypatch.setattr(ai_service, "write_opening_message", write)
    monkeypatch.setattr(ai_service, "translate_plan_texts", translate)
    return asked, reply


@pytest.fixture
async def athlete(client, auth_headers) -> str:
    """An account to compose the message for; it writes nothing."""
    return decode_token(auth_headers["Authorization"].split(" ", 1)[1])


async def _compose(
    user_id: str, language: str | None
) -> opening_message.OpeningMessage:
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        return await opening_message.compose_opening_message(
            db, user, language=language, today=TODAY
        )


@pytest.mark.asyncio
async def test_a_german_account_reads_the_assumptions_as_the_translation_has_them(
    model, athlete
):
    asked, _ = model

    opening = await _compose(athlete, "German")

    assert opening.content == (
        "Hallo, ich bin dein Coach.\n\n"
        "Zwei Annahmen über dich möchte ich prüfen:\n"
        f"- {starting_hypotheses.GERMAN[VOLUME]}\n"
        f"- {GERMAN_STATEMENTS[RECOVERY]}\n\n"
        "Die Fragen dazu sind oben im Chat angeheftet; antippen genügt."
    )
    # The hand-written German needs no call; only the model's own statement is
    # translated, and that German is kept for the hypothesis card.
    assert asked == [[RECOVERY]]
    assert opening.translations == {RECOVERY: GERMAN_STATEMENTS[RECOVERY]}
    assert [h.statement for h in opening.hypotheses] == [VOLUME, RECOVERY]


@pytest.mark.asyncio
async def test_an_english_account_reads_the_statements_as_stored(model, athlete):
    asked, _ = model

    opening = await _compose(athlete, None)

    assert opening.content.endswith(
        "There are two assumptions about you I'd like to check:\n"
        f"- {VOLUME}\n- {RECOVERY}\n\n"
        "The questions are pinned above the chat; one tap answers each."
    )
    assert asked == [] and opening.translations == {}


@pytest.mark.parametrize(
    "failure", ["left out", "raises", "too slow"], ids=lambda f: f.replace(" ", "-")
)
@pytest.mark.asyncio
async def test_a_german_line_that_cannot_be_written_gives_way_to_the_fixed_ones(
    model, athlete, monkeypatch, failure
):
    """Never English lines in a German message, never a guessed German."""
    _, reply = model
    if failure == "left out":
        reply["translations"] = {}
    elif failure == "raises":
        reply["translations"] = RuntimeError("provider down")
    else:

        async def slow(texts, language, provider="openai"):
            await asyncio.sleep(1)
            return dict(GERMAN_STATEMENTS)

        monkeypatch.setattr(ai_service, "translate_plan_texts", slow)
        monkeypatch.setattr(opening_message, "TRANSLATION_TIMEOUT_SECONDS", 0.01)

    opening = await _compose(athlete, "German")

    fixed = starting_hypotheses.fallback({}, "German")
    assert [h.statement for h in opening.hypotheses] == [h.statement for h in fixed]
    # The model's message stays; its assumptions do not.
    assert opening.content.startswith("Hallo, ich bin dein Coach.\n\n")
    for hypothesis in fixed:
        assert (
            f"- {starting_hypotheses.GERMAN[hypothesis.statement]}" in opening.content
        )
    assert RECOVERY not in opening.content
    assert opening.translations == {}


@pytest.mark.asyncio
async def test_the_fixed_message_lists_the_fixed_assumptions_in_german(
    athlete, monkeypatch
):
    async def broken(facts, language, sport, provider="openai"):
        raise RuntimeError("model down")

    monkeypatch.setattr(ai_service, "write_opening_message", broken)

    opening = await _compose(athlete, "German")

    assert opening.content.startswith("Hallo! Dein erster Plan steht.")
    for hypothesis in opening.hypotheses:
        assert (
            f"- {starting_hypotheses.GERMAN[hypothesis.statement]}" in opening.content
        )
    assert opening.content.endswith(
        "Die Fragen dazu sind oben im Chat angeheftet; antippen genügt."
    )


@pytest.mark.asyncio
async def test_without_any_german_the_assumptions_are_named_generically(
    model, athlete, monkeypatch
):
    """A fixed assumption whose German was never written, after a failed
    translation: the message still goes out, naming them without listing."""
    _, reply = model
    reply["translations"] = {}
    monkeypatch.setitem(plan_translations.FIXED, "de", {})

    opening = await _compose(athlete, "German")

    assert opening.content == (
        "Hallo, ich bin dein Coach.\n\n"
        + opening_message.assumptions_block([], "German")
    )
    assert opening.content.endswith(
        "Zwei Annahmen über dich möchte ich prüfen. "
        "Die Fragen dazu sind oben im Chat angeheftet; antippen genügt."
    )


@pytest.mark.asyncio
async def test_the_message_and_the_hypothesis_card_say_the_same_words(
    client, auth_headers, model
):
    asked, _ = model
    assert (await _onboard(client, auth_headers)).status_code == 200

    [message] = await _coach_messages()
    shown = (
        await client.get(
            "/api/v1/ai/plan-translations",
            headers={**auth_headers, "X-App-Language": "de"},
        )
    ).json()["translations"]
    for statement in (VOLUME, RECOVERY):
        assert f"- {shown[statement]}" in message.content
    # Kept where the cards read it: the page has nothing to ask for.
    assert asked == [[RECOVERY]]


@pytest.mark.asyncio
async def test_german_that_cannot_be_kept_leaves_the_message_and_the_assumptions(
    client, auth_headers, model, monkeypatch
):
    async def broken(*_args, **_kwargs):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(crud, "save_plan_text_translations", broken)

    assert (await _onboard(client, auth_headers)).status_code == 200

    [message] = await _coach_messages()
    assert f"- {GERMAN_STATEMENTS[RECOVERY]}" in message.content
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        statements = {
            h.statement for h in await crud.list_athlete_hypotheses(db, user_id)
        }
    assert statements == {VOLUME, RECOVERY}


def test_the_translation_codes_are_the_ones_the_cards_use():
    """One pipeline: the opening reads the same table the page asks."""
    assert plan_translations.LANGUAGE_CODES == {"German": "de"}
