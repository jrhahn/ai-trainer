"""No trait about the athlete from one word in a chat (ai-trainer-ops#216).

The opening message suggests "Ich bin heute müde, können wir es lockerer
machen?". Sent on a fresh account with no ride, it stored "Session quality
tracks sleep and life stress for this athlete, and they notice it themselves"
as a rider-identity observation: the post-workout rules ran on any chat text.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

import crud
import models
from services import opening_message, workout_curiosity
from services.dates import app_today
from tests.conftest import TestSessionLocal

TIRED = "Ich bin heute müde, können wir es lockerer machen?"


async def _rider_id() -> str:
    async with TestSessionLocal() as db:
        return (await crud.get_user_by_email(db, "rider@example.com")).id


async def _observations(user_id: str) -> list[str]:
    async with TestSessionLocal() as db:
        rows = await db.scalars(
            select(models.AthleteMemoryFact.fact).where(
                models.AthleteMemoryFact.user_id == user_id,
                models.AthleteMemoryFact.category == workout_curiosity.MEMORY_CATEGORY,
            )
        )
        return list(rows)


async def _ride(user_id: str, days_ago: int) -> None:
    day = (app_today() - timedelta(days=days_ago)).isoformat()
    async with TestSessionLocal() as db:
        db.add(
            models.RideMetric(
                user_id=user_id,
                strava_activity_id=96000 + days_ago,
                activity_source="strava",
                external_activity_id=str(96000 + days_ago),
                activity_date=day,
                activity_start_datetime=f"{day}T08:00:00",
                activity_name="Ride",
                sport_type="Ride",
                duration_seconds=3600,
                tss=60.0,
            )
        )
        await db.commit()


async def _ask(client, auth_headers, question: str, **extra) -> None:
    response = await client.post(
        "/api/v1/ai/ask-trainer", headers=auth_headers, json={"question": question, **extra}
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_the_opening_examples_on_a_fresh_account_leave_no_trait(client, auth_headers, mock_ai_service):
    for example in (*opening_message._EXAMPLES_DE, *opening_message.EXAMPLES):
        await _ask(client, auth_headers, example)

    assert await _observations(await _rider_id()) == []


@pytest.mark.asyncio
async def test_a_ride_in_the_last_three_days_makes_it_a_session_report(client, auth_headers, mock_ai_service):
    user_id = await _rider_id()
    await _ride(user_id, days_ago=workout_curiosity.RECENT_SESSION_DAYS)

    await _ask(client, auth_headers, TIRED)

    assert await _observations(user_id) == [
        "Session quality tracks sleep and life stress for this athlete"
    ]


@pytest.mark.asyncio
async def test_an_older_ride_does_not(client, auth_headers, mock_ai_service):
    user_id = await _rider_id()
    await _ride(user_id, days_ago=workout_curiosity.RECENT_SESSION_DAYS + 1)

    await _ask(client, auth_headers, TIRED)

    assert await _observations(user_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("completed", "stored"), [(True, 1), (False, 0)])
async def test_a_message_sent_from_a_finished_session_is_one(client, auth_headers, mock_ai_service, completed, stored):
    workout = {"date": app_today().isoformat(), "title": "Endurance Ride", "completed": completed}

    await _ask(client, auth_headers, TIRED, contextWorkout=workout)

    assert len(await _observations(await _rider_id())) == stored
