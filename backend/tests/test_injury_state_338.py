"""Pain in a joint is a stored state with a plan prior and, when it lasts, a
physio's word (review of 2026-11-01, training A6, ai-trainer-ops#338).

Werner's knee lived only in coach memory: no state, no planning constraint, no
referral, and nightly or automatic writers were not bound by it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

import models
from domain import athlete_states, injury
from domain.athlete_states import KIND_INJURY, StatedState, extract_states
from services import plan_priors
from services.dates import app_today
from tests.conftest import TestSessionLocal
from tests.test_athlete_states import _user_id
from tests.test_condition_doctor_338 import _fresh_reply, _kinds
from tests.test_red_flags_338 import _ask

MONDAY = date(2026, 11, 2)


@pytest.mark.parametrize(
    "message",
    [
        "Mein Knie tut beim Bergauffahren weh.",
        "Ich habe seit drei Wochen Knieschmerzen.",
        "My knee hurts on climbs.",
        "I have knee pain after long rides.",
        "Ich habe Schmerzen im linken Knie.",
        "Mir tut das Knie weh.",
        "Meine Achillessehne zwickt.",
    ],
)
def test_pain_in_a_joint_is_captured(message):
    assert extract_states(message, MONDAY) == [StatedState(KIND_INJURY, MONDAY, None)]


@pytest.mark.parametrize(
    "message",
    [
        "Keine Knieschmerzen mehr.",
        "Mein Knie tut nicht mehr weh.",
        "My wife has knee pain.",
        "Wenn mein Knie weh tut, höre ich auf.",
        "Back to back rides this weekend.",
        "I want to get back on the bike.",
        "Mein Rücken ist heute gut.",
        "Does knee pain mean my saddle is too low?",
    ],
)
def test_no_pain_of_the_athletes_own(message):
    assert extract_states(message, MONDAY) == []


def test_pain_holds_two_weeks_from_its_last_mention():
    said = MONDAY + timedelta(days=5)
    state = StatedState(KIND_INJURY, MONDAY, None, said_on=said)

    assert athlete_states.effective_end(state) == said + timedelta(
        days=athlete_states.OPEN_INJURY_DAYS - 1
    )
    assert athlete_states.return_days(KIND_INJURY, MONDAY, None) == 0


def _day(offset: int, title: str, workout: str = "endurance", **fields) -> dict:
    return {
        "date": (MONDAY + timedelta(days=offset)).isoformat(),
        "workoutType": workout,
        "title": title,
        "durationMinutes": 60,
        **fields,
    }


def test_torque_work_while_it_hurts_is_a_departure():
    pain = StatedState(KIND_INJURY, MONDAY, None, said_on=MONDAY)
    plan = [
        _day(1, "SFR am Berg", "intervals"),
        _day(2, "Endurance", description="Hold 50-60 rpm on the climbs."),
        _day(3, "Sweet Spot 3 x 12", "intervals"),
        _day(4, "Endurance", keyFocusPoints=["Big-gear efforts"]),
    ]

    [found] = [
        d
        for d in plan_priors.find_prior_departures(plan, MONDAY, states=[pain])
        if d.prior == plan_priors.PRIOR_INJURY
    ]

    assert [d.isoformat() for d in found.dates] == [
        "2026-11-03",
        "2026-11-04",
        "2026-11-06",
    ]


def test_pain_keeps_intensity_and_no_day_easy():
    pain = StatedState(KIND_INJURY, MONDAY, None, said_on=MONDAY)

    assert plan_priors._covered_by_states([pain], MONDAY) == set()
    assert not [
        d
        for d in plan_priors.find_prior_departures(
            [_day(1, "Sweet Spot", "intervals")], MONDAY, states=[pain]
        )
        if d.prior == plan_priors.PRIOR_INJURY
    ]


def test_the_writer_reads_the_prior():
    pain = StatedState(KIND_INJURY, MONDAY, None, said_on=MONDAY)

    section = plan_priors.athlete_states_section([pain], MONDAY)

    assert "85 rpm" in section
    assert "physio" in section


@pytest.mark.asyncio
async def test_pain_is_stored_with_a_chip_and_no_note_yet(
    client, auth_headers, mock_ai_service
):
    body = await _ask(client, auth_headers, "Mein Knie tut beim Bergauffahren weh.")

    today = app_today().isoformat()
    assert any(
        chip["label"] == f"Pain since {today}: no big-gear or low-cadence work"
        for chip in body["capturedStates"]
    )
    # Not yet two weeks: no physio note.
    assert "injury_physio" not in _kinds(body)


async def _injury_rows() -> list[models.AthleteState]:
    async with TestSessionLocal() as db:
        return list(
            (
                await db.scalars(
                    select(models.AthleteState).where(
                        models.AthleteState.kind == KIND_INJURY
                    )
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_pain_said_again_keeps_one_row_from_its_first_day(
    client, auth_headers, mock_ai_service
):
    await _pain_before(5, said_days_ago=5)

    await _ask(client, auth_headers, "Mein Knie tut immer noch weh.")

    [row] = await _injury_rows()
    assert row.starts_on == (app_today() - timedelta(days=5)).isoformat()
    assert row.said_on == app_today().isoformat()


@pytest.mark.asyncio
async def test_pain_for_weeks_gets_the_physio_note_once(
    client, auth_headers, mock_ai_service
):
    first = await _ask(client, auth_headers, "Ich habe seit drei Wochen Knieschmerzen.")
    _fresh_reply(mock_ai_service)
    again = await _ask(client, auth_headers, "Mein Knie tut seit Wochen weh.")

    [note] = [n for n in first["notes"] if n["kind"] == "injury_physio"]
    assert "Physiotherapeutin" in note["text"]
    assert "Bike-Fit" in note["text"]
    assert "injury_physio" not in _kinds(again)


async def _pain_before(days: int, said_days_ago: int = 3) -> None:
    """A pain first said ``days`` ago and last said ``said_days_ago`` ago."""
    start = app_today() - timedelta(days=days)
    said = app_today() - timedelta(days=min(said_days_ago, days))
    async with TestSessionLocal() as db:
        db.add(
            models.AthleteState(
                user_id=await _user_id(),
                kind=KIND_INJURY,
                starts_on=start.isoformat(),
                ends_on=None,
                said_on=said.isoformat(),
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_pain_said_again_two_weeks_after_the_first_gets_the_note(
    client, auth_headers, mock_ai_service
):
    await _pain_before(injury.LASTING_DAYS)

    body = await _ask(client, auth_headers, "My knee still hurts on climbs.")

    [note] = [n for n in body["notes"] if n["kind"] == "injury_physio"]
    assert "physiotherapist" in note["text"]


@pytest.mark.asyncio
async def test_pain_said_again_sooner_gets_no_note(
    client, auth_headers, mock_ai_service
):
    await _pain_before(injury.LASTING_DAYS - 1)

    body = await _ask(client, auth_headers, "My knee still hurts on climbs.")

    assert "injury_physio" not in _kinds(body)


@pytest.mark.asyncio
async def test_a_new_episode_after_the_last_one_lapsed_gets_no_note(
    client, auth_headers, mock_ai_service
):
    # First said 30 days ago and not since: it lapsed after 14 days. Today's
    # pain is a new episode, not one that has lasted (review of #1290).
    await _pain_before(30, said_days_ago=30)

    body = await _ask(client, auth_headers, "My knee hurts on climbs.")

    assert "injury_physio" not in _kinds(body)
    assert len(await _injury_rows()) == 2


@pytest.mark.parametrize(
    "description",
    [
        "Keine Kraftausdauer wegen Knie, locker kurbeln.",
        "No big-gear work this week; spin at 90 rpm.",
        "Ohne SFR, nur Grundlage.",
        "Avoid low cadence on the climbs.",
    ],
)
def test_a_plan_that_leaves_torque_out_is_no_departure(description):
    pain = StatedState(KIND_INJURY, MONDAY, None, said_on=MONDAY)
    plan = [_day(1, "Endurance", description=description)]

    assert not [
        d
        for d in plan_priors.find_prior_departures(plan, MONDAY, states=[pain])
        if d.prior == plan_priors.PRIOR_INJURY
    ]
