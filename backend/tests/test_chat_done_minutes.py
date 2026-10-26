"""A done day in the coach's context says what was ridden, not only what was
planned. The owner's chat of 2026-10-18: a 145-minute ride on a day planned for
75, and the coach answered "Das ist nach 75 Minuten bei Kälte … völlig normal"."""

from __future__ import annotations

import datetime
import json

import pytest

import models
from services import ai_service
from services.ride_matching import MATCH_AUTO, MATCH_MANUAL, done_minutes_by_session

DAY = "2026-10-18"


def _ride(seconds, *, date=DAY, slot=None, status=MATCH_AUTO):
    return models.RideMetric(
        matched_plan_date=date,
        matched_plan_slot=slot,
        plan_match_status=status,
        duration_seconds=seconds,
    )


def test_a_session_split_across_files_is_summed():
    # One session, two recordings (#543); a NULL slot is slot 0.
    rides = [_ride(3600), _ride(5100, slot=0, status=MATCH_MANUAL)]
    assert done_minutes_by_session(rides) == {(DAY, 0): 145}


def test_each_session_of_a_day_keeps_its_own_minutes():
    rides = [_ride(3600, slot=0), _ride(1800, slot=1)]
    assert done_minutes_by_session(rides) == {(DAY, 0): 60, (DAY, 1): 30}


def test_only_matched_rides_with_a_duration_count():
    rides = [
        _ride(3600, status="unmatched"),
        _ride(3600, status="ambiguous"),
        _ride(None),
        _ride(3600, date=None),
    ]
    assert done_minutes_by_session(rides) == {}


def test_a_done_day_carries_what_was_done_beside_the_plan():
    day = {"date": DAY, "durationMinutes": 75, "completed": True}
    slim = ai_service._slim_plan_entry(day, done_minutes=145)
    assert slim["durationMinutes"] == 75
    assert slim["actualDurationMinutes"] == 145


def test_a_session_logged_by_hand_brings_its_own_minutes():
    day = {
        "date": DAY,
        "durationMinutes": 60,
        "completed": True,
        "feedback": {"actualDurationMinutes": 50},
    }
    assert ai_service._slim_plan_entry(day)["actualDurationMinutes"] == 50


def test_a_day_not_done_shows_no_actual():
    day = {
        "date": DAY,
        "durationMinutes": 75,
        "feedback": {"actualDurationMinutes": 50},
    }
    assert "actualDurationMinutes" not in ai_service._slim_plan_entry(
        day, done_minutes=145
    )


@pytest.mark.asyncio
async def test_the_coach_sees_the_ride_beside_the_plan(monkeypatch):
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        ai_service, "app_today", lambda timezone_name=None: datetime.date(2026, 10, 18)
    )

    async def fake_chat_history(
        provider: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        task: str = "coach",
        response_schema: dict | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        return json.dumps({"response": "Gut gemacht.", "sources": []})

    monkeypatch.setattr(ai_service, "_chat_history", fake_chat_history)

    await ai_service.ask_trainer(
        "Die Fahrt war super, aber am Abend bin ich richtig müde.",
        plan=[
            {
                "date": DAY,
                "workoutType": "endurance",
                "title": "Endurance ride",
                "durationMinutes": 75,
                "completed": True,
            }
        ],
        profile={},
        timezone_name="Europe/Berlin",
        done_minutes={(DAY, 0): 145},
    )

    upcoming = captured["system_prompt"].split(
        "Upcoming plan (today and future only", 1
    )[1]
    assert '"durationMinutes": 75' in upcoming
    assert '"actualDurationMinutes": 145' in upcoming
    assert "actualDurationMinutes what the athlete did" in upcoming


@pytest.mark.asyncio
async def test_the_chat_route_passes_the_matched_minutes(
    client, auth_headers, mock_ai_service
):
    import crud
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        ride = await crud.upsert_ride_metric(
            db,
            user_id,
            strava_activity_id=71001,
            activity_date=DAY,
            sport_type="Ride",
            activity_name="Morning Ride",
            duration_seconds=8700,
        )
        await crud.update_ride_match(
            db,
            ride,
            status=MATCH_AUTO,
            matched_plan_date=DAY,
            matched_plan_snapshot={"date": DAY, "durationMinutes": 75},
        )
        await db.commit()

    await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Die Fahrt war super."},
    )

    kwargs = mock_ai_service["ask_trainer"].call_args.kwargs
    assert kwargs["done_minutes"] == {(DAY, 0): 145}
