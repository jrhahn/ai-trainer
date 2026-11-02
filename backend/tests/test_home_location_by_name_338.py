"""A training location from a town name alone (review of 2026-11-01, UI A11,
ai-trainer-ops#338).

Settings asked for latitude and longitude, and typing only "Freiburg" failed.
The endpoint now looks a name up, as the chat does for "bei Freiburg" (#129).
"""

from __future__ import annotations

import pytest

import routers.users.location as location_router

URL = "/api/v1/users/me/home-location"


@pytest.fixture
def geocoder(monkeypatch):
    asked: list[str] = []

    async def geocode(name: str):
        asked.append(name)
        return (47.9959, 7.8522, "Freiburg im Breisgau") if name == "Freiburg" else None

    monkeypatch.setattr(location_router, "geocode_place", geocode)
    return asked


@pytest.mark.asyncio
async def test_a_town_alone_is_looked_up_and_stored_as_the_athletes(
    client, auth_headers, geocoder
):
    response = await client.put(URL, headers=auth_headers, json={"label": "Freiburg"})

    assert response.status_code == 200
    location = response.json()["location"]
    assert location["label"] == "Freiburg im Breisgau"
    assert location["latitude"] == pytest.approx(47.9959)
    assert location["source"] == "user_set"
    assert geocoder == ["Freiburg"]


@pytest.mark.asyncio
async def test_a_place_that_cannot_be_found_says_so(client, auth_headers, geocoder):
    response = await client.put(URL, headers=auth_headers, json={"label": "Nowhere"})

    assert response.status_code == 422
    assert (
        response.json()["detail"]
        == "That place could not be found. Try the nearest town."
    )
    assert (await client.get(URL, headers=auth_headers)).json()["location"] is None


@pytest.mark.asyncio
async def test_coordinates_are_taken_as_given_without_a_lookup(
    client, auth_headers, geocoder
):
    response = await client.put(
        URL,
        headers=auth_headers,
        json={"label": "Home", "latitude": 48.0, "longitude": 7.9},
    )

    assert response.status_code == 200
    assert response.json()["location"]["label"] == "Home"
    assert geocoder == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{"latitude": 48.0, "label": "Home"}, {"label": "  "}, {}],
)
async def test_half_a_coordinate_or_nothing_is_refused(
    client, auth_headers, geocoder, body
):
    response = await client.put(URL, headers=auth_headers, json=body)

    assert response.status_code == 422
    assert geocoder == []
