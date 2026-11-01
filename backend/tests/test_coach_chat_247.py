"""What the coach's reply changes, without the app (ai-trainer-ops#247).

``coach_chat.apply_reply`` is what ask_trainer did with the model's reply
before it committed. These tests call it with a session and a user, no FastAPI.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import crud
import models
from domain.availability import SESSION_CAP
from services import chat_capture, coach_chat, plan_pipeline, reply_notes
from services.dates import app_today
from tests.conftest import names_day

QUESTION = "Can you move things around?"


def _iso(offset: int) -> str:
    return (app_today() + timedelta(days=offset)).isoformat()


def _plan() -> list[dict]:
    """A week of easy hours, starting tomorrow."""
    return [
        {
            "date": _iso(offset),
            "workoutType": "endurance",
            "title": f"Easy {offset}",
            "description": "Easy aerobic ride",
            "durationMinutes": 60,
        }
        for offset in range(1, 8)
    ]


def _captured(plan: list[dict], **fields) -> chat_capture.Captured:
    """What a message that captured nothing leaves, with ``fields`` changed."""
    values: dict = {
        "plan": plan,
        "profile": {},
        "availability_constraints": [],
        "lifted_constraints": [],
        "captured_constraints": [],
        "new_location": None,
        "captured_goals": [],
        "captured_states": [],
        "illness_restored": {},
        "illness_easy_until": None,
        "heart_rate_stripped": False,
        "red_flag": None,
        "heart_rate_newly_off": False,
        "race_proposal": None,
        "race_offer": None,
        "profile_proposal": None,
        "captured_day_preferences": [],
    }
    values.update(fields)
    return chat_capture.Captured(**values)


async def _stored_plan(db, user) -> list[dict]:
    committed = await plan_pipeline.commit_plan(
        db, user, _plan(), base_plan=[], source="generate"
    )
    return committed.plan


async def _apply(
    db, user, result: dict, captured, *, already_in_chat=False, german=False
) -> coach_chat.ReplyOutcome:
    return await coach_chat.apply_reply(
        db,
        user,
        result,
        captured,
        question=QUESTION,
        already_in_chat=already_in_chat,
        notes_in_german=german,
        timezone_name="UTC",
    )


def _kinds(notes) -> list[str]:
    return [note["kind"] for note in notes]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("already_in_chat", "rows"),
    [
        (False, [("user", QUESTION), ("assistant", "Ride easy today.")]),
        (True, [("assistant", "Ride easy today.")]),
    ],
)
async def test_the_athletes_message_and_the_reply_are_kept(
    db, user, already_in_chat, rows
):
    outcome = await _apply(
        db,
        user,
        {"response": "Ride easy today."},
        _captured([]),
        already_in_chat=already_in_chat,
    )

    messages = await crud.get_chat_messages(db, user.id)
    assert [(m.role, m.content) for m in messages] == rows
    assert outcome == coach_chat.ReplyOutcome(
        notes=[], updated_plan=None, ride_label_updates=[], message_id=messages[-1].id
    )


@pytest.mark.asyncio
async def test_an_update_for_a_day_outside_the_plan_is_dropped_and_said(db, user):
    plan = await _stored_plan(db, user)
    result = {
        "response": "Done.",
        "plan_updates": [
            {"date": _iso(1), "workoutType": "rest", "durationMinutes": 0},
            {"date": _iso(30), "workoutType": "rest", "durationMinutes": 0},
        ],
    }

    outcome = await _apply(db, user, result, _captured(plan))

    assert _kinds(outcome.notes) == [reply_notes.UNRESOLVED_DATES]
    assert _iso(30) in outcome.notes[0]["text"]
    assert outcome.updated_plan is not None
    reply = (await crud.get_chat_messages(db, user.id))[-1]
    assert reply.plan_update_count == 1
    assert reply.notes_json == outcome.notes


@pytest.mark.asyncio
async def test_a_change_a_constraint_blocks_is_said_and_flagged(db, user):
    plan = await _stored_plan(db, user)
    blocked = {"constraintType": "no_training", "constraintDate": _iso(2)}
    result = {
        "response": "Moved your intervals.",
        "plan_updates": [
            {"date": _iso(2), "workoutType": "intervals", "durationMinutes": 60}
        ],
    }

    outcome = await _apply(
        db, user, result, _captured(plan, availability_constraints=[blocked])
    )

    assert _kinds(outcome.notes) == [reply_notes.CONSTRAINT_OVERRIDE]
    reply = (await crud.get_chat_messages(db, user.id))[-1]
    assert reply.flagged_constraint_dates == [_iso(2)]
    assert reply.plan_update_count is None


@pytest.mark.asyncio
async def test_a_limit_per_session_shortens_the_stored_plan_and_says_so(db, user):
    plan = await _stored_plan(db, user)
    # Stored, as the capture stores it: the plan pipeline reads it from there.
    cap = {"maxDurationMinutes": 45, "since": _iso(0)}
    await crud.upsert_availability_constraint(
        db, user.id, constraint_type=SESSION_CAP, required_workout=cap
    )
    said = {"constraintType": SESSION_CAP, "requiredWorkout": cap}

    outcome = await _apply(
        db,
        user,
        {"response": "Okay."},
        _captured(plan, availability_constraints=[said]),
    )

    assert _kinds(outcome.notes) == [reply_notes.SESSION_CAP]
    assert outcome.updated_plan is not None
    assert all(
        int(day.get("durationMinutes") or 0) <= 45
        for day in outcome.updated_plan
        if day.get("date") >= _iso(1)
    )
    stored = await crud.get_training_plan(db, user.id)
    assert stored.plan == outcome.updated_plan


@pytest.mark.asyncio
@pytest.mark.parametrize("coach_changes_a_day", [False, True])
async def test_the_limit_s_note_names_what_the_write_shortened_in_the_plan_it_found(
    db, user, coach_changes_a_day
):
    """A rebuild landed while the coach answered (ai-trainer-ops#332). The
    request read tomorrow at 45 min and the day after at 90; the write found
    them at 75 and 50, and shortened tomorrow. The note names tomorrow, which
    the athlete finds shortened, and not the day after, which the rebuild had
    already brought under the limit."""
    days = _plan()
    days[0]["durationMinutes"], days[1]["durationMinutes"] = 45, 90
    read = (
        await plan_pipeline.commit_plan(db, user, days, base_plan=[], source="generate")
    ).plan
    rebuilt = [dict(day) for day in read]
    rebuilt[0]["durationMinutes"], rebuilt[1]["durationMinutes"] = 75, 50
    await plan_pipeline.commit_plan(
        db, user, rebuilt, base_plan=read, source="generate"
    )
    cap = {"maxDurationMinutes": 60, "since": _iso(0)}
    await crud.upsert_availability_constraint(
        db, user.id, constraint_type=SESSION_CAP, required_workout=cap
    )
    said = {"constraintType": SESSION_CAP, "requiredWorkout": cap}
    updates = (
        [{"date": _iso(5), "workoutType": "recovery", "durationMinutes": 30}]
        if coach_changes_a_day
        else []
    )

    outcome = await _apply(
        db,
        user,
        {"response": "Okay.", "plan_updates": updates},
        _captured(read, availability_constraints=[said]),
    )

    [note] = [n for n in outcome.notes if n["kind"] == reply_notes.SESSION_CAP]
    assert names_day(note["text"], _iso(1))
    assert not names_day(note["text"], _iso(2))
    stored = {d["date"]: d for d in (await crud.get_training_plan(db, user.id)).plan}
    assert [stored[_iso(1)]["durationMinutes"], stored[_iso(2)]["durationMinutes"]] == [
        60,
        50,
    ]
    reply = (await crud.get_chat_messages(db, user.id))[-1]
    assert reply.notes_json == outcome.notes


@pytest.mark.asyncio
async def test_what_the_capture_did_is_confirmed_in_the_athletes_language(db, user):
    plan = _plan()
    captured = _captured(
        plan,
        illness_restored={(_iso(1), 0): plan[0]},
        illness_easy_until=app_today() + timedelta(days=1),
        lifted_constraints=[{"date": _iso(3)}],
        new_location="Darmstadt",
    )

    outcome = await _apply(db, user, {"response": "Gut."}, captured, german=True)

    assert _kinds(outcome.notes) == [
        reply_notes.ILLNESS_RETURN,
        reply_notes.CONSTRAINT_LIFT,
        reply_notes.LOCATION,
    ]
    assert "wieder in deinem Plan" in outcome.notes[0]["text"]
    assert "(locker)" in outcome.notes[0]["text"]
    assert "aufgehoben" in outcome.notes[1]["text"]
    assert "Trainingsort auf Darmstadt" in outcome.notes[2]["text"]
    # The illness gave days back, and nothing wrote the plan: the reply carries it.
    assert outcome.updated_plan is plan


@pytest.mark.asyncio
@pytest.mark.parametrize("stripped", [True, False])
async def test_a_plan_the_heart_rate_caution_stripped_comes_back_with_the_reply(
    db, user, stripped
):
    plan = _plan()

    outcome = await _apply(
        db, user, {"response": "Ok."}, _captured(plan, heart_rate_stripped=stripped)
    )

    assert (outcome.updated_plan is plan) is stripped
    if not stripped:
        assert outcome.updated_plan is None


@pytest.mark.asyncio
async def test_the_coach_corrects_a_rides_note_and_label(db, user):
    day = _iso(-1)
    db.add(
        models.RideMetric(
            user_id=user.id,
            strava_activity_id=97101,
            activity_source="strava",
            external_activity_id="97101",
            activity_date=day,
            activity_start_datetime=f"{day}T08:00:00",
            activity_name="Ride",
            sport_type="Ride",
            duration_seconds=3600,
            tss=60.0,
        )
    )
    result = {
        "response": "Noted.",
        "ride_note_update": {"activity_date": day, "note": "Legs were heavy"},
        "ride_label_update": {"activity_date": day, "label": "Tempo"},
    }

    outcome = await _apply(db, user, result, _captured([]))

    ride = (await crud.get_ride_metrics_by_date(db, user.id, day))[0]
    assert ride.user_note == "Legs were heavy"
    assert ride.label_override == "Tempo"
    assert outcome.ride_label_updates == [
        {"stravaActivityId": 97101, "labelOverride": "Tempo"}
    ]
    # Taken out of the reply: the response does not carry the instructions.
    assert "ride_note_update" not in result
    assert "ride_label_update" not in result


@pytest.mark.asyncio
async def test_the_arrangement_the_coach_commits_to_is_kept(db, user):
    result = {
        "response": "Deal: Thursday stays easy.",
        "planCommitment": {
            "text": "Thursday stays easy",
            "startDate": _iso(1),
            "endDate": _iso(7),
        },
    }

    await _apply(db, user, result, _captured([]))

    commitments = await crud.list_active_plan_commitments(db, user.id, today=_iso(0))
    assert [c.text for c in commitments] == ["Thursday stays easy"]
