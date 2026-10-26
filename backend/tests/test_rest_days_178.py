"""Rest days and training days in a plan the coach wrote itself (ai-trainer-ops#178, #189)."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

import models
from services import rest_days
from services.dates import app_today
from services.prompts import opening_message_system
from tests.conftest import TestSessionLocal
from tests.test_starting_hypotheses_154 import INQUIRIES, _hypotheses, _onboard
from tests.test_opening_message_153 import MODEL_HYPOTHESES

TODAY = "2026-10-21"  # a Wednesday
ALL_DAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


def _day(iso: str, kind: str = "endurance", minutes: int = 60, **extra) -> dict:
    return {
        "date": iso,
        "workoutType": kind,
        "title": kind,
        "durationMinutes": minutes,
        **extra,
    }


def _days(first: str, count: int, **extra) -> list[dict]:
    start = date.fromisoformat(first)
    return [
        _day((start + timedelta(days=i)).isoformat(), **extra) for i in range(count)
    ]


def _run_lengths(plan: list[dict]) -> int:
    worst = run = 0
    for day in sorted(plan, key=lambda d: d["date"]):
        run = 0 if rest_days.is_rest(day) else run + 1
        worst = max(worst, run)
    return worst


def test_fourteen_training_days_without_data_get_a_rest_day_after_three():
    """The owner's test account: 7 training days, no rides, a session on all 14 days."""
    plan = _days("2026-10-22", 14)

    guarded, reasons = rest_days.guard(
        plan, training_days=ALL_DAYS, no_data_yet=True, today=TODAY, protected=set()
    )

    assert _run_lengths(guarded) <= rest_days.MAX_DAYS_IN_A_ROW
    for i in range(len(guarded) - 6):
        assert any(rest_days.is_rest(d) for d in guarded[i : i + 7])
    assert set(reasons.values()) == {rest_days.IN_A_ROW_REASON}
    assert [d["date"] for d in guarded] == [d["date"] for d in plan]


def test_with_data_a_long_run_stays():
    plan = _days("2026-10-22", 7)
    guarded, reasons = rest_days.guard(
        plan, training_days=ALL_DAYS, no_data_yet=False, today=TODAY, protected=set()
    )
    assert guarded == plan and reasons == {}


def test_a_strength_session_on_a_free_day_becomes_rest():
    """Werner (#189): Gym & Core on a Wednesday that is not a training day."""
    werner = ["monday", "tuesday", "thursday", "saturday", "sunday"]
    gym = _day(
        "2026-10-28", "strength", 45, sport="strength", title="Gym & Core Stability"
    )
    ride = _day("2026-10-27")

    guarded, reasons = rest_days.guard(
        [ride, gym],
        training_days=werner,
        no_data_yet=False,
        today=TODAY,
        protected=set(),
    )

    assert guarded[0] == ride
    assert rest_days.is_rest(guarded[1]) and guarded[1]["title"] == "Rest day"
    assert reasons == {("2026-10-28", 0): rest_days.OFF_DAY_REASON}


def test_today_a_protected_day_and_the_past_are_left_alone():
    plan = [_day("2026-10-21"), _day("2026-10-22"), _day("2026-10-23", completed=True)]
    guarded, reasons = rest_days.guard(
        plan,
        training_days=["monday"],
        no_data_yet=True,
        today=TODAY,
        protected={"2026-10-22"},
    )
    assert guarded == plan and reasons == {}


def test_a_first_plan_keeps_today_to_the_training_days_too():
    """Tom (#234): Mon/Wed/Fri, signs up on a Saturday, opens on a Saturday ride."""
    saturday = "2026-10-24"
    tom = ["monday", "wednesday", "friday"]
    plan = [_day(saturday, minutes=90), _day("2026-10-26")]

    guarded, reasons = rest_days.guard(
        plan,
        training_days=tom,
        no_data_yet=True,
        today=saturday,
        protected=set(),
        first_plan=True,
    )
    assert rest_days.is_rest(guarded[0]) and guarded[1] == plan[1]
    assert reasons == {(saturday, 0): rest_days.OFF_DAY_REASON}

    # A plan the athlete is already in keeps today, and so does a ridden today.
    assert (
        rest_days.guard(
            plan, training_days=tom, no_data_yet=True, today=saturday, protected=set()
        )[0]
        == plan
    )
    ridden, _ = rest_days.guard(
        plan,
        training_days=tom,
        no_data_yet=True,
        today=saturday,
        protected={saturday},
        first_plan=True,
    )
    assert ridden == plan


def test_a_protected_day_in_the_run_moves_the_rest_to_the_next_free_one():
    plan = _days("2026-10-22", 5)
    guarded, _ = rest_days.guard(
        plan,
        training_days=ALL_DAYS,
        no_data_yet=True,
        today=TODAY,
        protected={"2026-10-25"},
    )
    assert [rest_days.is_rest(d) for d in guarded] == [False, False, False, False, True]


def test_a_gap_and_a_rest_day_reset_the_run():
    plan = [
        *_days("2026-10-22", 3),
        *_days("2026-10-26", 3),
        _day("2026-10-29", "rest", 0),
        *_days("2026-10-30", 3),
    ]
    guarded, reasons = rest_days.guard(
        plan, training_days=ALL_DAYS, no_data_yet=True, today=TODAY, protected=set()
    )
    assert guarded == plan and reasons == {}


def test_a_two_a_day_rests_whole():
    plan = [
        *_days("2026-10-22", 3),
        _day("2026-10-25"),
        _day("2026-10-25", "strength", 30, slot=1),
    ]
    guarded, reasons = rest_days.guard(
        plan, training_days=ALL_DAYS, no_data_yet=True, today=TODAY, protected=set()
    )
    assert all(rest_days.is_rest(d) for d in guarded[3:])
    assert set(reasons) == {("2026-10-25", 0), ("2026-10-25", 1)}


# --- through the plan pipeline ------------------------------------------------


async def _user_days(days: list[str]) -> None:
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.training_days = days
        await db.commit()


async def _stored() -> list[dict]:
    async with TestSessionLocal() as db:
        return (await db.scalar(select(models.TrainingPlan))).plan


@pytest.mark.asyncio
async def test_a_first_plan_keeps_to_the_rest_day_rule(
    client, auth_headers, mock_ai_service
):
    await _user_days(ALL_DAYS)
    first = (app_today() + timedelta(days=1)).isoformat()
    mock_ai_service["generate_training_plan"].return_value = _days(first, 14)

    assert (
        await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})
    ).status_code == 200

    stored = await _stored()
    assert len(stored) == 14
    assert _run_lengths(stored) <= rest_days.MAX_DAYS_IN_A_ROW
    async with TestSessionLocal() as db:
        reasons = (await db.scalars(select(models.PlanDayHistory.reason))).all()
    assert rest_days.IN_A_ROW_REASON in reasons


@pytest.mark.asyncio
async def test_a_first_plan_puts_nothing_on_a_free_day(
    client, auth_headers, mock_ai_service
):
    tomorrow = app_today() + timedelta(days=1)
    free = rest_days._WEEKDAY_NAMES[tomorrow.weekday()]
    await _user_days([d for d in ALL_DAYS if d != free])
    mock_ai_service["generate_training_plan"].return_value = [
        _day(tomorrow.isoformat(), "strength", 45, sport="strength")
    ]

    await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})

    [day] = await _stored()
    assert rest_days.is_rest(day)


@pytest.mark.asyncio
async def test_a_sign_up_plan_puts_nothing_on_a_free_today(
    client, auth_headers, mock_ai_service
):
    """#234: the sign-up day is the plan's first day, not one the athlete is in."""
    today = app_today()
    free = rest_days._WEEKDAY_NAMES[today.weekday()]
    await _user_days([d for d in ALL_DAYS if d != free])
    mock_ai_service["generate_training_plan"].return_value = [
        _day(today.isoformat(), minutes=90)
    ]

    await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})

    [day] = await _stored()
    assert rest_days.is_rest(day)
    assert day["description"] == rest_days.OFF_DAY_REASON


@pytest.mark.asyncio
async def test_a_day_the_athlete_asked_for_in_chat_keeps_its_session(
    client, auth_headers, mock_ai_service
):
    tomorrow = app_today() + timedelta(days=1)
    free = rest_days._WEEKDAY_NAMES[tomorrow.weekday()]
    await _user_days([d for d in ALL_DAYS if d != free])
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Moved your ride to tomorrow.",
        "plan_updates": [_day(tomorrow.isoformat())],
        "sources": [],
    }

    await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Can I ride tomorrow?"},
    )

    assert not any(
        rest_days.is_rest(d)
        for d in await _stored()
        if d["date"] == tomorrow.isoformat()
    )


# --- "Pretty tired, needing rest" (#178) --------------------------------------


def test_the_opening_prompt_says_which_answer_refutes():
    prompt = opening_message_system("English", "cycling")
    assert '"needing rest" against "recovers quickly"' in prompt
    assert '"neutral" only for an answer that says neither' in prompt


@pytest.mark.asyncio
async def test_an_answer_that_refutes_an_assumption_offers_a_rebuild(
    client, auth_headers, mock_ai_service, monkeypatch
):
    [volume, _] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))
    assert (
        await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})
    ).status_code == 200
    refutes = next(
        i
        for i, o in enumerate(MODEL_HYPOTHESES[0]["options"])
        if o["stance"] == "refutes"
    )

    await client.post(
        f"{INQUIRIES}/{volume['id']}/answer",
        json={"answer": "x", "option": refutes},
        headers=auth_headers,
    )

    [hypothesis] = [
        h
        for h in (await _hypotheses()).values()
        if h.category == "starting_weekly_volume"
    ]
    assert hypothesis.status == "refuted"
    changed = (
        await client.get("/api/v1/ai/plan-inputs-changed", headers=auth_headers)
    ).json()
    # Offered, not applied: the card asks, the athlete decides.
    assert changed["changed"] is True and "assumptions" in changed["reasons"]
