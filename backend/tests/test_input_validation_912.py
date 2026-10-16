"""Input-validation hardening (public #912, from the ai-trainer-ops#102 assessment).

Three low-severity gaps the authenticated test found: a date validator that
checked the shape but not the calendar, and two inputs that reached the database
and came back 500 instead of 422 — an out-of-range elevation (int4 overflow) and
an over-long profile string. Each should be a 422 at the schema boundary.
"""


async def test_race_event_rejects_impossible_calendar_date(client, auth_headers):
    resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={"date": "2027-13-40", "distanceKm": 10.0, "elevationM": 100},
    )
    assert resp.status_code == 422, resp.text


async def test_race_event_rejects_out_of_range_elevation(client, auth_headers):
    resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={"date": "2027-05-01", "distanceKm": 10.0, "elevationM": 2147483648},
    )
    assert resp.status_code == 422, resp.text


async def test_profile_rejects_over_long_name(client, auth_headers):
    resp = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"name": "A" * 256},
    )
    assert resp.status_code == 422, resp.text


async def test_race_event_still_accepts_a_valid_entry(client, auth_headers):
    """The guards must not reject a legitimate race event."""
    resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={"date": "2027-02-28", "distanceKm": 42.2, "elevationM": 500},
    )
    assert resp.status_code == 200, resp.text
