"""Illness and tired days said in chat, as priors for the planner (ai-trainer-ops#18, #39 Q1)."""

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import select

import crud
import models
from services import plan_context
from services.athlete_states import (
    KIND_ILL,
    KIND_REDUCED,
    OPEN_ILLNESS_DAYS,
    RETURN_EASY_DAYS,
    StatedState,
    extract_states,
    says_recovered,
    still_relevant,
)
from services.dates import app_today
from services.plan_priors import (
    PRIOR_ILLNESS,
    PRIOR_REDUCED_DAY,
    athlete_states_section,
    find_prior_departures,
)
from tests.conftest import TestSessionLocal

MONDAY = date(2026, 10, 12)


@pytest.mark.parametrize(
    "message, ends_on",
    [
        ("I'm sick, not riding today.", None),
        ("Ich bin seit gestern krank.", None),
        ("hab seit Montag Fieber", None),
        ("I’ve got a cold for 3 days.", MONDAY + timedelta(days=2)),
        ("Ich bin erkältet bis Freitag.", date(2026, 10, 16)),
        ("I feel ill until tomorrow", MONDAY + timedelta(days=1)),
    ],
)
def test_saying_you_are_ill(message, ends_on):
    assert extract_states(message, MONDAY) == [StatedState(KIND_ILL, MONDAY, ends_on)]


@pytest.mark.parametrize(
    "message",
    [
        "I'm sick of intervals.",
        "I was ill last week.",
        "Am I ill?",
        "I'm not sick.",
        "I had a cold shower after the ride.",
        "Ich war krank, jetzt geht's wieder.",
    ],
)
def test_not_saying_it(message):
    assert extract_states(message, MONDAY) == []


@pytest.mark.parametrize(
    "message, day",
    [
        # A party costs the day after; a long work day costs the day itself.
        ("Party on Saturday!", date(2026, 10, 18)),
        ("Party tonight.", MONDAY + timedelta(days=1)),
        ("Gestern lange Nacht.", MONDAY),
        ("Morgen langer Arbeitstag.", MONDAY + timedelta(days=1)),
        ("Slept badly.", MONDAY),
    ],
)
def test_a_day_with_less_to_give(message, day):
    assert extract_states(message, MONDAY) == [StatedState(KIND_REDUCED, day, day)]


@pytest.mark.parametrize(
    "message, recovered",
    [
        ("Bin wieder gesund.", True),
        ("I'm feeling better.", True),
        ("No longer sick!", True),
        ("my legs feel better", False),
        ("Am I better?", False),
    ],
)
def test_saying_you_are_well_again(message, recovered):
    assert says_recovered(message) is recovered


def _day(offset: int, workout: str = "intervals", minutes: int = 60) -> dict:
    return {
        "date": (MONDAY + timedelta(days=offset)).isoformat(),
        "workoutType": workout,
        "durationMinutes": minutes,
    }


def test_intervals_during_an_illness_and_its_return_are_a_departure():
    ill = StatedState(KIND_ILL, MONDAY, MONDAY + timedelta(days=1))
    plan = [_day(1), _day(4), _day(5), _day(2, "endurance")]

    [found] = find_prior_departures(plan, MONDAY, states=[ill])

    assert found.prior == PRIOR_ILLNESS
    assert found.evidence == "expert consensus"
    # Ill until Tuesday, easy until Friday: Tuesday and Friday, not Saturday.
    assert "2026-10-13, 2026-10-16" in found.statement
    assert "2026-10-17" not in found.statement


def test_intensity_on_a_tired_day_is_a_departure_and_the_next_day_is_not():
    tired = StatedState(KIND_REDUCED, MONDAY + timedelta(days=1), MONDAY + timedelta(days=1))

    [found] = find_prior_departures([_day(1), _day(2)], MONDAY, states=[tired])

    assert found.prior == PRIOR_REDUCED_DAY
    assert "2026-10-13" in found.statement
    assert "2026-10-14" not in found.statement


def test_an_open_illness_expires_on_its_own():
    ill = StatedState(KIND_ILL, MONDAY, None)
    last = MONDAY + timedelta(days=OPEN_ILLNESS_DAYS - 1 + RETURN_EASY_DAYS)
    assert still_relevant(ill.kind, ill.starts_on, ill.ends_on, last)
    assert not still_relevant(ill.kind, ill.starts_on, ill.ends_on, last + timedelta(days=1))


def test_the_state_section_says_prior_not_rule():
    section = athlete_states_section([StatedState(KIND_ILL, MONDAY, None)], MONDAY)
    assert "Ill since 2026-10-12" in section
    assert "not rules" in section
    assert athlete_states_section([], MONDAY) == ""


@settings(max_examples=300, deadline=None)
@given(
    workouts=st.lists(
        st.sampled_from(["rest", "endurance", "recovery", "strength"]),
        min_size=14,
        max_size=14,
    ),
    ends=st.one_of(st.none(), st.integers(min_value=0, max_value=6)),
)
def test_an_easy_plan_never_departs_from_an_illness(workouts, ends):
    ill = StatedState(
        KIND_ILL, MONDAY, None if ends is None else MONDAY + timedelta(days=ends)
    )
    plan = [_day(offset, workout) for offset, workout in enumerate(workouts)]
    assert find_prior_departures(plan, MONDAY, states=[ill]) == []


@settings(max_examples=300, deadline=None)
@given(
    offset=st.integers(min_value=0, max_value=13),
    ends=st.one_of(st.none(), st.integers(min_value=0, max_value=6)),
)
def test_one_hard_day_departs_exactly_when_it_falls_in_the_window(offset, ends):
    ends_on = None if ends is None else MONDAY + timedelta(days=ends)
    ill = StatedState(KIND_ILL, MONDAY, ends_on)
    last = (ends_on or MONDAY + timedelta(days=OPEN_ILLNESS_DAYS - 1)) + timedelta(
        days=RETURN_EASY_DAYS
    )
    found = find_prior_departures([_day(offset)], MONDAY, states=[ill])
    assert bool(found) is (MONDAY + timedelta(days=offset) <= last)


async def _user_id() -> str:
    async with TestSessionLocal() as db:
        return (await db.scalar(select(models.User))).id


@pytest.mark.asyncio
async def test_an_illness_said_in_chat_comes_back_reaches_the_planner_and_can_be_undone(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Ich bin krank, Halsweh und Schnupfen."},
    )

    [captured] = response.json()["capturedStates"]
    today = app_today()
    assert captured["label"] == f"Ill since {today.isoformat()}"
    assert captured["removable"] is True

    plan = [
        {
            "date": (today + timedelta(days=1)).isoformat(),
            "workoutType": "vo2max",
            "durationMinutes": 60,
        }
    ]
    async with TestSessionLocal() as db:
        context = await plan_context.plan_writer_context(db, await _user_id(), plan)
    assert f"Ill since {today.isoformat()}" in context.coherence
    assert "[expert consensus] Intensity on" in context.coherence

    undone = await client.delete(
        f"/api/v1/users/me/states/{captured['id']}", headers=auth_headers
    )
    assert undone.status_code == 200
    async with TestSessionLocal() as db:
        assert await crud.list_relevant_states(db, await _user_id(), today) == []
        # Deleted, not flagged: an illness is health data.
        assert await db.scalar(select(models.AthleteState)) is None

    missing = await client.delete(
        "/api/v1/users/me/states/not-a-state", headers=auth_headers
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_well_again_ends_the_illness_and_starts_the_easy_return(
    client, auth_headers, mock_ai_service
):
    today = app_today()
    async with TestSessionLocal() as db:
        await crud.record_state(
            db,
            await _user_id(),
            StatedState(KIND_ILL, today - timedelta(days=3), None),
            today,
        )
        await db.commit()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Bin wieder gesund."},
    )

    [captured] = response.json()["capturedStates"]
    yesterday = today - timedelta(days=1)
    assert captured["removable"] is False
    assert captured["label"] == (
        f"Well again: easy days until "
        f"{(yesterday + timedelta(days=RETURN_EASY_DAYS)).isoformat()}"
    )
    async with TestSessionLocal() as db:
        [state] = await crud.list_relevant_states(db, await _user_id(), today)
    assert state.ends_on == yesterday


@pytest.mark.asyncio
async def test_saying_it_twice_keeps_one_illness(client, auth_headers, mock_ai_service):
    for question in ("I'm sick.", "I am still ill until Friday."):
        await client.post(
            "/api/v1/ai/ask-trainer", headers=auth_headers, json={"question": question}
        )
    async with TestSessionLocal() as db:
        states = await crud.list_relevant_states(db, await _user_id(), app_today())
    assert [s.kind for s in states] == [KIND_ILL]
    assert states[0].ends_on is not None
