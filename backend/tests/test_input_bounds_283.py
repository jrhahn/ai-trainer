"""Client input that lands in the plan and the prompts is bounded, and a coach
reply's planUpdates are kept to objects (ai-trainer-ops#283).

PUT /users/plan stored any JSON the client sent, and its titles and texts went
into every coach prompt; context_workout rode into the prompt without a size.
And a provider that ignores the response schema could answer planUpdates as a
single object or a list of strings, which failed the router with a 500 after
the paid call.
"""

from __future__ import annotations

import pytest

import schemas
from services import ai_service

PLAN = "/api/v1/users/me/plan"


def _day(date: str, **fields) -> dict:
    return {
        "date": date,
        "workoutType": "endurance",
        "title": "Endurance",
        "durationMinutes": 60,
        **fields,
    }


@pytest.mark.asyncio
async def test_a_plan_of_ordinary_size_is_stored(client, auth_headers):
    response = await client.put(
        PLAN, headers=auth_headers, json={"plan": [_day("2030-01-01")]}
    )

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan",
    [
        [_day("2030-01-01")] * (schemas.PLAN_REQUEST_MAX_DAYS + 1),
        [_day("2030-01-01", title="x" * (schemas.PLAN_DAY_TITLE_MAX_CHARS + 1))],
        [_day("2030-01-01", description="x" * (schemas.PLAN_DAY_TEXT_MAX_CHARS + 1))],
        [
            _day(
                "2030-01-01", workoutPurpose="x" * (schemas.PLAN_DAY_TEXT_MAX_CHARS + 1)
            )
        ],
        ["not a day"],
    ],
    ids=[
        "too-many-days",
        "long-title",
        "long-description",
        "long-purpose",
        "not-an-object",
    ],
)
async def test_a_plan_out_of_bounds_is_refused(client, auth_headers, plan):
    response = await client.put(PLAN, headers=auth_headers, json={"plan": plan})

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_an_oversized_workout_context_is_refused(client, auth_headers):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={
            "question": "Kürzer bitte?",
            "contextWorkout": {
                "description": "x" * (schemas.CONTEXT_WORKOUT_MAX_CHARS + 1)
            },
        },
    )

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    ("updates", "kept"),
    [
        (None, None),
        ({"date": "2030-01-01"}, []),
        (["move it", {"date": "2030-01-01"}], [{"date": "2030-01-01"}]),
        ([{"date": "2030-01-01"}], [{"date": "2030-01-01"}]),
    ],
    ids=["absent", "a-single-object", "strings-among-objects", "objects"],
)
def test_a_coach_reply_keeps_only_object_plan_updates(updates, kept):
    parsed = {} if updates is None else {"planUpdates": updates}

    assert ai_service._coach_result(parsed, "ok")["plan_updates"] == kept
