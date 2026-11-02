"""The home location and the weather forecast."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator
from schemas.base import CamelModel


# ---------------------------------------------------------------------------
# Weather: training location + upcoming forecast (#495)
# ---------------------------------------------------------------------------


class AthleteHomeLocationSchema(CamelModel):
    """The athlete's persisted training location.

    ``source`` is the authority marker: ``user_set`` (the athlete told the coach
    where they train) always outranks ``inferred`` (clustered ride starts) and is
    never overwritten by an inference pass.
    """

    latitude: float
    longitude: float
    label: str = ""
    source: str = "inferred"
    confidence: float = 0.0
    ride_count: int = 0
    updated_at: Optional[datetime] = None


class AthleteHomeLocationResponse(CamelModel):
    """Nullable wrapper — an athlete may not have a training location yet."""

    location: Optional[AthleteHomeLocationSchema] = None


class AthleteHomeLocationUpdate(CamelModel):
    """Athlete-supplied training location; always stored as ``user_set``.

    Coordinates are optional: nobody knows their own, and a town alone used to
    fail (review of 2026-11-01, ai-trainer-ops#338 A11). Without them the label
    is looked up, as the chat does for "bei Freiburg" (#129).
    """

    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    label: str = ""

    @model_validator(mode="after")
    def _a_place_or_both_coordinates(self) -> "AthleteHomeLocationUpdate":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Give both a latitude and a longitude, or neither.")
        if self.latitude is None and not self.label.strip():
            raise ValueError("Give a place name or coordinates.")
        return self


class DailyForecastSchema(CamelModel):
    """One day of the upcoming outlook near the athlete's training location.

    ``load_flag`` is the coaching-relevant summary (``very_hot``, ``freezing``,
    ``rain``, …) derived by :func:`services.weather_service.weather_load_flag`, so
    the UI and the plan prompts read the same judgement.
    """

    date: str
    condition: Optional[str] = None
    weather_code: Optional[int] = None
    temperature_max_c: Optional[float] = None
    temperature_min_c: Optional[float] = None
    precipitation_mm: Optional[float] = None
    wind_speed_kph: Optional[float] = None
    load_flag: Optional[str] = None


class WeatherForecastResponse(CamelModel):
    location: Optional[AthleteHomeLocationSchema] = None
    days: list[DailyForecastSchema] = []
