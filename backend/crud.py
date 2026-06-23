"""CRUD / data-access helpers.

All raw SQLAlchemy queries are isolated here so that routers stay thin and
tests can patch a single module instead of mocking low-level session methods.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import String, cast, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models
from services.activity_identity import are_near_duplicate_activities

ATHLETE_MEMORY_DEFAULT_CONFIDENCE = 0.35
ATHLETE_MEMORY_CONFIDENCE_STEP = 0.2
ATHLETE_MEMORY_PROMPT_MIN_CONFIDENCE = 0.5
ATHLETE_MEMORY_STALE_AFTER_DAYS = 90
ATHLETE_MEMORY_PROMPT_LIMIT = 12

_ATHLETE_MEMORY_ACTIVE_STATUSES = ("active", "user_confirmed")


def _ride_metrics_are_near_duplicates(
    ride: models.RideMetric,
    other: models.RideMetric,
) -> bool:
    return are_near_duplicate_activities(
        activity_date=ride.activity_date,
        sport_type=ride.sport_type,
        activity_name=ride.activity_name,
        activity_start_datetime=ride.activity_start_datetime,
        duration_seconds=ride.duration_seconds,
        other_activity_date=other.activity_date,
        other_sport_type=other.sport_type,
        other_activity_name=other.activity_name,
        other_activity_start_datetime=other.activity_start_datetime,
        other_duration_seconds=other.duration_seconds,
    )


def _preferred_visible_ride_metric(
    ride: models.RideMetric,
    other: models.RideMetric,
) -> models.RideMetric:
    ride_duration = ride.duration_seconds or 0
    other_duration = other.duration_seconds or 0
    if abs(ride_duration - other_duration) > 15 * 60:
        return ride if ride_duration > other_duration else other
    return ride


def _should_preserve_existing_near_duplicate(
    existing: models.RideMetric,
    incoming_duration_seconds: int | float | None,
) -> bool:
    existing_duration = existing.duration_seconds or 0
    incoming_duration = incoming_duration_seconds or 0
    return existing_duration - incoming_duration > 15 * 60


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

_USER_EAGER_OPTIONS = [
    selectinload(models.User.strava_token),
    selectinload(models.User.intervals_token),
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


async def get_users_with_training_plans(db: AsyncSession) -> list[models.User]:
    """Return onboarded users that have a persisted training plan."""
    result = await db.scalars(
        select(models.User)
        .join(models.TrainingPlan)
        .options(
            *_USER_EAGER_OPTIONS,
            selectinload(models.User.training_plan),
        )
        .where(models.User.is_onboarded.is_(True))
        .order_by(models.User.id)
    )
    return list(result)


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
    """Return all ChatMessages for a user in persisted conversation order."""
    result = await db.scalars(
        select(models.ChatMessage)
        .where(models.ChatMessage.user_id == user_id)
        .order_by(
            models.ChatMessage.timestamp,
            models.ChatMessage.created_at,
            models.ChatMessage.id,
        )
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
# AthleteContext
# ---------------------------------------------------------------------------


async def get_athlete_context(
    db: AsyncSession, user_id: str
) -> models.AthleteContext | None:
    """Return the structured AthleteContext for a user, or None."""
    return await db.get(models.AthleteContext, user_id)


async def upsert_athlete_context(
    db: AsyncSession,
    user_id: str,
    *,
    training_tendency: str = "unknown",
    rest_response: str = "unknown",
    motivation_drivers: list[str] | None = None,
    adherence_pattern: str = "unknown",
    strengths: list[str] | None = None,
    weaknesses: list[str] | None = None,
    preferred_terrain: list[str] | None = None,
    preferred_session_types: list[str] | None = None,
    coaching_risks: list[str] | None = None,
    notes: str = "",
) -> models.AthleteContext:
    """Create or update a user's structured athlete context and flush."""
    values = {
        "training_tendency": training_tendency,
        "rest_response": rest_response,
        "motivation_drivers": list(motivation_drivers or []),
        "adherence_pattern": adherence_pattern,
        "strengths": list(strengths or []),
        "weaknesses": list(weaknesses or []),
        "preferred_terrain": list(preferred_terrain or []),
        "preferred_session_types": list(preferred_session_types or []),
        "coaching_risks": list(coaching_risks or []),
        "notes": notes,
        "updated_at": datetime.now(timezone.utc),
    }
    existing = await get_athlete_context(db, user_id)
    if existing is None:
        existing = models.AthleteContext(user_id=user_id, **values)
        db.add(existing)
    else:
        for attr, value in values.items():
            setattr(existing, attr, value)
    await db.flush()
    return existing


# ---------------------------------------------------------------------------
# AthleteMemoryFact
# ---------------------------------------------------------------------------


def _normalise_athlete_memory_fact_key(fact: str) -> str:
    return " ".join(fact.casefold().split())[:255]


def _normalise_athlete_memory_category(category: str | None) -> str:
    value = (category or "general").strip().lower().replace(" ", "_")
    return value[:50] or "general"


def _clamp_confidence(value: float | None) -> float:
    if value is None:
        return ATHLETE_MEMORY_DEFAULT_CONFIDENCE
    return max(0.0, min(1.0, float(value)))


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def get_athlete_memory_fact(
    db: AsyncSession, user_id: str, fact_id: str
) -> models.AthleteMemoryFact | None:
    """Return one athlete memory fact owned by a user."""
    return await db.scalar(
        select(models.AthleteMemoryFact).where(
            models.AthleteMemoryFact.id == fact_id,
            models.AthleteMemoryFact.user_id == user_id,
        )
    )


async def list_athlete_memory_facts(
    db: AsyncSession, user_id: str, *, include_inactive: bool = False
) -> list[models.AthleteMemoryFact]:
    """Return stored athlete memory facts for management views."""
    stmt = select(models.AthleteMemoryFact).where(
        models.AthleteMemoryFact.user_id == user_id
    )
    if not include_inactive:
        stmt = stmt.where(
            models.AthleteMemoryFact.status.in_(_ATHLETE_MEMORY_ACTIVE_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthleteMemoryFact.status.desc(),
            models.AthleteMemoryFact.confidence.desc(),
            models.AthleteMemoryFact.last_confirmed_at.desc(),
            models.AthleteMemoryFact.id.desc(),
        )
    )
    return list(result)


async def observe_athlete_memory_fact(
    db: AsyncSession,
    user_id: str,
    *,
    fact: str,
    category: str = "general",
    source_snippet: str = "",
    source_exchange_id: str | None = None,
    confidence: float | None = None,
    observed_at: datetime | None = None,
) -> models.AthleteMemoryFact:
    """Create a fact or strengthen an existing fact from a new observation."""
    cleaned_fact = fact.strip()
    if not cleaned_fact:
        raise ValueError("fact must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    fact_key = _normalise_athlete_memory_fact_key(cleaned_fact)

    existing = await db.scalar(
        select(models.AthleteMemoryFact).where(
            models.AthleteMemoryFact.user_id == user_id,
            models.AthleteMemoryFact.category == normalized_category,
            models.AthleteMemoryFact.fact_key == fact_key,
        )
    )
    if existing is None:
        existing = models.AthleteMemoryFact(
            user_id=user_id,
            fact=cleaned_fact,
            fact_key=fact_key,
            category=normalized_category,
            source_snippet=source_snippet.strip(),
            source_exchange_id=source_exchange_id,
            first_observed_at=now,
            last_confirmed_at=now,
            confidence=_clamp_confidence(confidence),
            status="active",
            observation_count=1,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.observation_count += 1
        existing.last_confirmed_at = now
        existing.updated_at = now
        if source_snippet:
            existing.source_snippet = source_snippet.strip()
        if source_exchange_id is not None:
            existing.source_exchange_id = source_exchange_id
        if existing.status == "stale":
            existing.status = "active"
        base_confidence = max(existing.confidence, _clamp_confidence(confidence))
        existing.confidence = min(
            1.0, base_confidence + ATHLETE_MEMORY_CONFIDENCE_STEP
        )
    await db.flush()
    return existing


async def update_athlete_memory_fact(
    db: AsyncSession,
    user_id: str,
    fact_id: str,
    *,
    fact: str | None = None,
    category: str | None = None,
    source_snippet: str | None = None,
    source_exchange_id: str | None = None,
    confidence: float | None = None,
    status: str | None = None,
) -> models.AthleteMemoryFact | None:
    """Apply a user correction to an athlete memory fact and flush."""
    existing = await get_athlete_memory_fact(db, user_id, fact_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    if fact is not None:
        cleaned_fact = fact.strip()
        if not cleaned_fact:
            raise ValueError("fact must not be empty")
        existing.fact = cleaned_fact
        existing.fact_key = _normalise_athlete_memory_fact_key(cleaned_fact)
    if category is not None:
        existing.category = _normalise_athlete_memory_category(category)
    if source_snippet is not None:
        existing.source_snippet = source_snippet.strip()
    if source_exchange_id is not None:
        existing.source_exchange_id = source_exchange_id
    if confidence is not None:
        existing.confidence = _clamp_confidence(confidence)
    if status is not None:
        existing.status = status
        if status == "user_confirmed":
            existing.confidence = max(existing.confidence, 0.9)
            existing.last_confirmed_at = now
    existing.updated_at = now
    await db.flush()
    return existing


async def delete_athlete_memory_fact(
    db: AsyncSession, user_id: str, fact_id: str
) -> bool:
    """Permanently remove an athlete memory fact owned by a user.

    Returns ``True`` when a row was deleted, ``False`` when no matching fact
    exists for the user.
    """
    existing = await get_athlete_memory_fact(db, user_id, fact_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


async def get_prompt_athlete_memory_facts(
    db: AsyncSession,
    user_id: str,
    *,
    now: datetime | None = None,
    min_confidence: float = ATHLETE_MEMORY_PROMPT_MIN_CONFIDENCE,
    stale_after_days: int = ATHLETE_MEMORY_STALE_AFTER_DAYS,
    limit: int = ATHLETE_MEMORY_PROMPT_LIMIT,
) -> list[models.AthleteMemoryFact]:
    """Return only memory facts safe enough to include in coach prompts."""
    facts = await list_athlete_memory_facts(db, user_id, include_inactive=False)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=stale_after_days)
    prompt_facts: list[models.AthleteMemoryFact] = []
    for fact in facts:
        if fact.status == "user_confirmed":
            prompt_facts.append(fact)
        elif fact.confidence >= min_confidence and _as_aware_utc(
            fact.last_confirmed_at
        ) >= cutoff:
            prompt_facts.append(fact)
        if len(prompt_facts) >= limit:
            break
    return prompt_facts


async def clear_athlete_memory(db: AsyncSession, user_id: str) -> None:
    """Delete all memory facts and clear coach memory text for a user."""
    await db.execute(
        delete(models.AthleteMemoryFact).where(
            models.AthleteMemoryFact.user_id == user_id
        )
    )
    coach_memory_row = await get_coach_memory(db, user_id)
    if coach_memory_row is not None:
        coach_memory_row.memory = ""
        coach_memory_row.updated_at = datetime.now(timezone.utc)
    await db.flush()


# ---------------------------------------------------------------------------
# AthleteAvailabilityConstraint
# ---------------------------------------------------------------------------


async def upsert_availability_constraint(
    db: AsyncSession,
    user_id: str,
    *,
    constraint_type: str = "no_training",
    constraint_date: str | None = None,
    weekday: str | None = None,
    reason: str = "",
    source: str = "",
    expires_on: str | None = None,
) -> models.AthleteAvailabilityConstraint:
    """Create or refresh an availability constraint and flush."""
    existing = await db.scalar(
        select(models.AthleteAvailabilityConstraint).where(
            models.AthleteAvailabilityConstraint.user_id == user_id,
            models.AthleteAvailabilityConstraint.constraint_type == constraint_type,
            models.AthleteAvailabilityConstraint.constraint_date == constraint_date,
            models.AthleteAvailabilityConstraint.weekday == weekday,
            models.AthleteAvailabilityConstraint.active.is_(True),
        )
    )
    now = datetime.now(timezone.utc)
    if existing is None:
        existing = models.AthleteAvailabilityConstraint(
            user_id=user_id,
            constraint_type=constraint_type,
            constraint_date=constraint_date,
            weekday=weekday,
            reason=reason.strip(),
            source=source.strip(),
            expires_on=expires_on,
            active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.reason = reason.strip() or existing.reason
        existing.source = source.strip() or existing.source
        existing.expires_on = expires_on or existing.expires_on
        existing.updated_at = now
    await db.flush()
    return existing


async def list_active_availability_constraints(
    db: AsyncSession,
    user_id: str,
    *,
    today: str,
) -> list[models.AthleteAvailabilityConstraint]:
    """Return active constraints that have not expired in the athlete timezone."""
    result = await db.scalars(
        select(models.AthleteAvailabilityConstraint)
        .where(
            models.AthleteAvailabilityConstraint.user_id == user_id,
            models.AthleteAvailabilityConstraint.active.is_(True),
            or_(
                models.AthleteAvailabilityConstraint.expires_on.is_(None),
                models.AthleteAvailabilityConstraint.expires_on >= today,
            ),
        )
        .order_by(
            models.AthleteAvailabilityConstraint.constraint_date.asc(),
            models.AthleteAvailabilityConstraint.created_at.asc(),
        )
    )
    return list(result)


async def deactivate_expired_availability_constraints(
    db: AsyncSession,
    user_id: str,
    *,
    today: str,
) -> int:
    """Mark expired one-off constraints inactive."""
    result = await db.execute(
        update(models.AthleteAvailabilityConstraint)
        .where(
            models.AthleteAvailabilityConstraint.user_id == user_id,
            models.AthleteAvailabilityConstraint.active.is_(True),
            models.AthleteAvailabilityConstraint.expires_on.is_not(None),
            models.AthleteAvailabilityConstraint.expires_on < today,
        )
        .values(active=False, updated_at=datetime.now(timezone.utc))
    )
    await db.flush()
    return int(result.rowcount or 0)


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
    activity_source: str = "strava",
    external_activity_id: str | None = None,
    source_metadata: dict | None = None,
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
    """Insert or update a RideMetric row by stable activity identity."""
    normalized_source = activity_source or "strava"
    normalized_external_id = (
        str(external_activity_id)
        if external_activity_id is not None
        else str(strava_activity_id)
    )
    use_source_key = normalized_source != "strava" or external_activity_id is not None
    values = dict(
        user_id=user_id,
        strava_activity_id=strava_activity_id,
        activity_source=normalized_source,
        external_activity_id=normalized_external_id,
        source_metadata=source_metadata,
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

    exact_existing = (
        await get_ride_metric_by_source(
            db,
            user_id,
            normalized_source,
            normalized_external_id,
        )
        if use_source_key
        else await get_ride_metric_by_strava_id(db, user_id, strava_activity_id)
    )
    if exact_existing is not None:
        for attr, val in values.items():
            setattr(exact_existing, attr, val)
        await db.flush()
        return exact_existing

    near_duplicate = await get_near_duplicate_ride_metric(
        db,
        user_id,
        activity_date=activity_date,
        sport_type=sport_type,
        activity_name=activity_name,
        activity_start_datetime=activity_start_datetime,
        duration_seconds=duration_seconds,
        exclude_strava_activity_id=strava_activity_id,
        exclude_activity_source=normalized_source,
        exclude_external_activity_id=normalized_external_id,
    )
    if near_duplicate is not None:
        if _should_preserve_existing_near_duplicate(
            near_duplicate,
            duration_seconds,
        ):
            return near_duplicate
        for attr, val in values.items():
            setattr(near_duplicate, attr, val)
        await db.flush()
        return near_duplicate

    conn = await db.connection()
    if conn.dialect.name == "postgresql":
        conflict_keys = (
            ["user_id", "activity_source", "external_activity_id"]
            if use_source_key
            else ["user_id", "strava_activity_id"]
        )
        stmt = (
            pg_insert(models.RideMetric)
            .values(**values, id=str(__import__("uuid").uuid4()))
            .on_conflict_do_update(
                index_elements=conflict_keys,
                set_={
                    k: v
                    for k, v in values.items()
                    if k not in conflict_keys
                },
            )
            .returning(models.RideMetric)
        )
        result = await db.execute(stmt)
        return result.scalar_one()

    # SQLite (tests) — SELECT + update/insert; flush immediately to avoid autoflush issues
    if use_source_key:
        existing = await db.scalar(
            select(models.RideMetric)
            .where(
                models.RideMetric.user_id == user_id,
                models.RideMetric.activity_source == normalized_source,
                models.RideMetric.external_activity_id == normalized_external_id,
            )
            .order_by(
                models.RideMetric.created_at.desc(),
                models.RideMetric.activity_date.desc(),
                func.coalesce(models.RideMetric.activity_start_datetime, "").desc(),
                models.RideMetric.id.desc(),
            )
            .limit(1)
        )
    else:
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
    """Return the most recent *limit* visible RideMetric rows, newest first.

    Stale duplicate rows are collapsed by source/external activity identity so
    dashboards never show the same imported activity twice.
    """
    source_key = func.coalesce(models.RideMetric.activity_source, "strava")
    external_key = func.coalesce(
        models.RideMetric.external_activity_id,
        cast(models.RideMetric.strava_activity_id, String),
    )
    start_key = func.coalesce(models.RideMetric.activity_start_datetime, "")
    ranked = (
        select(
            models.RideMetric.id.label("ride_metric_id"),
            func.row_number()
            .over(
                partition_by=(
                    models.RideMetric.user_id,
                    source_key,
                    external_key,
                ),
                order_by=(
                    models.RideMetric.activity_date.desc(),
                    start_key.desc(),
                    models.RideMetric.id.desc(),
                ),
            )
            .label("dedupe_rank"),
        )
        .where(models.RideMetric.user_id == user_id)
        .subquery()
    )
    result = await db.scalars(
        select(models.RideMetric)
        .join(ranked, models.RideMetric.id == ranked.c.ride_metric_id)
        .where(ranked.c.dedupe_rank == 1)
        .order_by(
            models.RideMetric.activity_date.desc(),
            start_key.desc(),
            models.RideMetric.id.desc(),
        )
        .limit(max(limit * 3, limit))
    )
    rides = list(result)
    deduped: list[models.RideMetric] = []
    for ride in rides:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(deduped)
                if _ride_metrics_are_near_duplicates(ride, existing)
            ),
            None,
        )
        if duplicate_index is not None:
            deduped[duplicate_index] = _preferred_visible_ride_metric(
                ride,
                deduped[duplicate_index],
            )
            continue
        deduped.append(ride)
        if len(deduped) >= limit:
            break
    return deduped


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
    label_override: str | None = None,
) -> models.RideMetric | None:
    """Partially update coach_note, user_note, and/or label_override on a RideMetric row.

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
    if label_override is not None:
        row.label_override = label_override
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


async def get_ride_metric_by_source(
    db: AsyncSession,
    user_id: str,
    activity_source: str,
    external_activity_id: str,
) -> models.RideMetric | None:
    return await db.scalar(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_source == activity_source,
            models.RideMetric.external_activity_id == external_activity_id,
        )
    )


async def get_near_duplicate_ride_metric(
    db: AsyncSession,
    user_id: str,
    *,
    activity_date: str,
    sport_type: str | None,
    activity_name: str | None,
    activity_start_datetime: str | None,
    duration_seconds: int | float | None,
    exclude_strava_activity_id: int | None = None,
    exclude_activity_source: str | None = None,
    exclude_external_activity_id: str | None = None,
) -> models.RideMetric | None:
    """Return a likely duplicate imported activity for the same user/date."""
    result = await db.scalars(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_date == activity_date,
        )
        .order_by(
            models.RideMetric.created_at.desc(),
            func.coalesce(models.RideMetric.activity_start_datetime, "").desc(),
            models.RideMetric.id.desc(),
        )
    )
    for ride in result:
        if (
            exclude_strava_activity_id is not None
            and ride.strava_activity_id == exclude_strava_activity_id
        ):
            continue
        if (
            exclude_activity_source is not None
            and exclude_external_activity_id is not None
            and ride.activity_source == exclude_activity_source
            and ride.external_activity_id == exclude_external_activity_id
        ):
            continue
        if are_near_duplicate_activities(
            activity_date=activity_date,
            sport_type=sport_type,
            activity_name=activity_name,
            activity_start_datetime=activity_start_datetime,
            duration_seconds=duration_seconds,
            other_activity_date=ride.activity_date,
            other_sport_type=ride.sport_type,
            other_activity_name=ride.activity_name,
            other_activity_start_datetime=ride.activity_start_datetime,
            other_duration_seconds=ride.duration_seconds,
        ):
            return ride
    return None


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
        .order_by(
            models.RideMetric.activity_date,
            models.RideMetric.activity_start_datetime,
            models.RideMetric.strava_activity_id,
        )
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


def _activity_key_filters(activity_keys: list[int | str]) -> list:
    numeric_ids: list[int] = []
    external_ids: list[str] = []
    for key in activity_keys:
        key_text = str(key)
        external_ids.append(key_text)
        if key_text.isdigit():
            numeric_ids.append(int(key_text))
    filters = []
    if numeric_ids:
        filters.append(models.RideMetric.strava_activity_id.in_(numeric_ids))
    if external_ids:
        filters.append(models.RideMetric.external_activity_id.in_(external_ids))
    return filters


async def get_ride_metrics_by_activity_ids(
    db: AsyncSession, user_id: str, strava_activity_ids: list[int | str]
) -> list[models.RideMetric]:
    """Return RideMetric rows for the given activity keys, ordered oldest-first."""
    filters = _activity_key_filters(strava_activity_ids)
    if not filters:
        return []
    result = await db.execute(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            or_(*filters),
        )
        .order_by(
            models.RideMetric.activity_date,
            models.RideMetric.activity_start_datetime,
            models.RideMetric.strava_activity_id,
        )
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
