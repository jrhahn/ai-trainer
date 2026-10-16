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
        "I have a cold, but I normally ride 5 days a week.",
        "Ich bin krank, sonst trainiere ich 4 Tage.",
    ],
)
def test_a_number_of_days_that_is_not_the_illness_sets_no_end(message):
    assert extract_states(message, MONDAY) == [StatedState(KIND_ILL, MONDAY, None)]


@pytest.mark.parametrize(
    "message",
    [
        "Ich war eine Woche krank und bin seit gestern fieberfrei.",
        "Ich war bis gestern krank.",
        "I was ill all week, but I've been fever-free since yesterday.",
    ],
)
def test_a_past_illness_that_ended_yesterday(message):
    """The return prior needs a day to count from. The scenario run of
    2026-10-16 captured nothing here, in production either."""
    yesterday = MONDAY - timedelta(days=1)
    assert extract_states(message, MONDAY) == [StatedState(KIND_ILL, yesterday, yesterday)]


@pytest.mark.parametrize(
    "message",
    [
        "I'm sick of intervals.",
        "I was ill last week.",
        "Am I ill?",
        "I'm not sick.",
        "I had a cold shower after the ride.",
        "Ich war krank, jetzt geht's wieder.",
        # A past illness with no recent end stays not-now.
        "Ich war letzte Woche krank, jetzt bin ich wieder fit.",
        "Ich war krank. Seit gestern habe ich Kopfschmerzen.",
        "If I get a fever I'll skip Saturday.",
        "I hope I don't get the flu.",
        "Falls ich krank bin, sag ich Bescheid.",
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
        ("I'm better at climbing now.", False),
        ("Am I better?", False),
        ("Seit gestern fieberfrei.", True),
        ("I'm fever-free now.", True),
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

    # Friday and Saturday also stack hard days; that is a prior of its own.
    [found] = [
        f
        for f in find_prior_departures(plan, MONDAY, states=[ill])
        if f.prior == PRIOR_ILLNESS
    ]

    assert found.prior == PRIOR_ILLNESS
    assert found.evidence == "expert consensus"
    # Ill until Tuesday, easy until Friday: Tuesday and Friday, not Saturday.
    assert "2026-10-13, 2026-10-16" in found.statement
    assert "2026-10-17" not in found.statement


def test_intensity_on_a_tired_day_is_a_departure_and_the_next_day_is_not():
    tired = StatedState(KIND_REDUCED, MONDAY + timedelta(days=1), MONDAY + timedelta(days=1))

    [found] = [
        f
        for f in find_prior_departures([_day(1), _day(2)], MONDAY, states=[tired])
        if f.prior == PRIOR_REDUCED_DAY
    ]

    assert found.prior == PRIOR_REDUCED_DAY
    assert "2026-10-13" in found.statement
    assert "2026-10-14" not in found.statement


def test_an_open_illness_expires_on_its_own():
    ill = StatedState(KIND_ILL, MONDAY, None)
    last = MONDAY + timedelta(days=OPEN_ILLNESS_DAYS - 1 + RETURN_EASY_DAYS)
    assert still_relevant(ill, last)
    assert not still_relevant(ill, last + timedelta(days=1))
    # Said again on day 9: it holds for another ten days from there.
    again = StatedState(KIND_ILL, MONDAY, None, MONDAY + timedelta(days=8))
    assert still_relevant(again, last + timedelta(days=8))


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


@pytest.mark.asyncio
async def test_ill_again_after_well_again_reopens_the_illness(
    client, auth_headers, mock_ai_service
):
    today = app_today()
    async with TestSessionLocal() as db:
        user_id = await _user_id()
        await crud.record_state(
            db,
            user_id,
            StatedState(KIND_ILL, today - timedelta(days=6), today - timedelta(days=2)),
            today - timedelta(days=6),
        )
        await db.commit()

    await client.post(
        "/api/v1/ai/ask-trainer", headers=auth_headers, json={"question": "I'm sick again."}
    )

    async with TestSessionLocal() as db:
        [state] = await crud.list_relevant_states(db, user_id, today)
    assert state.ends_on is None
    assert state.said_on == today


@pytest.mark.asyncio
async def test_a_party_said_in_chat_marks_the_next_day_and_is_kept_once(
    client, auth_headers, mock_ai_service
):
    for _ in range(2):
        response = await client.post(
            "/api/v1/ai/ask-trainer",
            headers=auth_headers,
            json={"question": "Party tonight."},
        )

    tomorrow = app_today() + timedelta(days=1)
    [captured] = response.json()["capturedStates"]
    assert captured["label"] == f"Less to give on {tomorrow.isoformat()}"
    assert captured["removable"] is True

    plan = [{"date": tomorrow.isoformat(), "workoutType": "threshold", "durationMinutes": 60}]
    async with TestSessionLocal() as db:
        # Said twice, stored once.
        assert len((await db.scalars(select(models.AthleteState))).all()) == 1
        context = await plan_context.plan_writer_context(db, await _user_id(), plan)
    assert f"Less to give on {tomorrow.isoformat()}" in context.coherence
    assert f"[athlete-stated] Intensity on {tomorrow.isoformat()}" in context.coherence
