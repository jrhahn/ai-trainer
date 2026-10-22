"""Every onboarding answer can be changed later, in chat and in Settings (ai-trainer-ops#202).

The chat side: the level and the max heart rate are offered like the hours, the
days, the sports and the FTP. The plan side: a change of sports or level is a
reason to rebuild, like a change of the training days.
"""

import pytest
from sqlalchemy import select

import models
from services import plan_inputs
from services.profile_intent import propose_profile_change
from tests.conftest import TestSessionLocal

PROFILE = {
    "weekly_hours": None,
    "training_days": ["tuesday", "thursday", "saturday", "sunday"],
    "sports": ["cycling"],
    "fitness_level": "intermediate",
    "max_heart_rate": None,
}


@pytest.mark.parametrize(
    "message, proposal",
    [
        ("I'm a beginner.", {"fitness_level": "beginner"}),
        ("Ich bin eher Anfängerin.", {"fitness_level": "beginner"}),
        ("Ich bin noch Einsteiger beim Laufen.", {"fitness_level": "beginner"}),
        # German puts the verb second: "Ehrlich gesagt bin ich …".
        ("Ehrlich gesagt bin ich eher Anfänger.", {"fitness_level": "beginner"}),
        ("I would call myself advanced.", {"fitness_level": "advanced"}),
        ("Ich bin ziemlich fortgeschritten.", {"fitness_level": "advanced"}),
        ("Ich bin ein erfahrener Fahrer.", {"fitness_level": "advanced"}),
        ("My max heart rate is 186.", {"max_heart_rate": 186}),
        ("Mein Maximalpuls liegt bei 191.", {"max_heart_rate": 191}),
        ("HFmax 178", {"max_heart_rate": 178}),
        ("Max heart rate 186 bpm, and I'm a beginner.", {"max_heart_rate": 186, "fitness_level": "beginner"}),
    ],
)
def test_a_level_or_max_heart_rate_said_in_chat_is_proposed(message, proposal):
    assert propose_profile_change(message, **PROFILE) == proposal


@pytest.mark.parametrize(
    "message",
    [
        # About others or about a thing, not about oneself.
        "Can you make a plan for beginners?",
        "That was beginner's luck.",
        "Ich bin im fortgeschrittenen Alter.",
        # Said, but not this.
        "I'm not a beginner.",
        "Ich bin kein Anfänger mehr.",
        # The level the profile already has.
        "I'm an intermediate rider.",
        # Another heart rate, or none a heart can reach.
        "My average heart rate was 150.",
        "max HR: 250",
        "Max heart rate 99",
        # A question is not a statement.
        "Am I a beginner?",
    ],
)
def test_no_level_or_max_heart_rate_is_read_into_other_sentences(message):
    assert propose_profile_change(message, **PROFILE) is None


def test_a_max_heart_rate_the_profile_already_has_is_not_proposed():
    profile = {**PROFILE, "max_heart_rate": 186}
    assert propose_profile_change("My max heart rate is 186.", **profile) is None


BASE = {
    "trainingDays": ["saturday", "wednesday"],
    "ftp": 250,
    "activities": 3,
    "race": None,
    "assumptions": [],
    "hours": None,
    "raceEvents": [],
    "sports": ["cycling"],
    "level": "intermediate",
}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"sports": ["cycling", "running"]}, ["sports"]),
        ({"level": "beginner"}, ["level"]),
        (
            {"level": "advanced", "trainingDays": ["monday"], "sports": ["running"]},
            ["training_days", "sports", "level"],
        ),
    ],
)
def test_a_change_of_sports_or_level_is_a_reason_to_rebuild(changes, expected):
    assert plan_inputs.reasons(BASE, {**BASE, **changes}) == expected


def test_a_record_from_before_sports_and_level_were_kept_is_not_offered_a_rebuild_for_them():
    before = {key: value for key, value in BASE.items() if key not in ("sports", "level")}
    assert plan_inputs.reasons(before, {**BASE, "sports": ["running"], "level": "advanced"}) == []


@pytest.mark.asyncio
async def test_the_inputs_record_sports_and_level(client, auth_headers):
    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"sports": ["running", "cycling"], "fitnessLevel": "beginner"},
    )
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        inputs = await plan_inputs.current_inputs(db, user)

    assert inputs["sports"] == ["cycling", "running"]
    assert inputs["level"] == "beginner"


@pytest.mark.asyncio
async def test_the_chat_offers_a_level_and_a_max_heart_rate_and_sets_neither(client, auth_headers, mock_ai_service):
    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"fitnessLevel": "intermediate", "maxHeartRate": 180},
    )

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Ehrlich gesagt bin ich eher Anfänger. Mein Maximalpuls liegt bei 191."},
    )

    proposal = response.json()["profileProposal"]
    assert (proposal["fitnessLevel"], proposal["maxHeartRate"]) == ("beginner", 191)
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
    assert (user.fitness_level, user.max_heart_rate) == ("intermediate", 180)


@pytest.mark.asyncio
async def test_accepting_the_offer_sets_the_level_and_the_max_heart_rate(client, auth_headers):
    # The app accepts with the same PUT as every other profile change.
    response = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"fitnessLevel": "beginner", "maxHeartRate": 191},
    )

    assert response.status_code == 200
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
    assert (user.fitness_level, user.max_heart_rate) == ("beginner", 191)
