"""An absence with no dates is asked about, not guessed (ai-trainer-ops#143).

"Ich bin die nächsten drei Wochen am Reisen" blocked nothing, and the coach
planned rides through the trip. The owner's rule of 2026-11-05: what is unclear,
the coach asks. The reply now asks for the first and the last day in a fixed
note, and nothing is blocked until they come.
"""

from __future__ import annotations

from datetime import date

import pytest

from domain.availability import absence_without_dates
from tests.test_condition_doctor_338 import _fresh_reply
from tests.test_red_flags_338 import _ask

TODAY = date(2026, 11, 5)


@pytest.mark.parametrize(
    "text",
    [
        "Ich bin die nächsten drei Wochen am Reisen und kann kein Training machen.",
        "Die nächsten zwei Wochen kann ich nicht trainieren.",
        "Nächste Woche bin ich im Urlaub.",
        "Ich bin bald weg, kein Rad dabei.",
        "Ich bin eine Weile verreist.",
        "I'm away for a few weeks.",
        "Next week I'm on holiday, no training.",
    ],
)
def test_an_absence_with_no_dates_is_unclear(text):
    assert absence_without_dates(text, today=TODAY)


@pytest.mark.parametrize(
    "text",
    [
        # Dated: the days are blocked, nothing to ask.
        "Ich bin die nächsten drei Wochen bis zum 26.11. am Reisen und kann kein "
        "Training machen.",
        "Vom 5. bis 26. November bin ich auf Reise.",
        "Ich bin die nächsten drei Wochen bis zum 26. November auf Reise.",
        # Over, with the bike, asked, back, or no absence at all.
        "Ich war zwei Wochen im Urlaub.",
        "Die nächsten Wochen bin ich mit dem Rad im Urlaub.",
        "Bin ich die nächsten Wochen weg?",
        "Nächste Woche bin ich wieder da.",
        "In zwei Wochen ist mein Rennen.",
        "Die nächsten Wochen will ich viel fahren.",
        "",
    ],
)
def test_a_dated_or_no_absence_is_not_unclear(text):
    assert not absence_without_dates(text, today=TODAY)


def _kinds(body: dict) -> list[str]:
    return [note["kind"] for note in body["notes"]]


@pytest.mark.asyncio
async def test_the_reply_asks_for_the_days(client, auth_headers, mock_ai_service):
    body = await _ask(
        client,
        auth_headers,
        "Ich bin die nächsten drei Wochen am Reisen und kann kein Training machen.",
    )

    note = next(n for n in body["notes"] if n["kind"] == "absence_dates")
    assert "Start- und Enddatum" in note["text"]


@pytest.mark.asyncio
async def test_the_question_is_english_for_an_english_message(
    client, auth_headers, mock_ai_service
):
    body = await _ask(client, auth_headers, "I'm away for a few weeks, no bike.")

    note = next(n for n in body["notes"] if n["kind"] == "absence_dates")
    assert "first and the last day" in note["text"]


@pytest.mark.asyncio
async def test_dates_given_ask_nothing(client, auth_headers, mock_ai_service):
    _fresh_reply(mock_ai_service)
    body = await _ask(
        client,
        auth_headers,
        "Vom 5. bis 26. November bin ich auf Reise, kein Rad dabei.",
    )

    assert "absence_dates" not in _kinds(body)
