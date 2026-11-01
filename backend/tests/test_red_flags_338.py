"""Warning signs and heart-rate medication get fixed words (review of 2026-11-01, ai-trainer-ops#338)."""

from __future__ import annotations

import pytest

import crud
from auth import decode_token
from domain import athlete_states, red_flags
from services.dates import app_today
from tests.conftest import TestSessionLocal

ASK = "/api/v1/ai/ask-trainer"


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (
            "Mir ist schwindelig und ich habe Brustschmerzen beim Bergauffahren",
            red_flags.CHEST,
        ),
        ("Beim Intervall hatte ich Druck auf der Brust.", red_flags.CHEST),
        ("I had chest pain on the climb today.", red_flags.CHEST),
        ("Ich bin gestern auf dem Rad kurz umgekippt.", red_flags.FAINT),
        ("I nearly passed out at the top.", red_flags.FAINT),
        ("Herzrasen beim letzten Intervall, ganz komisch.", red_flags.HEART),
        ("My heart was racing for minutes after.", red_flags.HEART),
        ("Ich habe kaum Luft bekommen, richtige Atemnot.", red_flags.BREATH),
        ("Mir war auf dem Rad schwindelig.", red_flags.DIZZY),
        ("I felt dizzy after the sprint.", red_flags.DIZZY),
        # A negation that belongs to another clause negates nothing here
        # (review of #1239).
        (
            "Ich habe nicht gut geschlafen. Brustschmerzen beim Anstieg.",
            red_flags.CHEST,
        ),
        ("Ich bin nicht sicher, hatte Herzrasen am Berg.", red_flags.HEART),
        ("I'm not sure, but I fainted at the top.", red_flags.FAINT),
        ("Nicht schlimm,\nmir war schwindelig.", red_flags.DIZZY),
    ],
)
def test_a_warning_sign_is_read(message, kind):
    assert red_flags.red_flag(message) == kind


@pytest.mark.parametrize(
    "message",
    [
        "Ich habe keine Brustschmerzen, nur müde Beine.",
        "No chest pain, just tired legs.",
        "I never fainted, I just stopped.",
        "I haven't fainted once.",
        "Keine richtigen Brustschmerzen, eher Muskelkater.",
        "I did not get any chest pain.",
        "Das ist doch Schwindel mit diesen Zahlen.",
        "Ich bin schwindelfrei, Abfahrten machen mir nichts.",
        "Mein Rad ist umgekippt.",
        "Schöne Runde heute, alles gut.",
    ],
)
def test_no_warning_sign_where_none_is_stated(message):
    assert red_flags.red_flag(message) is None


async def _ask(client, auth_headers, question: str) -> dict:
    response = await client.post(ASK, headers=auth_headers, json={"question": question})
    assert response.status_code == 200
    return response.json()


async def _states(auth_headers) -> list[athlete_states.StatedState]:
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        return await crud.list_relevant_states(db, user_id, app_today())


@pytest.mark.asyncio
async def test_a_warning_sign_puts_the_fixed_words_first_and_holds_the_plan(
    client, auth_headers, mock_ai_service
):
    body = await _ask(
        client,
        auth_headers,
        "Mir ist schwindelig und ich habe Brustschmerzen beim Bergauffahren.",
    )

    first = body["notes"][0]
    assert first["kind"] == "red_flag"
    assert "ärztlich abklären" in first["text"] and "112" in first["text"]
    assert [s.kind for s in await _states(auth_headers)] == [
        athlete_states.KIND_CLEARANCE
    ]


@pytest.mark.asyncio
async def test_an_ordinary_message_gets_no_warning(
    client, auth_headers, mock_ai_service
):
    body = await _ask(
        client, auth_headers, "Schöne Runde heute, keine Brustschmerzen mehr."
    )

    assert "red_flag" not in [n["kind"] for n in body["notes"]]
    assert await _states(auth_headers) == []


@pytest.mark.asyncio
async def test_a_heart_rate_medication_names_a_doctor_once(
    client, auth_headers, mock_ai_service
):
    first = await _ask(
        client,
        auth_headers,
        "Ich nehme seit einer Woche Betablocker gegen meinen Bluthochdruck, das wollte ich dir noch sagen.",
    )
    # A fresh reply, as the model gives one each turn: the mock hands back one
    # shared dict, and the route stores the turn's notes on it.
    mock_ai_service["ask_trainer"].return_value = {
        **mock_ai_service["ask_trainer"].return_value,
        "notes": [],
    }
    again = await _ask(client, auth_headers, "Ich nehme ja Betablocker, wie gesagt.")

    assert "medication_doctor" in [n["kind"] for n in first["notes"]]
    doctor = next(n for n in first["notes"] if n["kind"] == "medication_doctor")
    assert "Arzt" in doctor["text"]
    assert "medication_doctor" not in [n["kind"] for n in again["notes"]]


@pytest.mark.asyncio
async def test_the_medication_note_is_in_english_for_an_english_message(
    client, auth_headers, mock_ai_service
):
    body = await _ask(
        client,
        auth_headers,
        "I started taking beta blockers for my blood pressure last week, just so you know.",
    )

    doctor = next(n for n in body["notes"] if n["kind"] == "medication_doctor")
    assert "doctor" in doctor["text"]
