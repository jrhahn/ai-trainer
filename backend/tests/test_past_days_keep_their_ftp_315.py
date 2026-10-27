"""A plan write aligns the FTP and the zones from today on, never on past days
(ai-trainer-ops#315).

A chat edit of one coming day used to rewrite the FTP figures of a dozen past
sessions to the profile's current FTP, and the change log booked them under
the reply.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

import crud
import models
from services import plan_pipeline
from services.dates import app_today
from tests.conftest import TestSessionLocal

OLD_FTP_WORDS = "Ride 60 minutes at 150–190 W (Zone 2, 56–70% of your 270 W FTP)."


def _session(date: str, description: str) -> dict:
    return {
        "date": date,
        "sport": "cycling",
        "workoutType": "endurance",
        "title": "Endurance ride",
        "description": description,
        "durationMinutes": 60,
        "targetPower": {"low": 150, "high": 190},
    }


@pytest.mark.asyncio
async def test_a_chat_edit_leaves_the_words_of_past_days_as_they_were(
    client, auth_headers
):
    today = app_today()
    past = (today - timedelta(days=40)).isoformat()
    coming = (today + timedelta(days=2)).isoformat()
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.current_ftp = 300
        # The plan as written when the FTP was 270, before the athlete raised it.
        await crud.upsert_training_plan(
            db,
            user.id,
            [
                plan_pipeline._to_canonical_day(_session(date, OLD_FTP_WORDS))
                for date in (past, coming)
            ],
        )
        await db.commit()

    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        current = (await crud.get_training_plan(db, user.id)).plan
        await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": coming, "durationMinutes": 75}],
            base_plan=current,
            source="coach_chat",
        )
        await db.commit()
        stored = {
            d["date"]: d for d in (await crud.get_training_plan(db, user.id)).plan
        }
        logged = set(
            await db.scalars(
                select(models.PlanDayHistory.date).where(
                    models.PlanDayHistory.user_id == user.id
                )
            )
        )

    assert stored[past]["description"] == OLD_FTP_WORDS
    assert past not in logged
    # The coming day still names the profile's FTP.
    assert "300 W FTP" in stored[coming]["description"]
    assert coming in logged
