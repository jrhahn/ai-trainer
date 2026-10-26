"""The home location and the weather forecast."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field
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
    """Athlete-supplied training location; always stored as ``user_set``."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str = ""


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
