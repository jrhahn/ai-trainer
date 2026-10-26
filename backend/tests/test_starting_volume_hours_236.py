"""The starting-volume answer sets the weekly hours, and the plan follows (ai-trainer-ops#236).

Tom tapped "Unter 3 Stunden" and his first week still asked for 4.6 h: the answer
settled an assumption and nothing else. The owner's (a), 2026-10-24: the answer
sets the weekly hours the way an accepted hours offer does, and the plan rebuilds
right away ("the hours you can train changed").
"""

import pytest
from sqlalchemy import select

import models
from services import plan_inputs
from services.starting_hypothesis_check import hours_cap
from tests.conftest import TestSessionLocal
from tests.test_opening_message_153 import MODEL_HYPOTHESES
from tests.test_starting_hypotheses_154 import INQUIRIES, _onboard, no_judge  # noqa: F401


@pytest.mark.parametrize(
    ("label", "cap"),
    [
        ("unter 3 h", 3),
        ("Unter 3 Stunden", 3),
        ("under 3 h", 3),
        ("less than 2.5 hours", 2.5),
        ("3–6 h", 6),
        ("3 bis 6 Stunden", 6),
        ("3 to 6 hours", 6),
        ("3-6 Std.", 6),
        ("mehr als 6 h", None),
        ("over 6 h", None),
        ("Weiß nicht", None),
    ],
)
def test_the_most_hours_an_answer_states(label, cap):
    assert hours_cap(label) == cap


async def _baseline() -> None:
    """What the plan was built from, so a change shows as a reason to rebuild."""
    async with TestSessionLocal() as db:
        await plan_inputs.record(db, await db.scalar(select(models.User)))
        await db.commit()


async def _user() -> models.User:
    async with TestSessionLocal() as db:
        return await db.scalar(select(models.User))


@pytest.mark.asyncio
@pytest.mark.parametrize(("option", "hours"), [(0, 3), (1, 6)])
async def test_the_volume_answer_sets_the_weekly_hours_and_the_plan_follows(
    client,
    auth_headers,
    no_judge,  # noqa: F811
    option,
    hours,
):
    [volume, _] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))
    await _baseline()

    response = await client.post(
        f"{INQUIRIES}/{volume['id']}/answer",
        json={"answer": volume["options"][option], "option": option},
        headers=auth_headers,
    )

    assert response.status_code == 200
    user = await _user()
    assert user.weekly_hours == hours
    async with TestSessionLocal() as db:
        assert "hours" in await plan_inputs.changes_since_generation(db, user)


@pytest.mark.asyncio
@pytest.mark.parametrize(("set_before", "after"), [(4, 4), (8, 6)])
async def test_the_answer_is_a_cap_on_hours_already_set(
    client,
    auth_headers,
    no_judge,  # noqa: F811
    set_before,
    after,
):
    """Review of #1060: "3–6 h" leaves 4 h as they are and brings 8 h down to 6."""
    [volume, _] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))
    await client.put(
        "/api/v1/users/me", json={"weeklyHours": set_before}, headers=auth_headers
    )

    await client.post(
        f"{INQUIRIES}/{volume['id']}/answer",
        json={"answer": volume["options"][1], "option": 1},
        headers=auth_headers,
    )

    assert (await _user()).weekly_hours == after


@pytest.mark.asyncio
async def test_more_than_six_hours_sets_no_limit(client, auth_headers, no_judge):  # noqa: F811
    [volume, _] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))
    before = (await _user()).weekly_hours

    await client.post(
        f"{INQUIRIES}/{volume['id']}/answer",
        json={"answer": volume["options"][2], "option": 2},
        headers=auth_headers,
    )

    assert (await _user()).weekly_hours == before


@pytest.mark.asyncio
async def test_another_question_sets_no_hours(client, auth_headers, no_judge):  # noqa: F811
    [_, recovery] = await _onboard(client, auth_headers, ("Hallo.", MODEL_HYPOTHESES))
    before = (await _user()).weekly_hours

    await client.post(
        f"{INQUIRIES}/{recovery['id']}/answer",
        json={"answer": recovery["options"][0], "option": 0},
        headers=auth_headers,
    )

    assert (await _user()).weekly_hours == before
