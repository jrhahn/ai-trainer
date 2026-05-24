"""Weather enrichment for Strava activities and training-plan context."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services.strava_service import fetch_activity_detail

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_HOURLY = (
    "temperature_2m,apparent_temperature,weather_code,precipitation,wind_speed_10m"
)
WEATHER_DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"


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


async def enrich_activity_weather(activity: dict[str, Any]) -> dict[str, Any]:
    """Return weather/coordinate fields for a raw Strava activity dict."""
    lat, lng = coordinates_from_activity(activity)
    activity_date_source = (
        activity.get("start_date_local")
        or activity.get("startDateLocal")
        or activity.get("start_date")
        or activity.get("startDate")
        or ""
    )
    activity_date = str(activity_date_source)[:10] if activity_date_source else ""
    start_datetime = (
        activity.get("start_date_local")
        or activity.get("startDateLocal")
        or activity.get("start_date")
        or activity.get("startDate")
    )
    weather = await fetch_activity_weather(
        lat, lng, activity_date, str(start_datetime) if start_datetime else None
    )
    return {
        "start_lat": lat,
        "start_lng": lng,
        **(weather or {}),
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
    Strava activity detail first to recover ``start_latlng``.
    """
    rows = await crud.get_ride_metrics_missing_weather(db, user_id, limit=limit)
    updated = 0
    for row in rows:
        lat = row.start_lat
        lng = row.start_lng

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

        weather = await fetch_activity_weather(
            lat,
            lng,
            row.activity_date,
            row.activity_start_datetime,
        )
        if weather:
            fields = {"start_lat": lat, "start_lng": lng, **weather}
            _apply_weather_fields(row, fields)
            updated += 1

    if updated:
        await db.flush()
    return updated


async def training_weather_context_for_user(
    db: AsyncSession,
    user_id: str,
    days: int = 14,
) -> str:
    """Return compact upcoming weather context for plan generation/adaptation."""
    location = await crud.get_latest_ride_metric_with_location(db, user_id)
    if location is None or location.start_lat is None or location.start_lng is None:
        return ""

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                FORECAST_URL,
                params={
                    "latitude": location.start_lat,
                    "longitude": location.start_lng,
                    "daily": WEATHER_DAILY,
                    "forecast_days": max(1, min(days, 16)),
                    "timezone": "auto",
                },
            )
        if not resp.is_success:
            return ""
        payload = resp.json()
    except Exception:
        logger.info("Upcoming weather lookup failed", exc_info=True)
        return ""

    daily = payload.get("daily") if isinstance(payload, dict) else None
    if not isinstance(daily, dict):
        return ""

    times = daily.get("time") or []
    max_temps = daily.get("temperature_2m_max") or []
    min_temps = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    precipitation = daily.get("precipitation_sum") or []
    wind = daily.get("wind_speed_10m_max") or []

    lines = ["Upcoming weather near the athlete's usual activity location:"]
    for idx, day in enumerate(times[:days]):
        high = _pick_hourly_value(max_temps, idx)
        low = _pick_hourly_value(min_temps, idx)
        code = _pick_hourly_value(codes, idx)
        condition = condition_from_code(
            int(code) if isinstance(code, (int, float)) else None
        )
        flag = weather_load_flag(
            float(high) if isinstance(high, (int, float)) else None, condition
        )
        parts = [str(day)]
        if condition:
            parts.append(condition.replace("_", " "))
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            parts.append(f"{round(low)}-{round(high)}C")
        precip = _pick_hourly_value(precipitation, idx)
        if isinstance(precip, (int, float)) and precip > 0:
            parts.append(f"precip {round(precip, 1)}mm")
        wind_kph = _pick_hourly_value(wind, idx)
        if isinstance(wind_kph, (int, float)) and wind_kph >= 25:
            parts.append(f"wind {round(wind_kph)}kph")
        if flag:
            parts.append(f"training flag:{flag}")
        lines.append("- " + " | ".join(parts))

    lines.append(
        "Use this weather only as a scheduling constraint: shorten or reduce intensity in heat, "
        "extend warmups and avoid long exposed sessions in freezing/cold conditions, and consider "
        "indoor, recovery, or strength sessions during severe rain, snow, thunderstorms, or high wind."
    )
    return "\n".join(lines)
