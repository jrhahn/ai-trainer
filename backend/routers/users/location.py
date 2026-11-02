"""The home location, and the weather forecast there."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import get_db
from services.home_location import geocode_place
from services.weather_service import clear_forecast_cache, daily_forecast_for_user

router = APIRouter(prefix="/users/me", tags=["users"])


def _home_location_schema(
    row: models.AthleteHomeLocation | None,
) -> schemas.AthleteHomeLocationSchema | None:
    if row is None:
        return None
    return schemas.AthleteHomeLocationSchema.model_validate(row, from_attributes=True)


@router.get("/home-location", response_model=schemas.AthleteHomeLocationResponse)
async def get_home_location(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteHomeLocationResponse:
    """Return the athlete's persisted training location, if one is known (#495)."""
    row = await crud.get_athlete_home_location(db, current_user.id)
    return schemas.AthleteHomeLocationResponse(location=_home_location_schema(row))


@router.put("/home-location", response_model=schemas.AthleteHomeLocationResponse)
async def save_home_location(
    body: schemas.AthleteHomeLocationUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.AthleteHomeLocationResponse:
    """Set the athlete's training location explicitly.

    Stored as ``user_set``, which pins it against every later inference pass — the
    athlete's own answer outranks a cluster of ride starts. Also drops the cached
    forecast so the next dashboard load reflects the new location immediately.
    A place name without coordinates is looked up (ai-trainer-ops#338 A11).
    """
    latitude, longitude, label = body.latitude, body.longitude, body.label
    if latitude is None or longitude is None:
        found = await geocode_place(label)
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="That place could not be found. Try the nearest town.",
            )
        latitude, longitude, label = found
    row = await crud.upsert_athlete_home_location(
        db,
        current_user.id,
        latitude=latitude,
        longitude=longitude,
        label=label,
        source="user_set",
        confidence=1.0,
    )
    await db.commit()
    clear_forecast_cache()
    return schemas.AthleteHomeLocationResponse(location=_home_location_schema(row))


@router.get("/weather-forecast", response_model=schemas.WeatherForecastResponse)
async def get_weather_forecast(
    days: int = 14,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.WeatherForecastResponse:
    """Return the upcoming daily outlook for the athlete's training location (#495).

    Powers the weather icon + temperature shown on planned days in the dashboard.
    Served from the hourly per-location forecast cache, so repeated dashboard loads
    cost no upstream calls, and degrades to an empty list when no location is known
    or Open-Meteo is unreachable.
    """
    location, forecast = await daily_forecast_for_user(
        db, current_user.id, max(1, min(days, 16))
    )
    stored = await crud.get_athlete_home_location(db, current_user.id)
    location_schema = _home_location_schema(stored)
    if location_schema is None and location is not None:
        # Falling back to the latest ride with GPS: report it honestly rather than
        # implying a persisted attribute exists.
        location_schema = schemas.AthleteHomeLocationSchema(
            latitude=location.latitude,
            longitude=location.longitude,
            label=location.label,
            source=location.source,
            confidence=location.confidence,
        )
    return schemas.WeatherForecastResponse(
        location=location_schema,
        days=[schemas.DailyForecastSchema(**day) for day in forecast],
    )
