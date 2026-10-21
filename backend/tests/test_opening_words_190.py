"""The opening message gets the interface's words, and training days are kept Monday first (ai-trainer-ops#190)."""

import pytest
from sqlalchemy import select

import models
import schemas
from services import ai_service, opening_message
from tests.conftest import TestSessionLocal
from tests.test_opening_message_153 import _onboard

LENA_TAPPED = ["tuesday", "thursday", "saturday", "sunday", "wednesday"]


def test_days_are_put_in_week_order():
    assert schemas.week_ordered(LENA_TAPPED) == ["tuesday", "wednesday", "thursday", "saturday", "sunday"]
    assert schemas.week_ordered(["sunday", "monday", "someday"]) == ["monday", "sunday", "someday"]
    assert schemas.week_ordered(None) == []


@pytest.mark.asyncio
async def test_saved_days_are_stored_monday_first(client, auth_headers):
    response = await client.put("/api/v1/users/me", json={"trainingDays": LENA_TAPPED}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["trainingDays"] == ["tuesday", "wednesday", "thursday", "saturday", "sunday"]
    async with TestSessionLocal() as db:
        assert (await db.scalar(select(models.User))).training_days == [
            "tuesday", "wednesday", "thursday", "saturday", "sunday"
        ]


@pytest.mark.asyncio
async def test_a_row_stored_in_tap_order_is_read_monday_first(client, auth_headers):
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.training_days = ["saturday", "monday", "wednesday"]
        await db.commit()

    profile = (await client.get("/api/v1/users/me", headers=auth_headers)).json()

    assert profile["trainingDays"] == ["monday", "wednesday", "saturday"]


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (
            "German",
            {
                "sports": ["Radfahren", "Kraft"],
                "goal": "Rennvorbereitung",
                "trainingDays": ["Dienstag", "Mittwoch", "Donnerstag", "Samstag", "Sonntag"],
                "level": "Erfahren",
            },
        ),
        (
            "English",
            {
                "sports": ["Cycling", "Strength"],
                "goal": "Race Prep",
                "trainingDays": ["Tuesday", "Wednesday", "Thursday", "Saturday", "Sunday"],
                "level": "Advanced",
            },
        ),
    ],
)
def test_the_answers_are_said_in_the_interface_words(language, expected):
    answers = {"sports": ["cycling", "strength"], "goal": "race", "trainingDays": LENA_TAPPED, "level": "advanced", "ftpWatts": 250}
    assert opening_message.shown_answers(answers, language) == {**expected, "ftpWatts": 250}


@pytest.mark.asyncio
async def test_the_model_gets_german_words_for_a_german_account(client, auth_headers, monkeypatch):
    seen: list[dict] = []

    async def write(facts, language, sport, provider="openai"):
        seen.append(facts["answers"])
        return "Hallo!", []

    monkeypatch.setattr(ai_service, "write_opening_message", write)
    await _onboard(client, auth_headers, language="de")

    [answers] = seen
    # _onboard taps Wednesday and Saturday.
    assert answers["trainingDays"] == ["Mittwoch", "Samstag"]
    assert answers["sports"] == ["Radfahren"]
    raw = {"cycling", "running", "strength", "race", "general fitness", "beginner", "intermediate", "advanced", *schemas.WEEK_ORDER}
    assert not raw & {str(v).lower() for v in [*answers["sports"], *answers["trainingDays"], answers["goal"], answers.get("level", "")]}
