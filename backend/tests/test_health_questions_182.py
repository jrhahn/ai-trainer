"""Age and health, as an older athlete brings them up (ai-trainer-ops#182).

"given my age i want to stay healthy": nobody asked how old the athlete was,
nor about their health.
"""

from __future__ import annotations

from datetime import date

import pytest

from domain import age_health
from services.dates import app_today
from tests.test_condition_doctor_338 import _fresh_reply
from tests.test_opening_message_153 import _coach_messages, _onboard, model_reply  # noqa: F401
from tests.test_red_flags_338 import _ask

TODAY = date(2026, 11, 7)
ME = "/api/v1/users/me"


@pytest.mark.parametrize(
    ("text", "age"),
    [
        ("Ich bin 67 Jahre alt und will gesund bleiben.", 67),
        ("ich bin jetzt 58 jahre", 58),
        ("I'm 67 years old.", 67),
        ("I am 72 yrs old", 72),
        ("Mit 61 Jahre alt fühle ich mich noch fit.", 61),
        ("I'm 60 minutes late.", None),
        ("Ich bin 5 Jahre gefahren.", None),
        ("I'm 150 years old", None),
        ("Ich bin 70 km gefahren.", None),
    ],
)
def test_an_age_is_read_only_where_it_is_one(text, age):
    assert age_health.stated_age(text) == age


@pytest.mark.parametrize(
    "text",
    [
        "given my age i want to stay healthy",
        "In meinem Alter muss ich aufpassen.",
        "Ich bin über 60, geht das noch?",
        "I'm in my sixties.",
    ],
)
def test_age_without_a_number_is_asked_while_the_profile_has_none(text):
    assert age_health.age_unasked(text, None)
    assert not age_health.age_unasked(text, 1960)


@pytest.mark.parametrize(
    "text",
    ["Ich bin 67 Jahre alt, in meinem Alter …", "Wie lange soll ich fahren?", ""],
)
def test_an_age_said_or_none_brought_up_asks_nothing(text):
    assert not age_health.age_unasked(text, None)


@pytest.mark.parametrize(
    ("goal", "birth_year", "due"),
    [
        ("health", None, True),
        ("general_fitness", 1966, True),
        ("general_fitness", 1967, False),
        ("race", None, False),
    ],
)
def test_the_health_check_goes_to_a_health_goal_and_from_sixty(goal, birth_year, due):
    assert age_health.health_check_due(goal, birth_year, TODAY) is due


@pytest.mark.asyncio
async def test_the_reply_asks_the_age(client, auth_headers, mock_ai_service):
    _fresh_reply(mock_ai_service)

    body = await _ask(client, auth_headers, "In meinem Alter muss ich aufpassen.")

    note = next(n for n in body["notes"] if n["kind"] == "age_unknown")
    assert note["text"] == age_health.AGE_NOTE["German"]


@pytest.mark.asyncio
async def test_a_stored_birth_year_asks_nothing(client, auth_headers, mock_ai_service):
    _fresh_reply(mock_ai_service)
    await client.put(ME, headers=auth_headers, json={"birthYear": 1958})

    body = await _ask(client, auth_headers, "In meinem Alter muss ich aufpassen.")

    assert "age_unknown" not in [n["kind"] for n in body["notes"]]


@pytest.mark.asyncio
async def test_an_age_said_is_offered_as_the_birth_year(
    client, auth_headers, mock_ai_service
):
    _fresh_reply(mock_ai_service)

    body = await _ask(client, auth_headers, "Ich bin 67 Jahre alt.")

    assert body["profileProposal"]["birthYear"] == app_today().year - 67
    offers = mock_ai_service["ask_trainer"].call_args.kwargs.get("app_offers") or []
    assert any("year of birth" in offer for offer in offers)


@pytest.mark.asyncio
async def test_the_same_birth_year_is_not_offered_again(
    client, auth_headers, mock_ai_service
):
    _fresh_reply(mock_ai_service)
    await client.put(
        ME, headers=auth_headers, json={"birthYear": app_today().year - 67}
    )

    body = await _ask(client, auth_headers, "Ich bin 67 Jahre alt.")

    assert body.get("profileProposal") is None


@pytest.mark.asyncio
async def test_the_first_message_asks_an_older_athlete_about_health(
    client,
    auth_headers,
    model_reply,  # noqa: F811
):
    await client.put(ME, headers=auth_headers, json={"birthYear": 1958})

    await _onboard(client, auth_headers)

    [message] = await _coach_messages()
    assert age_health.HEALTH_CHECK["German"] in message.content


@pytest.mark.asyncio
async def test_the_first_message_to_a_younger_athlete_asks_nothing(
    client,
    auth_headers,
    model_reply,  # noqa: F811
):
    await client.put(ME, headers=auth_headers, json={"birthYear": 1990})

    await _onboard(client, auth_headers)

    [message] = await _coach_messages()
    assert "Bevor wir loslegen" not in message.content
