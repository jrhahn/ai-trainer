"""A race the athlete says is moved replaces the race they have (ai-trainer-ops#175, comment 5111).

The persona run of 2026-10-21: "Das Rennen wird verschoben: neues Datum ist der
17. Januar" offered 17 January as a calendar race and kept the profile's 30
December beside it, so the plan would have worked toward both.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

import models
from services.dates import app_today
from services.race_intent import stated_race_move
from tests.conftest import TestSessionLocal

TODAY = date(2026, 10, 21)
ASK = "/api/v1/ai/ask-trainer"
MOVED = "Das Rennen wird verschoben, es ist jetzt in 3 Wochen."


@pytest.mark.parametrize(
    ("message", "moved"),
    [
        ("Das Rennen wird verschoben: neues Datum ist der 17. Januar, gleiche Strecke.", True),
        ("My race is postponed to 17 January.", True),
        ("Race rescheduled, the new date is 17 January", True),
        ("Der Radmarathon ist jetzt am 3. Mai 2027.", True),
        ("I have a race on 17 January.", False),
        ("Ich habe in drei Wochen ein Rennen.", False),
        # "statt am" swaps a training day, it does not move the race (review of #1005).
        ("Statt am Samstag trainiere ich am Sonntag, Rennen am 5. Mai.", False),
    ],
)
def test_a_move_is_told_from_a_new_race(message, moved):
    assert stated_race_move(message, TODAY) is moved


def _in(days: int) -> str:
    return (app_today() + timedelta(days=days)).isoformat()


async def _with(*, race_date: str | None = None, events: tuple[str, ...] = ()) -> list[str]:
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.training_goal, user.race_date = ("race", race_date) if race_date else ("general_fitness", None)
        rows = [models.RaceEvent(user_id=user.id, date=day, distance_km=100, elevation_m=1500) for day in events]
        db.add_all(rows)
        await db.commit()
        return [row.id for row in rows]


@pytest.mark.asyncio
async def test_moving_the_profiles_race_replaces_it(client, auth_headers, mock_ai_service):
    await _with(race_date=_in(70))

    offer = (await client.post(ASK, headers=auth_headers, json={"question": MOVED})).json()["raceOffer"]

    assert offer == {"date": _in(21), "replaces": {"date": _in(70), "kind": "profile", "eventId": None}}


@pytest.mark.asyncio
async def test_moving_the_one_calendar_race_replaces_it(client, auth_headers, mock_ai_service):
    [event_id] = await _with(events=(_in(70),))

    offer = (await client.post(ASK, headers=auth_headers, json={"question": MOVED})).json()["raceOffer"]

    assert offer["replaces"] == {"date": _in(70), "kind": "calendar", "eventId": event_id}


@pytest.mark.asyncio
async def test_with_two_races_ahead_nobody_can_tell_which_moved(client, auth_headers, mock_ai_service):
    await _with(events=(_in(60), _in(90)))

    offer = (await client.post(ASK, headers=auth_headers, json={"question": MOVED})).json()["raceOffer"]

    assert offer == {"date": _in(21), "replaces": None}


@pytest.mark.asyncio
async def test_a_new_race_beside_the_profiles_is_added_not_moved(client, auth_headers, mock_ai_service):
    await _with(race_date=_in(70))

    offer = (await client.post(ASK, headers=auth_headers, json={"question": "Ich habe in drei Wochen ein Rennen."})).json()["raceOffer"]

    assert offer == {"date": _in(21), "replaces": None}


@pytest.mark.asyncio
async def test_the_profiles_race_is_the_one_moved_beside_several_calendar_races(client, auth_headers, mock_ai_service):
    await _with(race_date=_in(70), events=(_in(50), _in(90)))

    offer = (await client.post(ASK, headers=auth_headers, json={"question": MOVED})).json()["raceOffer"]

    assert offer["replaces"] == {"date": _in(70), "kind": "profile", "eventId": None}
