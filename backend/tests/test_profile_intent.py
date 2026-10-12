"""Hours, training days and sports said in chat, as a change to confirm (ai-trainer-ops#43)."""

import pytest
from sqlalchemy import select

import models
from services.profile_intent import propose_profile_change
from tests.conftest import TestSessionLocal

PROFILE = {
    "weekly_hours": 6,
    "training_days": ["tuesday", "thursday", "saturday", "sunday"],
    "sports": ["cycling"],
}


@pytest.mark.parametrize(
    "message, proposal",
    [
        ("Ab jetzt habe ich nur noch 4 Stunden pro Woche.", {"weekly_hours": 4.0}),
        ("I can do 8 hours a week now.", {"weekly_hours": 8.0}),
        ("7,5 Std. die Woche gehen.", {"weekly_hours": 7.5}),
        (
            "Ich kann nur noch dienstags und samstags trainieren.",
            {"training_days": ["tuesday", "saturday"]},
        ),
        (
            "From now on Monday, Wednesday and Friday.",
            {"training_days": ["monday", "wednesday", "friday"]},
        ),
        ("I've started running.", {"sports": ["cycling", "running"]}),
        ("Ich laufe jetzt auch.", {"sports": ["cycling", "running"]}),
        ("I also do strength now.", {"sports": ["cycling", "strength"]}),
        ("Kein Radfahren mehr, nur Krafttraining jetzt auch.", {"sports": ["strength"]}),
    ],
)
def test_a_stated_change_is_proposed(message, proposal):
    assert propose_profile_change(message, **PROFILE) == proposal


@pytest.mark.parametrize(
    "message",
    [
        # Reports, questions and one-off days are not the profile.
        "Last week I did 10 hours a week.",
        "Can I do 8 hours a week?",
        "Every Saturday a long ride.",
        "Thursday I have no time.",
        "Keine Zeit fürs Laufen.",
        # Dropping the only sport leaves nothing to plan.
        "Ich habe mit dem Radfahren aufgehört.",
        # Already the profile.
        "I have 6 hours a week.",
        "I only train on Tuesday, Thursday, Saturday and Sunday.",
        "60 hours a week at work.",
    ],
)
def test_nothing_is_proposed(message):
    assert propose_profile_change(message, **PROFILE) is None


@pytest.mark.asyncio
async def test_the_chat_proposes_and_does_not_apply(client, auth_headers, mock_ai_service):
    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"weeklyHours": 6, "trainingDays": ["saturday"], "sports": ["cycling"]},
    )

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Ab jetzt nur noch 4 Stunden pro Woche, und ich laufe jetzt auch."},
    )

    assert response.json()["profileProposal"] == {
        "weeklyHours": 4.0,
        "trainingDays": None,
        "sports": ["cycling", "running"],
    }
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
    assert (user.weekly_hours, user.sports) == (6, ["cycling"])
