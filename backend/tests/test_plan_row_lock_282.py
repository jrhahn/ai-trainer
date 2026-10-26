"""Two writers of one plan no longer lose each other's days (ai-trainer-ops#282).

The plan pipeline read the stored plan with a plain select, merged, and wrote it
back. Another writer in between (the nightly job overlapping a chat edit, a sync
overlapping a manual edit) went unnoticed: in Postgres nothing locked the row,
and a session that had loaded the plan before — the chat does, before its
model call — got its stale copy back from the identity map. The merge, which
keeps whatever changed since the trigger read the plan, then saw no change and
wrote the old day back.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.dialects import postgresql

import crud
from services import plan_pipeline
from services.dates import app_today
from tests.conftest import TestSessionLocal
from tests.factories import create_planned_user
from tests.test_plan_pipeline import _day


@pytest.mark.asyncio
async def test_a_write_from_a_stale_session_keeps_a_day_another_writer_changed():
    first = (app_today() + timedelta(days=2)).isoformat()
    second = (app_today() + timedelta(days=4)).isoformat()
    user_id = await create_planned_user("plan-lock-282@example.com", [_day(first), _day(second)])

    async with TestSessionLocal() as chat:
        user = await crud.get_user_by_id(chat, user_id)
        # Loaded before the model call and held, as ask_trainer holds
        # existing_plan for the whole request. The identity map keeps the object
        # only while something refers to it.
        snapshot = await crud.get_training_plan(chat, user_id)
        base = list(snapshot.plan)

        async with TestSessionLocal() as nightly:
            nightly_user = await crud.get_user_by_id(nightly, user_id)
            await plan_pipeline.commit_plan_updates(
                nightly,
                nightly_user,
                [{"date": first, "workoutType": "recovery", "durationMinutes": 30}],
                base_plan=base,
                source="nightly_maintenance",
            )
            await nightly.commit()

        await plan_pipeline.commit_plan_updates(
            chat,
            user,
            [{"date": second, "workoutType": "tempo", "durationMinutes": 75}],
            base_plan=base,
            source="coach_chat",
        )
        await chat.commit()
        assert snapshot is not None  # keeps the stale copy in the identity map until here

    async with TestSessionLocal() as db:
        stored = {day["date"]: day for day in (await crud.get_training_plan(db, user_id)).plan}
    assert stored[first]["workoutType"] == "recovery"
    assert stored[second]["workoutType"] == "tempo"


def test_the_plan_write_locks_its_row_in_postgres_and_reads_it_fresh():
    statement = crud.locked_training_plan_select("u-1")
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    assert statement.get_execution_options()["populate_existing"] is True
