"""Source-neutral imported activity normalization.

The rest of the app still exposes the historical ``strava_activity_id`` field as
the stable ride identifier. This module keeps that compatibility layer in one
place while importers work with explicit source/external-id metadata.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

import crud
import models
from sqlalchemy.ext.asyncio import AsyncSession

ActivitySource = Literal["strava", "intervals", "fit"]


def synthetic_activity_id(source: ActivitySource, external_activity_id: str) -> int:
    """Return a positive legacy BIGINT id for non-Strava source identifiers."""
    basis = f"{source}:{external_activity_id}"
    return int(hashlib.sha256(basis.encode()).hexdigest()[:15], 16)


def fallback_fingerprint(
    *,
    source: ActivitySource,
    name: str | None,
    start_datetime: str | None,
    activity_date: str,
    sport_type: str,
    duration_seconds: int | None,
) -> str:
    payload = {
        "source": source,
        "name": name or "",
        "start_datetime": start_datetime or "",
        "activity_date": activity_date,
        "sport_type": sport_type,
        "duration_seconds": duration_seconds or 0,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(slots=True)
class ImportedActivity:
    source: ActivitySource
    external_activity_id: str | None
    name: str | None
    start_datetime: str | None
    activity_date: str
    sport_type: str = "cycling"
    duration_seconds: int | None = None
    streams: dict[str, Any] = field(default_factory=dict)
    start_lat: float | None = None
    start_lng: float | None = None
    weather: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    summary_avg_power_w: int | None = None
    summary_normalized_power_w: int | None = None
    summary_tss: float | None = None
    legacy_activity_id: int | None = None

    @property
    def source_key(self) -> str:
        if self.external_activity_id:
            return self.external_activity_id
        return fallback_fingerprint(
            source=self.source,
            name=self.name,
            start_datetime=self.start_datetime,
            activity_date=self.activity_date,
            sport_type=self.sport_type,
            duration_seconds=self.duration_seconds,
        )

    @property
    def legacy_metric_id(self) -> int:
        if self.legacy_activity_id is not None:
            return self.legacy_activity_id
        if self.source == "strava" and self.external_activity_id is not None:
            try:
                return int(self.external_activity_id)
            except (TypeError, ValueError):
                pass
        return synthetic_activity_id(self.source, self.source_key)

    def to_ride_input(self) -> dict[str, Any]:
        return {
            "strava_activity_id": self.legacy_metric_id,
            "activity_source": self.source,
            "external_activity_id": self.source_key,
            "source_metadata": self.metadata or None,
            "activity_name": self.name,
            "activity_start_datetime": self.start_datetime,
            "activity_date": self.activity_date,
            "sport_type": self.sport_type,
            "duration_seconds": self.duration_seconds,
            "start_lat": self.start_lat,
            "start_lng": self.start_lng,
            "streams": self.streams,
            "_summary_avg_power_w": self.summary_avg_power_w,
            "_summary_np_w": self.summary_normalized_power_w,
            "_summary_tss": self.summary_tss,
            **self.weather,
        }


async def find_existing_import(
    db: AsyncSession,
    user_id: str,
    activity: ImportedActivity,
) -> models.RideMetric | None:
    existing = await crud.get_ride_metric_by_source(
        db,
        user_id,
        activity.source,
        activity.source_key,
    )
    if existing is not None:
        return existing
    existing = await crud.get_ride_metric_by_strava_id(
        db,
        user_id,
        activity.legacy_metric_id,
    )
    if existing is not None:
        return existing
    return await crud.get_near_duplicate_ride_metric(
        db,
        user_id,
        activity_date=activity.activity_date,
        sport_type=activity.sport_type,
        activity_name=activity.name,
        activity_start_datetime=activity.start_datetime,
        duration_seconds=activity.duration_seconds,
        exclude_strava_activity_id=activity.legacy_metric_id,
        exclude_activity_source=activity.source,
        exclude_external_activity_id=activity.source_key,
    )


def to_ride_inputs(activities: list[ImportedActivity]) -> list[dict[str, Any]]:
    return [activity.to_ride_input() for activity in activities]
