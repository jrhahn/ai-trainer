"""An automated write leaves no session on a date the athlete ruled out, whatever
a guard hands back (ai-trainer-ops#316).

The guards of an automated run restore what the athlete pinned or rode, and
some of them never asked the constraints: a session the athlete had set by hand
came back onto a Thursday they had since ruled out, while the run's message
called Thursday free.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import crud
from domain.plan_day import planned_minutes
from services import plan_pipeline
from services.dates import app_today
from tests.conftest import TestSessionLocal
from tests.factories import create_planned_user


def _day(offset: int, **fields) -> dict:
    return {
        "date": (app_today() + timedelta(days=offset)).isoformat(),
        "workoutType": "strength",
        "sport": "strength",
        "title": "Kraft Ganzkörper",
        "description": "45 Minuten Kraft.",
        "durationMinutes": 45,
        **fields,
    }


async def _night(email: str, ruled_out: dict) -> dict:
    """The nightly run over a plan whose ruled-out day the athlete had pinned."""
    user_id = await create_planned_user(email, [ruled_out])
    async with TestSessionLocal() as db:
        await crud.upsert_availability_constraint(
            db,
            user_id,
            constraint_type="no_training",
            constraint_date=ruled_out["date"],
            expires_on=ruled_out["date"],
            reason="Athlete said they are unavailable for training.",
        )
        await db.commit()
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = (await crud.get_training_plan(db, user_id)).plan
        # The writer leaves the day as it was; the gate rests it, and the guard
        # for a day the athlete set by hand used to give it back.
        await plan_pipeline.commit_plan(
            db, user, current, base_plan=current, source="nightly_maintenance"
        )
        await db.commit()
        [stored] = (await crud.get_training_plan(db, user_id)).plan
    return stored


@pytest.mark.asyncio
async def test_a_pinned_session_does_not_come_back_on_a_ruled_out_day(client):
    stored = await _night("gate-316a@example.com", _day(3, source="user"))

    assert planned_minutes(stored) == 0


@pytest.mark.asyncio
async def test_a_session_already_done_stays_as_it_was(client):
    stored = await _night(
        "gate-316b@example.com", _day(0, source="user", completed=True)
    )

    assert planned_minutes(stored) == 45
