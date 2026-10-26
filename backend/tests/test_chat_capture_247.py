"""What the chat reads from the athlete's words, without the app (ai-trainer-ops#247).

``chat_capture.capture_from_message`` is what ask_trainer ran inline before it
asks the model. These tests call it with a session and a user, no FastAPI.
"""

from __future__ import annotations

import pytest

import schemas
from services import chat_capture

PLAN = [
    {
        "date": "2030-01-07",
        "workoutType": "endurance",
        "title": "Endurance Ride",
        "description": "Steady.",
        "durationMinutes": 60,
    }
]


async def _capture(db, user, message: str) -> chat_capture.Captured:
    profile = schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True)
    return await chat_capture.capture_from_message(
        db, user, message, plan=list(PLAN), profile=profile, timezone_name="UTC"
    )


@pytest.mark.asyncio
async def test_a_question_captures_nothing_and_keeps_the_plan(db, user):
    captured = await _capture(db, user, "How was my week?")

    assert captured.plan == PLAN
    assert captured.captured_constraints == []
    assert captured.captured_states == []
    assert captured.captured_goals == []
    assert captured.captured_day_preferences == []
    assert captured.race_proposal is None
    assert captured.race_offer is None
    assert captured.profile_proposal is None
    assert captured.heart_rate_stripped is False


@pytest.mark.asyncio
async def test_a_day_the_athlete_cannot_train_is_kept_and_reaches_the_prompt(db, user):
    captured = await _capture(db, user, "I can't train next Thursday.")

    assert len(captured.captured_constraints) == 1
    assert len(captured.availability_constraints) == 1
    # The profile the prompts read carries it, so the coach plans around it.
    assert (
        captured.profile["availabilityConstraints"] == captured.availability_constraints
    )


@pytest.mark.asyncio
async def test_an_illness_is_kept_as_a_state(db, user):
    captured = await _capture(db, user, "Ich bin krank, Halsweh und Schnupfen.")

    assert len(captured.captured_states) == 1


@pytest.mark.asyncio
async def test_beta_blockers_show_the_heart_rate_chip(db, user):
    captured = await _capture(db, user, "Ich nehme Betablocker.")

    assert [state["id"] for state in captured.captured_states] == [
        chat_capture.HEART_RATE_CAUTION_ID
    ]


@pytest.mark.asyncio
async def test_a_coming_race_is_offered_for_the_calendar(db, user):
    captured = await _capture(db, user, "I have a race in 3 weeks.")

    assert captured.race_offer is not None
    assert captured.race_proposal is None


@pytest.mark.asyncio
async def test_a_climbing_goal_is_kept(db, user):
    captured = await _capture(db, user, "Mein Ziel ist 4000 hm an einem Tag.")

    assert len(captured.captured_goals) == 1


@pytest.mark.asyncio
async def test_an_ftp_said_in_chat_is_offered_not_set(db, user):
    captured = await _capture(db, user, "My FTP is 255 W.")

    assert captured.profile_proposal == {"ftp": 255}
    assert user.current_ftp != 255
