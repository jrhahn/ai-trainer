"""A warning sign holds every session until the athlete says a doctor has
checked it (review of 2026-11-01, ai-trainer-ops#338, finding A1)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import crud
from domain import athlete_states
from domain.athlete_states import (
    KIND_CLEARANCE,
    KIND_ILL,
    OPEN_CLEARANCE_DAYS,
    StatedState,
    effective_end,
    says_cleared,
    still_relevant,
)
from services.dates import app_today
from services.plan_priors import (
    PRIOR_CLEARANCE,
    athlete_states_section,
    find_prior_departures,
)
from tests.conftest import TestSessionLocal
from tests.test_red_flags_338 import _ask, _states

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
        "Mein Arzt hat grünes Licht gegeben.",
        "War beim Kardiologen, alles in Ordnung.",
        "Der Arzt hat nichts gefunden, ich darf wieder fahren.",
        "Belastungs-EKG beim Arzt war unauffällig.",
        "The doctor cleared me.",
        "Saw my GP, all fine.",
        "My cardiologist gave me the green light.",
    ],
)
def test_a_doctors_all_clear_is_read(message):
    assert says_cleared(message)


@pytest.mark.parametrize(
    "message",
    [
        "Ich gehe morgen zum Arzt.",
        "Der Arzt hat noch nicht grünes Licht gegeben.",
        "Hat der Arzt grünes Licht gegeben?",
        "Ich hoffe, der Arzt sagt alles in Ordnung.",
        "Mir geht es besser, alles gut.",
        "I have a doctor's appointment next week, hopefully all fine.",
        "If the doctor clears me I'll ride on Sunday.",
        "Er sagt, alles in Ordnung.",
    ],
)
def test_no_all_clear_where_no_doctor_has_given_one(message):
    assert not says_cleared(message)


def test_every_session_while_waiting_for_the_doctor_is_a_departure():
    waiting = StatedState(KIND_CLEARANCE, MONDAY, None, MONDAY)
    plan = [_day(0, 0), _day(1, 30), _day(5, 90, "intervals"), _day(20, 60)]

    findings = find_prior_departures(plan, MONDAY, states=[waiting])

    (found,) = [f for f in findings if f.prior == PRIOR_CLEARANCE]
    assert found.prior == PRIOR_CLEARANCE
    assert found.dates == (MONDAY + timedelta(days=1), MONDAY + timedelta(days=5))
    assert "no training until one has" in found.statement


def test_an_unanswered_warning_sign_holds_for_two_weeks_from_its_last_mention():
    said = MONDAY + timedelta(days=3)
    waiting = StatedState(KIND_CLEARANCE, MONDAY, None, said)
    last = said + timedelta(days=OPEN_CLEARANCE_DAYS - 1)

    assert effective_end(waiting) == last
    assert still_relevant(waiting, last)
    assert not still_relevant(waiting, last + timedelta(days=1))


def test_after_the_doctor_the_plan_has_no_easy_days_to_keep():
    cleared = StatedState(KIND_CLEARANCE, MONDAY, MONDAY + timedelta(days=2), MONDAY)

    assert (
        find_prior_departures(
            [_day(3, 60, "intervals")], MONDAY + timedelta(days=3), states=[cleared]
        )
        == []
    )


def test_the_writer_and_the_chip_say_no_training_until_a_doctor_has_checked_it():
    waiting = StatedState(KIND_CLEARANCE, MONDAY, None, MONDAY)

    assert "until the athlete says a doctor has cleared them" in (
        athlete_states_section([waiting], MONDAY)
    )
    assert athlete_states.label(KIND_CLEARANCE, MONDAY, None) == (
        "Warning sign on 2026-10-12: no training until a doctor has checked it"
    )
    # Checked: the writer hears nothing of it any more.
    over = StatedState(KIND_CLEARANCE, MONDAY, MONDAY, MONDAY)
    assert athlete_states_section([over], MONDAY + timedelta(days=1)) == ""


@pytest.mark.asyncio
async def test_a_warning_sign_said_again_keeps_one_row_and_waits_from_then(
    auth_headers,
):
    today = app_today()
    from tests.test_fever_no_riding_338 import _user_id

    user_id = await _user_id()
    async with TestSessionLocal() as db:
        first = await crud.record_state(
            db,
            user_id,
            StatedState(KIND_CLEARANCE, today - timedelta(days=3), None),
            today - timedelta(days=3),
        )
        again = await crud.record_state(
            db, user_id, StatedState(KIND_CLEARANCE, today, None), today
        )
        await db.commit()

    assert first.id == again.id
    assert again.said_on == today.isoformat()


@pytest.mark.asyncio
async def test_the_doctors_word_in_the_chat_ends_the_wait_and_says_so(
    client, auth_headers, mock_ai_service
):
    from tests.test_fever_no_riding_338 import _user_id

    today = app_today()
    flagged = today - timedelta(days=2)
    async with TestSessionLocal() as db:
        await crud.record_state(
            db, await _user_id(), StatedState(KIND_CLEARANCE, flagged, None), flagged
        )
        await db.commit()

    body = await _ask(client, auth_headers, "War beim Kardiologen, alles in Ordnung.")

    labels = [c["label"] for c in body["capturedStates"]]
    assert "Cleared by a doctor: the plan goes on" in labels
    session = {"date": today.isoformat(), "workoutType": "endurance"}
    held = find_prior_departures(
        [{**session, "durationMinutes": 60}],
        today,
        states=await _states(auth_headers),
    )
    assert held == []


@pytest.mark.asyncio
async def test_the_doctors_word_beside_a_new_warning_sign_ends_nothing(
    client, auth_headers, mock_ai_service
):
    await _ask(client, auth_headers, "Ich hatte Brustschmerzen am Berg.")

    body = await _ask(
        client,
        auth_headers,
        "Der Arzt sagt, alles in Ordnung, aber heute hatte ich wieder Herzrasen.",
    )

    assert "Cleared by a doctor: the plan goes on" not in [
        c["label"] for c in body["capturedStates"]
    ]
    (state,) = await _states(auth_headers)
    assert state.kind == KIND_CLEARANCE and state.ends_on is None


@pytest.mark.asyncio
async def test_a_doctors_word_ends_no_illness(client, auth_headers, mock_ai_service):
    from tests.test_fever_no_riding_338 import _user_id

    today = app_today()
    async with TestSessionLocal() as db:
        await crud.record_state(
            db, await _user_id(), StatedState(KIND_ILL, today, None), today
        )
        await db.commit()

    await _ask(client, auth_headers, "The doctor cleared me.")

    (state,) = await _states(auth_headers)
    assert state.kind == KIND_ILL and state.ends_on is None
