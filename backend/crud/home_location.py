"""The athlete's home location."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from crud.memory import _clamp_confidence


async def get_athlete_home_location(
    db: AsyncSession,
    user_id: str,
) -> models.AthleteHomeLocation | None:
    """Return the athlete's persisted training location, if any (#495)."""
    return await db.scalar(
        select(models.AthleteHomeLocation).where(
            models.AthleteHomeLocation.user_id == user_id
        )
    )


async def upsert_athlete_home_location(
    db: AsyncSession,
    user_id: str,
    *,
    latitude: float,
    longitude: float,
    label: str = "",
    source: str = "inferred",
    confidence: float = 0.0,
    ride_count: int = 0,
    now: datetime | None = None,
) -> models.AthleteHomeLocation | None:
    """Write the athlete's training location, honouring the user-set override.

    An ``inferred`` write is refused when a ``user_set`` row already exists and
    returns the stored row untouched: the athlete told the coach where they train,
    so the next clustering pass must not silently move it back (#342/#345/#346).
    A ``user_set`` write always wins. Returns the stored row, or ``None`` when the
    coordinates are unusable.
    """
    if latitude is None or longitude is None:
        return None
    if not (-90.0 <= float(latitude) <= 90.0 and -180.0 <= float(longitude) <= 180.0):
        return None

    normalized_source = "user_set" if source == "user_set" else "inferred"
    timestamp = now or datetime.now(timezone.utc)
    existing = await get_athlete_home_location(db, user_id)

    if existing is not None:
        if existing.source == "user_set" and normalized_source != "user_set":
            return existing
        existing.latitude = float(latitude)
        existing.longitude = float(longitude)
        # An inference pass must not blank a label the athlete gave us.
        if label or normalized_source == "user_set":
            existing.label = label[:120]
        existing.source = normalized_source
        existing.confidence = _clamp_confidence(confidence)
        existing.ride_count = max(0, int(ride_count))
        existing.updated_at = timestamp
        await db.flush()
        return existing

    row = models.AthleteHomeLocation(
        user_id=user_id,
        latitude=float(latitude),
        longitude=float(longitude),
        label=label[:120],
        source=normalized_source,
        confidence=_clamp_confidence(confidence),
        ride_count=max(0, int(ride_count)),
        updated_at=timestamp,
    )
    db.add(row)
    await db.flush()
    return row
