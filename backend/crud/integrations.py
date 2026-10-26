"""The Strava and intervals.icu tokens."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

import models


# ---------------------------------------------------------------------------
# StravaToken
# ---------------------------------------------------------------------------


async def get_strava_token(db: AsyncSession, user_id: str) -> models.StravaToken | None:
    """Return the StravaToken for a user, or None."""
    return await db.get(models.StravaToken, user_id)


async def upsert_strava_token(
    db: AsyncSession,
    user_id: str,
    *,
    access_token: str,
    refresh_token: str,
    expires_at: int,
    athlete_id: int,
    athlete_name: str,
) -> models.StravaToken:
    """Create or update the StravaToken for a user and flush."""
    existing = await get_strava_token(db, user_id)
    if existing is None:
        existing = models.StravaToken(
            user_id=user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            athlete_id=athlete_id,
            athlete_name=athlete_name,
        )
        db.add(existing)
    else:
        existing.access_token = access_token
        existing.refresh_token = refresh_token
        existing.expires_at = expires_at
        existing.athlete_id = athlete_id
        existing.athlete_name = athlete_name
    await db.flush()
    return existing


async def delete_strava_token(db: AsyncSession, user_id: str) -> None:
    """Delete the StravaToken for a user if it exists and flush."""
    token_row = await get_strava_token(db, user_id)
    if token_row is not None:
        await db.delete(token_row)
        await db.flush()


# ---------------------------------------------------------------------------
# IntervalsToken
# ---------------------------------------------------------------------------


async def get_intervals_token(
    db: AsyncSession, user_id: str
) -> models.IntervalsToken | None:
    """Return the Intervals.icu token for a user, or None."""
    return await db.get(models.IntervalsToken, user_id)


async def upsert_intervals_token(
    db: AsyncSession,
    user_id: str,
    *,
    api_key: str,
    athlete_id: str = "0",
    athlete_name: str = "",
) -> models.IntervalsToken:
    """Create or update Intervals.icu credentials for a user and flush."""
    existing = await get_intervals_token(db, user_id)
    if existing is None:
        existing = models.IntervalsToken(
            user_id=user_id,
            api_key=api_key,
            athlete_id=athlete_id or "0",
            athlete_name=athlete_name,
        )
        db.add(existing)
    else:
        existing.api_key = api_key
        existing.athlete_id = athlete_id or "0"
        existing.athlete_name = athlete_name
        existing.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return existing


async def delete_intervals_token(db: AsyncSession, user_id: str) -> None:
    """Delete Intervals.icu credentials for a user if present and flush."""
    token_row = await get_intervals_token(db, user_id)
    if token_row is not None:
        await db.delete(token_row)
        await db.flush()
