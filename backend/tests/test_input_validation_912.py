"""Input-validation hardening (public #912, from the ai-trainer-ops#102 assessment).

Low-severity gaps the authenticated test found: date validators that checked the
shape but not the calendar, and several inputs that reached the database and came
back 500 instead of 422 — an out-of-range elevation (int4 overflow) and over-long
profile strings (varchar overflow). Each should be a 422 at the schema boundary.
Both routes that share ``RaceEventRequest`` (POST and PUT) are exercised, every
capped string column has a rejection case, and the accepted boundaries are pinned
so a moved or dropped cap fails the suite.
"""

import uuid

import pytest


async def test_race_event_rejects_impossible_calendar_date(client, auth_headers):
    resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={"date": "2027-13-40", "distanceKm": 10.0, "elevationM": 100},
    )
    assert resp.status_code == 422, resp.text


async def test_race_event_put_also_rejects_impossible_date(client, auth_headers):
    """PUT shares RaceEventRequest; body validation (422) runs before the id lookup."""
    resp = await client.put(
        f"/api/v1/users/me/race-events/{uuid.uuid4()}",
        headers=auth_headers,
        json={"date": "2027-13-40", "distanceKm": 10.0, "elevationM": 100},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("elevation", [-1, 100_001, 2_147_483_648])
async def test_race_event_rejects_out_of_range_elevation(
    client, auth_headers, elevation
):
    resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={"date": "2027-05-01", "distanceKm": 10.0, "elevationM": elevation},
    )
    assert resp.status_code == 422, resp.text


async def test_race_event_accepts_elevation_upper_bound(client, auth_headers):
    """100000 is the top of the accepted range; 100001 is rejected above."""
    resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={"date": "2027-05-02", "distanceKm": 10.0, "elevationM": 100_000},
    )
    assert resp.status_code == 200, resp.text


# camelCase field -> backing column length (models.User).
@pytest.mark.parametrize(
    "field, max_len",
    [("name", 255), ("bikeType", 50), ("fitnessLevel", 50), ("aiProvider", 20)],
)
async def test_profile_rejects_over_long_string(client, auth_headers, field, max_len):
    resp = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={field: "A" * (max_len + 1)},
    )
    assert resp.status_code == 422, resp.text


async def test_profile_accepts_name_at_max_length(client, auth_headers):
    """A name of exactly 255 characters must pass — the cap is max, not max-1."""
    resp = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"name": "A" * 255},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("race_date", ["2027-13-40", "A" * 21, "01/05/2027"])
async def test_profile_rejects_bad_race_date(client, auth_headers, race_date):
    """race_date is String(20) and read with date.fromisoformat downstream."""
    resp = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"raceDate": race_date},
    )
    assert resp.status_code == 422, resp.text


async def test_profile_accepts_and_clears_race_date(client, auth_headers):
    """A real ISO date is kept; null clears it."""
    set_resp = await client.put(
        "/api/v1/users/me", headers=auth_headers, json={"raceDate": "2027-05-13"}
    )
    assert set_resp.status_code == 200, set_resp.text
    clear_resp = await client.put(
        "/api/v1/users/me", headers=auth_headers, json={"raceDate": None}
    )
    assert clear_resp.status_code == 200, clear_resp.text


async def test_race_event_still_accepts_a_valid_entry(client, auth_headers):
    """The guards must not reject a legitimate race event."""
    resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={"date": "2027-02-28", "distanceKm": 42.2, "elevationM": 500},
    )
    assert resp.status_code == 200, resp.text
