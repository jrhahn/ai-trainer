"""A logged session's normalized power and, for a planned one, its distance
(ai-trainer-ops#169, #171).

An athlete without an upload still reads the normalized power off the head
unit. With an FTP it prices the ride the way a recording's power does, through
the same ``compute_ride_tss``; without one, or for a run, it changes nothing
about the load. The planned-session log and the entered activity are one form
now (#171), so the planned log takes a distance too.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update

import crud
import models
from services.analysis import compute_ride_tss
from services.prompts import rate_workout_user
from services.training_load import LOAD_SOURCE_POWER
from tests.conftest import TestSessionLocal

URL = "/api/v1/users/me/activities"
EMAIL = "rider@example.com"
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def _ride(**overrides) -> dict:
    return {
        "date": YESTERDAY,
        "sport": "cycling",
        "durationMinutes": 60,
        "perceivedEffort": 3,
        "notes": "",
        **overrides,
    }


async def _set_ftp(ftp: int | None) -> None:
    async with TestSessionLocal() as db:
        await db.execute(
            update(models.User).where(models.User.email == EMAIL).values(current_ftp=ftp)
        )
        await db.commit()


async def _metrics() -> list[models.RideMetric]:
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email(db, EMAIL)
        return list(await crud.get_all_ride_metrics_ordered(db, user.id))


async def test_an_entered_ride_with_normalized_power_is_priced_from_power(
    client: AsyncClient, auth_headers
):
    await _set_ftp(250)

    response = await client.post(
        URL, headers=auth_headers, json=_ride(normalizedPower=200, averageHeartRate=140)
    )

    assert response.status_code == 201, response.text
    [row] = await _metrics()
    assert row.tss_source == LOAD_SOURCE_POWER
    assert row.tss == compute_ride_tss(3600, 200.0, 250.0) == 64.0
    assert row.normalized_power_w == 200
    assert row.avg_heart_rate_bpm == 140


async def test_without_an_ftp_the_normalized_power_is_kept_but_does_not_price(
    client: AsyncClient, auth_headers
):
    await _set_ftp(None)

    await client.post(URL, headers=auth_headers, json=_ride(normalizedPower=200))

    [row] = await _metrics()
    assert row.tss_source != LOAD_SOURCE_POWER
    assert row.normalized_power_w == 200


async def test_a_runs_watts_never_price_it_against_the_cycling_ftp(
    client: AsyncClient, auth_headers
):
    await _set_ftp(250)

    await client.post(
        URL, headers=auth_headers, json=_ride(sport="running", normalizedPower=300)
    )

    [row] = await _metrics()
    assert row.tss_source != LOAD_SOURCE_POWER
    assert row.normalized_power_w is None


async def test_a_correction_keeps_the_normalized_power_unless_it_clears_it(
    client: AsyncClient, auth_headers
):
    await _set_ftp(250)
    created = await client.post(URL, headers=auth_headers, json=_ride(normalizedPower=200))
    slot = created.json()["slot"]
    edit = f"{URL}/{YESTERDAY}/{slot}"
    body = {"sport": "cycling", "durationMinutes": 60, "perceivedEffort": 4, "notes": "x"}

    assert (await client.put(edit, headers=auth_headers, json=body)).status_code == 200
    listed = (await client.get("/api/v1/users/me/workouts", headers=auth_headers)).json()
    assert listed[f"{YESTERDAY}#{slot}"]["normalizedPower"] == 200
    assert (await _metrics())[0].tss_source == LOAD_SOURCE_POWER

    cleared = {**body, "normalizedPower": None}
    assert (await client.put(edit, headers=auth_headers, json=cleared)).status_code == 200
    listed = (await client.get("/api/v1/users/me/workouts", headers=auth_headers)).json()
    assert listed[f"{YESTERDAY}#{slot}"]["normalizedPower"] is None
    [row] = await _metrics()
    assert row.tss_source != LOAD_SOURCE_POWER and row.normalized_power_w is None


@pytest.mark.parametrize("bad", [0, 2501])
async def test_an_implausible_normalized_power_is_refused(
    client: AsyncClient, auth_headers, bad
):
    response = await client.post(URL, headers=auth_headers, json=_ride(normalizedPower=bad))
    assert response.status_code == 422


async def test_a_planned_log_stores_normalized_power_and_distance_and_keeps_them(
    client: AsyncClient, auth_headers
):
    url = f"/api/v1/users/me/workouts/{YESTERDAY}"
    feedback = {
        "actualDurationMinutes": 60,
        "perceivedEffort": 3,
        "notes": "",
        "completedAt": f"{YESTERDAY}T18:00:00Z",
    }

    saved = await client.post(
        url,
        headers=auth_headers,
        json={"feedback": {**feedback, "normalizedPower": 205, "distanceKm": 30}},
    )
    assert saved.status_code == 200, saved.text
    # A client that does not know the fields leaves them as they were.
    again = await client.post(url, headers=auth_headers, json={"feedback": feedback})
    assert again.status_code == 200

    log = (await client.get("/api/v1/users/me/workouts", headers=auth_headers)).json()[YESTERDAY]
    assert log["normalizedPower"] == 205
    assert log["distanceKm"] == 30


def test_the_coach_reads_the_normalized_power_when_rating():
    prompt = rate_workout_user(
        {"date": YESTERDAY, "workoutType": "endurance", "durationMinutes": 60},
        {"actualDurationMinutes": 60, "perceivedEffort": 3, "normalizedPower": 205},
    )
    assert "- Normalized power: 205W" in prompt
