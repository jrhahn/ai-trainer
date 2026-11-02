"""Tiredness that lasts gets a blood-check note, once (review of 2026-11-01,
training A7, ai-trainer-ops#338).

"Seit einer Woche bin ich ständig müde und meine Wattwerte sind schlecht" got
three easy days and no word on a medical check; saying it again extended the
easy days, indefinitely.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import models
from domain import athlete_states, fatigue
from services.dates import app_today
from tests.conftest import TestSessionLocal
from tests.test_athlete_states import _user_id
from tests.test_condition_doctor_338 import _fresh_reply, _kinds
from tests.test_red_flags_338 import _ask


@pytest.mark.parametrize(
    "message",
    [
        "Meine Wattwerte sind schlecht.",
        "Die Leistung bricht ein.",
        "Meine Beine sind weg.",
        "My power is down.",
        "I can't hit my numbers.",
        "Schlechte Wattwerte seit Tagen.",
        "Ich komme nicht mehr auf meine Watt.",
        "Der Puls geht nicht mehr hoch.",
    ],
)
def test_lower_performance_is_heard(message):
    assert fatigue.says_performance_down(message)


@pytest.mark.parametrize(
    "message",
    [
        "Meine Wattwerte sind nicht schlecht.",
        "Die Form ist gut.",
        "My power is great.",
        # Off the plan, not off form (review of #1285).
        "Das Tempo war weg vom Plan.",
        "",
    ],
)
def test_no_lower_performance(message):
    assert not fatigue.says_performance_down(message)


@pytest.mark.parametrize(
    ("message", "weeks"),
    [
        ("Ich bin seit Wochen müde.", True),
        ("Seit drei Wochen ständig platt.", True),
        ("I've been tired for weeks.", True),
        ("Seit einem Monat kaputt.", True),
        ("Seit einer Woche müde.", False),
        ("I've been tired all week.", False),
    ],
)
def test_weeks_not_days(message, weeks):
    assert fatigue.says_weeks(message) is weeks


@pytest.mark.asyncio
async def test_tired_with_lower_watts_gets_the_note_once(
    client, auth_headers, mock_ai_service
):
    message = "Seit einer Woche bin ich ständig müde und meine Wattwerte sind schlecht."
    first = await _ask(client, auth_headers, message)
    _fresh_reply(mock_ai_service)
    again = await _ask(client, auth_headers, message)

    [note] = [n for n in first["notes"] if n["kind"] == "fatigue_doctor"]
    assert "Blutbild" in note["text"]
    assert "fatigue_doctor" not in _kinds(again)


@pytest.mark.asyncio
async def test_tired_for_weeks_gets_the_note_in_english(
    client, auth_headers, mock_ai_service
):
    body = await _ask(client, auth_headers, "I've been tired for weeks now.")

    [note] = [n for n in body["notes"] if n["kind"] == "fatigue_doctor"]
    assert "blood test" in note["text"]


@pytest.mark.asyncio
async def test_a_week_of_tiredness_alone_gets_easy_days_but_no_note(
    client, auth_headers, mock_ai_service
):
    body = await _ask(client, auth_headers, "Ich bin seit einer Woche ständig müde.")

    assert "fatigue_doctor" not in _kinds(body)


async def _tired_before(days: int) -> None:
    today = app_today()
    start = today - timedelta(days=days)
    async with TestSessionLocal() as db:
        db.add(
            models.AthleteState(
                user_id=await _user_id(),
                kind=athlete_states.KIND_TIRED,
                starts_on=start.isoformat(),
                ends_on=(start + timedelta(days=2)).isoformat(),
                said_on=start.isoformat(),
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_tiredness_said_again_ten_days_after_the_first_gets_the_note(
    client, auth_headers, mock_ai_service
):
    await _tired_before(fatigue.LASTING_DAYS)

    body = await _ask(client, auth_headers, "Ich bin seit Tagen platt.")

    assert "fatigue_doctor" in _kinds(body)


@pytest.mark.asyncio
async def test_tiredness_said_again_sooner_or_long_after_gets_no_note(
    client, auth_headers, mock_ai_service
):
    await _tired_before(fatigue.LASTING_DAYS - 1)
    await _tired_before(fatigue.LOOKBACK_DAYS + 1)

    body = await _ask(client, auth_headers, "Ich bin seit Tagen platt.")

    assert "fatigue_doctor" not in _kinds(body)


@pytest.mark.asyncio
async def test_lower_watts_without_tiredness_for_days_gets_no_note(
    client, auth_headers, mock_ai_service
):
    body = await _ask(client, auth_headers, "Meine Wattwerte sind heute schlecht.")

    assert "fatigue_doctor" not in _kinds(body)


@pytest.mark.asyncio
async def test_a_warning_sign_gets_its_own_note_only(
    client, auth_headers, mock_ai_service
):
    body = await _ask(
        client,
        auth_headers,
        "Ich bin seit Wochen ständig müde und habe Brustschmerzen beim Bergauffahren.",
    )

    assert "red_flag" in _kinds(body)
    assert "fatigue_doctor" not in _kinds(body)
