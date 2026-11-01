"""Fever or symptoms below the neck: no session at all, and a return as long as
the illness (review of 2026-11-01, ai-trainer-ops#338, finding A3)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

import crud
import models
from domain import athlete_states
from domain.athlete_states import (
    KIND_FEVER,
    KIND_ILL,
    RETURN_EASY_DAYS,
    StatedState,
    extract_states,
    return_days,
    still_relevant,
)
from services import plan_priors
from services.dates import app_today
from services.plan_priors import (
    PRIOR_FEVER,
    PRIOR_ILLNESS,
    athlete_states_section,
    find_prior_departures,
)
from tests.conftest import TestSessionLocal

MONDAY = date(2026, 10, 12)


def _day(offset: int, minutes: int, kind: str | None = None) -> dict:
    return {
        "date": (MONDAY + timedelta(days=offset)).isoformat(),
        "workoutType": kind or ("endurance" if minutes else "rest"),
        "durationMinutes": minutes,
    }


@pytest.mark.parametrize(
    "message",
    [
        "hab seit Montag Fieber",
        "Ich bin krank, 38,5 Grad.",
        "Ich habe die Grippe.",
        "I have the flu.",
        "I'm sick with a fever and body aches.",
        "Ich bin krank, Magen-Darm.",
        "Ich bin krank, Gliederschmerzen überall.",
        "I'm ill, it's covid.",
    ],
)
def test_an_illness_with_fever_or_below_the_neck_is_a_fever(message):
    (state,) = extract_states(message, MONDAY)

    assert state.kind == KIND_FEVER


@pytest.mark.parametrize(
    "message",
    [
        "Ich bin erkältet, aber kein Fieber.",
        "I have a cold, no fever.",
        "I'm sick, just a runny nose, without fever.",
        "Ich bin krank, aber fieberfrei.",
        "I'm ill but fever-free since yesterday.",
        "Ich bin erkältet bis Freitag.",
    ],
)
def test_a_cold_above_the_neck_or_a_denied_fever_stays_an_illness(message):
    (state,) = extract_states(message, MONDAY)

    assert state.kind == KIND_ILL


def test_a_session_while_ill_with_fever_is_a_departure_even_when_easy():
    fever = StatedState(KIND_FEVER, MONDAY, MONDAY + timedelta(days=2), MONDAY)
    plan = [_day(0, 0), _day(1, 45), _day(2, 0), _day(3, 60)]

    findings = find_prior_departures(plan, MONDAY, states=[fever])

    (found,) = [f for f in findings if f.prior == PRIOR_FEVER]
    assert found.dates == (MONDAY + timedelta(days=1),)
    assert "no training at all" in found.statement
    # The same easy ride with a cold is fine.
    cold = StatedState(KIND_ILL, MONDAY, MONDAY + timedelta(days=2), MONDAY)
    assert find_prior_departures(plan, MONDAY, states=[cold]) == []


def test_a_hard_session_in_the_fever_is_named_once():
    fever = StatedState(KIND_FEVER, MONDAY, MONDAY + timedelta(days=2), MONDAY)
    plan = [_day(1, 60, "intervals")]

    findings = find_prior_departures(plan, MONDAY, states=[fever])

    assert [f.prior for f in findings] == [PRIOR_FEVER]


def test_after_a_fever_the_easy_days_are_as_many_as_the_illness_lasted():
    """A week of flu is no 3-day cold: hard work stays out for 7 days after."""
    week = StatedState(KIND_FEVER, MONDAY, MONDAY + timedelta(days=6), MONDAY)
    end = MONDAY + timedelta(days=6)

    assert return_days(KIND_FEVER, MONDAY, end) == 7
    assert return_days(KIND_FEVER, MONDAY, MONDAY) == RETURN_EASY_DAYS
    assert return_days(KIND_ILL, MONDAY, end) == RETURN_EASY_DAYS
    assert return_days(athlete_states.KIND_TIRED, MONDAY, end) == 0

    hard_on = [_day(6 + d, 60, "intervals") for d in (5, 7, 8)]
    findings = find_prior_departures(hard_on, MONDAY, states=[week])
    (illness,) = [f for f in findings if f.prior == PRIOR_ILLNESS]
    assert illness.dates == (end + timedelta(days=5), end + timedelta(days=7))
    assert still_relevant(week, end + timedelta(days=7))
    assert not still_relevant(week, end + timedelta(days=8))


def test_the_taper_does_not_ask_for_intensity_inside_the_longer_return():
    week = StatedState(KIND_FEVER, MONDAY, MONDAY + timedelta(days=6), MONDAY)

    covered = plan_priors._covered_by_states([week], MONDAY)

    assert max(covered) == MONDAY + timedelta(days=13)


def test_the_writer_is_told_there_is_no_training_while_the_fever_lasts():
    section = athlete_states_section(
        [StatedState(KIND_FEVER, MONDAY, None, MONDAY)], MONDAY
    )

    assert "no training at all while ill" in section
    # Over, it reads as any illness over, with the longer return.
    over = athlete_states_section(
        [StatedState(KIND_FEVER, MONDAY, MONDAY + timedelta(days=4), MONDAY)],
        MONDAY + timedelta(days=6),
    )
    assert "Was ill until" in over
    assert (MONDAY + timedelta(days=9)).isoformat() in over


def test_the_chip_says_no_training():
    assert athlete_states.label(KIND_FEVER, MONDAY, None) == (
        "Ill with fever since 2026-10-12: no training"
    )
    assert athlete_states.label(KIND_FEVER, MONDAY, MONDAY) == (
        "Ill with fever until 2026-10-12: no training"
    )


async def _user_id() -> str:
    async with TestSessionLocal() as db:
        return (await db.scalar(select(models.User))).id


@pytest.mark.asyncio
async def test_a_fever_said_later_makes_the_illness_in_force_one_with_fever(
    auth_headers,
):
    today = app_today()
    user_id = await _user_id()
    async with TestSessionLocal() as db:
        first = await crud.record_state(
            db, user_id, StatedState(KIND_ILL, today, None), today
        )
        again = await crud.record_state(
            db, user_id, StatedState(KIND_FEVER, today, None), today
        )
        # A plain "still ill" later does not take the fever back.
        still = await crud.record_state(
            db, user_id, StatedState(KIND_ILL, today, None), today
        )
        await db.commit()

    assert first.id == again.id == still.id
    assert still.kind == KIND_FEVER
    async with TestSessionLocal() as db:
        assert [
            s.kind for s in await crud.list_relevant_states(db, user_id, today)
        ] == [KIND_FEVER]


@pytest.mark.asyncio
async def test_saying_the_fever_is_over_gives_the_longer_return_in_the_chip(
    client, auth_headers, mock_ai_service
):
    today = app_today()
    start = today - timedelta(days=6)
    async with TestSessionLocal() as db:
        await crud.record_state(
            db, await _user_id(), StatedState(KIND_FEVER, start, None), start
        )
        await db.commit()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Ich bin wieder gesund."},
    )

    labels = [c["label"] for c in response.json()["capturedStates"]]
    # Ill for six days until yesterday (or seven until today): at least six
    # easy days, not three.
    assert any(
        label.startswith("Well again: easy days until ")
        and date.fromisoformat(label.rsplit(" ", 1)[1]) >= today + timedelta(days=5)
        for label in labels
    ), labels
