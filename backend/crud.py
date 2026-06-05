"""CRUD / data-access helpers.

All raw SQLAlchemy queries are isolated here so that routers stay thin and
tests can patch a single module instead of mocking low-level session methods.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models

# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

_USER_EAGER_OPTIONS = [
    selectinload(models.User.strava_token),
    selectinload(models.User.rider_assessment),
]


async def get_user_by_id(db: AsyncSession, user_id: str) -> models.User | None:
    """Return a User with all relationships eagerly loaded, or None."""
    return await db.scalar(
        select(models.User)
        .options(*_USER_EAGER_OPTIONS)
        .where(models.User.id == user_id)
    )


async def get_user_by_email(db: AsyncSession, email: str) -> models.User | None:
    """Return a User by email with all relationships eagerly loaded, or None."""
    return await db.scalar(
        select(models.User)
        .options(*_USER_EAGER_OPTIONS)
        .where(models.User.email == email)
    )


async def get_user_by_email_simple(db: AsyncSession, email: str) -> models.User | None:
    """Return a User by email without loading relationships, or None."""
    return await db.scalar(select(models.User).where(models.User.email == email))


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    name: str | None,
    hashed_password: str,
) -> models.User:
    """Create a new User, flush, and return the persisted instance."""
    user = models.User(email=email, name=name, hashed_password=hashed_password)
    db.add(user)
    await db.flush()
    return user


async def increment_user_consumed_tokens(
    db: AsyncSession, user: models.User, tokens: int
) -> None:
    """Add provider-reported LLM tokens to a user's running usage counter."""
    if tokens <= 0:
        return
    user.consumed_tokens = int(user.consumed_tokens or 0) + int(tokens)
    await db.flush()


# ---------------------------------------------------------------------------
# TrainingPlan
# ---------------------------------------------------------------------------


async def get_training_plan(
    db: AsyncSession, user_id: str
) -> models.TrainingPlan | None:
    """Return the TrainingPlan for a user, or None."""
    return await db.scalar(
        select(models.TrainingPlan).where(models.TrainingPlan.user_id == user_id)
    )


async def upsert_training_plan(
    db: AsyncSession, user_id: str, plan: Any
) -> models.TrainingPlan:
    """Create or replace the training plan for a user and flush."""
    existing = await get_training_plan(db, user_id)
    if existing is None:
        existing = models.TrainingPlan(user_id=user_id, plan=plan)
        db.add(existing)
    else:
        existing.plan = plan
    await db.flush()
    return existing


# ---------------------------------------------------------------------------
# WorkoutLog
# ---------------------------------------------------------------------------


async def get_workout_logs(db: AsyncSession, user_id: str) -> list[models.WorkoutLog]:
    """Return all WorkoutLog rows for a user."""
    result = await db.scalars(
        select(models.WorkoutLog).where(models.WorkoutLog.user_id == user_id)
    )
    return list(result)


async def get_workout_log_by_date(
    db: AsyncSession, user_id: str, date: str
) -> models.WorkoutLog | None:
    """Return the WorkoutLog for a specific user/date, or None."""
    return await db.scalar(
        select(models.WorkoutLog).where(
            models.WorkoutLog.user_id == user_id,
            models.WorkoutLog.date == date,
        )
    )


async def upsert_workout_log(
    db: AsyncSession,
    user_id: str,
    date: str,
    *,
    actual_duration_minutes: int,
    average_power: int | None,
    average_heart_rate: int | None,
    peak_power: int | None,
    perceived_effort: int,
    notes: str,
    completed_at: str,
    sport_type: str = "cycling",
) -> models.WorkoutLog:
    """Create or update a WorkoutLog for a user/date and flush."""
    existing = await get_workout_log_by_date(db, user_id, date)
    if existing is None:
        existing = models.WorkoutLog(
            user_id=user_id,
            date=date,
            actual_duration_minutes=actual_duration_minutes,
            average_power=average_power,
            average_heart_rate=average_heart_rate,
            peak_power=peak_power,
            perceived_effort=perceived_effort,
            notes=notes,
            completed_at=completed_at,
            sport_type=sport_type,
        )
        db.add(existing)
    else:
        existing.actual_duration_minutes = actual_duration_minutes
        existing.average_power = average_power
        existing.average_heart_rate = average_heart_rate
        existing.peak_power = peak_power
        existing.perceived_effort = perceived_effort
        existing.notes = notes
        existing.completed_at = completed_at
        existing.sport_type = sport_type
    await db.flush()
    return existing


# ---------------------------------------------------------------------------
# ChatMessage
# ---------------------------------------------------------------------------


async def get_chat_messages(db: AsyncSession, user_id: str) -> list[models.ChatMessage]:
    """Return all ChatMessages for a user ordered by timestamp."""
    result = await db.scalars(
        select(models.ChatMessage)
        .where(models.ChatMessage.user_id == user_id)
        .order_by(models.ChatMessage.timestamp, models.ChatMessage.id)
    )
    return list(result)


async def create_chat_message(
    db: AsyncSession,
    user_id: str,
    *,
    role: str,
    content: str,
    timestamp: str,
    plan_update_count: int | None = None,
) -> models.ChatMessage:
    """Create a ChatMessage, flush, and return the persisted instance."""
    message = models.ChatMessage(
        user_id=user_id,
        role=role,
        content=content,
        timestamp=timestamp,
        plan_update_count=plan_update_count,
    )
    db.add(message)
    await db.flush()
    return message


async def delete_chat_messages(db: AsyncSession, user_id: str) -> None:
    """Delete all ChatMessages for a user."""
    await db.execute(
        delete(models.ChatMessage).where(models.ChatMessage.user_id == user_id)
    )


# ---------------------------------------------------------------------------
# CoachMemory
# ---------------------------------------------------------------------------


async def get_coach_memory(db: AsyncSession, user_id: str) -> models.CoachMemory | None:
    """Return the CoachMemory for a user, or None."""
    return await db.get(models.CoachMemory, user_id)


async def upsert_coach_memory(
    db: AsyncSession, user_id: str, memory: str
) -> models.CoachMemory:
    """Create or update the CoachMemory for a user and flush."""
    existing = await get_coach_memory(db, user_id)
    if existing is None:
        existing = models.CoachMemory(user_id=user_id, memory=memory)
        db.add(existing)
    else:
        existing.memory = memory
    await db.flush()
    return existing


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
# RiderAssessment
# ---------------------------------------------------------------------------


async def get_rider_assessment(
    db: AsyncSession, user_id: str
) -> models.RiderAssessment | None:
    """Return the RiderAssessment for a user, or None."""
    return await db.get(models.RiderAssessment, user_id)


async def upsert_rider_assessment(
    db: AsyncSession,
    user_id: str,
    *,
    estimated_ftp: int | None,
    rider_type: str | None = None,
    notes: str | None = None,
    hr_zones: Any | None = None,
    ride_insights: str | None = None,
    last_ride_feedback: str | None = None,
    login_summary: str | None = None,
) -> models.RiderAssessment:
    """Create or update the RiderAssessment for a user and flush.

    For updates, ``rider_type`` and ``notes`` fall back to the existing values
    when *None* is passed.  ``last_ride_feedback`` is only written when the
    caller provides a non-empty value.
    """
    assessment = await get_rider_assessment(db, user_id)
    if assessment is None:
        assessment = models.RiderAssessment(
            user_id=user_id,
            estimated_ftp=estimated_ftp,
            rider_type=rider_type if rider_type is not None else "allrounder",
            notes=notes if notes is not None else "",
            hr_zones=hr_zones,
            ride_insights=ride_insights,
            last_ride_feedback=last_ride_feedback,
            login_summary=login_summary,
        )
        db.add(assessment)
    else:
        assessment.estimated_ftp = estimated_ftp
        if rider_type is not None:
            assessment.rider_type = rider_type
        if notes is not None:
            assessment.notes = notes
        assessment.hr_zones = hr_zones
        assessment.ride_insights = ride_insights
        if last_ride_feedback:
            assessment.last_ride_feedback = last_ride_feedback
        if login_summary:
            assessment.login_summary = login_summary
    await db.flush()
    return assessment


# ---------------------------------------------------------------------------
# AthleteMetricSnapshot
# ---------------------------------------------------------------------------


async def create_athlete_metric_snapshot(
    db: AsyncSession,
    user_id: str,
    *,
    ftp: int | None,
    threshold_hr: int | None = None,
    ctl: float | None = None,
    atl: float | None = None,
    tsb: float | None = None,
    source: str = "strava_analysis",
    recorded_at: datetime | None = None,
) -> models.AthleteMetricSnapshot:
    """Insert a new AthleteMetricSnapshot row and flush.

    ``recorded_at`` defaults to the current UTC time when not provided.  Pass
    an explicit value to back-date a snapshot to the originating ride date.
    """
    kwargs: dict = dict(
        user_id=user_id,
        ftp=ftp,
        ctl=ctl,
        atl=atl,
        tsb=tsb,
        source=source,
    )
    if recorded_at is not None:
        kwargs["recorded_at"] = recorded_at
    snapshot = models.AthleteMetricSnapshot(**kwargs)
    db.add(snapshot)
    await db.flush()
    return snapshot


async def get_athlete_metric_history(
    db: AsyncSession,
    user_id: str,
    limit: int = 90,
) -> list[models.AthleteMetricSnapshot]:
    """Return the most recent *limit* AthleteMetricSnapshot rows for a user, oldest first."""
    recent_ids = (
        select(models.AthleteMetricSnapshot.id)
        .where(models.AthleteMetricSnapshot.user_id == user_id)
        .order_by(models.AthleteMetricSnapshot.recorded_at.desc())
        .limit(limit)
        .scalar_subquery()
    )
    result = await db.scalars(
        select(models.AthleteMetricSnapshot)
        .where(models.AthleteMetricSnapshot.id.in_(recent_ids))
        .order_by(models.AthleteMetricSnapshot.recorded_at.asc())
    )
    return list(result)


async def delete_athlete_metric_snapshots(db: AsyncSession, user_id: str) -> None:
    """Delete all AthleteMetricSnapshot rows for a user and flush.

    Used when rebuilding the full metric history after an FTP recalculation so
    stale snapshots do not pollute the progression chart.
    """
    await db.execute(
        delete(models.AthleteMetricSnapshot).where(
            models.AthleteMetricSnapshot.user_id == user_id
        )
    )
    await db.flush()


# ---------------------------------------------------------------------------
# Ride metrics
# ---------------------------------------------------------------------------


async def get_all_ride_metrics_ordered(
    db: AsyncSession,
    user_id: str,
) -> list[models.RideMetric]:
    """Return all RideMetric rows for a user sorted by activity_date ascending.

    Used when rebuilding the full CTL/ATL/TSB chain after an FTP change.
    """
    result = await db.scalars(
        select(models.RideMetric)
        .where(models.RideMetric.user_id == user_id)
        .order_by(models.RideMetric.activity_date.asc())
    )
    return list(result)


async def delete_all_ride_metrics(db: AsyncSession, user_id: str) -> None:
    """Delete all RideMetric rows for a user and flush."""
    await db.execute(
        delete(models.RideMetric).where(models.RideMetric.user_id == user_id)
    )
    await db.flush()


async def upsert_ride_metric(
    db: AsyncSession,
    user_id: str,
    *,
    strava_activity_id: int,
    activity_date: str,
    sport_type: str = "cycling",
    activity_name: str | None = None,
    activity_start_datetime: str | None = None,
    duration_seconds: int | None = None,
    start_lat: float | None = None,
    start_lng: float | None = None,
    weather_temperature_c: float | None = None,
    weather_apparent_temperature_c: float | None = None,
    weather_condition: str | None = None,
    weather_code: int | None = None,
    weather_wind_speed_kph: float | None = None,
    weather_precipitation_mm: float | None = None,
    weather_source: str | None = None,
    avg_power_w: int | None = None,
    normalized_power_w: int | None = None,
    intensity_factor: float | None = None,
    tss: float | None = None,
    ftp_used: int | None = None,
    ctl_after: float | None = None,
    atl_after: float | None = None,
    tsb_after: float | None = None,
    ride_purpose: str | None = None,
    classification_confidence: str | None = None,
    classification_reason: str | None = None,
    summary: str | None = None,
) -> models.RideMetric:
    """Insert or update a RideMetric row identified by (user_id, strava_activity_id)."""
    values = dict(
        user_id=user_id,
        strava_activity_id=strava_activity_id,
        activity_date=activity_date,
        sport_type=sport_type,
        activity_name=activity_name,
        activity_start_datetime=activity_start_datetime,
        duration_seconds=duration_seconds,
        start_lat=start_lat,
        start_lng=start_lng,
        weather_temperature_c=weather_temperature_c,
        weather_apparent_temperature_c=weather_apparent_temperature_c,
        weather_condition=weather_condition,
        weather_code=weather_code,
        weather_wind_speed_kph=weather_wind_speed_kph,
        weather_precipitation_mm=weather_precipitation_mm,
        weather_source=weather_source,
        avg_power_w=avg_power_w,
        normalized_power_w=normalized_power_w,
        intensity_factor=intensity_factor,
        tss=tss,
        ftp_used=ftp_used,
        ctl_after=ctl_after,
        atl_after=atl_after,
        tsb_after=tsb_after,
        ride_purpose=ride_purpose,
        classification_confidence=classification_confidence,
        classification_reason=classification_reason,
        summary=summary,
    )

    conn = await db.connection()
    if conn.dialect.name == "postgresql":
        stmt = (
            pg_insert(models.RideMetric)
            .values(**values, id=str(__import__("uuid").uuid4()))
            .on_conflict_do_update(
                index_elements=["user_id", "strava_activity_id"],
                set_={
                    k: v
                    for k, v in values.items()
                    if k not in ("user_id", "strava_activity_id")
                },
            )
            .returning(models.RideMetric)
        )
        result = await db.execute(stmt)
        return result.scalar_one()

    # SQLite (tests) — SELECT + update/insert; flush immediately to avoid autoflush issues
    existing = await db.scalar(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.strava_activity_id == strava_activity_id,
        )
    )
    if existing is not None:
        for attr, val in values.items():
            setattr(existing, attr, val)
        await db.flush()
        return existing

    row = models.RideMetric(id=str(__import__("uuid").uuid4()), **values)
    db.add(row)
    await db.flush()
    return row


async def get_ride_metrics_history(
    db: AsyncSession,
    user_id: str,
    limit: int = 60,
) -> list[models.RideMetric]:
    """Return the most recent *limit* RideMetric rows for a user, newest first."""
    result = await db.scalars(
        select(models.RideMetric)
        .where(models.RideMetric.user_id == user_id)
        .order_by(models.RideMetric.activity_date.desc())
        .limit(limit)
    )
    return list(result)


async def get_ride_metrics_missing_weather(
    db: AsyncSession,
    user_id: str,
    limit: int = 90,
) -> list[models.RideMetric]:
    """Return recent RideMetric rows that do not yet have weather attached."""
    result = await db.scalars(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.weather_temperature_c.is_(None),
        )
        .order_by(models.RideMetric.activity_date.desc())
        .limit(limit)
    )
    return list(result)


async def get_latest_ride_metric_with_location(
    db: AsyncSession,
    user_id: str,
) -> models.RideMetric | None:
    """Return the latest ride metric with usable start coordinates."""
    return await db.scalar(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.start_lat.is_not(None),
            models.RideMetric.start_lng.is_not(None),
        )
        .order_by(models.RideMetric.activity_date.desc())
        .limit(1)
    )


async def get_latest_ride_metric(
    db: AsyncSession,
    user_id: str,
) -> models.RideMetric | None:
    """Return the single most recent RideMetric for a user.

    Used to seed CTL/ATL for incremental chain continuation.
    """
    return await db.scalar(
        select(models.RideMetric)
        .where(models.RideMetric.user_id == user_id)
        .order_by(models.RideMetric.activity_date.desc())
        .limit(1)
    )


async def update_ride_metric_notes(
    db: AsyncSession,
    user_id: str,
    strava_activity_id: int,
    *,
    coach_note: str | None = None,
    user_note: str | None = None,
) -> models.RideMetric | None:
    """Partially update coach_note and/or user_note on a RideMetric row.

    Only overwrites fields whose values are explicitly provided (not None).
    Returns the updated row, or None if not found.
    """
    row = await db.scalar(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.strava_activity_id == strava_activity_id,
        )
    )
    if row is None:
        return None
    if coach_note is not None:
        row.coach_note = coach_note
    if user_note is not None:
        row.user_note = user_note
    return row


async def get_ride_metric_by_strava_id(
    db: AsyncSession,
    user_id: str,
    strava_activity_id: int,
) -> models.RideMetric | None:
    """Return the RideMetric for a given Strava activity ID, or None if not found."""
    return await db.scalar(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.strava_activity_id == strava_activity_id,
        )
    )


async def get_ride_metric_by_date(
    db: AsyncSession,
    user_id: str,
    activity_date: str,
) -> models.RideMetric | None:
    """Return the RideMetric whose activity_date matches the given ISO date string.

    Used to resolve chat-inferred user feedback to a specific ride.
    """
    return await db.scalar(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_date == activity_date,
        )
    )


async def get_ride_metrics_by_date(
    db: AsyncSession,
    user_id: str,
    activity_date: str,
) -> list[models.RideMetric]:
    """Return all RideMetrics for a user on a given ISO date string.

    Used by ride-matching logic to handle multiple rides on the same day.
    """
    result = await db.scalars(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_date == activity_date,
        )
    )
    return list(result)


# ---------------------------------------------------------------------------
# RaceEvent
# ---------------------------------------------------------------------------


async def get_race_events(db: AsyncSession, user_id: str) -> list[models.RaceEvent]:
    result = await db.execute(
        select(models.RaceEvent)
        .where(models.RaceEvent.user_id == user_id)
        .order_by(models.RaceEvent.date)
    )
    return list(result.scalars().all())


async def get_race_event(
    db: AsyncSession, user_id: str, event_id: str
) -> models.RaceEvent | None:
    return await db.scalar(
        select(models.RaceEvent).where(
            models.RaceEvent.user_id == user_id,
            models.RaceEvent.id == event_id,
        )
    )


async def create_race_event(
    db: AsyncSession,
    user_id: str,
    *,
    date: str,
    start_time: str | None,
    distance_km: float,
    elevation_m: int,
) -> models.RaceEvent:
    event = models.RaceEvent(
        user_id=user_id,
        date=date,
        start_time=start_time,
        distance_km=distance_km,
        elevation_m=elevation_m,
    )
    db.add(event)
    await db.flush()
    return event


async def update_race_event(
    db: AsyncSession,
    event: models.RaceEvent,
    *,
    date: str,
    start_time: str | None,
    distance_km: float,
    elevation_m: int,
) -> models.RaceEvent:
    event.date = date
    event.start_time = start_time
    event.distance_km = distance_km
    event.elevation_m = elevation_m
    await db.flush()
    return event


async def delete_race_event(db: AsyncSession, event: models.RaceEvent) -> None:
    await db.delete(event)
    await db.flush()


# ---------------------------------------------------------------------------
# Ride review helpers
# ---------------------------------------------------------------------------


async def get_unreviewed_ride_metrics(
    db: AsyncSession, user_id: str
) -> list[models.RideMetric]:
    """Return all RideMetric rows for a user where coach_reviewed_at is NULL, ordered oldest-first."""
    result = await db.execute(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.coach_reviewed_at.is_(None),
        )
        .order_by(models.RideMetric.activity_date)
    )
    return list(result.scalars().all())


async def mark_rides_as_reviewed(
    db: AsyncSession, user_id: str, strava_activity_ids: list[int]
) -> int:
    """Set coach_reviewed_at to now for the given activity IDs. Returns count updated."""
    if not strava_activity_ids:
        return 0
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.strava_activity_id.in_(strava_activity_ids),
        )
        .values(coach_reviewed_at=now)
    )
    await db.flush()
    return result.rowcount


async def get_ride_metrics_by_activity_ids(
    db: AsyncSession, user_id: str, strava_activity_ids: list[int]
) -> list[models.RideMetric]:
    """Return RideMetric rows for the given Strava activity IDs, ordered oldest-first."""
    result = await db.execute(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.strava_activity_id.in_(strava_activity_ids),
        )
        .order_by(models.RideMetric.activity_date)
    )
    return list(result.scalars().all())


async def update_ride_match(
    db: AsyncSession,
    ride: models.RideMetric,
    *,
    status: str,
    matched_plan_date: str | None = None,
    matched_plan_snapshot: dict | None = None,
    matched_at: datetime | None = None,
    label_override: str | None = None,
) -> models.RideMetric:
    """Update the plan-match fields on a RideMetric row and flush."""
    ride.plan_match_status = status
    ride.matched_plan_date = matched_plan_date
    ride.matched_plan_snapshot = matched_plan_snapshot
    ride.matched_at = matched_at
    ride.label_override = label_override
    await db.flush()
    return ride
