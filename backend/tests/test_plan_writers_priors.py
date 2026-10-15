"""The plan writers without a reply, held to the hard priors (ai-trainer-ops#87).

A full generation, the nightly adaptation and the adaptation after a ride each
write once more when their plan breaks a hard prior on a day they change. What
survives reaches the athlete as a note on the day, which the persist gate sets
on every write.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

import crud
import models
from auth import hash_password
from services import ai_service, plan_pipeline, plan_priors
from tests.conftest import TestSessionLocal

BACK_TO_BACK = plan_priors.PriorDeparture(
    plan_priors.PRIOR_BACK_TO_BACK,
    plan_priors.EVIDENCE_HEURISTIC,
    "Intensity on consecutive days.",
    (date(2026, 4, 16), date(2026, 4, 17)),
)


def _breaks(updates):
    return [BACK_TO_BACK] if any(u.get("workoutType") == "intervals" for u in updates or []) else []


def _model(*replies):
    calls: list[str] = []

    async def chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        calls.append(user_msg)
        return json.dumps(replies[len(calls) - 1])

    return chat, calls


INTERVALS = {"date": "2026-04-16", "workoutType": "intervals", "title": "VO2", "description": "5x4", "durationMinutes": 60}
EASY = {"date": "2026-04-16", "workoutType": "endurance", "title": "Easy", "description": "Z2", "durationMinutes": 60}
PLAN = [{"date": "2026-04-16", "workoutType": "endurance", "title": "Ride", "description": "Z2", "durationMinutes": 60}]
PROFILE = {"name": "Rider", "weeklyHours": 8, "currentFTP": 250}


@pytest.mark.asyncio
async def test_the_nightly_adaptation_writes_once_more_when_it_breaks_a_hard_prior():
    chat, calls = _model({"updatedDays": [INTERVALS]}, {"updatedDays": [EASY]})

    with patch.object(ai_service, "_chat", side_effect=chat):
        updates = await ai_service.adapt_training_plan(PLAN, [], PROFILE, prior_check=_breaks)

    assert updates == [EASY]
    assert len(calls) == 2
    assert BACK_TO_BACK.statement in calls[1]


@pytest.mark.asyncio
async def test_the_adaptation_after_a_ride_writes_once_more_when_it_breaks_a_hard_prior():
    chat, calls = _model(
        {"response": "Go hard.", "planUpdates": [INTERVALS]},
        {"response": "Keep it easy.", "planUpdates": [EASY]},
    )

    with patch.object(ai_service, "_chat", side_effect=chat):
        result = await ai_service.recommend_next_session([], PLAN, PROFILE, prior_check=_breaks)

    assert result["plan_updates"] == [EASY]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_new_plan_writes_once_more_when_it_breaks_a_hard_prior():
    chat, calls = _model({"plan": [INTERVALS]}, {"plan": [EASY]})

    with patch.object(ai_service, "_chat", side_effect=chat):
        plan = await ai_service.generate_training_plan(PROFILE, prior_check=_breaks)

    assert [d["workoutType"] for d in plan] == ["endurance"]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_writer_that_breaks_no_prior_is_asked_once():
    chat, calls = _model({"updatedDays": [EASY]})

    with patch.object(ai_service, "_chat", side_effect=chat):
        await ai_service.adapt_training_plan(PLAN, [], PROFILE, prior_check=_breaks)

    assert len(calls) == 1


async def _user_with(plan: list[dict]) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=f"notes-{len(plan)}-{plan[0]['date']}@example.com",
            name="Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            current_ftp=250,
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, plan)
        await db.commit()
        return user.id


def _day(on: date, kind: str) -> dict:
    return {"date": on.isoformat(), "workoutType": kind, "title": kind, "description": "", "durationMinutes": 60}


@pytest.mark.asyncio
async def test_a_day_that_still_breaks_a_hard_prior_carries_a_note_until_it_is_fixed():
    first = date.today() + timedelta(days=2)
    second = first + timedelta(days=1)
    user_id = await _user_with([_day(first, "intervals"), _day(second, "endurance")])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = (await crud.get_training_plan(db, user_id)).plan
        noted = await plan_pipeline.commit_plan_updates(
            db, user, [{"date": second.isoformat(), "workoutType": "intervals"}],
            base_plan=plan, source="coach_chat",
        )
        await db.commit()
    by_date = {d["date"]: d for d in noted.plan}
    assert by_date[first.isoformat()]["priorNotes"] == [plan_priors.PRIOR_BACK_TO_BACK]
    assert by_date[second.isoformat()]["priorNotes"] == [plan_priors.PRIOR_BACK_TO_BACK]

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = (await crud.get_training_plan(db, user_id)).plan
        fixed = await plan_pipeline.commit_plan_updates(
            db, user, [{"date": second.isoformat(), "workoutType": "endurance"}],
            base_plan=plan, source="coach_chat",
        )
        await db.commit()
    assert all("priorNotes" not in d for d in fixed.plan)


@pytest.mark.asyncio
async def test_a_note_is_no_change_to_the_day():
    """Notes follow the plan; refreshing one is not a change to narrate or record."""
    first = date.today() + timedelta(days=2)
    user_id = await _user_with([_day(first, "intervals"), _day(first + timedelta(days=1), "intervals")])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = (await crud.get_training_plan(db, user_id)).plan
        result = await plan_pipeline.commit_plan_updates(
            db, user, [{"date": first.isoformat(), "workoutType": "intervals"}],
            base_plan=plan, source="coach_chat",
        )
        await db.commit()
        history = await crud.list_plan_day_history(db, user_id, limit=10)

    assert result.applied_changes == []
    assert history == []
