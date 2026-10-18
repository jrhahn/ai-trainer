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
        ("I can do 7.5 hours a week now.", {"weekly_hours": 7.5}),
        ("I can do 7.25 hours a week now.", {"weekly_hours": 7.25}),
        ("1,25 Stunden pro Woche, mehr nicht.", {"weekly_hours": 1.25}),
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
        "I can only ride on Saturday this week.",
        "Diese Woche kann ich nur Samstag.",
        "Ich kann morgen nur kurz, dienstags geht nicht.",
        "On Sunday I only have an hour.",
        "Only Thursday I have no time.",
        "Ich habe nur Montag Zeit für ein kurzes Training.",
        "Add a run on Friday to the plan.",
    ],
)
def test_nothing_is_proposed(message):
    assert propose_profile_change(message, **PROFILE) is None


def test_the_last_stated_hours_win_even_when_they_are_the_profiles():
    message = "8 hours a week from now on. No wait, 6 hours a week is right."
    assert propose_profile_change(message, **PROFILE) is None


def test_the_last_stated_days_win_even_when_they_are_the_profiles():
    message = (
        "Only Tuesday and Saturday. "
        "Actually, my training days are Tuesday, Thursday, Saturday and Sunday."
    )
    assert propose_profile_change(message, **PROFILE) is None


def test_the_same_sports_in_another_order_are_no_change():
    profile = {**PROFILE, "sports": ["running", "cycling"]}
    message = "I've started strength. Actually, I stopped strength."
    assert propose_profile_change(message, **profile) is None


@pytest.mark.asyncio
async def test_confirming_hours_alone_leaves_sports_and_days(client, auth_headers):
    """What Update profile sends for an hours-only proposal: omitted fields stay."""
    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"weeklyHours": 6, "trainingDays": ["saturday"], "sports": ["cycling"]},
    )
    await client.put("/api/v1/users/me", headers=auth_headers, json={"weeklyHours": 4})
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
    assert (user.weekly_hours, user.training_days, user.sports) == (4, ["saturday"], ["cycling"])


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
        # No FTP was said (ai-trainer-ops#129).
        "ftp": None,
    }
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
    assert (user.weekly_hours, user.sports) == (6, ["cycling"])
