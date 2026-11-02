"""A known condition gets a doctor's word, once (review of 2026-11-01, training
A2, ai-trainer-ops#338).

The prompt asked the model to suggest a doctor for a pre-existing condition, and
nothing checked it: Werner's "Bluthochdruck" got watt targets and no doctor.
"""

from __future__ import annotations

import pytest

from domain import conditions
from tests.test_red_flags_338 import _ask


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("Ich habe seit Jahren Bluthochdruck.", conditions.HYPERTENSION),
        (
            "Mein Blutdruck ist zu hoch, also hoher Blutdruck, sagt der Arzt.",
            conditions.HYPERTENSION,
        ),
        ("I have high blood pressure, is that a problem?", conditions.HYPERTENSION),
        ("Vor drei Jahren hatte ich einen Herzinfarkt.", conditions.HEART),
        ("Ich habe Vorhofflimmern.", conditions.HEART),
        ("Ich habe Herzprobleme seit der Corona-Infektion.", conditions.HEART),
        ("I had a heart attack in 2019 and got a stent.", conditions.HEART),
        ("Ich bin Typ-1-Diabetiker.", conditions.DIABETES),
        ("I'm diabetic, type 2.", conditions.DIABETES),
    ],
)
def test_a_condition_of_the_athletes_own_is_named(message, kind):
    assert conditions.names_condition(message) == kind


@pytest.mark.parametrize(
    "message",
    [
        "Ich habe keinen Bluthochdruck und keine Herzprobleme.",
        "No heart problems, no diabetes, all good.",
        "Mein Vater hat Diabetes, ich zum Glück nicht.",
        "My wife has high blood pressure.",
        "Mein Blutdruck ist völlig normal.",
        "Schöne Runde heute, 2 Stunden locker.",
        "",
    ],
)
def test_no_condition_where_none_of_the_athletes_own_is_named(message):
    assert conditions.names_condition(message) is None


def _fresh_reply(mock_ai_service):
    # A fresh reply, as the model gives one each turn: the mock hands back one
    # shared dict, and the route stores the turn's notes on it.
    mock_ai_service["ask_trainer"].return_value = {
        **mock_ai_service["ask_trainer"].return_value,
        "notes": [],
    }


def _kinds(body: dict) -> list[str]:
    return [note["kind"] for note in body["notes"]]


@pytest.mark.asyncio
async def test_a_condition_names_a_doctor_once(client, auth_headers, mock_ai_service):
    first = await _ask(
        client,
        auth_headers,
        "Ich habe übrigens Bluthochdruck, nehme aber nichts dagegen.",
    )
    _fresh_reply(mock_ai_service)
    again = await _ask(
        client, auth_headers, "Wegen dem Bluthochdruck: ist Intervalltraining okay?"
    )

    doctor = next(n for n in first["notes"] if n["kind"] == "condition_doctor")
    assert "Ärztin oder deinem Arzt" in doctor["text"]
    assert "condition_doctor" not in _kinds(again)


@pytest.mark.asyncio
async def test_the_note_is_english_for_an_english_message(
    client, auth_headers, mock_ai_service
):
    body = await _ask(client, auth_headers, "Just so you know, I'm diabetic, type 2.")

    doctor = next(n for n in body["notes"] if n["kind"] == "condition_doctor")
    assert "doctor" in doctor["text"]


@pytest.mark.asyncio
async def test_a_medication_or_warning_note_is_not_doubled(
    client, auth_headers, mock_ai_service
):
    # The medication note already names the doctor in this reply.
    medication = await _ask(
        client,
        auth_headers,
        "Ich nehme seit einer Woche Betablocker gegen meinen Bluthochdruck.",
    )
    _fresh_reply(mock_ai_service)
    # And after it, the condition note is not owed any more.
    later = await _ask(client, auth_headers, "Ich habe Bluthochdruck, wie gesagt.")

    assert "medication_doctor" in _kinds(medication)
    assert "condition_doctor" not in _kinds(medication)
    assert "condition_doctor" not in _kinds(later)


@pytest.mark.asyncio
async def test_a_warning_sign_with_a_condition_gets_the_warning_only(
    client, auth_headers, mock_ai_service
):
    body = await _ask(
        client,
        auth_headers,
        "Ich habe Bluthochdruck und heute Brustschmerzen beim Bergauffahren.",
    )

    assert _kinds(body)[0] == "red_flag"
    assert "condition_doctor" not in _kinds(body)


@pytest.mark.asyncio
async def test_someone_elses_condition_gets_no_note(
    client, auth_headers, mock_ai_service
):
    body = await _ask(
        client, auth_headers, "Mein Vater hat Diabetes, deshalb achte ich aufs Essen."
    )

    assert "condition_doctor" not in _kinds(body)
