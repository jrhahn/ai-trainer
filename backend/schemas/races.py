"""Race events."""

from __future__ import annotations

import re
from typing import Optional

from pydantic import ConfigDict, field_validator
from schemas.base import CamelModel, _to_camel
from schemas.profile import _require_iso_calendar_date


# ---------------------------------------------------------------------------
# Race events
# ---------------------------------------------------------------------------


class RaceEventRequest(CamelModel):
    date: str
    start_time: Optional[str] = None
    distance_km: float
    elevation_m: int

    @field_validator("date")
    @classmethod
    def date_must_be_iso(cls, value: str) -> str:
        return _require_iso_calendar_date(value)

    @field_validator("start_time")
    @classmethod
    def time_must_be_optional_hhmm(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("startTime must be HH:MM")
        return value

    @field_validator("distance_km")
    @classmethod
    def distance_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("distanceKm must be greater than 0")
        return value

    @field_validator("elevation_m")
    @classmethod
    def elevation_must_be_in_range(cls, value: int) -> int:
        # 0..100_000 m: non-negative, and bounded well under the DB int4 ceiling
        # so an out-of-range value is a 422 here rather than an unhandled 500 at
        # insert (#912).
        if value < 0 or value > 100_000:
            raise ValueError("elevationM must be between 0 and 100000")
        return value


class RaceEventResponse(CamelModel):
    id: str
    date: str
    start_time: Optional[str] = None
    distance_km: float
    elevation_m: int

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class RaceEventsResponse(CamelModel):
    events: list[RaceEventResponse]
