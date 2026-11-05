"""The plan row lock ends before a model call (review of 2026-11-01, C2,
ai-trainer-ops#338).

A plan write locks the athlete's plan row to the end of the transaction
(#282). The ride review wrote the plan and then asked the model for the
narration, and the sync marked the matched days and then asked the model for
two reviews, all with the row still locked: a chat reply waited for the sync.
That the lock lasts and that a commit ends it is seen on Postgres in
``test_postgres_338``; these pin where the commits sit.
"""

from __future__ import annotations

import pytest

import crud
from services import coach_summary, plan_pipeline
from tests.conftest import TestSessionLocal
from tests.test_ride_review_billing_244 import ACTIVITY, _plan_and_user


@pytest.mark.asyncio
async def test_the_ride_review_commits_its_write_before_the_narration(
    client, auth_headers, mock_ai_service, monkeypatch
):
    steps: list[str] = []
    write, release = plan_pipeline.commit_plan_updates, plan_pipeline.release_plan_lock
    narrate = coach_summary.narrate_plan_changes

    async def spy_write(*args, **kwargs):
        steps.append("write")
        return await write(*args, **kwargs)

    async def spy_release(db):
        steps.append("release")
        await release(db)

    async def spy_narrate(*args, **kwargs):
        steps.append("narrate")
        return await narrate(*args, **kwargs)

    monkeypatch.setattr(plan_pipeline, "commit_plan_updates", spy_write)
    monkeypatch.setattr(plan_pipeline, "release_plan_lock", spy_release)
    monkeypatch.setattr(coach_summary, "narrate_plan_changes", spy_narrate)
    await _plan_and_user(auth_headers)
    # The review proposes a change to the next day, so it writes and narrates.
    recommend = mock_ai_service["recommend_next_session"]
    recommend.return_value = {
        **(recommend.return_value or {}),
        "plan_updates": [{"date": "2026-05-05", "workoutType": "rest"}],
    }

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={"activities": [ACTIVITY]},
    )

    assert response.status_code == 200
    # The marking of the matched day writes and is released before the review;
    # the review's write is released before the narration's model call.
    assert steps == ["write", "release", "write", "release", "narrate"]


@pytest.mark.asyncio
async def test_inside_a_savepoint_the_release_leaves_the_transaction_open():
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email="c2-savepoint@example.com", hashed_password="x", name="C2"
        )
        async with db.begin_nested():
            await plan_pipeline.release_plan_lock(db)
            assert db.in_transaction()
        await plan_pipeline.release_plan_lock(db)
        assert not db.in_transaction()
        assert await crud.get_user_by_id(db, user.id) is not None


@pytest.mark.asyncio
async def test_the_analysis_commits_its_write_before_the_narration(monkeypatch):
    from types import SimpleNamespace

    from services import activity_analysis

    steps: list[str] = []

    async def write(*args, **kwargs):
        steps.append("write")
        return SimpleNamespace(batch_id="b", applied_changes=[], plan=[])

    async def release(db):
        steps.append("release")

    async def narrate(*args, **kwargs):
        steps.append("narrate")

    async def no_plan(*args, **kwargs):
        return None

    monkeypatch.setattr(crud, "get_training_plan", no_plan)
    monkeypatch.setattr(plan_pipeline, "commit_plan_updates", write)
    monkeypatch.setattr(plan_pipeline, "release_plan_lock", release)
    monkeypatch.setattr(coach_summary, "narrate_plan_changes", narrate)

    await activity_analysis.commit_plan_updates(
        None,
        SimpleNamespace(id="u-c2"),
        [{"date": "2026-06-02", "workoutType": "rest"}],
        source="ride_review",
    )

    assert steps == ["write", "release", "narrate"]
