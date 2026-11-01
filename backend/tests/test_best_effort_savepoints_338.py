"""Three more best-effort steps run in a savepoint of their own (review of
2026-11-01, ai-trainer-ops#338 C9; the pattern of #281)."""

from __future__ import annotations

import pytest

import crud
import models
from services import learning_pipeline, plan_context, plan_pipeline
from tests.conftest import TestSessionLocal
from tests.factories import create_planned_user
from tests.test_best_effort_savepoints_281 import _fail_a_flush

DAY = {
    "date": "2099-01-05",
    "workoutType": "endurance",
    "title": "Ride",
    "description": "Easy.",
    "durationMinutes": 60,
}


async def _broken(db, *args, **kwargs):
    await _fail_a_flush(db)


@pytest.mark.asyncio
async def test_a_failing_learning_step_leaves_the_others_and_the_commit(monkeypatch):
    user_id = await create_planned_user("learn-338@example.com", [])
    monkeypatch.setattr(learning_pipeline, "_expire_stale_uncertainties_step", _broken)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        user.is_onboarded = True
        result = await learning_pipeline.run_learning_step(db, user)
        db.add(
            models.User(email="kept-338@example.com", name="Kept", hashed_password="x")
        )
        await db.commit()

    # Only the broken step failed: the ones after it ran on a usable session.
    assert result.failed_steps == ["expiries"]
    async with TestSessionLocal() as db:
        assert await crud.get_user_by_email_simple(db, "kept-338@example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        (crud, "list_active_plan_commitments"),
        (plan_context, "prior_check_for_user"),
    ],
)
async def test_a_failing_read_in_the_plan_write_leaves_the_write(monkeypatch, target):
    user_id = await create_planned_user(f"plan-338-{target[1]}@example.com", [])
    monkeypatch.setattr(*target, _broken)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, [DAY], base_plan=[], source="generate"
        )
        await db.commit()

    async with TestSessionLocal() as db:
        stored = await crud.get_training_plan(db, user_id)
    assert [d["date"] for d in stored.plan] == ["2099-01-05"]
