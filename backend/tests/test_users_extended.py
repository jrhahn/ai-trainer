"""Extended user endpoint tests: coach memory, strava connection in profile."""

from __future__ import annotations

import pytest

from auth import create_access_token, decode_token
from database import async_session_maker
import models


# ---------------------------------------------------------------------------
# Coach memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_coach_memory_empty(client, auth_headers):
    response = await client.get("/api/v1/users/me/coach-memory", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["memory"] == ""


@pytest.mark.asyncio
async def test_save_and_get_coach_memory(client, auth_headers):
    save_response = await client.put(
        "/api/v1/users/me/coach-memory",
        headers=auth_headers,
        json={"memory": "Prefers morning rides. FTP ~280 W."},
    )
    assert save_response.status_code == 200
    assert save_response.json()["memory"] == "Prefers morning rides. FTP ~280 W."

    get_response = await client.get("/api/v1/users/me/coach-memory", headers=auth_headers)
    assert get_response.status_code == 200
    assert get_response.json()["memory"] == "Prefers morning rides. FTP ~280 W."


@pytest.mark.asyncio
async def test_update_coach_memory_overwrites(client, auth_headers):
    await client.put(
        "/api/v1/users/me/coach-memory",
        headers=auth_headers,
        json={"memory": "First memory."},
    )
    await client.put(
        "/api/v1/users/me/coach-memory",
        headers=auth_headers,
        json={"memory": "Updated memory."},
    )
    response = await client.get("/api/v1/users/me/coach-memory", headers=auth_headers)
    assert response.json()["memory"] == "Updated memory."


# ---------------------------------------------------------------------------
# User profile — strava connection visible in response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_me_includes_strava_connection(client, auth_headers):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])

    async with async_session_maker() as session:
        session.add(
            models.StravaToken(
                user_id=user_id,
                access_token="tok",
                refresh_token="ref",
                expires_at=9999999999,
                athlete_id=42,
                athlete_name="Jane Cyclist",
            )
        )
        await session.commit()

    response = await client.get("/api/v1/users/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["stravaConnection"] is not None
    assert body["stravaConnection"]["athleteId"] == 42
    assert body["stravaConnection"]["athleteName"] == "Jane Cyclist"


@pytest.mark.asyncio
async def test_get_me_includes_rider_assessment(client, auth_headers, mock_ai_service):
    """After an analysis, the user profile should include the rider assessment."""
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 1,
                    "name": "Test Ride",
                    "type": "Ride",
                    "distance": 50000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 500,
                    "startDate": "2026-04-10T08:00:00Z",
                    "averageWatts": 220,
                }
            ]
        },
    )

    response = await client.get("/api/v1/users/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["riderAssessment"] is not None
    assert response.json()["riderAssessment"]["riderType"] is not None


# ---------------------------------------------------------------------------
# Auth — expired and invalid tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_token_returns_401(client, monkeypatch):
    """A JWT with a past expiry should be rejected with 401."""
    import jwt
    import auth as auth_mod
    from datetime import datetime, timezone, timedelta

    payload = {
        "sub": "some-user-id",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    expired_token = jwt.encode(payload, auth_mod.JWT_SECRET, algorithm=auth_mod.JWT_ALGORITHM)

    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_returns_401(client):
    """A completely garbage token should be rejected with 401."""
    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer not.a.valid.jwt.token"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_token_for_deleted_user_returns_401(client, auth_headers):
    """A valid JWT whose user has since been deleted should return 401."""
    # Delete the user
    await client.delete("/api/v1/users/me", headers=auth_headers)

    # Try to use the now-orphaned token
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_bearer_token_returns_401(client):
    """A request with no Authorization header should return 401."""
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401
