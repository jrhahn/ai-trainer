"""Extended Strava router tests: callback, refresh, fetch_activity_streams."""

from __future__ import annotations

import time

import pytest

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


class FakeAsyncHttpClient:
    """Minimal httpx.AsyncClient stub."""

    def __init__(self, response: DummyResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return self._response

    async def get(self, *args, **kwargs):
        return self._response


# ---------------------------------------------------------------------------
# _backend_url helper
# ---------------------------------------------------------------------------


def test_backend_url_uses_server_url(monkeypatch):
    monkeypatch.setattr(strava_router.settings, "server_url", "trainlikea.pro")
    result = strava_router._backend_url()
    assert result == "https://trainlikea.pro"


def test_backend_url_falls_back_to_backend_url(monkeypatch):
    monkeypatch.setattr(strava_router.settings, "server_url", "")
    monkeypatch.setattr(strava_router.settings, "backend_url", "http://localhost:8000")
    result = strava_router._backend_url()
    assert result == "http://localhost:8000"


def test_backend_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setattr(strava_router.settings, "server_url", "example.com/")
    result = strava_router._backend_url()
    assert not result.endswith("/")


# ---------------------------------------------------------------------------
# strava_callback — error cases (no real HTTP needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strava_callback_missing_code_redirects_to_error(client):
    response = await client.get(
        "/api/v1/auth/strava/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "error" in response.headers["location"]


@pytest.mark.asyncio
async def test_strava_callback_invalid_state_redirects_to_error(client):
    response = await client.get(
        "/api/v1/auth/strava/callback",
        params={"code": "abc123", "state": "invalid-state-xyz"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "error" in response.headers["location"].lower()


@pytest.mark.asyncio
async def test_strava_callback_expired_state_redirects_to_error(client, auth_headers):
    """A state that has already expired should redirect with an error."""
    # Inject a pre-expired state directly into the module-level dict
    expired_state = "expired-state-token-xyz"
    strava_router._oauth_states[expired_state] = ("some-user-id", time.time() - 1)

    response = await client.get(
        "/api/v1/auth/strava/callback",
        params={"code": "abc123", "state": expired_state},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "error" in response.headers["location"].lower()


@pytest.mark.asyncio
async def test_strava_callback_unknown_user_redirects_to_error(client, auth_headers):
    """Valid state referencing a non-existent user should redirect with an error."""
    ghost_state = "ghost-user-state-token"
    strava_router._oauth_states[ghost_state] = ("nonexistent-user-id", time.time() + 600)

    response = await client.get(
        "/api/v1/auth/strava/callback",
        params={"code": "abc123", "state": ghost_state},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "error" in response.headers["location"].lower()


@pytest.mark.asyncio
async def test_strava_callback_successful_token_exchange(client, auth_headers, monkeypatch):
    """A full successful callback should redirect with success=true and persist the token."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    valid_state = "valid-callback-state-123"
    strava_router._oauth_states[valid_state] = (user_id, time.time() + 600)

    fake_token_data = {
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
        "expires_at": 9999999999,
        "athlete": {"id": 42, "firstname": "Jane", "lastname": "Doe"},
    }
    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(200, fake_token_data)),
    )

    response = await client.get(
        "/api/v1/auth/strava/callback",
        params={"code": "exchange-code", "state": valid_state},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "success=true" in response.headers["location"]

    # Verify token was persisted in the DB
    async with async_session_maker() as session:
        token_row = await session.get(models.StravaToken, user_id)
    assert token_row is not None
    assert token_row.access_token == "new-access-token"
    assert token_row.athlete_name == "Jane Doe"


@pytest.mark.asyncio
async def test_strava_callback_token_exchange_failure(client, auth_headers, monkeypatch):
    """A failed token exchange should redirect with an error message."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    fail_state = "fail-exchange-state-456"
    strava_router._oauth_states[fail_state] = (user_id, time.time() + 600)

    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(400, {"message": "Bad Request"})),
    )

    response = await client.get(
        "/api/v1/auth/strava/callback",
        params={"code": "bad-code", "state": fail_state},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "error" in response.headers["location"].lower()


@pytest.mark.asyncio
async def test_strava_callback_updates_existing_token(client, auth_headers, monkeypatch):
    """When a Strava token already exists it should be updated, not duplicated."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    # Insert an existing token first
    async with async_session_maker() as session:
        session.add(
            models.StravaToken(
                user_id=user_id,
                access_token="old-access",
                refresh_token="old-refresh",
                expires_at=1111111111,
                athlete_id=10,
                athlete_name="Old Name",
            )
        )
        await session.commit()

    update_state = "update-state-789"
    strava_router._oauth_states[update_state] = (user_id, time.time() + 600)

    fake_token_data = {
        "access_token": "updated-access",
        "refresh_token": "updated-refresh",
        "expires_at": 9999999999,
        "athlete": {"id": 10, "firstname": "Updated", "lastname": "Rider"},
    }
    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(200, fake_token_data)),
    )

    response = await client.get(
        "/api/v1/auth/strava/callback",
        params={"code": "new-code", "state": update_state},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "success=true" in response.headers["location"]

    async with async_session_maker() as session:
        token_row = await session.get(models.StravaToken, user_id)
    assert token_row is not None
    assert token_row.access_token == "updated-access"
    assert token_row.athlete_name == "Updated Rider"


# ---------------------------------------------------------------------------
# strava_refresh — token refresh endpoint
# ---------------------------------------------------------------------------


async def _seed_strava_token(
    user_id: str,
    *,
    access_token: str = "stale-access",
    refresh_token: str = "stale-refresh",
    expires_at: int = 1000000000,
) -> None:
    async with async_session_maker() as session:
        session.add(
            models.StravaToken(
                user_id=user_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                athlete_id=7,
                athlete_name="Rider",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_strava_refresh_success(client, auth_headers, monkeypatch):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    await _seed_strava_token(user_id)

    fake_refresh_data = {
        "access_token": "refreshed-access",
        "refresh_token": "refreshed-refresh",
        "expires_at": 9999999999,
    }
    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(200, fake_refresh_data)),
    )

    response = await client.post(
        "/api/v1/auth/strava/refresh",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accessToken"] == "refreshed-access"
    assert body["refreshToken"] == "refreshed-refresh"


@pytest.mark.asyncio
async def test_strava_refresh_failure_returns_400(client, auth_headers, monkeypatch):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    await _seed_strava_token(user_id)

    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(401, {"message": "Unauthorized"})),
    )

    response = await client.post(
        "/api/v1/auth/strava/refresh",
        headers=auth_headers,
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_strava_refresh_without_connection_returns_404(client, auth_headers):
    """Refreshing with no stored Strava connection returns 404, not a refresh."""
    response = await client.post("/api/v1/auth/strava/refresh", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_strava_refresh_uses_stored_token_not_request_body(
    client, auth_headers, monkeypatch
):
    """The refresh must use the stored refresh token, never a client-supplied one."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    await _seed_strava_token(user_id, refresh_token="stored-refresh")

    captured: dict = {}

    class CapturingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            captured["json"] = kwargs.get("json")
            return DummyResponse(
                200,
                {
                    "access_token": "fresh-access",
                    "refresh_token": "fresh-refresh",
                    "expires_at": 9999999999,
                },
            )

    monkeypatch.setattr(
        strava_router.httpx, "AsyncClient", lambda: CapturingClient()
    )

    # Even a forged refresh token in the body must be ignored.
    response = await client.post(
        "/api/v1/auth/strava/refresh",
        headers=auth_headers,
        json={"refreshToken": "attacker-supplied"},
    )
    assert response.status_code == 200
    assert captured["json"]["refresh_token"] == "stored-refresh"


@pytest.mark.asyncio
async def test_strava_refresh_updates_stored_token(client, auth_headers, monkeypatch):
    """When a StravaToken row exists, it should be updated after a refresh."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    await _seed_strava_token(user_id)

    fake_refresh_data = {
        "access_token": "fresh-access",
        "refresh_token": "fresh-refresh",
        "expires_at": 9999999999,
    }
    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(200, fake_refresh_data)),
    )

    await client.post(
        "/api/v1/auth/strava/refresh",
        headers=auth_headers,
    )

    async with async_session_maker() as session:
        token_row = await session.get(models.StravaToken, user_id)
    assert token_row is not None
    assert token_row.access_token == "fresh-access"


# ---------------------------------------------------------------------------
# fetch_activity_streams
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_activity_streams_success(monkeypatch):
    fake_streams = {
        "watts": {"data": [200.0, 210.0, 220.0]},
        "heartrate": {"data": [150, 152, 154]},
        "time": {"data": [0, 1, 2]},
    }
    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(200, fake_streams)),
    )
    result = await strava_router.fetch_activity_streams("access-token", 12345)
    assert result == fake_streams


@pytest.mark.asyncio
async def test_fetch_activity_streams_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(404, {})),
    )
    result = await strava_router.fetch_activity_streams("access-token", 99999)
    assert result == {}


# ---------------------------------------------------------------------------
# ensure_fresh_strava_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_fresh_strava_token_not_expired(monkeypatch):
    """When token is still valid, no refresh request is made."""

    class NeverCalledClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise AssertionError("Should not refresh a valid token")

    monkeypatch.setattr(strava_router.httpx, "AsyncClient", NeverCalledClient)

    token_row = models.StravaToken(
        user_id="fake-user",
        access_token="still-valid",
        refresh_token="some-refresh",
        expires_at=int(time.time()) + 3600,
        athlete_id=1,
    )
    async with async_session_maker() as session:
        result = await strava_router.ensure_fresh_strava_token(token_row, session)
    assert result == "still-valid"


@pytest.mark.asyncio
async def test_ensure_fresh_strava_token_refreshes_when_expired(
    auth_headers, monkeypatch
):
    """An expired token is refreshed and the rotated token is persisted."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    await _seed_strava_token(
        user_id,
        access_token="stale-access",
        refresh_token="stale-refresh",
        expires_at=int(time.time()) - 3600,
    )

    fresh = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_at": int(time.time()) + 3600,
    }
    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(200, fresh)),
    )

    async with async_session_maker() as session:
        token_row = await session.get(models.StravaToken, user_id)
        result = await strava_router.ensure_fresh_strava_token(token_row, session)
        await session.commit()

    assert result == "new-access"
    async with async_session_maker() as session:
        reloaded = await session.get(models.StravaToken, user_id)
    assert reloaded.refresh_token == "new-refresh"


@pytest.mark.asyncio
async def test_ensure_fresh_strava_token_reuses_token_refreshed_under_lock(
    auth_headers, monkeypatch
):
    """If another caller already refreshed the row while we waited for the lock,
    reuse the fresh token rather than refreshing again (which would invalidate
    the just-rotated refresh token)."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    await _seed_strava_token(
        user_id,
        access_token="already-fresh-access",
        refresh_token="already-fresh-refresh",
        expires_at=int(time.time()) + 3600,
    )

    class NeverCalledClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise AssertionError("must not refresh an already-fresh token")

    monkeypatch.setattr(strava_router.httpx, "AsyncClient", NeverCalledClient)

    # A caller holding a stale in-memory view of the token (e.g. loaded before
    # a concurrent refresh committed) should still pick up the fresh DB row.
    stale_view = models.StravaToken(
        user_id=user_id,
        access_token="stale-access",
        refresh_token="stale-refresh",
        expires_at=int(time.time()) - 3600,
        athlete_id=7,
    )
    async with async_session_maker() as session:
        result = await strava_router.ensure_fresh_strava_token(stale_view, session)

    assert result == "already-fresh-access"


@pytest.mark.asyncio
async def test_get_strava_activities_no_strava_connected(client, auth_headers):
    """Without a connected Strava account, activities endpoint returns 404."""
    response = await client.get("/api/v1/strava/activities", headers=auth_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_strava_activities_with_after_id_filter(client, auth_headers, monkeypatch):
    """The after_id query param filters out older activities."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with async_session_maker() as session:
        session.add(
            models.StravaToken(
                user_id=user_id,
                access_token="tok",
                refresh_token="ref",
                expires_at=9999999999,
                athlete_id=1,
            )
        )
        await session.commit()

    fake_activities = [
        {"id": 100, "name": "Old Ride", "type": "Ride", "distance": 10000},
        {"id": 200, "name": "New Ride", "type": "Ride", "distance": 20000},
    ]
    monkeypatch.setattr(
        strava_router.httpx,
        "AsyncClient",
        lambda: FakeAsyncHttpClient(DummyResponse(200, fake_activities)),
    )

    response = await client.get(
        "/api/v1/strava/activities",
        headers=auth_headers,
        params={"after_id": 150},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == 200
