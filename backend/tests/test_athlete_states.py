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
    KIND_TIRED,
    OPEN_ILLNESS_DAYS,
    RETURN_EASY_DAYS,
    TIRED_EASY_DAYS,
    StatedState,
    extract_states,
    says_not_ill,
    says_recovered,
    still_relevant,
)
from services.dates import app_today
from services.plan_priors import (
    PRIOR_ILLNESS,
    PRIOR_REDUCED_DAY,
    PRIOR_TIREDNESS,
    athlete_states_section,
    find_prior_departures,
)
from tests.conftest import TestSessionLocal

# Hundreds of examples per property: `-m "not slow"` leaves them out of a
# quick local run; CI runs them all (ai-trainer-ops#287).
pytestmark = pytest.mark.slow

MONDAY = date(2026, 10, 12)


@pytest.mark.parametrize(
    "message, ends_on",
    [
        ("I'm sick, not riding today.", None),
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
        "Ich war krank. Seit gestern bin ich fieberfrei.",
        # Raised in review. Whoever "war krank" was, "bin ich fieberfrei" is the
        # athlete's own end of a fever.
        "Mein Bruder war krank. Seit gestern bin ich fieberfrei.",
    ],
)
def test_a_past_illness_that_ended_yesterday(message):
    """The return prior needs a day to count from. The scenario run of
    2026-10-16 captured nothing here, in production either."""
    yesterday = MONDAY - timedelta(days=1)
    assert extract_states(message, MONDAY) == [
        StatedState(KIND_ILL, yesterday, yesterday)
    ]


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
        # The end has to be the illness's own (raised in review).
        "Ich war letzte Woche krank. Bis gestern war ich im Urlaub.",
        "I was sick last week. Until yesterday I was on holiday.",
        "If I get a fever I'll skip Saturday.",
        "I hope I don't get the flu.",
        "Falls ich krank bin, sag ich Bescheid.",
        # Tired today is not tired for days, and a part is not the athlete.
        "Ich bin müde.",
        "Ich bin seit einer Woche nicht mehr müde.",
        "Das Schaltwerk ist seit einer Woche kaputt.",
        "Mein Reifen ist seit Tagen platt, ich fahre trotzdem.",
        # A broken part beside an "ich" elsewhere (raised in review).
        "Mein Rad ist seit Tagen kaputt und ich weiß nicht, was ich tun soll.",
        "I can't ride, my derailleur has been kaputt for days.",
        # Someone else's tiredness beside the athlete's "ich" (raised in review).
        "Mein Sohn ist seit Tagen müde und ich mache mir Sorgen.",
        "My dog has been tired for days, I think I'll take him to the vet.",
        # German "im" is no "I'm" (raised in review).
        "Mein Rad im Keller ist seit Tagen kaputt.",
        "Das Schaltwerk im Rad ist seit einer Woche kaputt.",
        "Wenn ich ständig müde bin, sag ich es.",
        "I'm not tired.",
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
    "message",
    [
        # The scenario run 1536 failed on (ai-trainer-ops#103 Q34).
        "Seit einer Woche bin ich ständig müde und meine Wattwerte sind schlecht.",
        "Ich bin seit Tagen platt.",
        "Fühle mich seit zwei Wochen ausgelaugt.",
        "Meine Beine sind seit Tagen kaputt.",
        "Die Müdigkeit zieht sich bei mir seit einer Woche.",
        "I've been tired all week.",
        "I'm always tired lately.",
        "Bin seit Tagen platt.",
        "I’m tired all week.",
        # A bad night in the same sentence is covered by the easy days.
        "Seit Tagen schlecht geschlafen und ich bin dauernd müde.",
    ],
)
def test_saying_you_have_been_tired_for_days(message):
    end = MONDAY + timedelta(days=TIRED_EASY_DAYS - 1)
    assert extract_states(message, MONDAY) == [StatedState(KIND_TIRED, MONDAY, end)]


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
    tired = StatedState(
        KIND_REDUCED, MONDAY + timedelta(days=1), MONDAY + timedelta(days=1)
    )

    [found] = [
        f
        for f in find_prior_departures([_day(1), _day(2)], MONDAY, states=[tired])
        if f.prior == PRIOR_REDUCED_DAY
    ]

    assert found.prior == PRIOR_REDUCED_DAY
    assert "2026-10-13" in found.statement
    assert "2026-10-14" not in found.statement


def test_intensity_while_tired_for_days_is_a_departure_until_the_easy_days_end():
    tired = StatedState(
        KIND_TIRED, MONDAY, MONDAY + timedelta(days=TIRED_EASY_DAYS - 1)
    )

    [found] = [
        f
        for f in find_prior_departures(
            [_day(1), _day(2), _day(3)], MONDAY, states=[tired]
        )
        if f.prior == PRIOR_TIREDNESS
    ]

    assert found.evidence == "expert consensus"
    assert "2026-10-13, 2026-10-14" in found.statement
    assert "2026-10-15" not in found.statement
    assert still_relevant(tired, MONDAY + timedelta(days=2))
    assert not still_relevant(tired, MONDAY + timedelta(days=3))


def test_a_state_departure_names_the_days_with_intensity():
    # A chat reply is held to them on the days it changes and in the coming week
    # (ai-trainer-ops#103 Q37).
    ill = StatedState(KIND_ILL, MONDAY, MONDAY + timedelta(days=1))

    [found] = [
        f
        for f in find_prior_departures(
            [_day(1), _day(4), _day(2, "endurance")], MONDAY, states=[ill]
        )
        if f.prior == PRIOR_ILLNESS
    ]

    assert found.dates == (MONDAY + timedelta(days=1), MONDAY + timedelta(days=4))


def test_a_session_already_done_is_no_state_departure():
    # No reply can make it easy any more, so holding a reply to it would only
    # cost a rewrite and leave a note about history.
    tired = StatedState(
        KIND_TIRED, MONDAY, MONDAY + timedelta(days=TIRED_EASY_DAYS - 1)
    )
    plan = [{**_day(0), "completed": True}, _day(2)]

    [found] = find_prior_departures(plan, MONDAY, states=[tired])

    assert found.dates == (MONDAY + timedelta(days=2),)


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


def test_the_state_section_names_lasting_tiredness_and_its_easy_days():
    tired = StatedState(KIND_TIRED, MONDAY, MONDAY + timedelta(days=2), MONDAY)
    section = athlete_states_section([tired], MONDAY)
    assert "Tired for days (said on 2026-10-12)" in section
    assert "easy sessions until 2026-10-14" in section


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
async def test_a_past_illness_said_with_its_end_is_one_chip_whether_stored_or_not(
    client, auth_headers, mock_ai_service
):
    """Saying it ended both ends a stored illness and states one: one row, one chip."""
    today = app_today()
    yesterday = today - timedelta(days=1)
    question = "Ich war krank und bin seit gestern fieberfrei."

    first = await client.post(
        "/api/v1/ai/ask-trainer", headers=auth_headers, json={"question": question}
    )
    [captured] = first.json()["capturedStates"]
    assert captured["label"] == f"Ill until {yesterday.isoformat()}"
    assert captured["removable"] is True

    async with TestSessionLocal() as db:
        await crud.delete_state(db, await _user_id(), captured["id"])
        await crud.record_state(
            db,
            await _user_id(),
            StatedState(KIND_ILL, today - timedelta(days=5), None),
            today,
        )
        await db.commit()
    second = await client.post(
        "/api/v1/ai/ask-trainer", headers=auth_headers, json={"question": question}
    )
    [captured] = second.json()["capturedStates"]
    assert captured["removable"] is False
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
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I'm sick again."},
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

    plan = [
        {
            "date": tomorrow.isoformat(),
            "workoutType": "threshold",
            "durationMinutes": 60,
        }
    ]
    async with TestSessionLocal() as db:
        # Said twice, stored once.
        assert len((await db.scalars(select(models.AthleteState))).all()) == 1
        context = await plan_context.plan_writer_context(db, await _user_id(), plan)
    assert f"Less to give on {tomorrow.isoformat()}" in context.coherence
    assert f"[athlete-stated] Intensity on {tomorrow.isoformat()}" in context.coherence


@pytest.mark.asyncio
async def test_tiredness_said_in_chat_reaches_the_planner_and_saying_it_again_extends_it(
    client, auth_headers, mock_ai_service
):
    for _ in range(2):
        response = await client.post(
            "/api/v1/ai/ask-trainer",
            headers=auth_headers,
            json={"question": "Seit einer Woche bin ich ständig müde."},
        )

    today = app_today()
    end = today + timedelta(days=TIRED_EASY_DAYS - 1)
    [captured] = response.json()["capturedStates"]
    assert captured["label"] == f"Tired for days: easy until {end.isoformat()}"
    assert captured["removable"] is True

    plan = [
        {
            "date": (today + timedelta(days=1)).isoformat(),
            "workoutType": "vo2max",
            "durationMinutes": 60,
        }
    ]
    async with TestSessionLocal() as db:
        # Said twice, stored once.
        assert len((await db.scalars(select(models.AthleteState))).all()) == 1
        context = await plan_context.plan_writer_context(db, await _user_id(), plan)
    assert "Tired for days" in context.coherence
    assert "[expert consensus] Intensity on" in context.coherence

    # Still tired two days later: the easy days count from then.
    later = today + timedelta(days=2)
    async with TestSessionLocal() as db:
        await crud.record_state(
            db,
            await _user_id(),
            StatedState(KIND_TIRED, later, later + timedelta(days=2)),
            later,
        )
        await db.commit()
        [row] = (await db.scalars(select(models.AthleteState))).all()
    assert row.ends_on == (later + timedelta(days=2)).isoformat()
    assert row.said_on == later.isoformat()


# ---------------------------------------------------------------------------
# What is no illness, and what takes one back (ai-trainer-ops#115)
# ---------------------------------------------------------------------------

SUNDAY = MONDAY - timedelta(days=1)


@pytest.mark.parametrize(
    "message",
    [
        # A shot, a jab or a test.
        "I got a flu shot yesterday, arm is sore",
        "I have a covid test tomorrow",
        "I've got the flu jab on Friday",
        "Ich habe morgen einen Corona-Test",
        "Ich habe heute die Grippeimpfung bekommen",
        # A negated symptom, and one that was.
        "Ich habe kein Fieber, nur ein bisschen Halskratzen.",
        "Ich habe gestern Fieber gehabt, heute geht es.",
        # Words that only contain "sick" or "ill".
        "I'm sick and tired of the rain.",
        "I feel ill-prepared for Sunday's race.",
    ],
)
def test_what_is_no_illness(message):
    assert [s for s in extract_states(message, MONDAY) if s.kind == KIND_ILL] == []


def test_a_cold_that_is_gone_is_a_recovery():
    assert says_recovered("Ich habe keine Erkältung mehr.")
    assert extract_states("Ich habe keine Erkältung mehr.", MONDAY) == []


@pytest.mark.parametrize(
    ("message", "starts_on"),
    [
        ("Ich bin seit gestern krank.", SUNDAY),
        ("I've been sick since yesterday.", SUNDAY),
        ("Seit vorgestern erkältet.", MONDAY - timedelta(days=2)),
        ("Ich bin seit drei Tagen krank.", MONDAY - timedelta(days=3)),
        ("I've been sick for 3 days.", MONDAY - timedelta(days=3)),
    ],
)
def test_an_illness_starts_when_the_athlete_says(message, starts_on):
    (state,) = extract_states(message, MONDAY)

    assert state.kind == KIND_ILL
    assert state.starts_on == starts_on
    # Time already ill sets no end: it is still open.
    assert state.ends_on is None


@pytest.mark.parametrize(
    ("message", "days"),
    [
        ("I'm ill for the next 14 days.", 14),
        ("Ich bin noch zwei Wochen krank.", 14),
        ("Ich bin noch eine Woche krankgeschrieben.", 7),
        ("I have the flu, the doctor says for three weeks.", 21),
    ],
)
def test_a_stated_illness_may_run_past_ten_days(message, days):
    (state,) = extract_states(message, MONDAY)

    assert state.ends_on == MONDAY + timedelta(days=days - 1)


@pytest.mark.parametrize(
    "message",
    [
        "Ich war nicht krank, das war ein Missverständnis.",
        "Ich bin doch nicht krank.",
        "I'm not sick, I was joking.",
        "I wasn't ill, that was a typo.",
    ],
)
def test_an_illness_can_be_taken_back(message):
    assert says_not_ill(message)
    assert not says_recovered(message)


@pytest.mark.parametrize(
    "message",
    ["Ich bin nicht mehr krank.", "I'm not sick any more.", "Ich bin krank."],
)
def test_a_recovery_or_an_illness_takes_nothing_back(message):
    assert not says_not_ill(message)


@pytest.mark.asyncio
async def test_taking_an_illness_back_deletes_it_without_easy_days(
    client, auth_headers, mock_ai_service
):
    today = app_today()
    async with TestSessionLocal() as db:
        await crud.record_state(
            db, await _user_id(), StatedState(KIND_ILL, today, None), today
        )
        await db.commit()

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Ich war nicht krank, das war ein Missverständnis."},
    )

    [captured] = response.json()["capturedStates"]
    assert captured["label"] == "Not ill: the illness is removed"
    assert captured["removable"] is False
    async with TestSessionLocal() as db:
        assert await crud.list_relevant_states(db, await _user_id(), today) == []
        assert await db.scalar(select(models.AthleteState)) is None


# --- Review of #927 -----------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "I'm not sick of the rain.",
        "I'm not ill-prepared for Sunday.",
        "I hope I'm not sick tomorrow.",
        "If I'm not sick I'll ride.",
        "I wasn't sick last week, but I am now.",
        "Ich war nicht krank letzte Woche, aber jetzt.",
        "Ich bin nicht krank, aber ich habe Fieber.",
    ],
)
def test_what_takes_nothing_back(message):
    """A take-back deletes real health data: a false one is worse than a miss."""
    assert not says_not_ill(message)


def test_gehabt_about_something_else_keeps_the_illness():
    (state,) = extract_states("Ich bin krank und habe gestern frei gehabt.", MONDAY)

    assert state.kind == KIND_ILL


def test_a_remainder_said_beside_the_time_already_ill_is_the_end():
    (state,) = extract_states("Ich bin seit 3 Tagen krank, noch 2 Tage.", MONDAY)

    assert state.starts_on == MONDAY - timedelta(days=3)
    assert state.ends_on == MONDAY + timedelta(days=1)


@pytest.mark.parametrize(
    "message",
    [
        "I've been training for 3 days, now I'm sick.",
        "Ich trainiere seit 3 Tagen, jetzt bin ich krank.",
    ],
)
def test_time_spent_on_something_else_does_not_backdate_the_illness(message):
    (state,) = extract_states(message, MONDAY)

    assert state.starts_on == MONDAY


@pytest.mark.asyncio
async def test_taking_back_an_illness_nobody_stated_changes_nothing(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Ich war nicht krank, das war ein Missverständnis."},
    )

    assert response.json()["capturedStates"] == []


@pytest.mark.parametrize(
    "message",
    [
        # A countdown.
        "Ich bin krank, noch 3 Wochen bis Weihnachten.",
        "I'm sick, and I'm away for the next 2 weeks until Christmas.",
        # An event in the duration's clause.
        "I have the flu, my race is in the next 2 weeks.",
        "Ich bin krank und habe noch 2 Wochen Urlaub.",
        # Time already past.
        "I've been training for 3 weeks, now I'm sick.",
        "I have a cold, I've had it for 3 days.",
        "Ich war für 2 Wochen weg, jetzt bin ich krank.",
        "I've been sick for 2 weeks.",
    ],
)
def test_a_duration_that_is_no_time_left_ill_sets_no_end(message):
    """Each was read as weeks of illness, holding intensity back for a month."""
    (state,) = extract_states(message, MONDAY)

    assert state.kind == KIND_ILL
    assert state.ends_on is None


@pytest.mark.parametrize(
    ("message", "days"),
    [
        # "Bis ich", "until I" and "to" end the illness itself.
        ("Ich bin krank, noch 3 Tage bis ich wieder fit bin.", 3),
        ("I'm sick, I'll rest for 3 days until I feel better.", 3),
        ("I'm sick, I'll rest for 3 days to recover.", 3),
        # The race is another clause's.
        ("Ich bin noch 2 Wochen krank, dann kommt das Rennen.", 14),
        ("Mein Rennen ist abgesagt, ich bin noch 2 Wochen krank.", 14),
        # A past form further back is not the duration's.
        ("Ich war beim Arzt und bin für eine Woche krankgeschrieben.", 7),
        # Singular units, each with its own language's count word.
        ("Ich bin krank, noch einen Tag.", 1),
        ("I'm sick for one week.", 7),
        # Six weeks is past what an illness may run: the next duration is read.
        (
            "Ich bin krank, die Reha dauert noch 6 Wochen, krankgeschrieben für 2 Wochen.",
            14,
        ),
    ],
)
def test_time_left_ill_still_ends_the_illness(message, days):
    (state,) = extract_states(message, MONDAY)

    assert state.ends_on == MONDAY + timedelta(days=days - 1)


@pytest.mark.parametrize(
    "message",
    [
        "I'm sick, I'll rest for 3 days before riding again.",
        "I'm sick, I was told to rest for 5 days.",
        "I have the flu so I'll skip the event and rest for 3 days.",
    ],
)
def test_a_doubtful_duration_leaves_the_illness_open(message):
    """Trade-offs pinned (review of #927): each could be a real end, but a
    countdown or past time read as one held intensity back for weeks. An open
    illness lasts ``OPEN_ILLNESS_DAYS`` from the day it was said instead."""
    (state,) = extract_states(message, MONDAY)

    assert state.kind == KIND_ILL
    assert state.ends_on is None


@pytest.mark.parametrize(
    ("message", "starts_on"),
    [
        # Weeks already ill, and longer than an open illness lasts: it expires
        # from the day it was said, so the start can say the truth.
        ("I've been sick for 2 weeks.", MONDAY - timedelta(days=14)),
        ("Ich bin seit einer Woche krank.", MONDAY - timedelta(days=7)),
        ("I've been sick for 14 days.", MONDAY - timedelta(days=14)),
        # "Since" in the illness's clause, or in a clause of its own beside it.
        ("Seit gestern habe ich Fieber.", SUNDAY),
        ("Since yesterday, I'm sick.", SUNDAY),
        ("Ich bin krank, seit gestern.", SUNDAY),
        ("Ich habe nicht trainiert, ich bin seit gestern krank.", SUNDAY),
        # The running's "since", not the illness's.
        ("I'm sick, I haven't run since yesterday.", MONDAY),
        ("Ich bin krank, ich war seit vorgestern nicht laufen.", MONDAY),
    ],
)
def test_the_illness_starts_where_its_own_words_say(message, starts_on):
    (state,) = extract_states(message, MONDAY)

    assert state.starts_on == starts_on


@pytest.mark.asyncio
async def test_an_illness_that_has_lasted_weeks_is_in_force(auth_headers):
    today = app_today()
    (state,) = extract_states("I've been sick for 2 weeks.", today)
    async with TestSessionLocal() as db:
        user_id = await _user_id()
        await crud.record_state(db, user_id, state, today)
        await db.commit()

        [stored] = await crud.list_relevant_states(db, user_id, today)

    assert stored.starts_on == today - timedelta(days=14)


@pytest.mark.asyncio
async def test_taking_an_illness_back_leaves_a_tiredness_said_before_it(auth_headers):
    today = app_today()
    async with TestSessionLocal() as db:
        user_id = await _user_id()
        tired = await crud.record_state(
            db,
            user_id,
            StatedState(
                KIND_TIRED, today - timedelta(days=1), today + timedelta(days=1)
            ),
            today,
        )
        ill = await crud.record_state(
            db, user_id, StatedState(KIND_ILL, today, None), today
        )
        tired_id, ill_id = tired.id, ill.id
        await db.commit()

        assert await crud.retract_illness(db, user_id, today) == ill_id
        await db.commit()
        assert [row.id for row in await db.scalars(select(models.AthleteState))] == [
            tired_id
        ]


@pytest.mark.asyncio
async def test_taking_an_illness_back_leaves_one_that_is_over(auth_headers):
    """An illness that ended is no claim to take back: its easy days stand."""
    today = app_today()
    over = StatedState(KIND_ILL, today - timedelta(days=4), today - timedelta(days=1))
    async with TestSessionLocal() as db:
        user_id = await _user_id()
        await crud.record_state(db, user_id, over, today)
        await db.commit()

        assert await crud.retract_illness(db, user_id, today) is None
        assert [
            (state.kind, state.starts_on, state.ends_on)
            for state in await crud.list_relevant_states(db, user_id, today)
        ] == [(KIND_ILL, over.starts_on, over.ends_on)]
