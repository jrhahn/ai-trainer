"""A write pins only the days it was asked to change (review of 2026-11-01, ai-trainer-ops#338 C1).

Passes on the way (the FTP and zone alignment, the canonical backfill, a struck
sentence) adjust other days too. A chat edit stamped every adjusted day
``source: "user"``, which pins it against every automatic writer.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import crud
from services import plan_pipeline
from services.dates import app_today
from tests.factories import create_planned_user


def _iso(offset: int) -> str:
    return (app_today() + timedelta(days=offset)).isoformat()


def _ride(offset: int, text: str, **extra) -> dict:
    return {
        "date": _iso(offset),
        "workoutType": "endurance",
        "title": "Endurance",
        "description": text,
        "durationMinutes": 60,
        **extra,
    }


@pytest.mark.asyncio
async def test_a_chat_edit_pins_the_day_it_changed_and_no_other(db):
    """Day B was written at a 270 W FTP; the profile now says 250. The chat
    changes day A only. B's figures follow the FTP, but B stays unpinned."""
    a = _ride(2, "Easy hour.", source="generate")
    b = _ride(4, "Ride at 189–230 W, 70–85% of your 270 W FTP.", source="generate")
    user_id = await create_planned_user("pin-asked-338@example.com", [a, b])
    user = await crud.get_user_by_id(db, user_id)
    stored = (await crud.get_training_plan(db, user_id)).plan

    written = await plan_pipeline.commit_plan_updates(
        db,
        user,
        [{"date": a["date"], "workoutType": "recovery", "durationMinutes": 30}],
        base_plan=stored,
        source="coach_chat",
    )

    by_date = {d["date"]: d for d in written.plan}
    assert by_date[a["date"]]["source"] == plan_pipeline.USER_SOURCE
    # B was adjusted on the way (the FTP pass), and still is not the athlete's.
    assert "270 W" not in by_date[b["date"]]["description"]
    assert by_date[b["date"]].get("source") != plan_pipeline.USER_SOURCE


@pytest.mark.asyncio
async def test_a_day_the_athlete_sets_is_still_theirs(db):
    a = _ride(2, "Easy hour.", source="generate")
    user_id = await create_planned_user("pin-asked-338b@example.com", [a])
    user = await crud.get_user_by_id(db, user_id)
    stored = (await crud.get_training_plan(db, user_id)).plan

    written = await plan_pipeline.commit_plan_updates(
        db,
        user,
        [{"date": a["date"], "workoutType": "endurance", "durationMinutes": 90}],
        base_plan=stored,
        source="user_edit",
    )

    assert written.plan[0]["source"] == plan_pipeline.USER_SOURCE


@pytest.mark.asyncio
async def test_a_generation_stamps_every_day_it_writes(db):
    """A new plan is the generator's on every day it writes."""
    user_id = await create_planned_user("pin-asked-338c@example.com", [])
    user = await crud.get_user_by_id(db, user_id)

    written = await plan_pipeline.commit_plan(
        db,
        user,
        [_ride(2, "Easy hour."), _ride(4, "Steady hour.")],
        base_plan=[],
        source="generate",
    )

    assert {d.get("source") for d in written.plan} == {"generate"}


@pytest.mark.asyncio
async def test_the_strike_spares_only_the_days_the_chat_asked_to_change(db):
    """A chat move frees Wednesday. Its own new words stay; Tuesday, which the
    chat did not ask to change, loses its sentence about Wednesday's session."""
    monday = app_today() + timedelta(days=7 - app_today().weekday())
    mon, tue, wed = (str(monday + timedelta(days=n)) for n in range(3))
    plan = [
        {**_ride(0, "Easy ride.", source="generate"), "date": mon},
        {
            "date": tue,
            "workoutType": "rest",
            "title": "Rest",
            "description": "Rest between your Monday and Wednesday sessions. Sleep well.",
            "durationMinutes": 0,
            "source": "generate",
        },
        {**_ride(0, "Steady ride.", source="generate"), "date": wed},
    ]
    user_id = await create_planned_user("pin-asked-338d@example.com", plan)
    user = await crud.get_user_by_id(db, user_id)
    stored = (await crud.get_training_plan(db, user_id)).plan

    written = await plan_pipeline.commit_plan_updates(
        db,
        user,
        [
            {
                "date": wed,
                "workoutType": "rest",
                "durationMinutes": 0,
                "description": "Rest today and recover from Tuesday's ride.",
            }
        ],
        base_plan=stored,
        source="coach_chat",
    )

    by_date = {d["date"]: d for d in written.plan}
    assert by_date[tue]["description"] == "Sleep well."
    # Wednesday's words are the coach's, written now: kept, though they name a
    # session on a day that is rest.
    assert by_date[wed]["description"] == "Rest today and recover from Tuesday's ride."
