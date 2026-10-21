"""The coach knows what the buttons under its reply offer (ai-trainer-ops#195).

Persona run 3 of 2026-10-22: "Das Rennen wird verschoben: neues Datum ist der
17. Januar" got "der 17. Januar bleibt als neuer Zieltermin fest eingetragen",
while the move was only offered and the profile still had 30 December. The
offers are worked out before the model is called, and it was told none of them.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

import models
from routers.ai import _offers_for_prompt
from services.dates import app_today
from services.prompts import ask_trainer_system_sections
from tests.conftest import TestSessionLocal

ASK = "/api/v1/ai/ask-trainer"


def _offers_section(offers: list[str] | None) -> str:
    return ask_trainer_system_sections({}, "2026-10-22", [], [], "", "", "", "", app_offers=offers)["app-offers"]


def test_the_prompt_says_an_offer_is_not_saved_yet():
    section = _offers_section(["moving the race on 2026-12-30 to 2027-01-17"])

    assert "a button for moving the race on 2026-12-30 to 2027-01-17." in section
    assert "Nothing of it is saved until they press it. Never say it is entered" in section


def test_without_an_offer_the_prompt_is_unchanged():
    assert _offers_section(None) == ""
    assert _offers_section([]) == ""


def test_every_offer_is_named():
    offers = _offers_for_prompt(
        {"date": "2027-01-17", "replaces": {"date": "2026-12-30", "kind": "profile"}},
        {"race_date": "2026-12-30", "race_description": "Puig Major", "calendar_races": 1},
        {"weekly_hours": 6, "training_days": ["monday", "thursday"], "sports": ["cycling", "running"], "ftp": 250},
    )

    assert offers == [
        "moving the race on 2026-12-30 to 2027-01-17",
        "removing the race from the profile and the calendar",
        "setting the weekly hours to 6",
        "setting the training days to monday, thursday",
        "setting the sports to cycling, running",
        "setting the FTP to 250 W",
    ]
    assert _offers_for_prompt({"date": "2027-01-17", "replaces": None}, None, None) == [
        "adding a race on 2027-01-17 to the calendar"
    ]
    assert _offers_for_prompt(None, None, None) == []


@pytest.mark.asyncio
async def test_the_chat_tells_the_coach_about_the_race_it_offers_to_move(client, auth_headers, mock_ai_service):
    race_day = (app_today() + timedelta(days=70)).isoformat()
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.training_goal, user.race_date = "race", race_day
        await db.commit()

    reply = await client.post(ASK, headers=auth_headers, json={"question": "Das Rennen wird verschoben, es ist jetzt in 3 Wochen."})

    moved_to = (app_today() + timedelta(days=21)).isoformat()
    assert reply.json()["raceOffer"]["date"] == moved_to
    assert mock_ai_service["ask_trainer"].call_args.kwargs["app_offers"] == [
        f"moving the race on {race_day} to {moved_to}"
    ]


@pytest.mark.asyncio
async def test_a_plain_question_names_no_offer(client, auth_headers, mock_ai_service):
    await client.post(ASK, headers=auth_headers, json={"question": "Wie war mein Training diese Woche?"})

    assert mock_ai_service["ask_trainer"].call_args.kwargs["app_offers"] == []
