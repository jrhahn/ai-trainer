import urllib.parse

import pytest
from fastapi import HTTPException

from auth import decode_token
from database import async_session_maker
import models
from routers import strava as strava_router


class FakeAsyncHttpClient:
    """Minimal httpx.AsyncClient stub for token exchange."""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return self._response

    async def get(self, *args, **kwargs):
        return self._response


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
async def test_strava_auth_uses_runtime_env_credentials(client, auth_headers, monkeypatch):
    monkeypatch.setattr(strava_router.settings, "strava_client_id", "runtime-client-id")
    monkeypatch.setattr(strava_router.settings, "strava_client_secret", "runtime-client-secret")

    response = await client.get("/api/v1/auth/strava", headers=auth_headers)

    assert response.status_code == 200
    query = urllib.parse.parse_qs(urllib.parse.urlparse(response.json()["authUrl"]).query)
    assert query["client_id"][0] == "runtime-client-id"


@pytest.mark.asyncio
async def test_strava_auth_shows_server_side_setup_message(client, auth_headers, monkeypatch):
    monkeypatch.setattr(strava_router.settings, "strava_client_id", "")
    monkeypatch.setattr(strava_router.settings, "strava_client_secret", "")

    response = await client.get("/api/v1/auth/strava", headers=auth_headers)

    assert response.status_code == 500
    assert "users do not need their own API keys" in response.json()["detail"]


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


@pytest.mark.asyncio
async def test_connect_to_strava_end_to_end(client, auth_headers, monkeypatch):
    """Full 'Connect to Strava' integration test.

    Simulates the exact flow a user experiences during setup:
    1. Frontend requests the Strava OAuth URL (GET /auth/strava).
    2. The backend returns a URL containing a short-lived opaque state token.
    3. After the user authorises on Strava, the callback arrives at
       GET /auth/strava/callback with the same state and a code.
    4. The backend exchanges the code for tokens, persists them, and redirects
       the browser to the frontend with ``?success=true``.

    This test ensures all four steps stay wired together and will catch any
    regression that makes "Connect to Strava" stop working.
    """
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    # Step 1 & 2 – request the OAuth URL; backend stores state internally
    auth_response = await client.get("/api/v1/auth/strava", headers=auth_headers)
    assert auth_response.status_code == 200
    auth_url = auth_response.json()["authUrl"]
    assert "www.strava.com/oauth/authorize" in auth_url

    # Extract the opaque state value that the backend embedded in the URL
    state = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)["state"][0]
    assert state  # must be non-empty

    # Verify the state cannot be decoded as a JWT (it is a random token, not a JWT)
    with pytest.raises(HTTPException, match="Invalid token"):
        decode_token(state)

    # The state must have been stored so the callback can retrieve it
    assert state in strava_router._oauth_states

    # Step 3 & 4 – Strava redirects back; mock the token-exchange HTTP call
    fake_token_payload = {
        "access_token": "e2e-access-token",
        "refresh_token": "e2e-refresh-token",
        "expires_at": 9999999999,
        "athlete": {"id": 77, "firstname": "End", "lastname": "ToEnd"},
    }
    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(200, fake_token_payload)),
    )

    callback_response = await client.get(
        "/api/v1/auth/strava/callback",
        params={"code": "strava-auth-code", "state": state},
        follow_redirects=False,
    )

    # Backend must redirect to the frontend success URL
    assert callback_response.status_code in (302, 307)
    assert "success=true" in callback_response.headers["location"]

    # The state must have been consumed (one-time use)
    assert state not in strava_router._oauth_states

    # The Strava token must be persisted in the database
    async with async_session_maker() as session:
        token_row = await session.get(models.StravaToken, user_id)
    assert token_row is not None
    assert token_row.access_token == "e2e-access-token"
    assert token_row.athlete_name == "End ToEnd"
