"""Strava token management and stream-fetching helpers.

Extracted from ``routers/strava.py`` so that the AI router can import these
functions without creating a router→router dependency.
"""

from __future__ import annotations

import time

import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import models
from config import settings

STRAVA_OAUTH_BASE = "https://www.strava.com"


async def ensure_fresh_strava_token(
    token_row: models.StravaToken,
    db: AsyncSession,
) -> str:
    """Return a valid access token, refreshing it first if expired."""
    if token_row.expires_at >= time.time():
        return token_row.access_token

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRAVA_OAUTH_BASE}/oauth/token",
            json={
                "client_id": settings.strava_client_id,
                "client_secret": settings.strava_client_secret,
                "refresh_token": token_row.refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if not resp.is_success:
        raise HTTPException(
            status_code=401,
            detail="Strava token expired and could not be refreshed",
        )

    data = resp.json()
    token_row.access_token = data["access_token"]
    token_row.refresh_token = data["refresh_token"]
    token_row.expires_at = data["expires_at"]
    await db.flush()
    return token_row.access_token


async def fetch_activity_streams(access_token: str, activity_id: int) -> dict:
    """Fetch per-second timeseries streams for a single Strava activity.

    Returns a dict keyed by stream type (e.g. ``"watts"``, ``"heartrate"``)
    whose values are Strava stream objects with a ``data`` list.  Returns an
    empty dict if the activity has no stream data or the request fails.
    """
    keys = "watts,heartrate,cadence,velocity_smooth,altitude,time"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{STRAVA_OAUTH_BASE}/api/v3/activities/{activity_id}/streams",
            params={"keys": keys, "key_by_type": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if not resp.is_success:
        return {}
    return resp.json()


async def fetch_activity_detail(access_token: str, activity_id: int) -> dict:
    """Fetch a single Strava activity summary/detail payload."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{STRAVA_OAUTH_BASE}/api/v3/activities/{activity_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if not resp.is_success:
        return {}
    data = resp.json()
    return data if isinstance(data, dict) else {}
