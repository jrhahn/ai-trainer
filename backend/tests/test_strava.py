import urllib.parse

import pytest
from fastapi import HTTPException

from auth import decode_token
from database import async_session_maker
import models
from routers import strava as strava_router


class DummyResponse:
    def __init__(self, status_code: int, payload: dict | list):
        self.status_code = status_code
        self._payload = payload

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_strava_auth_redirect_contains_state(client, auth_headers):
    token = auth_headers["Authorization"].split(" ", 1)[1]
    response = await client.get("/api/v1/auth/strava", headers=auth_headers)

    assert response.status_code == 200
    auth_url = response.json()["authUrl"]
    assert "www.strava.com/oauth/authorize" in auth_url

    state = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)["state"][0]
    assert state != token
    with pytest.raises(HTTPException):
        decode_token(state)


@pytest.mark.asyncio
async def test_get_strava_activities_uses_stored_token(client, auth_headers, monkeypatch):
    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None):
            assert headers["Authorization"] == "Bearer access-123"
            return DummyResponse(
                200,
                [{"id": 1, "name": "Morning Ride", "type": "Ride", "distance": 50000}],
            )

    monkeypatch.setattr(strava_router.httpx, "AsyncClient", FakeAsyncClient)

    async with async_session_maker() as session:
        user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
        session.add(
            models.StravaToken(
                user_id=user_id,
                access_token="access-123",
                refresh_token="refresh-123",
                expires_at=9999999999,
                athlete_id=7,
                athlete_name="Test Rider",
            )
        )
        await session.commit()

    response = await client.get("/api/v1/strava/activities", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()[0]["name"] == "Morning Ride"


@pytest.mark.asyncio
async def test_disconnect_strava_removes_token(client, auth_headers):
    async with async_session_maker() as session:
        user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
        session.add(
            models.StravaToken(
                user_id=user_id,
                access_token="access-123",
                refresh_token="refresh-123",
                expires_at=9999999999,
                athlete_id=7,
                athlete_name="Test Rider",
            )
        )
        await session.commit()

    response = await client.delete("/api/v1/strava/disconnect", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "disconnected"
