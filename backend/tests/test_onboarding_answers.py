"""The onboarding answers reach the profile and the planner (ai-trainer-ops#43)."""

import pytest

import models
from schemas import UserProfileSchema
from services.prompts import TRAINING_PLAN_PRINCIPLES


async def _register(client) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={"name": "Dana", "email": "dana@example.com", "password": "Str0ng!Pass"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_sports_days_and_hours_are_stored_and_returned(client):
    headers = await _register(client)
    response = await client.put(
        "/api/v1/users/me",
        headers=headers,
        json={
            "sports": ["running", "strength"],
            "trainingDays": ["tuesday", "saturday"],
            "weeklyHours": 5,
        },
    )
    assert response.status_code == 200, response.text
    me = (await client.get("/api/v1/users/me", headers=headers)).json()
    assert me["sports"] == ["running", "strength"]
    assert me["trainingDays"] == ["tuesday", "saturday"]
    assert me["weeklyHours"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"sports": ["swimming"]},
        {"sports": []},
        {"trainingDays": ["someday"]},
        {"trainingDays": []},
        {"weeklyHours": 0},
        {"weeklyHours": 100},
    ],
)
async def test_answers_outside_the_choices_are_rejected(client, body):
    headers = await _register(client)
    response = await client.put("/api/v1/users/me", headers=headers, json=body)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_an_account_that_never_answered_reads_null(client):
    headers = await _register(client)
    me = (await client.get("/api/v1/users/me", headers=headers)).json()
    assert me["sports"] is None
    assert me["trainingDays"] is None
    assert me["weeklyHours"] is None


@pytest.mark.asyncio
async def test_a_repeated_choice_is_kept_once(client):
    headers = await _register(client)
    response = await client.put(
        "/api/v1/users/me",
        headers=headers,
        json={"trainingDays": ["monday", "friday", "monday"]},
    )
    assert response.json()["trainingDays"] == ["monday", "friday"]


def test_the_planner_sees_the_answers():
    user = models.User(
        email="eve@example.com",
        name="Eve",
        sports=["running"],
        training_days=["saturday"],
        weekly_hours=4,
        follows_training_plan=False,
    )
    profile = UserProfileSchema.from_user(user).model_dump(by_alias=True)
    assert profile["sports"] == ["running"]
    assert profile["trainingDays"] == ["saturday"]
    assert profile["weeklyHours"] == 4
    assert "'trainingDays'" in TRAINING_PLAN_PRINCIPLES
    assert "at or below it" in TRAINING_PLAN_PRINCIPLES
    assert "Do not rely on a 'weeklyHours' field" not in TRAINING_PLAN_PRINCIPLES
