import pytest


@pytest.mark.asyncio
async def test_get_empty_plan_then_save_and_get(client, auth_headers):
    empty_response = await client.get("/api/v1/users/me/plan", headers=auth_headers)
    assert empty_response.status_code == 200
    assert empty_response.json()["plan"] == []

    plan = [
        {
            "date": "2026-04-10",
            "workoutType": "tempo",
            "title": "Tempo Ride",
            "description": "Steady tempo effort",
            "durationMinutes": 75,
        }
    ]
    save_response = await client.put(
        "/api/v1/users/me/plan",
        headers=auth_headers,
        json={"plan": plan},
    )
    assert save_response.status_code == 200
    saved = save_response.json()["plan"]
    # A manual save pins each day as a user edit so automated triggers can't
    # overwrite it (#342); the workout content is otherwise unchanged.
    assert all(day["source"] == "user" for day in saved)
    assert [{k: v for k, v in day.items() if k != "source"} for day in saved] == plan

    get_response = await client.get("/api/v1/users/me/plan", headers=auth_headers)
    assert get_response.status_code == 200
    assert get_response.json()["plan"] == saved
