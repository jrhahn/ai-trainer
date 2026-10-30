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
        # Written out, as a beginner writes it (ai-trainer-ops#321).
        (
            "Ich fange gerade erst mit dem Radfahren an und habe drei Stunden pro Woche.",
            {"weekly_hours": 3.0},
        ),
        ("I have three hours a week.", {"weekly_hours": 3.0}),
        ("Zweieinhalb Stunden die Woche gehen.", {"weekly_hours": 2.5}),
        ("I can do two and a half hours a week.", {"weekly_hours": 2.5}),
        ("Anderthalb Std. pro Woche, mehr ist nicht drin.", {"weekly_hours": 1.5}),
        ("I have five hours a week more or less.", {"weekly_hours": 5.0}),
        ("Fünf Stunden pro Woche mehr oder weniger.", {"weekly_hours": 5.0}),
        ("Nicht mehr als fünfzehn Stunden pro Woche.", {"weekly_hours": 15.0}),
        ("Zwoelf Stunden pro Woche.", {"weekly_hours": 12.0}),
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
        (
            "Kein Radfahren mehr, nur Krafttraining jetzt auch.",
            {"sports": ["strength"]},
        ),
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
        # Written out, the same: a report, a question, a number past forty.
        "Last week I did twelve hours a week.",
        "Reichen drei Stunden pro Woche?",
        "Fünfzig Stunden pro Woche im Büro.",
        # One hour in words is one session as often as the week.
        "I have an hour a week for stretching.",
        "Ich trainiere eine Stunde pro Woche mit dem Trainer.",
        "One hour a week of yoga.",
        # A change by so many hours, not the hours the athlete has.
        "Ich kann zwei Stunden pro Woche mehr trainieren.",
        "I can add another three hours a week.",
        "Zusätzlich vier Stunden pro Woche gehen.",
        "I want to ride 2 hours a week less.",
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
    assert (user.weekly_hours, user.training_days, user.sports) == (
        4,
        ["saturday"],
        ["cycling"],
    )


@pytest.mark.asyncio
async def test_the_chat_proposes_and_does_not_apply(
    client, auth_headers, mock_ai_service
):
    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"weeklyHours": 6, "trainingDays": ["saturday"], "sports": ["cycling"]},
    )

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={
            "question": "Ab jetzt nur noch 4 Stunden pro Woche, und ich laufe jetzt auch."
        },
    )

    assert response.json()["profileProposal"] == {
        "weeklyHours": 4.0,
        "trainingDays": None,
        "sports": ["cycling", "running"],
        # No FTP was said (ai-trainer-ops#129).
        "ftp": None,
        # Nor a level or a max heart rate (ai-trainer-ops#202).
        "fitnessLevel": None,
        "maxHeartRate": None,
        # Not a swap of the days for a preferred one (ai-trainer-ops#204).
        "standingDaySwap": False,
    }
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
    assert (user.weekly_hours, user.sports) == (6, ["cycling"])


@pytest.mark.parametrize(
    "word, hours",
    [
        ("two", 2.0),
        ("nineteen", 19.0),
        ("twenty", 20.0),
        ("forty", 40.0),
        ("zwei", 2.0),
        ("neunzehn", 19.0),
        ("zwanzig", 20.0),
        ("dreißig", 30.0),
        ("dreissig", 30.0),
        ("vierzig", 40.0),
        ("fuenfzehn", 15.0),
        ("eineinhalb", 1.5),
        ("one and a half", 1.5),
        ("zwölfeinhalb", 12.5),
    ],
)
def test_each_number_word_is_its_number(word, hours):
    """The words are zipped onto their values: one out of place moves every
    number after it (ai-trainer-ops#321)."""
    message = f"Ich habe {word} Stunden pro Woche."
    assert propose_profile_change(message, **PROFILE) == {"weekly_hours": hours}
