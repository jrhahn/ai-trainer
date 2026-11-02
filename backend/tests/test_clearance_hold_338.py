"""A warning sign holds every session until a doctor has checked it, by code
(ai-trainer-ops#338, A1; the owner's decision of 2026-11-02).

In the model runs the coach, rewritten once by the clearance prior, still left
sessions in the hold in 6 of 10 chest-pain replies and said it had rested them
all.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import crud
import models
from auth import decode_token
from domain.athlete_states import KIND_CLEARANCE, KIND_ILL, StatedState
from domain.plan_day import planned_minutes
from services import clearance_hold, plan_pipeline, plan_translations
from services.dates import app_today
from tests.conftest import TestSessionLocal
from tests.factories import create_planned_user

ASK = "/api/v1/ai/ask-trainer"
MONDAY = date(2026, 10, 12)


def _day(start: date, offset: int, **fields) -> dict:
    return {
        "date": (start + timedelta(days=offset)).isoformat(),
        "workoutType": "intervals",
        "title": "VO2 max 5x4",
        "description": "5x4 min hard.",
        "durationMinutes": 75,
        "intervals": [{"duration": 240, "power": 300, "rest": 240}],
        **fields,
    }


def _soon(seconds: int) -> datetime:
    """A history row's time, after the state it follows, in a set order."""
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _clearance(start: date, said: date | None = None) -> StatedState:
    return StatedState(KIND_CLEARANCE, start, None, said_on=said or start)


def test_every_session_in_the_hold_becomes_rest_with_the_reason():
    plan = [_day(MONDAY, offset) for offset in range(16)]

    held, reasons = clearance_hold.hold(
        plan, [_clearance(MONDAY)], today=MONDAY, done=set()
    )

    assert [planned_minutes(d) for d in held] == [0] * 14 + [75, 75]
    assert held[0]["description"] == clearance_hold.REASON
    assert held[0]["intervals"] is None
    assert held[0]["keyFocusPoints"] == list(clearance_hold.FOCUS)
    assert set(reasons) == {(d["date"], 0) for d in plan[:14]}


def test_a_day_done_ridden_or_past_stays_as_it_was():
    said = MONDAY - timedelta(days=2)
    plan = [
        _day(MONDAY, -1),
        _day(MONDAY, 0),
        _day(MONDAY, 1, completed=True),
        _day(MONDAY, 2),
    ]

    held, _ = clearance_hold.hold(
        plan, [_clearance(said)], today=MONDAY, done={plan[0]["date"], plan[1]["date"]}
    )

    assert [planned_minutes(d) for d in held] == [75, 75, 75, 0]


def test_no_hold_without_an_open_warning_sign():
    plan = [_day(MONDAY, 1)]
    ill = StatedState(KIND_ILL, MONDAY, None, said_on=MONDAY)
    cleared = StatedState(KIND_CLEARANCE, MONDAY - timedelta(days=3), MONDAY)

    assert clearance_hold.hold(plan, [ill, cleared], today=MONDAY, done=set()) == (
        plan,
        {},
    )


def test_the_hold_s_words_have_fixed_german():
    texts = {clearance_hold.REASON, clearance_hold.PURPOSE, *clearance_hold.FOCUS}

    assert texts <= plan_translations.FIXED["de"].keys()


async def _held_plan(email: str, source: str) -> list[dict]:
    today = app_today()
    user_id = await create_planned_user(email, [_day(today, o) for o in (1, 20)])
    async with TestSessionLocal() as db:
        await crud.record_state(db, user_id, _clearance(today), today)
        await db.commit()
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = (await crud.get_training_plan(db, user_id)).plan
        await plan_pipeline.commit_plan(
            db, user, current, base_plan=current, source=source
        )
        await db.commit()
        return (await crud.get_training_plan(db, user_id)).plan


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["nightly_maintenance", "coach_chat", "generate"])
async def test_every_writer_is_held(client, source):
    tomorrow, later = await _held_plan(f"hold-{source}@example.com", source)

    assert planned_minutes(tomorrow) == 0
    assert tomorrow["description"] == clearance_hold.REASON
    assert planned_minutes(later) == 75


@pytest.mark.asyncio
async def test_the_athlete_s_own_edit_is_not_held(client):
    tomorrow, _later = await _held_plan("hold-edit@example.com", "user_edit")

    assert planned_minutes(tomorrow) == 75


@pytest.mark.asyncio
async def test_a_warning_sign_in_the_chat_holds_the_plan_the_coach_left(
    client, auth_headers, mock_ai_service
):
    today = app_today()
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(
            db, user_id, [_day(today, o) for o in (1, 3, 20)]
        )
        await db.commit()

    response = await client.post(
        ASK,
        headers=auth_headers,
        json={"question": "Ich hatte beim Bergauffahren Brustschmerzen."},
    )

    assert response.status_code == 200
    async with TestSessionLocal() as db:
        plan = (await crud.get_training_plan(db, user_id)).plan
    assert [planned_minutes(d) for d in plan] == [0, 0, 75]


@pytest.mark.asyncio
async def test_an_ordinary_message_holds_nothing(client, auth_headers, mock_ai_service):
    today = app_today()
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [_day(today, 1)])
        await db.commit()

    await client.post(
        ASK, headers=auth_headers, json={"question": "Schöne Runde heute."}
    )

    async with TestSessionLocal() as db:
        [day] = (await crud.get_training_plan(db, user_id)).plan
    assert planned_minutes(day) == 75


@pytest.mark.asyncio
async def test_after_the_doctor_s_all_clear_no_write_holds_again(
    client, auth_headers, mock_ai_service
):
    """The all-clear ends the clearance yesterday, so a later write keeps the
    sessions the coach plans again (review of #1293)."""
    today = app_today()
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    await client.post(
        ASK, headers=auth_headers, json={"question": "Mir wurde schwarz vor Augen."}
    )
    await client.post(
        ASK,
        headers=auth_headers,
        json={"question": "Mein Arzt hat grünes Licht gegeben."},
    )
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [_day(today, 1)])
        await db.commit()
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        current = (await crud.get_training_plan(db, user_id)).plan
        await plan_pipeline.commit_plan(
            db, user, current, base_plan=current, source="nightly_maintenance"
        )
        await db.commit()
        [day] = (await crud.get_training_plan(db, user_id)).plan

    assert planned_minutes(day) == 75


@pytest.mark.asyncio
async def test_a_failed_hold_costs_the_hold_not_the_reply(
    client, auth_headers, mock_ai_service, monkeypatch
):
    today = app_today()
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, [_day(today, 1)])
        await db.commit()
    real = plan_pipeline.commit_plan

    async def commit_plan(*args, source, **kwargs):
        if source == "clearance_hold":
            raise RuntimeError("database gone")
        return await real(*args, source=source, **kwargs)

    monkeypatch.setattr(plan_pipeline, "commit_plan", commit_plan)

    response = await client.post(
        ASK, headers=auth_headers, json={"question": "Ich habe Brustschmerzen."}
    )

    assert response.status_code == 200
    async with TestSessionLocal() as db:
        [day] = (await crud.get_training_plan(db, user_id)).plan
    assert planned_minutes(day) == 75


def test_a_same_day_all_clear_holds_today_no_longer():
    """Warning and all-clear on one day: the clearance ends today, and today's
    session is the athlete's again (review of #1293)."""
    plan = [_day(MONDAY, 0), _day(MONDAY, 1)]
    cleared = StatedState(KIND_CLEARANCE, MONDAY, MONDAY, said_on=MONDAY)

    assert clearance_hold.hold(plan, [cleared], today=MONDAY, done=set()) == (plan, {})


async def _plan_of(user_id: str) -> list[dict]:
    async with TestSessionLocal() as db:
        return (await crud.get_training_plan(db, user_id)).plan


async def _warned(client, auth_headers) -> str:
    """A plan of three sessions, held by a warning sign in the chat."""
    today = app_today()
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(
            db, user_id, [_day(today, o) for o in (1, 3, 20)]
        )
        await db.commit()
    await client.post(
        ASK, headers=auth_headers, json={"question": "Ich hatte Herzrasen beim Sprint."}
    )
    assert [planned_minutes(d) for d in await _plan_of(user_id)] == [0, 0, 75]
    return user_id


@pytest.mark.asyncio
async def test_the_doctor_s_all_clear_gives_the_held_sessions_back(
    client, auth_headers, mock_ai_service
):
    user_id = await _warned(client, auth_headers)

    response = await client.post(
        ASK,
        headers=auth_headers,
        json={"question": "Mein Arzt hat grünes Licht gegeben."},
    )

    plan = await _plan_of(user_id)
    assert [planned_minutes(d) for d in plan] == [75, 75, 75]
    assert plan[0]["description"] == "5x4 min hard."
    assert plan[0]["intervals"]
    [note] = [n for n in response.json()["notes"] if n["kind"] == "clearance_return"]
    assert note["text"].startswith("Diese Einheiten sind wieder in deinem Plan:")
    assert "locker" not in note["text"]


@pytest.mark.asyncio
async def test_a_held_day_given_something_else_since_stays_as_it_is(
    client, auth_headers, mock_ai_service
):
    user_id = await _warned(client, auth_headers)
    plan = await _plan_of(user_id)
    plan[1] = {
        **plan[1],
        "workoutType": "endurance",
        "title": "Walk",
        "description": "A walk.",
        "durationMinutes": 30,
    }
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, plan, base_plan=await _plan_of(user_id), source="user_edit"
        )
        await db.commit()

    await client.post(
        ASK, headers=auth_headers, json={"question": "The doctor cleared me."}
    )

    assert [planned_minutes(d) for d in await _plan_of(user_id)] == [75, 30, 75]


@pytest.mark.asyncio
async def test_no_all_clear_gives_nothing_back(client, auth_headers, mock_ai_service):
    user_id = await _warned(client, auth_headers)

    body = (
        await client.post(
            ASK, headers=auth_headers, json={"question": "Ich gehe morgen zum Arzt."}
        )
    ).json()

    assert [planned_minutes(d) for d in await _plan_of(user_id)] == [0, 0, 75]
    assert not [n for n in body["notes"] if n["kind"] == "clearance_return"]


@pytest.mark.asyncio
async def test_a_failed_return_costs_the_return_not_the_reply(
    client, auth_headers, mock_ai_service, monkeypatch
):
    user_id = await _warned(client, auth_headers)
    real = plan_pipeline.commit_plan

    async def commit_plan(*args, source, **kwargs):
        if source == "clearance_return":
            raise RuntimeError("database gone")
        return await real(*args, source=source, **kwargs)

    monkeypatch.setattr(plan_pipeline, "commit_plan", commit_plan)

    response = await client.post(
        ASK, headers=auth_headers, json={"question": "Der Arzt hat nichts gefunden."}
    )

    assert response.status_code == 200
    assert [planned_minutes(d) for d in await _plan_of(user_id)] == [0, 0, 75]


@pytest.mark.asyncio
async def test_only_the_hold_s_own_rest_days_are_read_from_the_history(client):
    """A rest day another reason made (a trip, the illness) is not the hold's to
    give back; the session before the hold is (ai-trainer-ops#338)."""
    today = app_today()
    when = (today + timedelta(days=2)).isoformat()
    held = {
        **_day(today, 2),
        **clearance_hold.hold(
            [_day(today, 2)], [_clearance(today)], today=today, done=set()
        )[0][0],
    }
    user_id = await create_planned_user("hold-history@example.com", [held])
    async with TestSessionLocal() as db:
        await crud.record_state(db, user_id, _clearance(today), today)
        await db.commit()
    async with TestSessionLocal() as db:
        clearance = await db.scalar(
            select(models.AthleteState).where(models.AthleteState.user_id == user_id)
        )
        before = _day(today, 2)
        trip = {
            **_day(today, 2, title="Hills"),
            "workoutType": "rest",
            "durationMinutes": 0,
            "description": "Away on a trip.",
        }
        db.add_all(
            [
                models.PlanDayHistory(
                    user_id=user_id,
                    date=when,
                    slot=0,
                    old_day=before,
                    new_day=held,
                    source="coach_chat",
                    recorded_at=_soon(1),
                ),
                models.PlanDayHistory(
                    user_id=user_id,
                    date=when,
                    slot=0,
                    old_day=_day(today, 2, title="Hills"),
                    new_day=trip,
                    source="coach_chat",
                    recorded_at=_soon(2),
                ),
            ]
        )
        await db.commit()
        user = await crud.get_user_by_id(db, user_id)
        restored = await clearance_hold.restored_sessions(
            db, user, clearance, [held], today=today
        )

    assert [s["title"] for s in restored.values()] == ["VO2 max 5x4"]


@pytest.mark.asyncio
async def test_a_day_edited_and_held_again_comes_back_as_edited(client):
    """The athlete moved to an easier session during the hold and a later write
    held it again: the easier one comes back, the last the athlete had."""
    today = app_today()
    when = (today + timedelta(days=2)).isoformat()
    held = {
        **_day(today, 2),
        **clearance_hold.hold(
            [_day(today, 2)], [_clearance(today)], today=today, done=set()
        )[0][0],
    }
    user_id = await create_planned_user("hold-history-2@example.com", [held])
    async with TestSessionLocal() as db:
        await crud.record_state(db, user_id, _clearance(today), today)
        await db.commit()
    async with TestSessionLocal() as db:
        clearance = await db.scalar(
            select(models.AthleteState).where(models.AthleteState.user_id == user_id)
        )
        before = _day(today, 2)
        db.add_all(
            [
                models.PlanDayHistory(
                    user_id=user_id,
                    date=when,
                    slot=0,
                    old_day=before,
                    new_day=held,
                    source="coach_chat",
                    recorded_at=_soon(1),
                ),
                models.PlanDayHistory(
                    user_id=user_id,
                    date=when,
                    slot=0,
                    old_day=_day(today, 2, title="Hills"),
                    new_day=held,
                    source="nightly_maintenance",
                    recorded_at=_soon(2),
                ),
            ]
        )
        await db.commit()
        user = await crud.get_user_by_id(db, user_id)
        restored = await clearance_hold.restored_sessions(
            db, user, clearance, [held], today=today
        )

    assert [s["title"] for s in restored.values()] == ["Hills"]
