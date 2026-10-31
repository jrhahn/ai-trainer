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
from domain.passwords import hash_password
from services import ai_service, plan_maintenance, plan_pipeline, plan_priors
from domain import athlete_states
from domain.availability import SESSION_CAP
from tests.conftest import TestSessionLocal

BACK_TO_BACK = plan_priors.PriorDeparture(
    plan_priors.PRIOR_BACK_TO_BACK,
    plan_priors.EVIDENCE_HEURISTIC,
    "Intensity on consecutive days.",
    (date(2026, 4, 16), date(2026, 4, 17)),
)


def _breaks(updates):
    return (
        [BACK_TO_BACK]
        if any(u.get("workoutType") == "intervals" for u in updates or [])
        else []
    )


def _model(*replies):
    calls: list[str] = []

    async def chat(provider, system_prompt, user_msg, json_mode=False, **kwargs):
        calls.append(user_msg)
        return json.dumps(replies[len(calls) - 1])

    return chat, calls


INTERVALS = {
    "date": "2026-04-16",
    "workoutType": "intervals",
    "title": "VO2",
    "description": "5x4",
    "durationMinutes": 60,
}
EASY = {
    "date": "2026-04-16",
    "workoutType": "endurance",
    "title": "Easy",
    "description": "Z2",
    "durationMinutes": 60,
}
PLAN = [
    {
        "date": "2026-04-16",
        "workoutType": "endurance",
        "title": "Ride",
        "description": "Z2",
        "durationMinutes": 60,
    }
]


def _fortnight(first: dict) -> list[dict]:
    """``first`` and thirteen easy days after it: a new plan is a fortnight
    from today (ai-trainer-ops#113), and today is pinned to ``first``'s date."""
    start = date.fromisoformat(first["date"])
    return [first] + [
        {**EASY, "date": (start + timedelta(days=offset)).isoformat()}
        for offset in range(1, 14)
    ]


def _today_is_the_sixteenth():
    return patch.object(
        ai_service, "app_today_iso", lambda timezone_name=None: "2026-04-16"
    )


PROFILE = {"name": "Rider", "weeklyHours": 8, "currentFTP": 250}


@pytest.mark.asyncio
async def test_the_nightly_adaptation_writes_once_more_when_it_breaks_a_hard_prior():
    chat, calls = _model({"updatedDays": [INTERVALS]}, {"updatedDays": [EASY]})

    with patch.object(ai_service, "_chat", side_effect=chat):
        updates = await ai_service.adapt_training_plan(
            PLAN, [], PROFILE, prior_check=_breaks
        )

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
        result = await ai_service.recommend_next_session(
            [], PLAN, PROFILE, prior_check=_breaks
        )

    assert result["plan_updates"] == [EASY]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_new_plan_writes_once_more_when_it_breaks_a_hard_prior():
    chat, calls = _model({"plan": _fortnight(INTERVALS)}, {"plan": _fortnight(EASY)})

    with patch.object(ai_service, "_chat", side_effect=chat), _today_is_the_sixteenth():
        plan = await ai_service.generate_training_plan(PROFILE, prior_check=_breaks)

    assert {d["workoutType"] for d in plan} == {"endurance"}
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
    return {
        "date": on.isoformat(),
        "workoutType": kind,
        "title": kind,
        "description": "",
        "durationMinutes": 60,
    }


@pytest.mark.asyncio
async def test_a_day_that_still_breaks_a_hard_prior_carries_a_note_until_it_is_fixed():
    first = date.today() + timedelta(days=2)
    second = first + timedelta(days=1)
    user_id = await _user_with([_day(first, "intervals"), _day(second, "endurance")])

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = (await crud.get_training_plan(db, user_id)).plan
        noted = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": second.isoformat(), "workoutType": "intervals"}],
            base_plan=plan,
            source="coach_chat",
        )
        await db.commit()
    by_date = {d["date"]: d for d in noted.plan}
    assert by_date[first.isoformat()]["priorNotes"] == [plan_priors.PRIOR_BACK_TO_BACK]
    assert by_date[second.isoformat()]["priorNotes"] == [plan_priors.PRIOR_BACK_TO_BACK]

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = (await crud.get_training_plan(db, user_id)).plan
        fixed = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": second.isoformat(), "workoutType": "endurance"}],
            base_plan=plan,
            source="coach_chat",
        )
        await db.commit()
    assert all("priorNotes" not in d for d in fixed.plan)


@pytest.mark.asyncio
async def test_a_note_is_no_change_to_the_day():
    """Notes follow the plan; refreshing one is not a change to narrate or record."""
    first = date.today() + timedelta(days=2)
    user_id = await _user_with(
        [_day(first, "intervals"), _day(first + timedelta(days=1), "intervals")]
    )

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = (await crud.get_training_plan(db, user_id)).plan
        result = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": first.isoformat(), "workoutType": "intervals"}],
            base_plan=plan,
            source="coach_chat",
        )
        await db.commit()
        history = await crud.list_plan_day_history(db, user_id, limit=10)

    assert result.applied_changes == []
    assert history == []


@pytest.mark.asyncio
async def test_a_rewrite_that_still_breaks_a_prior_is_not_asked_a_third_time():
    """What survives the one rewrite goes through, and the persist gate notes it."""
    chat, calls = _model({"updatedDays": [INTERVALS]}, {"updatedDays": [INTERVALS]})
    counter = ai_service.metrics.COACH_REWRITES.labels(
        surface="adapt_plan", reason="priors"
    )
    before = counter._value.get()

    with patch.object(ai_service, "_chat", side_effect=chat):
        updates = await ai_service.adapt_training_plan(
            PLAN, [], PROFILE, prior_check=_breaks
        )

    assert updates == [INTERVALS]
    assert len(calls) == 2
    assert counter._value.get() == before + 1


@pytest.mark.asyncio
async def test_the_rewrite_for_a_prior_does_not_use_up_the_validation_budget():
    """Two invalid plans, then one that breaks a prior: the rewrite still gets
    its turn, because it is not one of the validation attempts."""
    chat, calls = _model(
        {"plan": ["not a day"]},
        {"plan": ["not a day either"]},
        {"plan": _fortnight(INTERVALS)},
        {"plan": _fortnight(EASY)},
    )

    with patch.object(ai_service, "_chat", side_effect=chat), _today_is_the_sixteenth():
        plan = await ai_service.generate_training_plan(PROFILE, prior_check=_breaks)

    assert {d["workoutType"] for d in plan} == {"endurance"}
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_a_note_that_cannot_be_judged_does_not_cost_the_write(monkeypatch):
    first = date.today() + timedelta(days=2)
    user_id = await _user_with([_day(first, "endurance")])

    async def broken(*args, **kwargs):
        raise RuntimeError("no inputs")

    monkeypatch.setattr(plan_pipeline.plan_context, "prior_check_for_user", broken)
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = (await crud.get_training_plan(db, user_id)).plan
        result = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": first.isoformat(), "workoutType": "intervals"}],
            base_plan=plan,
            source="coach_chat",
        )
        await db.commit()

    assert result.plan[0]["workoutType"] == "intervals"


def test_a_new_plan_is_judged_on_every_day_it_has():
    check = plan_pipeline.full_plan_prior_check(lambda plan: [BACK_TO_BACK])

    assert check([INTERVALS]) == [BACK_TO_BACK]
    assert check([{**EASY, "date": "2026-05-01"}]) == []
    assert plan_pipeline.full_plan_prior_check(None) is None


# ---------------------------------------------------------------------------
# The writers nobody watches, held to the athlete's states (ai-trainer-ops#112)
# ---------------------------------------------------------------------------

ILLNESS = plan_priors.PriorDeparture(
    plan_priors.PRIOR_ILLNESS,
    plan_priors.EVIDENCE_CONSENSUS,
    "Intensity during a stated illness.",
    (date(2026, 4, 16),),
)


def test_an_unattended_writer_is_held_to_the_athletes_states():
    """The nightly job, an adaptation and a ride review heard no reason."""
    check = plan_pipeline.update_prior_check(lambda plan: [ILLNESS], PLAN)

    assert check([INTERVALS]) == [ILLNESS]
    # Only on the days the write changes, as for a hard prior.
    assert check([{**INTERVALS, "date": "2026-04-20"}]) == []


def test_a_new_plan_is_held_to_the_athletes_states():
    check = plan_pipeline.full_plan_prior_check(lambda plan: [ILLNESS])

    assert check([INTERVALS]) == [ILLNESS]


HOURS = plan_priors.PriorDeparture(
    plan_priors.PRIOR_STATED_HOURS,
    plan_priors.EVIDENCE_ATHLETE,
    "Eight hours planned against four stated.",
    (date(2026, 4, 16),),
)


def test_an_unattended_writer_is_held_to_the_stated_hours():
    """Advice only, the rebuild after a beginner accepted 3 h planned 3.75 to
    5.25 h into the next week in 20 runs of 20; the owner chose one rewrite
    with the week's sum (ai-trainer-ops#321)."""
    check = plan_pipeline.update_prior_check(lambda plan: [HOURS], PLAN)

    assert check([INTERVALS]) == [HOURS]
    # Only when the write changes a day of that week.
    assert check([{**INTERVALS, "date": "2026-04-20"}]) == []


def test_a_new_plan_is_held_to_the_stated_hours():
    check = plan_pipeline.full_plan_prior_check(lambda plan: [HOURS])

    assert check([INTERVALS]) == [HOURS]


def _mias_week() -> list[dict]:
    """Tuesday 60, Thursday 75 and Sunday 90 min in the week of 2026-11-02,
    as Mia's first plan had them (ai-trainer-ops#336)."""
    minutes = {"2026-11-03": 60, "2026-11-05": 75, "2026-11-08": 90}
    start = date(2026, 11, 2)
    days = [(start + timedelta(days=offset)).isoformat() for offset in range(7)]
    return [
        {**EASY, "date": day, "durationMinutes": minutes[day]}
        if day in minutes
        else {**EASY, "date": day, "workoutType": "rest", "durationMinutes": 0}
        for day in days
    ]


def _three_hours(plan):
    return [
        d
        for d in plan_priors.find_prior_departures(
            plan, date(2026, 10, 31), weekly_hours=3
        )
        if d.prior == plan_priors.PRIOR_STATED_HOURS
    ]


SESSION_CAP_60 = {
    "constraintType": SESSION_CAP,
    "requiredWorkout": {"maxDurationMinutes": 60, "since": "2026-10-31"},
}


def test_a_reply_is_judged_after_the_limit_per_session_shortens_the_week():
    """Mia said "Max 1 hour per session" while her week held 3.75 h against 3
    stated. The coach shortened Tuesday to 30: 3.25 h before the limit, 2.5 h
    after it, which is what the commit stores (ai-trainer-ops#336)."""
    plan = _mias_week()
    tuesday = [{**EASY, "date": "2026-11-03", "durationMinutes": 30}]
    with_limit = plan_pipeline.update_prior_check(
        _three_hours, plan, today=date(2026, 10, 31), constraints=[SESSION_CAP_60]
    )
    without = plan_pipeline.update_prior_check(
        _three_hours, plan, today=date(2026, 10, 31)
    )

    assert with_limit(tuesday) == []
    assert [d.prior for d in without(tuesday)] == [plan_priors.PRIOR_STATED_HOURS]
    # Still held where the limit leaves the week over: 4 x 60 min.
    friday = [{**EASY, "date": "2026-11-06", "durationMinutes": 60}]
    assert [d.prior for d in with_limit(friday)] == [plan_priors.PRIOR_STATED_HOURS]


def test_an_update_the_commit_drops_for_a_day_off_is_not_counted():
    """A session on a day the athlete ruled out never lands: the commit's
    filter drops it, and the gate would rest the day anyway. The check rests it
    too (ai-trainer-ops#336)."""
    plan = [
        {**day, "durationMinutes": min(day["durationMinutes"], 60)}
        for day in _mias_week()
    ]
    day_off = {"constraintType": "no_training", "constraintDate": "2026-11-06"}
    friday = [{**EASY, "date": "2026-11-06", "durationMinutes": 60}]
    check = plan_pipeline.update_prior_check(
        _three_hours, plan, today=date(2026, 10, 31), constraints=[day_off]
    )

    # 3 h stored; Friday's hour would make 4, but it never lands.
    assert check(friday) == []


def _three_days_a_week(first: str) -> list[dict]:
    """A fortnight from ``first`` with an hour on Tuesday, Thursday and Saturday."""
    start = date.fromisoformat(first)
    days = [start + timedelta(days=offset) for offset in range(14)]
    return [
        {**EASY, "date": day.isoformat()}
        if day.weekday() in (1, 3, 5)
        else {
            "date": day.isoformat(),
            "workoutType": "rest",
            "title": "Rest",
            "description": "Rest",
            "durationMinutes": 0,
        }
        for day in days
    ]


@pytest.mark.asyncio
async def test_a_new_plan_over_the_stated_hours_is_written_again_with_the_sum():
    """The rebuild after the athlete states their hours (ai-trainer-ops#321):
    an hour on each of fourteen days against three stated."""
    check = plan_pipeline.full_plan_prior_check(
        lambda plan: plan_priors.find_prior_departures(
            plan, date(2026, 4, 16), weekly_hours=3
        )
    )
    inside = _three_days_a_week("2026-04-16")
    chat, calls = _model({"plan": _fortnight(EASY)}, {"plan": inside})

    with patch.object(ai_service, "_chat", side_effect=chat), _today_is_the_sixteenth():
        plan = await ai_service.generate_training_plan(PROFILE, prior_check=check)

    assert len(calls) == 2
    assert "the athlete said they can give 3 h a week" in calls[1]
    assert "To keep to the stated 180 min" in calls[1]
    assert [d["durationMinutes"] for d in plan] == [
        d["durationMinutes"] for d in inside
    ]


async def _ill_from_today(user_id: str) -> None:
    async with TestSessionLocal() as db:
        await crud.record_state(
            db,
            user_id,
            athlete_states.StatedState(athlete_states.KIND_ILL, date.today(), None),
            date.today(),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_intensity_in_a_stated_illness_carries_a_note():
    first = date.today() + timedelta(days=2)
    user_id = await _user_with(
        [_day(first, "endurance"), _day(first + timedelta(days=1), "endurance")]
    )
    await _ill_from_today(user_id)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        plan = (await crud.get_training_plan(db, user_id)).plan
        written = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": first.isoformat(), "workoutType": "intervals"}],
            base_plan=plan,
            source="nightly_maintenance",
        )
        await db.commit()

    by_date = {d["date"]: d for d in written.plan}
    assert by_date[first.isoformat()]["priorNotes"] == [plan_priors.PRIOR_ILLNESS]
    assert "priorNotes" not in by_date[(first + timedelta(days=1)).isoformat()]


@pytest.mark.asyncio
async def test_the_nightly_job_is_told_when_it_reschedules_into_an_illness(monkeypatch):
    """The issue's sequence: ill since Monday, Tuesday's VO2 missed, 02:00."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    # Tomorrow is a rest day: without one, a new account with no rides would
    # have the moved session turned into rest by the rule of ops#178, and this
    # test is about the illness note on it.
    plan = [_day(yesterday, "intervals")] + [
        _day(today + timedelta(days=offset), "rest" if offset == 1 else "endurance")
        for offset in range(0, 13)
    ]
    user_id = await _user_with(plan)
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        user.is_onboarded = True
        await db.commit()
    await _ill_from_today(user_id)
    seen: dict[str, list[str]] = {}

    async def reschedule(plan, *args, prior_check=None, **kwargs):
        moved = {**_day(today + timedelta(days=2), "intervals"), "title": "VO2"}
        seen["held"] = [d.prior for d in prior_check([moved])]
        return [moved]

    async def nothing(*args, **kwargs):
        return ""

    async def no_narration(*args, **kwargs):
        return None

    async def unchanged(*args, **kwargs):
        return False

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", reschedule)
    monkeypatch.setattr(plan_maintenance, "training_weather_context_for_user", nothing)
    monkeypatch.setattr(
        plan_maintenance.coach_summary, "narrate_plan_changes", no_narration
    )
    monkeypatch.setattr(plan_maintenance, "_week_targets_moved", unchanged)

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_maintenance.maintain_user_training_plan(db, user)
        await db.commit()

    assert plan_priors.PRIOR_ILLNESS in seen["held"]
    stored = {d["date"]: d for d in (await _plan_of(user_id))}
    assert stored[(today + timedelta(days=2)).isoformat()]["priorNotes"] == [
        plan_priors.PRIOR_ILLNESS
    ]


async def _plan_of(user_id: str) -> list[dict]:
    async with TestSessionLocal() as db:
        return (await crud.get_training_plan(db, user_id)).plan


@pytest.mark.asyncio
async def test_a_week_over_the_stated_hours_is_noted_on_its_sessions_only():
    """Advice for these writers, but worth a note (ai-trainer-ops#116)."""
    monday = date.today() + timedelta(days=7 - date.today().weekday())
    plan = [
        _day(monday + timedelta(days=offset), "endurance") for offset in (0, 2, 4)
    ] + [_day(monday + timedelta(days=offset), "rest") for offset in (1, 3, 5, 6)]
    async with TestSessionLocal() as db:
        user = models.User(
            email="hours-note@example.com",
            name="Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            current_ftp=250,
            weekly_hours=2,
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, plan)
        await db.commit()
        user_id = user.id

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        stored = (await crud.get_training_plan(db, user_id)).plan
        written = await plan_pipeline.commit_plan_updates(
            db,
            user,
            [{"date": monday.isoformat(), "durationMinutes": 90}],
            base_plan=stored,
            source="nightly_maintenance",
        )
        await db.commit()

    noted = {d["date"]: d.get("priorNotes") for d in written.plan}
    for offset in (0, 2, 4):
        assert noted[(monday + timedelta(days=offset)).isoformat()] == [
            plan_priors.PRIOR_STATED_HOURS
        ]
    for offset in (1, 3, 5, 6):
        assert noted[(monday + timedelta(days=offset)).isoformat()] is None
