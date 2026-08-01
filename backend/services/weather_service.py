"""Weather enrichment for Strava activities and training-plan context."""

from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services.home_location import TrainingLocation, resolve_training_location
from services.strava_service import fetch_activity_detail, fetch_activity_streams

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_HOURLY = (
    "temperature_2m,apparent_temperature,weather_code,precipitation,wind_speed_10m"
)
WEATHER_DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"

# Open-Meteo's daily outlook barely moves within an hour, and the dashboard hits it
# on every hydration, so the daily forecast is cached rather than re-fetched per
# request. The key rounds coordinates to ~11 km so nearby athletes share one
# lookup, and a single 16-day fetch serves every shorter horizon by slicing.
FORECAST_CACHE_TTL_SECONDS = 3600
FORECAST_CACHE_COORD_PRECISION = 1
FORECAST_MAX_DAYS = 16

_forecast_cache: dict[tuple[float, float], tuple[float, list[dict[str, Any]]]] = {}


def condition_from_code(code: int | None) -> str | None:
    """Map WMO weather codes to compact display/prompt labels."""
    if code is None:
        return None
    if code == 0:
        return "clear"
    if code in {1, 2}:
        return "partly_cloudy"
    if code == 3:
        return "cloudy"
    if code in {45, 48}:
        return "fog"
    if code in {51, 53, 55, 56, 57}:
        return "drizzle"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if code in {95, 96, 99}:
        return "thunderstorm"
    return "unknown"


def weather_load_flag(
    temperature_c: float | None, condition: str | None = None
) -> str | None:
    """Return a coaching-relevant weather flag for plan prompts."""
    if temperature_c is None:
        return None
    if temperature_c <= 0:
        return "freezing"
    if temperature_c < 8:
        return "cold"
    if temperature_c >= 35:
        return "very_hot"
    if temperature_c >= 29:
        return "hot"
    if condition in {"rain", "snow", "thunderstorm"}:
        return condition
    return None


def coordinates_from_activity(
    activity: dict[str, Any],
) -> tuple[float | None, float | None]:
    raw = (
        activity.get("start_latlng")
        or activity.get("startLatlng")
        or activity.get("latlng")
        or activity.get("start_lat_lng")
    )
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None, None
    lat, lng = raw[0], raw[1]
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None, None
    if float(lat) == 0.0 and float(lng) == 0.0:
        return None, None
    return float(lat), float(lng)


def coordinates_from_streams(
    streams: dict[str, Any] | None,
) -> tuple[float | None, float | None]:
    """Return a midpoint GPS coordinate from a Strava latlng stream."""
    if not isinstance(streams, dict):
        return None, None
    latlng_stream = streams.get("latlng")
    if not isinstance(latlng_stream, dict):
        return None, None
    data = latlng_stream.get("data")
    if not isinstance(data, list):
        return None, None

    valid_points: list[tuple[int, float, float]] = []
    for idx, point in enumerate(data):
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        lat, lng = point[0], point[1]
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        if float(lat) == 0.0 and float(lng) == 0.0:
            continue
        valid_points.append((idx, float(lat), float(lng)))
    if not valid_points:
        return None, None

    time_stream = streams.get("time")
    time_data = time_stream.get("data") if isinstance(time_stream, dict) else None
    if isinstance(time_data, list) and len(time_data) >= len(data):
        numeric_times = [t for t in time_data if isinstance(t, (int, float))]
        if numeric_times:
            target = (float(numeric_times[0]) + float(numeric_times[-1])) / 2.0
            _idx, lat, lng = min(
                valid_points,
                key=lambda p: (
                    abs(float(time_data[p[0]]) - target)
                    if p[0] < len(time_data)
                    and isinstance(time_data[p[0]], (int, float))
                    else float("inf")
                ),
            )
            return lat, lng

    _idx, lat, lng = valid_points[len(valid_points) // 2]
    return lat, lng


def _stream_midpoint_offset_seconds(streams: dict[str, Any] | None) -> float | None:
    if not isinstance(streams, dict):
        return None
    time_stream = streams.get("time")
    time_data = time_stream.get("data") if isinstance(time_stream, dict) else None
    if not isinstance(time_data, list):
        return None
    numeric = [float(t) for t in time_data if isinstance(t, (int, float))]
    if not numeric:
        return None
    return (numeric[0] + numeric[-1]) / 2.0


def _activity_midpoint_datetime(
    activity: dict[str, Any],
    streams: dict[str, Any] | None,
) -> str | None:
    start_datetime = (
        activity.get("start_date_local")
        or activity.get("startDateLocal")
        or activity.get("start_date")
        or activity.get("startDate")
    )
    if not start_datetime:
        return None

    try:
        parsed_start = datetime.fromisoformat(
            str(start_datetime).replace("Z", "+00:00")
        )
    except ValueError:
        return str(start_datetime)

    midpoint_offset = _stream_midpoint_offset_seconds(streams)
    if midpoint_offset is None:
        duration = (
            activity.get("elapsed_time")
            or activity.get("elapsedTime")
            or activity.get("moving_time")
            or activity.get("movingTime")
        )
        midpoint_offset = (
            float(duration) / 2.0 if isinstance(duration, (int, float)) else 0.0
        )

    return (parsed_start + timedelta(seconds=midpoint_offset)).isoformat()


def _midpoint_datetime_from_values(
    start_datetime: str | None,
    duration_seconds: int | None,
) -> str | None:
    if not start_datetime:
        return None
    try:
        parsed_start = datetime.fromisoformat(
            str(start_datetime).replace("Z", "+00:00")
        )
    except ValueError:
        return str(start_datetime)
    midpoint_offset = float(duration_seconds or 0) / 2.0
    return (parsed_start + timedelta(seconds=midpoint_offset)).isoformat()


def _activity_hour(activity_start_datetime: str | None) -> int:
    if not activity_start_datetime:
        return 12
    raw = activity_start_datetime.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).hour
    except ValueError:
        return 12


def _nearest_hour_index(times: list[str], activity_date: str, hour: int) -> int | None:
    if not times:
        return None
    target = f"{activity_date}T{hour:02}:00"
    if target in times:
        return times.index(target)

    best_idx: int | None = None
    best_delta = 99
    for idx, raw_time in enumerate(times):
        if not raw_time.startswith(f"{activity_date}T"):
            continue
        try:
            candidate_hour = int(raw_time[11:13])
        except ValueError:
            continue
        delta = abs(candidate_hour - hour)
        if delta < best_delta:
            best_idx = idx
            best_delta = delta
    return best_idx


def _pick_hourly_value(values: list[Any] | None, idx: int | None) -> Any:
    if idx is None or not isinstance(values, list) or idx >= len(values):
        return None
    return values[idx]


async def fetch_activity_weather(
    latitude: float | None,
    longitude: float | None,
    activity_date: str,
    activity_start_datetime: str | None = None,
) -> dict[str, Any] | None:
    """Fetch hourly weather nearest to an activity start.

    Returns snake_case keys matching RideMetric columns, or None when location,
    date, or the upstream weather service is unavailable.
    """
    if latitude is None or longitude is None or not activity_date:
        return None

    try:
        parsed_date = date.fromisoformat(activity_date)
    except ValueError:
        return None

    today = datetime.now(timezone.utc).date()
    # Open-Meteo's archive can lag for very recent days; the forecast endpoint
    # serves today/future data plus recent past days.
    url = FORECAST_URL if parsed_date >= today - timedelta(days=7) else ARCHIVE_URL
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": WEATHER_HOURLY,
        "timezone": "auto",
    }
    if url == FORECAST_URL and parsed_date < today:
        params["past_days"] = 7
        params["forecast_days"] = 1
    else:
        params["start_date"] = activity_date
        params["end_date"] = activity_date

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params=params)
        if not resp.is_success:
            return None
        payload = resp.json()
    except Exception:
        logger.info(
            "Weather lookup failed for activity on %s", activity_date, exc_info=True
        )
        return None

    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict):
        return None

    idx = _nearest_hour_index(
        hourly.get("time") or [],
        activity_date,
        _activity_hour(activity_start_datetime),
    )
    temperature = _pick_hourly_value(hourly.get("temperature_2m"), idx)
    apparent = _pick_hourly_value(hourly.get("apparent_temperature"), idx)
    code = _pick_hourly_value(hourly.get("weather_code"), idx)
    wind = _pick_hourly_value(hourly.get("wind_speed_10m"), idx)
    precipitation = _pick_hourly_value(hourly.get("precipitation"), idx)

    numeric_code = int(code) if isinstance(code, (int, float)) else None
    if temperature is None and numeric_code is None:
        return None

    return {
        "weather_temperature_c": (
            round(float(temperature), 1)
            if isinstance(temperature, (int, float))
            else None
        ),
        "weather_apparent_temperature_c": (
            round(float(apparent), 1) if isinstance(apparent, (int, float)) else None
        ),
        "weather_condition": condition_from_code(numeric_code),
        "weather_code": numeric_code,
        "weather_wind_speed_kph": (
            round(float(wind), 1) if isinstance(wind, (int, float)) else None
        ),
        "weather_precipitation_mm": (
            round(float(precipitation), 1)
            if isinstance(precipitation, (int, float))
            else None
        ),
        "weather_source": "open_meteo",
    }


async def home_coordinates_for_user(
    db: AsyncSession,
    user_id: str,
) -> tuple[float, float] | None:
    """Coordinates to fall back on for activities that carry no GPS (#495).

    Resolved once per import batch and handed to
    :func:`enrich_activity_weather` so indoor/trainer rides still record the
    conditions the athlete chose to avoid.
    """
    location = await resolve_training_location(db, user_id)
    if location is None:
        return None
    return location.latitude, location.longitude


async def enrich_activity_weather(
    activity: dict[str, Any],
    streams: dict[str, Any] | None = None,
    fallback_coordinates: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Return weather/coordinate fields for a raw Strava activity dict.

    ``fallback_coordinates`` is the athlete's persisted training location, used to
    look up conditions for activities that carry no GPS — indoor/trainer rides
    above all. That is what makes "it was 34 °C and the athlete rode inside" a
    learnable signal (#495) instead of a blank row. The fallback never populates
    ``start_lat``/``start_lng`` (those stay an honest record of measured GPS) and
    is tagged ``weather_source="open_meteo_home"`` so consumers can tell a
    location-derived reading from a measured one.
    """
    lat, lng = coordinates_from_streams(streams)
    if lat is None or lng is None:
        lat, lng = coordinates_from_activity(activity)
    weather_datetime = _activity_midpoint_datetime(activity, streams)
    activity_date = str(weather_datetime)[:10] if weather_datetime else ""

    if lat is not None and lng is not None:
        weather = await fetch_activity_weather(
            lat, lng, activity_date, weather_datetime
        )
        return {"start_lat": lat, "start_lng": lng, **(weather or {})}

    if fallback_coordinates is None:
        return {"start_lat": None, "start_lng": None}
    weather = await fetch_activity_weather(
        fallback_coordinates[0], fallback_coordinates[1], activity_date, weather_datetime
    )
    if not weather:
        return {"start_lat": None, "start_lng": None}
    return {
        "start_lat": None,
        "start_lng": None,
        **weather,
        "weather_source": "open_meteo_home",
    }


def _apply_weather_fields(metric: models.RideMetric, fields: dict[str, Any]) -> None:
    for key, value in fields.items():
        if hasattr(metric, key):
            setattr(metric, key, value)


async def backfill_missing_ride_weather(
    db: AsyncSession,
    user_id: str,
    access_token: str | None,
    limit: int = 90,
) -> int:
    """Attach missing weather to recent stored rides.

    Existing imports may lack coordinates, so this optionally fetches the
    Strava activity detail first to recover ``start_latlng``. Rides that still
    have no GPS afterwards (indoor/trainer sessions) fall back to the athlete's
    persisted training location, tagged ``weather_source="open_meteo_home"``, so
    the weather-preference engine can see what the athlete rode *away* from (#495).
    """
    rows = await crud.get_ride_metrics_missing_weather(db, user_id, limit=limit)
    home = await resolve_training_location(db, user_id)
    updated = 0
    for row in rows:
        lat = row.start_lat
        lng = row.start_lng

        if (lat is None or lng is None) and access_token:
            try:
                streams = await fetch_activity_streams(
                    access_token, row.strava_activity_id
                )
                stream_lat, stream_lng = coordinates_from_streams(streams)
                lat = stream_lat if stream_lat is not None else lat
                lng = stream_lng if stream_lng is not None else lng
            except Exception:
                logger.info(
                    "Could not fetch Strava GPS stream while backfilling weather for %s",
                    row.strava_activity_id,
                    exc_info=True,
                )

        if (lat is None or lng is None) and access_token:
            try:
                detail = await fetch_activity_detail(
                    access_token, row.strava_activity_id
                )
                detail_lat, detail_lng = coordinates_from_activity(detail)
                lat = detail_lat if detail_lat is not None else lat
                lng = detail_lng if detail_lng is not None else lng
                if row.activity_name is None and isinstance(detail.get("name"), str):
                    row.activity_name = detail["name"]
            except Exception:
                logger.info(
                    "Could not fetch Strava detail while backfilling weather for %s",
                    row.strava_activity_id,
                    exc_info=True,
                )

        weather_datetime = _midpoint_datetime_from_values(
            row.activity_start_datetime,
            row.duration_seconds,
        )
        weather_date = (
            str(weather_datetime)[:10] if weather_datetime else row.activity_date
        )
        if lat is not None and lng is not None:
            weather = await fetch_activity_weather(
                lat, lng, weather_date, weather_datetime
            )
            if weather:
                _apply_weather_fields(row, {"start_lat": lat, "start_lng": lng, **weather})
                updated += 1
            continue

        if home is None:
            continue
        weather = await fetch_activity_weather(
            home.latitude, home.longitude, weather_date, weather_datetime
        )
        if weather:
            _apply_weather_fields(
                row, {**weather, "weather_source": "open_meteo_home"}
            )
            updated += 1

    if updated:
        await db.flush()
    return updated


def _cache_key(latitude: float, longitude: float) -> tuple[float, float]:
    return (
        round(float(latitude), FORECAST_CACHE_COORD_PRECISION),
        round(float(longitude), FORECAST_CACHE_COORD_PRECISION),
    )


def clear_forecast_cache() -> None:
    """Drop every cached forecast (used by tests and after a location change)."""
    _forecast_cache.clear()


def _parse_daily_payload(payload: Any) -> list[dict[str, Any]]:
    """Map an Open-Meteo daily response into our per-day forecast dicts."""
    daily = payload.get("daily") if isinstance(payload, dict) else None
    if not isinstance(daily, dict):
        return []

    times = daily.get("time") or []
    max_temps = daily.get("temperature_2m_max") or []
    min_temps = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    precipitation = daily.get("precipitation_sum") or []
    wind = daily.get("wind_speed_10m_max") or []

    forecast: list[dict[str, Any]] = []
    for idx, day in enumerate(times):
        high = _pick_hourly_value(max_temps, idx)
        low = _pick_hourly_value(min_temps, idx)
        code = _pick_hourly_value(codes, idx)
        precip = _pick_hourly_value(precipitation, idx)
        wind_kph = _pick_hourly_value(wind, idx)
        numeric_code = int(code) if isinstance(code, (int, float)) else None
        condition = condition_from_code(numeric_code)
        high_c = round(float(high), 1) if isinstance(high, (int, float)) else None
        forecast.append(
            {
                "date": str(day),
                "condition": condition,
                "weather_code": numeric_code,
                "temperature_max_c": high_c,
                "temperature_min_c": (
                    round(float(low), 1) if isinstance(low, (int, float)) else None
                ),
                "precipitation_mm": (
                    round(float(precip), 1)
                    if isinstance(precip, (int, float))
                    else None
                ),
                "wind_speed_kph": (
                    round(float(wind_kph), 1)
                    if isinstance(wind_kph, (int, float))
                    else None
                ),
                "load_flag": weather_load_flag(high_c, condition),
            }
        )
    return forecast


async def fetch_daily_forecast(
    latitude: float | None,
    longitude: float | None,
    days: int = 14,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Return the cached daily forecast for a location, newest day first-in-list.

    One upstream call per rounded location per hour serves every caller and every
    horizon: the full 16-day window is fetched and cached, then sliced to ``days``.
    Returns an empty list on any failure — weather is always additive context, so
    a forecast outage must never break plan generation or a dashboard load.
    """
    if latitude is None or longitude is None:
        return []
    wanted = max(1, min(days, FORECAST_MAX_DAYS))
    key = _cache_key(latitude, longitude)
    clock = now if now is not None else _time.monotonic()

    cached = _forecast_cache.get(key)
    if cached is not None and clock - cached[0] < FORECAST_CACHE_TTL_SECONDS:
        return cached[1][:wanted]

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                FORECAST_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "daily": WEATHER_DAILY,
                    "forecast_days": FORECAST_MAX_DAYS,
                    "timezone": "auto",
                },
            )
        if not resp.is_success:
            return []
        payload = resp.json()
    except Exception:
        logger.info("Upcoming weather lookup failed", exc_info=True)
        return []

    forecast = _parse_daily_payload(payload)
    if forecast:
        _forecast_cache[key] = (clock, forecast)
    return forecast[:wanted]


async def daily_forecast_for_user(
    db: AsyncSession,
    user_id: str,
    days: int = 14,
) -> tuple[TrainingLocation | None, list[dict[str, Any]]]:
    """Return ``(training location, daily forecast)`` for an athlete.

    The location comes from the persisted ``home_location`` attribute when set —
    athlete override first, inferred cluster second — and falls back to the latest
    ride with GPS (:func:`services.home_location.resolve_training_location`).
    """
    location = await resolve_training_location(db, user_id)
    if location is None:
        return None, []
    forecast = await fetch_daily_forecast(
        location.latitude, location.longitude, days
    )
    return location, forecast


def format_forecast_day(day: dict[str, Any]) -> str:
    """One compact prompt line for a forecast day, e.g. ``2026-08-02 | rain | 14-19C``."""
    parts = [str(day.get("date"))]
    condition = day.get("condition")
    if condition:
        parts.append(str(condition).replace("_", " "))
    low = day.get("temperature_min_c")
    high = day.get("temperature_max_c")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        parts.append(f"{round(low)}-{round(high)}C")
    precip = day.get("precipitation_mm")
    if isinstance(precip, (int, float)) and precip > 0:
        parts.append(f"precip {round(precip, 1)}mm")
    wind_kph = day.get("wind_speed_kph")
    if isinstance(wind_kph, (int, float)) and wind_kph >= 25:
        parts.append(f"wind {round(wind_kph)}kph")
    flag = day.get("load_flag")
    if flag:
        parts.append(f"training flag:{flag}")
    return " | ".join(parts)


async def training_weather_context_for_user(
    db: AsyncSession,
    user_id: str,
    days: int = 14,
) -> str:
    """Return compact upcoming weather context for plan generation/adaptation.

    The per-day forecast is a *scheduling constraint* the coach may act on, and it
    is presented alongside what the coach has actually learned about this
    athlete's weather tolerances (:mod:`services.weather_preference`) so a proven
    heat-tolerant rider is not nagged off every warm day (#495).
    """
    # Imported lazily: the preference engine reads hypotheses through crud, and a
    # module-level import would tie every weather lookup to the learning stack.
    from services import weather_preference

    location, forecast = await daily_forecast_for_user(db, user_id, days)
    if location is None or not forecast:
        return ""

    where = f" near {location.label}" if location.label else " near their usual training location"
    lines = [f"Upcoming weather{where}:"]
    lines.extend("- " + format_forecast_day(day) for day in forecast)

    # Best-effort: the forecast is the part callers depend on, so a failure reading
    # the learned beliefs must degrade to "no preferences known", never break the
    # plan prompt that asked for weather.
    try:
        preference_section = (
            await weather_preference.weather_preference_context_for_user(db, user_id)
        )
    except Exception:
        logger.info("Weather-preference context lookup failed", exc_info=True)
        preference_section = ""
    if preference_section:
        lines.append("")
        lines.append(preference_section)

    lines.append(
        "Use this weather only as a scheduling constraint, and adapt rather than rewrite: move or "
        "soften high-intensity work on extreme-heat days, offer an indoor trainer session or a "
        "cooler earlier window, extend warmups and avoid long exposed sessions in freezing/cold "
        "conditions, and swap to endurance, recovery, or strength work during severe rain, snow, "
        "thunderstorms, or high wind. Weight this against what is known about the athlete's own "
        "tolerances above — do not move a session for weather this athlete demonstrably handles "
        "well. Whenever weather is why a session changed, say so explicitly in your reasoning."
    )
    return "\n".join(lines)
