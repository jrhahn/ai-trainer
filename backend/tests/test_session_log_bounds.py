"""The log of a planned session is bounded like a hand-entered activity (ai-trainer-ops#127)."""

import pytest

import schemas

GOOD = {
    "actualDurationMinutes": 75,
    "averagePower": 215,
    "averageHeartRate": 152,
    "peakPower": 380,
    "perceivedEffort": 3,
    "notes": "Felt strong today",
    "completedAt": "2026-05-01T10:30:00.000Z",
}


@pytest.mark.asyncio
async def test_a_sound_log_is_stored(client, auth_headers):
    response = await client.post(
        "/api/v1/users/me/workouts/2026-05-01", headers=auth_headers, json={"feedback": GOOD}
    )

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        # 900 typed for 90 multiplied the load by ten; 100,000,000 made a CTL of 1.8 million.
        {"actualDurationMinutes": 100_000_000},
        {"actualDurationMinutes": 1441},
        {"actualDurationMinutes": -5},
        {"actualDurationMinutes": 0},
        {"perceivedEffort": 999},
        {"perceivedEffort": 0},
        {"averageHeartRate": 300},
        {"averagePower": 9000},
        {"peakPower": -1},
        {"notes": "x" * 2001},
        {"completedAt": "not-a-time"},
    ],
)
async def test_an_out_of_range_log_is_refused(client, auth_headers, change):
    response = await client.post(
        "/api/v1/users/me/workouts/2026-05-01",
        headers=auth_headers,
        json={"feedback": {**GOOD, **change}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("date", ["not-a-date", "2026-05-01" + "x" * 29])
async def test_a_log_for_no_date_is_refused(client, auth_headers, date):
    """A 39-character "date" was a 500 on Postgres, where the column holds 20."""
    response = await client.post(
        f"/api/v1/users/me/workouts/{date}", headers=auth_headers, json={"feedback": GOOD}
    )

    assert response.status_code == 422


def test_a_plan_day_keeps_old_feedback_it_already_holds():
    """The bounds are the request's: a stored log from before them must not stop
    the plan it sits on from being read or saved."""
    day = schemas.PlanDay.model_validate(
        {
            "date": "2026-05-01",
            "workoutType": "endurance",
            "title": "Ride",
            "description": "Steady",
            "durationMinutes": 60,
            "feedback": {**GOOD, "actualDurationMinutes": 100_000_000, "perceivedEffort": 9},
        }
    )

    assert day.feedback.actual_duration_minutes == 100_000_000
