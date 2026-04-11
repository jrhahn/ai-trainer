import pytest


@pytest.mark.asyncio
async def test_save_and_get_workouts(client, auth_headers):
    feedback = {
        "actualDurationMinutes": 61,
        "averagePower": 240,
        "averageHeartRate": 158,
        "peakPower": 640,
        "perceivedEffort": 3,
        "notes": "Felt controlled.",
        "completedAt": "2026-04-10T10:00:00Z",
    }
    post_response = await client.post(
        "/api/v1/users/me/workouts/2026-04-10",
        headers=auth_headers,
        json={"feedback": feedback},
    )
    assert post_response.status_code == 200

    get_response = await client.get("/api/v1/users/me/workouts", headers=auth_headers)
    assert get_response.status_code == 200
    assert get_response.json()["2026-04-10"]["averagePower"] == 240


@pytest.mark.asyncio
async def test_duplicate_workout_date_overwrites(client, auth_headers):
    first = {
        "actualDurationMinutes": 45,
        "perceivedEffort": 2,
        "notes": "First",
        "completedAt": "2026-04-10T09:00:00Z",
    }
    second = {
        "actualDurationMinutes": 50,
        "perceivedEffort": 4,
        "notes": "Second",
        "completedAt": "2026-04-10T10:00:00Z",
    }
    await client.post("/api/v1/users/me/workouts/2026-04-10", headers=auth_headers, json={"feedback": first})
    await client.post("/api/v1/users/me/workouts/2026-04-10", headers=auth_headers, json={"feedback": second})

    get_response = await client.get("/api/v1/users/me/workouts", headers=auth_headers)
    assert get_response.json()["2026-04-10"]["actualDurationMinutes"] == 50
    assert get_response.json()["2026-04-10"]["notes"] == "Second"
