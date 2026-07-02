import urllib.parse
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from auth import decode_token
from database import async_session_maker
import crud
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


class ImportFlowHttpClient:
    """AsyncClient stub for background import pagination + stream fetches."""

    def __init__(self):
        self._activity_pages = {
            1: [
                {
                    "id": 111,
                    "name": "Morning Hike",
                    "start_date": "2026-04-01T22:30:00Z",
                    "start_date_local": "2026-04-02T00:30:00",
                    "type": "Hike",
                    "elapsed_time": 3600,
                },
                {
                    "id": 222,
                    "start_date": "2026-04-02T08:00:00Z",
                    "sport_type": "Ride",
                    "elapsed_time": 3600,
                },
            ],
            2: [],
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, headers=None):
        if "/athlete/activities" in url:
            page = int((params or {}).get("page", 1))
            return DummyResponse(200, self._activity_pages.get(page, []))

        if "/activities/111/streams" in url:
            return DummyResponse(
                200,
                {
                    "watts": {"data": [200, 220, 210, 230]},
                    "time": {"data": [0, 60, 120, 180]},
                    "heartrate": {"data": [145, 150, 152, 154]},
                },
            )

        if "/activities/222/streams" in url:
            # Genuine "no streams" (permanent 404) — imported summary-only.
            return DummyResponse(404, {})

        return DummyResponse(404, {})


class TransientStreamHttpClient(ImportFlowHttpClient):
    """Like ImportFlowHttpClient but activity 222's streams fail transiently."""

    async def get(self, url, params=None, headers=None):
        if "/activities/222/streams" in url:
            return DummyResponse(500, {})
        return await super().get(url, params=params, headers=headers)


def test_sanitize_streams_keeps_latlng_points():
    streams = strava_router._sanitize_streams(
        {
            "watts": {"data": [200, "bad", 210]},
            "latlng": {"data": [[52.52, 13.405], ["bad", 13.4], [52.53, 13.41]]},
        }
    )

    assert streams["watts"]["data"] == [200.0, 210.0]
    assert streams["latlng"]["data"] == [[52.52, 13.405], [52.53, 13.41]]


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
async def test_strava_auth_uses_runtime_env_credentials(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(strava_router.settings, "strava_client_id", "runtime-client-id")
    monkeypatch.setattr(
        strava_router.settings, "strava_client_secret", "runtime-client-secret"
    )

    response = await client.get("/api/v1/auth/strava", headers=auth_headers)

    assert response.status_code == 200
    query = urllib.parse.parse_qs(
        urllib.parse.urlparse(response.json()["authUrl"]).query
    )
    assert query["client_id"][0] == "runtime-client-id"


@pytest.mark.asyncio
async def test_strava_auth_shows_server_side_setup_message(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(strava_router.settings, "strava_client_id", "")
    monkeypatch.setattr(strava_router.settings, "strava_client_secret", "")

    response = await client.get("/api/v1/auth/strava", headers=auth_headers)

    assert response.status_code == 500
    assert "users do not need their own API keys" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_strava_activities_uses_stored_token(
    client, auth_headers, monkeypatch
):
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


@pytest.mark.asyncio
async def test_import_background_keeps_activity_with_no_streams(
    auth_headers, monkeypatch
):
    """An activity that genuinely has no streams (permanent 404) is imported summary-only."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    monkeypatch.setattr(strava_router.httpx, "AsyncClient", ImportFlowHttpClient)

    await strava_router._run_import_background(
        user_id=user_id,
        access_token="tok",
        ftp=250.0,
        after_ts=0,
        replace_existing=False,
    )

    progress = strava_router._import_progress[user_id]
    assert progress["status"] == "done"
    assert progress["total"] == 2
    assert progress["skipped"] == 0
    assert progress["imported"] == 2

    async with async_session_maker() as session:
        rides = await crud.get_all_ride_metrics_ordered(session, user_id)
    assert len(rides) == 2
    rides_by_id = {ride.strava_activity_id: ride for ride in rides}
    assert rides_by_id[111].activity_name == "Morning Hike"
    assert rides_by_id[111].activity_date == "2026-04-02"
    assert rides_by_id[111].sport_type == "Hike"
    assert rides_by_id[222].sport_type == "Ride"
    assert rides_by_id[222].ride_purpose == "unknown"


@pytest.mark.asyncio
async def test_import_background_skips_activity_on_transient_stream_failure(
    auth_headers, monkeypatch
):
    """A transient stream failure (5xx) skips the activity rather than importing it degraded (#325)."""
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    monkeypatch.setattr(strava_router.httpx, "AsyncClient", TransientStreamHttpClient)

    await strava_router._run_import_background(
        user_id=user_id,
        access_token="tok",
        ftp=250.0,
        after_ts=0,
        replace_existing=False,
    )

    progress = strava_router._import_progress[user_id]
    assert progress["status"] == "done"
    assert progress["total"] == 2
    assert progress["skipped"] == 1  # activity 222 skipped, not imported degraded
    assert progress["imported"] == 1

    async with async_session_maker() as session:
        rides = await crud.get_all_ride_metrics_ordered(session, user_id)
    # Only the good activity is persisted; 222 is left for a re-import.
    assert {ride.strava_activity_id for ride in rides} == {111}


@pytest.mark.asyncio
async def test_import_background_matches_imported_activities_to_plan(
    auth_headers, monkeypatch
):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    async with async_session_maker() as session:
        await crud.upsert_training_plan(
            session,
            user_id,
            [
                {
                    "date": "2026-04-02",
                    "workoutType": "endurance",
                    "title": "Aerobic Base Builder",
                    "description": "Steady aerobic work",
                    "durationMinutes": 90,
                }
            ],
        )
        await session.commit()

    monkeypatch.setattr(strava_router.httpx, "AsyncClient", ImportFlowHttpClient)

    await strava_router._run_import_background(
        user_id=user_id,
        access_token="tok",
        ftp=250.0,
        after_ts=0,
        replace_existing=False,
    )

    async with async_session_maker() as session:
        rides = await crud.get_all_ride_metrics_ordered(session, user_id)

    assert {ride.plan_match_status for ride in rides} == {"ambiguous"}
    assert {ride.matched_plan_date for ride in rides} == {"2026-04-02"}
    assert {
        ride.matched_plan_snapshot["title"]
        for ride in rides
        if ride.matched_plan_snapshot
    } == {"Aerobic Base Builder"}


@pytest.mark.asyncio
async def test_import_background_persists_activity_weather(auth_headers, monkeypatch):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    monkeypatch.setattr(strava_router.httpx, "AsyncClient", ImportFlowHttpClient)
    monkeypatch.setattr(
        strava_router,
        "enrich_activity_weather",
        AsyncMock(
            return_value={
                "start_lat": 52.52,
                "start_lng": 13.405,
                "weather_temperature_c": 4.3,
                "weather_condition": "cloudy",
                "weather_code": 3,
                "weather_source": "open_meteo",
            }
        ),
    )

    await strava_router._run_import_background(
        user_id=user_id,
        access_token="tok",
        ftp=250.0,
        after_ts=0,
        replace_existing=False,
    )

    async with async_session_maker() as session:
        rides = await crud.get_all_ride_metrics_ordered(session, user_id)

    assert rides
    assert {ride.weather_temperature_c for ride in rides} == {4.3}
    assert {ride.weather_condition for ride in rides} == {"cloudy"}


@pytest.mark.asyncio
async def test_import_background_replace_existing_overwrites_prior_rows(
    auth_headers, monkeypatch
):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    # Seed existing rows that should be removed by replace_existing=True.
    async with async_session_maker() as session:
        await crud.upsert_ride_metric(
            session,
            user_id,
            strava_activity_id=9999,
            activity_date="2026-03-01",
            sport_type="cycling",
            duration_seconds=3600,
            avg_power_w=180,
            normalized_power_w=190,
            intensity_factor=0.76,
            tss=55.0,
            ftp_used=250,
            ctl_after=10.0,
            atl_after=12.0,
            tsb_after=-2.0,
            ride_purpose="endurance",
            summary="Seed ride",
        )
        await crud.create_athlete_metric_snapshot(
            session,
            user_id,
            ftp=250,
            threshold_hr=None,
            ctl=10.0,
            atl=12.0,
            tsb=-2.0,
            source="manual_recalculate",
        )
        await session.commit()

    monkeypatch.setattr(strava_router.httpx, "AsyncClient", ImportFlowHttpClient)

    await strava_router._run_import_background(
        user_id=user_id,
        access_token="tok",
        ftp=250.0,
        after_ts=0,
        replace_existing=True,
    )

    async with async_session_maker() as session:
        rides = await crud.get_all_ride_metrics_ordered(session, user_id)
        snapshots = await crud.get_athlete_metric_history(session, user_id)

    assert all(r.strava_activity_id != 9999 for r in rides)
    assert {ride.strava_activity_id for ride in rides} == {111, 222}
    assert all(s.source != "manual_recalculate" for s in snapshots)
