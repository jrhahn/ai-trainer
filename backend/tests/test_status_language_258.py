"""The dashboard's status badge in the athlete's language (ai-trainer-ops#258).

Its prompt had no language rule at all, a badge in the wrong language was
stored as written, and the fallback for a failed call was English. Now the
prompt names the language, a badge in the other one is asked for once more,
and the fallback speaks it too.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

import crud
import models
from services import ai_service, status_pipeline
from services.prompts import (
    ATHLETE_LANGUAGE_RULE,
    named_language,
    training_status_system,
)
from services.training_status import fallback_status
from tests.conftest import TestSessionLocal

ENGLISH = {
    "label": "On track",
    "tone": "positive",
    "rationale": "You did the ride on Tuesday and the intervals on Thursday, so the week is on plan.",
}
GERMAN = {
    "label": "Auf Kurs",
    "tone": "positive",
    "rationale": "Du hast die Ausfahrt am Dienstag und die Intervalle am Donnerstag gemacht, also ist die Woche im Plan.",
}
FACTS = {"adherence": 1.0, "plannedDone": 2, "plannedDue": 2, "extraActivities": []}


def _replies(monkeypatch, *replies: dict) -> list[str]:
    asked: list[str] = []
    queue = list(replies)

    async def fake_chat(provider, system_prompt, user_msg, **kwargs):
        asked.append(user_msg)
        return json.dumps(queue.pop(0))

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    return asked


def test_the_prompt_names_the_language_it_knows():
    assert training_status_system("German").endswith(
        f'{ATHLETE_LANGUAGE_RULE} The athlete writes German: write "label" and "rationale" in German.'
    )
    assert training_status_system().endswith(ATHLETE_LANGUAGE_RULE)
    assert named_language(None, '"label"') == ""


@pytest.mark.asyncio
async def test_a_badge_in_the_other_language_is_asked_for_once_more(monkeypatch):
    asked = _replies(monkeypatch, ENGLISH, GERMAN)

    status = await ai_service.generate_training_status(FACTS, reply_language="German")

    assert status == ("Auf Kurs", "positive", GERMAN["rationale"])
    assert len(asked) == 2 and asked[1].endswith(
        "Write 'label' and 'rationale' in German."
    )


@pytest.mark.asyncio
async def test_a_second_badge_in_the_other_language_leaves_it_to_the_fallback(
    monkeypatch,
):
    _replies(monkeypatch, ENGLISH, ENGLISH)

    assert (
        await ai_service.generate_training_status(FACTS, reply_language="German")
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "language"), [(GERMAN, "German"), (ENGLISH, None)])
async def test_a_badge_in_the_right_or_an_unknown_language_is_taken_at_once(
    monkeypatch, reply, language
):
    asked = _replies(monkeypatch, reply)

    assert (
        await ai_service.generate_training_status(FACTS, reply_language=language)
        is not None
    )
    assert len(asked) == 1


@pytest.mark.parametrize(
    ("facts", "label"),
    [
        ({"adherence": None}, "Keine Einheit fällig"),
        (FACTS, "Auf Kurs"),
        ({"adherence": 0.6, "plannedDone": 3, "plannedDue": 5}, "Etwas hinterher"),
        (
            {
                "adherence": 0.2,
                "plannedDone": 1,
                "plannedDue": 5,
                "extraActivities": [{}],
            },
            "Hinter dem Plan",
        ),
    ],
)
def test_the_fallback_speaks_german(facts, label):
    german_label, _, rationale = fallback_status(facts, "German")

    assert german_label == label
    assert fallback_status(facts)[0] != label  # English without a language


def test_the_german_fallback_counts_what_was_done():
    _, _, rationale = fallback_status(
        {
            "adherence": 0.2,
            "plannedDone": 1,
            "plannedDue": 5,
            "extraActivities": [{}, {}],
        },
        "German",
    )

    assert rationale == (
        "Du hast 1 von 5 Einheiten erledigt, die in den letzten 7 Tagen fällig waren, "
        "dazu 2 ungeplante Einheiten."
    )


async def _user_with(messages: list[str]) -> models.User:
    async with TestSessionLocal() as db:
        user = models.User(
            email="status-language@example.com",
            name="Rider",
            hashed_password="x",
            current_ftp=250,
        )
        db.add(user)
        await db.flush()
        for i, text in enumerate(messages):
            await crud.create_chat_message(
                db,
                user.id,
                role="user",
                content=text,
                timestamp=f"2026-10-2{i}T08:00:00Z",
            )
        await crud.upsert_rider_assessment(db, user.id, estimated_ftp=250)
        await db.commit()
        return user


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("messages", "interface", "expected"),
    [
        (
            ["Ich bin heute müde und habe keine Lust auf die Intervalle."],
            None,
            "German",
        ),
        ([], "German", "German"),
        (
            ["Ich bin heute müde und habe keine Lust auf die Intervalle."],
            "English",
            "German",
        ),
        ([], None, None),
    ],
)
async def test_the_badge_is_written_in_their_words_language_then_the_interfaces(
    monkeypatch, messages, interface, expected
):
    told: list[str | None] = []

    async def fake_generate(facts, *, reply_language=None, **kwargs):
        told.append(reply_language)
        return None

    monkeypatch.setattr(ai_service, "generate_training_status", fake_generate)
    await _user_with(messages)

    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        label, _, _ = await status_pipeline.regenerate(
            db, user, interface_language=interface
        )

    assert told == [expected]
    assert label == fallback_status({"adherence": None}, expected)[0]


@pytest.mark.asyncio
async def test_the_endpoint_passes_the_interface_language(
    client, auth_headers, monkeypatch
):
    told: list[str | None] = []

    async def fake_regenerate(db, user, *, interface_language=None, **kwargs):
        told.append(interface_language)
        return fallback_status({"adherence": None}, interface_language)

    monkeypatch.setattr(status_pipeline, "regenerate", fake_regenerate)
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        await crud.upsert_rider_assessment(db, user.id, estimated_ftp=250)
        await db.commit()

    response = await client.post(
        "/api/v1/ai/refresh-training-status",
        headers={**auth_headers, "X-App-Language": "de"},
    )

    assert response.status_code == 200
    assert told == ["German"] and response.json()["label"] == "Keine Einheit fällig"
