"""HTTP surface for the training location and the planned-day forecast (#495)."""

import pytest

import services.weather_service as weather_service
from services.home_location import TrainingLocation


@pytest.fixture(autouse=True)
def _clear_forecast_cache():
    weather_service.clear_forecast_cache()
    yield
    weather_service.clear_forecast_cache()


@pytest.mark.asyncio
async def test_home_location_is_absent_until_set(client, auth_headers):
    response = await client.get("/api/v1/users/me/home-location", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["location"] is None


@pytest.mark.asyncio
async def test_put_home_location_stores_a_user_set_override(client, auth_headers):
    response = await client.put(
        "/api/v1/users/me/home-location",
        headers=auth_headers,
        json={"latitude": 47.99, "longitude": 7.85, "label": "Freiburg"},
    )

    assert response.status_code == 200
    location = response.json()["location"]
    assert location["source"] == "user_set"
    assert location["label"] == "Freiburg"
    assert location["latitude"] == pytest.approx(47.99)

    stored = await client.get("/api/v1/users/me/home-location", headers=auth_headers)
    assert stored.json()["location"]["source"] == "user_set"


@pytest.mark.asyncio
async def test_put_home_location_rejects_impossible_coordinates(client, auth_headers):
    response = await client.put(
        "/api/v1/users/me/home-location",
        headers=auth_headers,
        json={"latitude": 120.0, "longitude": 7.85},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_home_location_requires_auth(client):
    assert (await client.get("/api/v1/users/me/home-location")).status_code == 401


@pytest.mark.asyncio
async def test_weather_forecast_is_empty_without_a_location(client, auth_headers):
    """No known location: report nothing rather than guessing one."""
    response = await client.get(
        "/api/v1/users/me/weather-forecast", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["days"] == []
    assert response.json()["location"] is None


@pytest.mark.asyncio
async def test_weather_forecast_returns_days_for_the_stored_location(
    client, auth_headers, monkeypatch
):
    await client.put(
        "/api/v1/users/me/home-location",
        headers=auth_headers,
        json={"latitude": 47.99, "longitude": 7.85, "label": "Freiburg"},
    )

    async def fake_forecast(latitude, longitude, days=14, *, now=None):
        assert (latitude, longitude) == (pytest.approx(47.99), pytest.approx(7.85))
        return [
            {
                "date": "2026-08-01",
                "condition": "rain",
                "weather_code": 61,
                "temperature_max_c": 17.0,
                "temperature_min_c": 11.0,
                "precipitation_mm": 6.4,
                "wind_speed_kph": 20.0,
                "load_flag": "rain",
            }
        ][:days]

    monkeypatch.setattr(weather_service, "fetch_daily_forecast", fake_forecast)

    response = await client.get(
        "/api/v1/users/me/weather-forecast?days=1", headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["location"]["label"] == "Freiburg"
    assert body["location"]["source"] == "user_set"
    assert body["days"][0]["date"] == "2026-08-01"
    assert body["days"][0]["condition"] == "rain"
    assert body["days"][0]["temperatureMaxC"] == 17.0
    assert body["days"][0]["loadFlag"] == "rain"


@pytest.mark.asyncio
async def test_weather_forecast_reports_a_latest_ride_fallback_honestly(
    client, auth_headers, monkeypatch
):
    """With nothing persisted the source must say so, not imply a stored attribute."""

    async def fake_resolve(db, user_id):
        return TrainingLocation(latitude=52.5, longitude=13.4, source="latest_ride")

    async def fake_forecast(latitude, longitude, days=14, *, now=None):
        return [{"date": "2026-08-01", "condition": "clear", "temperature_max_c": 24.0}]

    monkeypatch.setattr(weather_service, "resolve_training_location", fake_resolve)
    monkeypatch.setattr(weather_service, "fetch_daily_forecast", fake_forecast)

    response = await client.get(
        "/api/v1/users/me/weather-forecast", headers=auth_headers
    )

    assert response.json()["location"]["source"] == "latest_ride"


@pytest.mark.asyncio
async def test_weather_forecast_requires_auth(client):
    assert (await client.get("/api/v1/users/me/weather-forecast")).status_code == 401
