"""CRUD / data-access helpers.

All raw SQLAlchemy queries are isolated here so that routers stay thin and
tests can patch a single module instead of mocking low-level session methods.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

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
# Evidence-based promotion (#387). The coach must not turn a single event into
# trusted knowledge: an inferred observation has to recur before it earns the
# coach's trust. A first sighting is seeded as a low-confidence candidate whose
# stored confidence is capped at the default floor (below the prompt threshold),
# and only repeated confirmation grows both its ``observation_count`` and its
# confidence past the bar. ``get_prompt_athlete_memory_facts`` then requires at
# least ``ATHLETE_MEMORY_MIN_EVIDENCE`` observations before an unconfirmed fact
# reaches a coach prompt. Deliberate, athlete-driven writes (confirming a
# hypothesis) bypass the cap via ``trusted=True``.
ATHLETE_MEMORY_MIN_EVIDENCE = 2
ATHLETE_MEMORY_INITIAL_CONFIDENCE_CAP = ATHLETE_MEMORY_DEFAULT_CONFIDENCE
# Confidence in an unconfirmed observation erodes once it has gone unmentioned
# for a grace period, so the coach trusts fresh evidence over old assumptions.
ATHLETE_MEMORY_DECAY_GRACE_DAYS = 30
ATHLETE_MEMORY_CONFIDENCE_DECAY_PER_DAY = 0.01
ATHLETE_MEMORY_STALE_CONFIDENCE = 0.3
# Once an unconfirmed observation has gone unmentioned this long it is archived:
# retained for audit and revival, but hidden from coach prompts and the default
# management view. A fresh observation revives it.
ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS = 180
# How much confidence a fact loses when fresh training evidence contradicts it.
# Larger than a single decay day so a real contradiction meaningfully demotes the
# fact and, together with the ``needs_validation`` flag, prompts the athlete to
# confirm or correct it.
ATHLETE_MEMORY_CONTRADICTION_PENALTY = 0.25

_ATHLETE_MEMORY_ACTIVE_STATUSES = ("active", "user_confirmed")
# Statuses shown in the default management view: currently trusted facts plus
# facts flagged for the athlete's attention (contradicted by fresh evidence).
_ATHLETE_MEMORY_VISIBLE_STATUSES = ("active", "user_confirmed", "needs_validation")
# Unconfirmed statuses whose confidence still decays / that can be archived.
_ATHLETE_MEMORY_DECAYABLE_STATUSES = ("active", "stale")
# Statuses a fresh observation revives back to ``active``.
_ATHLETE_MEMORY_REVIVABLE_STATUSES = ("stale", "archived", "needs_validation")


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
    db: AsyncSession,
    user: models.User,
    tokens: int,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
) -> None:
    """Add provider-reported LLM tokens to a user's running usage counters.

    The split is kept alongside the total because input and output bill at very
    different rates and cached input at a tenth of the input rate, so the total
    alone cannot be converted into a cost (#516). ``cached_tokens`` is a subset
    of ``input_tokens``, not a fourth bucket.
    """
    if tokens <= 0 and input_tokens <= 0 and output_tokens <= 0:
        return
    user.consumed_tokens = int(user.consumed_tokens or 0) + max(0, int(tokens))
    user.consumed_input_tokens = int(user.consumed_input_tokens or 0) + max(
        0, int(input_tokens)
    )
    user.consumed_output_tokens = int(user.consumed_output_tokens or 0) + max(
        0, int(output_tokens)
    )
    user.consumed_cached_tokens = int(user.consumed_cached_tokens or 0) + max(
        0, int(cached_tokens)
    )
    await db.flush()


async def record_llm_calls(
    db: AsyncSession,
    user_id: str | None,
    source: str,
    records: Sequence[Any],
) -> None:
    """Store one row per provider call so the record outlives the container.

    The counters above answer "how much in total"; these rows answer "which
    feature, which model, which prompt, when" — the questions #516 built the
    log line for, which a deploy then threw away with the container (#549).

    *records* are ``services.token_accounting.CallRecord`` instances, taken
    structurally rather than by import so ``crud`` keeps knowing nothing about
    the accounting layer.
    """
    if not records:
        return
    db.add_all(
        [
            models.LlmCall(
                user_id=user_id,
                task=record.task[:30],
                provider=record.provider[:30],
                model=record.model[:80],
                source=source[:60],
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cached_tokens=record.cached_tokens,
                total_tokens=record.total_tokens,
                latency_ms=record.latency_ms,
                json_mode=record.json_mode,
                ok=record.ok,
                # Provider error strings carry the whole failed request in some
                # SDKs; the class name is what a cost query groups by.
                error=(record.error or None) and str(record.error)[:200],
                prompt_sha=record.prompt_sha[:12],
            )
            for record in records
        ]
    )
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
# PlanDayHistory
# ---------------------------------------------------------------------------


async def record_plan_day_changes(
    db: AsyncSession,
    user_id: str,
    changes: list[dict],
    source: str,
) -> list[models.PlanDayHistory]:
    """Append one PlanDayHistory row per per-session change and flush.

    Each ``changes`` entry is ``{"date", "slot", "old_day", "new_day", "applied"}``.
    ``applied`` defaults to True, ``slot`` to 0 (the single-session day, #496).
    No-op when ``changes`` is empty.

    All rows from this call share one ``batch_id`` so the coach run they belong
    to (one plan generation, nightly tune-up or chat edit) can be reconstituted
    from the log and collapsed into a single Coach Timeline card (#435).
    """
    if not changes:
        return []
    batch_id = models._uuid()
    rows = [
        models.PlanDayHistory(
            user_id=user_id,
            date=change["date"],
            slot=int(change.get("slot") or 0),
            batch_id=batch_id,
            old_day=change.get("old_day"),
            new_day=change.get("new_day"),
            source=source,
            applied=change.get("applied", True),
        )
        for change in changes
    ]
    db.add_all(rows)
    await db.flush()
    return rows


async def list_plan_day_history(
    db: AsyncSession,
    user_id: str,
    *,
    date: str | None = None,
    limit: int | None = None,
) -> list[models.PlanDayHistory]:
    """Return a user's plan-day change log, newest first, optionally by date."""
    stmt = select(models.PlanDayHistory).where(
        models.PlanDayHistory.user_id == user_id
    )
    if date is not None:
        stmt = stmt.where(models.PlanDayHistory.date == date)
    stmt = stmt.order_by(models.PlanDayHistory.recorded_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(await db.scalars(stmt))


async def set_plan_day_reasons(
    db: AsyncSession, batch_id: str, reasons: dict[str, str]
) -> None:
    """Backfill each ``PlanDayHistory`` row's coach ``reason`` for one run (#439).

    ``reasons`` maps a plan-day date to its one-line rationale. Only the rows of
    the given ``batch_id`` are touched, so the per-day "why" lands next to the
    diff it explains. Dates absent from ``reasons`` are left as-is.
    """
    if not reasons:
        return
    rows = await db.scalars(
        select(models.PlanDayHistory).where(
            models.PlanDayHistory.batch_id == batch_id
        )
    )
    for row in rows:
        reason = reasons.get(row.date)
        if reason:
            row.reason = reason
    await db.flush()


async def create_plan_change_summary(
    db: AsyncSession,
    user_id: str,
    *,
    batch_id: str,
    source: str,
    summary: str,
) -> models.PlanChangeSummary:
    """Persist the one athlete-facing narrative for a coach run (#439)."""
    row = models.PlanChangeSummary(
        user_id=user_id, batch_id=batch_id, source=source, summary=summary
    )
    db.add(row)
    await db.flush()
    return row


async def get_narrated_batch_ids(
    db: AsyncSession, user_id: str, batch_ids: list[str]
) -> set[str]:
    """Return which of ``batch_ids`` have a ``PlanChangeSummary`` (#439).

    Lets the plan-history endpoint tag entries whose run was narrated in chat so
    the frontend can suppress the now-redundant Coach-Timeline card.
    """
    if not batch_ids:
        return set()
    rows = await db.scalars(
        select(models.PlanChangeSummary.batch_id).where(
            models.PlanChangeSummary.user_id == user_id,
            models.PlanChangeSummary.batch_id.in_(batch_ids),
        )
    )
    return set(rows)


async def plan_day_history_stats(
    db: AsyncSession,
    user_id: str,
    *,
    top_dates: int = 5,
) -> dict[str, Any]:
    """Aggregate a user's plan-day change log for the athlete analytics view.

    Returns counts grouped in SQL (no per-row Python loops):
    ``by_source`` (changes per trigger), ``applied_count`` / ``blocked_count``
    (an ``applied=False`` row is an automated change a user pin or completed day
    blocked — see #343), ``most_changed_dates`` (the ``top_dates`` dates with the
    most changes, newest count first) and ``total``. See #357.
    """
    hist = models.PlanDayHistory
    base = select(hist).where(hist.user_id == user_id).subquery()

    by_source_rows = await db.execute(
        select(base.c.source, func.count()).group_by(base.c.source)
    )
    by_source = {source: count for source, count in by_source_rows.all()}

    applied_rows = await db.execute(
        select(base.c.applied, func.count()).group_by(base.c.applied)
    )
    applied_count = 0
    blocked_count = 0
    for applied, count in applied_rows.all():
        if applied:
            applied_count = count
        else:
            blocked_count = count

    date_rows = await db.execute(
        select(base.c.date, func.count().label("n"))
        .group_by(base.c.date)
        .order_by(func.count().desc(), base.c.date.desc())
        .limit(top_dates)
    )
    most_changed_dates = [{"date": date, "count": n} for date, n in date_rows.all()]

    return {
        "by_source": by_source,
        "applied_count": applied_count,
        "blocked_count": blocked_count,
        "most_changed_dates": most_changed_dates,
        "total": applied_count + blocked_count,
    }


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
    db: AsyncSession, user_id: str, date: str, slot: int = 0
) -> models.WorkoutLog | None:
    """Return the WorkoutLog for a specific user/date/session, or None.

    ``slot`` selects which session on ``date`` (#496); it defaults to 0, the only
    session a legacy single-workout day has.
    """
    return await db.scalar(
        select(models.WorkoutLog).where(
            models.WorkoutLog.user_id == user_id,
            models.WorkoutLog.date == date,
            models.WorkoutLog.slot == slot,
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
    slot: int = 0,
) -> models.WorkoutLog:
    """Create or update a WorkoutLog for one user/date/session and flush (#496)."""
    existing = await get_workout_log_by_date(db, user_id, date, slot)
    if existing is None:
        existing = models.WorkoutLog(
            user_id=user_id,
            date=date,
            slot=slot,
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
    flagged_constraint_dates: list[str] | None = None,
) -> models.ChatMessage:
    """Create a ChatMessage, flush, and return the persisted instance."""
    message = models.ChatMessage(
        user_id=user_id,
        role=role,
        content=content,
        timestamp=timestamp,
        plan_update_count=plan_update_count,
        flagged_constraint_dates=flagged_constraint_dates or None,
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


async def get_coach_memory(
    db: AsyncSession, user_id: str, *, for_update: bool = False
) -> models.CoachMemory | None:
    """Return the CoachMemory for a user, or None.

    Set ``for_update`` to lock the row (``SELECT … FOR UPDATE``) so a
    read-modify-write of the memory serialises against concurrent writers and
    cannot lose a committed edit (#346).
    """
    if for_update:
        return await db.get(models.CoachMemory, user_id, with_for_update=True)
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


async def update_coach_memory_if_unchanged(
    db: AsyncSession, user_id: str, *, expected: str, new: str
) -> bool:
    """Set memory to ``new`` only if it still equals ``expected``.

    Returns True when applied, False when the stored value has diverged from
    ``expected`` (a concurrent edit) so the caller can retry against a fresh
    base. The row is locked ``FOR UPDATE`` for the read-check-write, so a
    concurrent write from another transaction is never silently overwritten
    (#346). Handles the create case (no row yet) only when ``expected`` is empty.
    """
    existing = await db.get(models.CoachMemory, user_id, with_for_update=True)
    if existing is None:
        if expected:
            # We based our update on a non-empty memory that no longer exists —
            # treat as a conflict rather than resurrecting stale content.
            return False
        db.add(models.CoachMemory(user_id=user_id, memory=new))
        await db.flush()
        return True
    if existing.memory != expected:
        return False
    existing.memory = new
    existing.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return True


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
# AthleteModel (long-term structured athlete model, #384)
# ---------------------------------------------------------------------------


async def get_athlete_model(
    db: AsyncSession, user_id: str
) -> models.AthleteModel | None:
    """Return the long-term AthleteModel for a user, or None."""
    return await db.get(models.AthleteModel, user_id)


async def upsert_athlete_model(
    db: AsyncSession,
    user_id: str,
    *,
    ftp_watts: int | None = None,
    vo2max: float | None = None,
    pacing_quality: str = "",
    recovery_ability: str = "",
    threshold_durability: str = "",
    heat_tolerance: str = "",
    preferred_training_style: str = "",
    strengths: list[str] | None = None,
    weaknesses: list[str] | None = None,
    risk_factors: list[str] | None = None,
    summary: str = "",
    confidence: float | None = None,
) -> models.AthleteModel:
    """Create or update a user's long-term athlete model and flush.

    ``confidence`` is only written when explicitly provided so that an athlete's
    manual edit (which omits it) preserves the coach's last confidence score.
    """
    values: dict[str, object] = {
        "ftp_watts": ftp_watts,
        "vo2max": vo2max,
        "pacing_quality": pacing_quality,
        "recovery_ability": recovery_ability,
        "threshold_durability": threshold_durability,
        "heat_tolerance": heat_tolerance,
        "preferred_training_style": preferred_training_style,
        "strengths": list(strengths or []),
        "weaknesses": list(weaknesses or []),
        "risk_factors": list(risk_factors or []),
        "summary": summary,
        "updated_at": datetime.now(timezone.utc),
    }
    if confidence is not None:
        values["confidence"] = confidence

    existing = await get_athlete_model(db, user_id)
    if existing is None:
        existing = models.AthleteModel(user_id=user_id, **values)
        db.add(existing)
    else:
        for attr, value in values.items():
            setattr(existing, attr, value)
    await db.flush()
    return existing


# ---------------------------------------------------------------------------
# AthletePerformanceModel (#475)
# ---------------------------------------------------------------------------


async def get_athlete_performance_model(
    db: AsyncSession, user_id: str
) -> models.AthletePerformanceModel | None:
    """Return the deterministic Athlete Performance Model for a user, or None."""
    return await db.get(models.AthletePerformanceModel, user_id)


async def upsert_athlete_performance_model(
    db: AsyncSession,
    user_id: str,
    *,
    attributes: dict[str, object],
    likely_limiter: str | None = None,
    limiters: list[object] | None = None,
    source_window_days: int | None = None,
    derived_from_rides: int = 0,
) -> models.AthletePerformanceModel:
    """Create or update a user's Athlete Performance Model and flush."""
    values: dict[str, object] = {
        "attributes": attributes,
        "likely_limiter": likely_limiter,
        "limiters": limiters,
        "source_window_days": source_window_days,
        "derived_from_rides": derived_from_rides,
        "updated_at": datetime.now(timezone.utc),
    }
    existing = await get_athlete_performance_model(db, user_id)
    if existing is None:
        existing = models.AthletePerformanceModel(user_id=user_id, **values)
        db.add(existing)
    else:
        for attr, value in values.items():
            setattr(existing, attr, value)
    await db.flush()
    return existing


async def create_athlete_performance_snapshot(
    db: AsyncSession,
    user_id: str,
    *,
    attributes: dict[str, object],
    likely_limiter: str | None = None,
    limiters: list[object] | None = None,
    recorded_at: datetime | None = None,
) -> models.AthletePerformanceSnapshot:
    """Insert a new AthletePerformanceSnapshot row and flush."""
    kwargs: dict = dict(
        user_id=user_id,
        attributes=attributes,
        likely_limiter=likely_limiter,
        limiters=limiters,
    )
    if recorded_at is not None:
        kwargs["recorded_at"] = recorded_at
    snapshot = models.AthletePerformanceSnapshot(**kwargs)
    db.add(snapshot)
    await db.flush()
    return snapshot


async def get_athlete_performance_snapshots(
    db: AsyncSession,
    user_id: str,
    limit: int = 90,
) -> list[models.AthletePerformanceSnapshot]:
    """Return the most recent *limit* performance snapshots for a user, oldest first."""
    recent_ids = (
        select(models.AthletePerformanceSnapshot.id)
        .where(models.AthletePerformanceSnapshot.user_id == user_id)
        .order_by(models.AthletePerformanceSnapshot.recorded_at.desc())
        .limit(limit)
        .scalar_subquery()
    )
    result = await db.scalars(
        select(models.AthletePerformanceSnapshot)
        .where(models.AthletePerformanceSnapshot.id.in_(recent_ids))
        .order_by(models.AthletePerformanceSnapshot.recorded_at.asc())
    )
    return list(result)


# ---------------------------------------------------------------------------
# AthleteMemoryFact
# ---------------------------------------------------------------------------


def _normalise_athlete_memory_fact_key(fact: str) -> str:
    return " ".join(fact.casefold().split())[:255]


def _normalise_athlete_memory_category(category: str | None) -> str:
    value = (category or "general").strip().lower().replace(" ", "_")
    return value[:50] or "general"


def _normalise_athlete_memory_kind(kind: str | None) -> str:
    """Coerce a knowledge-type label to ``fact`` or ``observation`` (#386).

    Anything that is not an explicit stable ``fact`` falls back to
    ``observation`` — the safer default, since an inferred pattern carries less
    authority than a stated value.
    """
    return "fact" if (kind or "").strip().lower() == "fact" else "observation"


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


async def apply_athlete_memory_confidence_decay(
    db: AsyncSession, user_id: str, *, now: datetime | None = None
) -> int:
    """Erode confidence in stale observations and archive obsolete ones.

    Active facts that have not been re-observed within
    ``ATHLETE_MEMORY_DECAY_GRACE_DAYS`` lose confidence linearly for each
    additional day of silence. Once confidence falls below
    ``ATHLETE_MEMORY_STALE_CONFIDENCE`` the fact is marked ``stale`` so it drops
    out of coach prompts. If it then stays unmentioned until
    ``ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS`` have passed since its last
    confirmation, it is ``archived`` — retained for audit and revival but hidden
    from the default management view. A fresh observation revives a stale or
    archived fact. ``user_confirmed`` facts are vetted by the athlete and never
    decay or archive. Returns the number of facts whose status or confidence
    changed.
    """
    reference = now or datetime.now(timezone.utc)
    facts = await db.scalars(
        select(models.AthleteMemoryFact).where(
            models.AthleteMemoryFact.user_id == user_id,
            models.AthleteMemoryFact.status.in_(_ATHLETE_MEMORY_DECAYABLE_STATUSES),
        )
    )
    changed = 0
    for fact in facts:
        idle_days = (reference - _as_aware_utc(fact.last_confirmed_at)).days
        # Archive observations that have gone unmentioned for a long time. This
        # is keyed purely on elapsed time, so it is deterministic regardless of
        # how often decay is applied.
        if idle_days >= ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS:
            fact.status = "archived"
            fact.updated_at = reference
            changed += 1
            continue
        # Stale facts hold their eroded confidence until they are archived.
        if fact.status != "active":
            continue
        decay_days = idle_days - ATHLETE_MEMORY_DECAY_GRACE_DAYS
        if decay_days <= 0:
            continue
        decayed = _clamp_confidence(
            fact.confidence - ATHLETE_MEMORY_CONFIDENCE_DECAY_PER_DAY * decay_days
        )
        if decayed >= fact.confidence:
            continue
        fact.confidence = decayed
        fact.updated_at = reference
        if decayed < ATHLETE_MEMORY_STALE_CONFIDENCE:
            fact.status = "stale"
        changed += 1
    if changed:
        await db.flush()
    return changed


async def list_athlete_memory_facts(
    db: AsyncSession,
    user_id: str,
    *,
    include_inactive: bool = False,
    now: datetime | None = None,
) -> list[models.AthleteMemoryFact]:
    """Return stored athlete memory facts for management views."""
    await apply_athlete_memory_confidence_decay(db, user_id, now=now)
    stmt = select(models.AthleteMemoryFact).where(
        models.AthleteMemoryFact.user_id == user_id
    )
    if not include_inactive:
        stmt = stmt.where(
            models.AthleteMemoryFact.status.in_(_ATHLETE_MEMORY_VISIBLE_STATUSES)
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
    kind: str = "observation",
    category: str = "general",
    source_snippet: str = "",
    source_exchange_id: str | None = None,
    confidence: float | None = None,
    observed_at: datetime | None = None,
    trusted: bool = False,
) -> models.AthleteMemoryFact:
    """Create a fact or strengthen an existing fact from a new observation.

    An ordinary (``trusted=False``) observation is treated as tentative evidence
    (#387): its first sighting is stored below the prompt-trust threshold and its
    confidence can only climb through the per-observation step, so it takes
    repeated confirmation before the coach relies on it. A ``trusted`` write —
    the athlete confirming a hypothesis — records the supplied confidence as-is
    and seeds enough evidence to be usable immediately.
    """
    cleaned_fact = fact.strip()
    if not cleaned_fact:
        raise ValueError("fact must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    normalized_kind = _normalise_athlete_memory_kind(kind)
    fact_key = _normalise_athlete_memory_fact_key(cleaned_fact)
    # Untrusted evidence can only claim up to the candidate cap; trust is earned
    # by recurrence, not asserted in a single extraction.
    contributed_confidence = _clamp_confidence(confidence)
    if not trusted:
        contributed_confidence = min(
            contributed_confidence, ATHLETE_MEMORY_INITIAL_CONFIDENCE_CAP
        )

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
            kind=normalized_kind,
            category=normalized_category,
            source_snippet=source_snippet.strip(),
            source_exchange_id=source_exchange_id,
            first_observed_at=now,
            last_confirmed_at=now,
            confidence=contributed_confidence,
            status="active",
            # A confirmed hypothesis arrives already backed by evidence, so it
            # meets the promotion bar at once; a plain observation starts at one.
            observation_count=ATHLETE_MEMORY_MIN_EVIDENCE if trusted else 1,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.observation_count += 1
        existing.last_confirmed_at = now
        existing.updated_at = now
        # Fresh evidence can re-classify a row (e.g. a value the coach once
        # inferred is later stated outright), but a single automated observation
        # must not overwrite knowledge the athlete has confirmed (#387).
        if trusted or existing.status != "user_confirmed":
            existing.kind = normalized_kind
        if source_snippet:
            existing.source_snippet = source_snippet.strip()
        if source_exchange_id is not None:
            existing.source_exchange_id = source_exchange_id
        revived = existing.status in _ATHLETE_MEMORY_REVIVABLE_STATUSES
        if revived:
            existing.status = "active"
            # Fresh evidence re-confirms the fact, so any pending contradiction is
            # resolved and the validation prompt is cleared.
            existing.contradiction_note = None
        base_confidence = max(existing.confidence, contributed_confidence)
        # A revived observation may have decayed to near-zero; refresh it to at
        # least the default so fresh evidence is trusted again.
        if revived:
            base_confidence = max(base_confidence, ATHLETE_MEMORY_DEFAULT_CONFIDENCE)
        existing.confidence = min(
            1.0, base_confidence + ATHLETE_MEMORY_CONFIDENCE_STEP
        )
    await db.flush()
    return existing


async def record_athlete_memory_fact_contradiction(
    db: AsyncSession,
    fact: models.AthleteMemoryFact,
    *,
    reason: str,
    now: datetime | None = None,
) -> models.AthleteMemoryFact:
    """Flag a fact that fresh training evidence contradicts.

    Lowers the fact's confidence by ``ATHLETE_MEMORY_CONTRADICTION_PENALTY`` and
    moves it to ``needs_validation`` so it is pulled from coach prompts and
    surfaced to the athlete to confirm or correct. ``reason`` explains, in the
    coach's words, why the evidence disagrees with the stored fact. This is a
    deliberate, evidence-driven demotion, so it applies to ``user_confirmed``
    facts too — even a vetted value is worth re-checking when the numbers
    disagree. A later fresh, consistent observation revives the fact and clears
    the flag (see :func:`observe_athlete_memory_fact`).
    """
    reference = now or datetime.now(timezone.utc)
    fact.confidence = _clamp_confidence(
        fact.confidence - ATHLETE_MEMORY_CONTRADICTION_PENALTY
    )
    fact.status = "needs_validation"
    fact.contradiction_note = reason.strip()[:500] or None
    fact.updated_at = reference
    await db.flush()
    return fact


async def update_athlete_memory_fact(
    db: AsyncSession,
    user_id: str,
    fact_id: str,
    *,
    fact: str | None = None,
    kind: str | None = None,
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
        # Editing the fact text resolves any pending contradiction.
        existing.contradiction_note = None
    if kind is not None:
        existing.kind = _normalise_athlete_memory_kind(kind)
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
        # Any move off ``needs_validation`` means the athlete has acted on the
        # validation prompt, so the contradiction note no longer applies.
        if status != "needs_validation":
            existing.contradiction_note = None
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
    min_evidence: int = ATHLETE_MEMORY_MIN_EVIDENCE,
    stale_after_days: int = ATHLETE_MEMORY_STALE_AFTER_DAYS,
    limit: int = ATHLETE_MEMORY_PROMPT_LIMIT,
) -> list[models.AthleteMemoryFact]:
    """Return only memory facts safe enough to include in coach prompts."""
    reference = now or datetime.now(timezone.utc)
    facts = await list_athlete_memory_facts(
        db, user_id, include_inactive=False, now=reference
    )
    cutoff = reference - timedelta(days=stale_after_days)
    prompt_facts: list[models.AthleteMemoryFact] = []
    for fact in facts:
        # Facts contradicted by fresh evidence are withheld from the coach until
        # the athlete validates or corrects them.
        if fact.status == "needs_validation":
            continue
        if fact.status == "user_confirmed":
            prompt_facts.append(fact)
        # An unconfirmed observation must clear both the confidence and the
        # evidence bar before it informs coaching, so a single event never
        # reaches the coach (#387).
        elif (
            fact.confidence >= min_confidence
            and fact.observation_count >= min_evidence
            and _as_aware_utc(fact.last_confirmed_at) >= cutoff
        ):
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
    await db.execute(
        delete(models.AthleteHypothesis).where(
            models.AthleteHypothesis.user_id == user_id
        )
    )
    await db.execute(
        delete(models.AthleteOpenQuestion).where(
            models.AthleteOpenQuestion.user_id == user_id
        )
    )
    await db.execute(
        delete(models.AthleteExperiment).where(
            models.AthleteExperiment.user_id == user_id
        )
    )
    await db.execute(
        delete(models.AthletePrediction).where(
            models.AthletePrediction.user_id == user_id
        )
    )
    # Inquiries carry the athlete's own words in their answers, so a memory wipe
    # has to take them too (#506).
    await db.execute(
        delete(models.AthleteInquiry).where(models.AthleteInquiry.user_id == user_id)
    )
    coach_memory_row = await get_coach_memory(db, user_id)
    if coach_memory_row is not None:
        coach_memory_row.memory = ""
        coach_memory_row.updated_at = datetime.now(timezone.utc)
    await db.flush()


# ---------------------------------------------------------------------------
# AthleteHypothesis
# ---------------------------------------------------------------------------

# Statuses shown by default: open hypotheses still awaiting the athlete's
# judgement. Confirmed/refuted hypotheses are resolved and only surfaced on
# request (management/export views).
_ATHLETE_HYPOTHESIS_OPEN_STATUSES = ("proposed",)
# Confidence a hypothesis is floored to when the athlete confirms it, before it
# is promoted into a memory fact.
ATHLETE_HYPOTHESIS_CONFIRM_CONFIDENCE = 0.75
# When the performance model stops supporting a deterministic hypothesis (#479),
# its confidence steps down each pass; once it falls below the retire floor the
# hypothesis is deleted so the coach stops asserting a claim the model no longer
# backs, rather than leaving a stale duplicate around.
ATHLETE_HYPOTHESIS_DECAY_STEP = 0.2
ATHLETE_HYPOTHESIS_RETIRE_CONFIDENCE = 0.2
# Prompt-facing cap (#512). This table only grows: a weekly generator keeps
# proposing, and in production all 62 rows were still ``proposed`` — nothing had
# ever retired one — so the section was 3,560 tokens (17 %) of every coach
# message and got more expensive each week no matter what the athlete did. The
# coach does not need to recite every idea it has ever had; it needs the ones it
# can actually defend. Eight is roughly what fits in a conversation before the
# hypotheses drown out the answer. The floor is the confidence a hypothesis is
# seeded with, so only one actively knocked down by
# :func:`decay_unsupported_model_hypotheses` falls below it.
ATHLETE_HYPOTHESIS_PROMPT_LIMIT = 8
ATHLETE_HYPOTHESIS_PROMPT_MIN_CONFIDENCE = ATHLETE_MEMORY_DEFAULT_CONFIDENCE
# The generators run weekly and re-propose what the data still supports, which
# bumps ``updated_at``. Surviving eight such passes without one fresh
# observation means the evidence has moved on.
ATHLETE_HYPOTHESIS_PROMPT_STALE_AFTER_DAYS = 56


def _normalise_hypothesis_key(statement: str) -> str:
    return " ".join(statement.casefold().split())[:255]


async def get_athlete_hypothesis(
    db: AsyncSession, user_id: str, hypothesis_id: str
) -> models.AthleteHypothesis | None:
    """Return one athlete hypothesis owned by a user."""
    return await db.scalar(
        select(models.AthleteHypothesis).where(
            models.AthleteHypothesis.id == hypothesis_id,
            models.AthleteHypothesis.user_id == user_id,
        )
    )


async def list_athlete_hypotheses(
    db: AsyncSession,
    user_id: str,
    *,
    include_resolved: bool = False,
) -> list[models.AthleteHypothesis]:
    """Return stored hypotheses for a user, strongest evidence first."""
    stmt = select(models.AthleteHypothesis).where(
        models.AthleteHypothesis.user_id == user_id
    )
    if not include_resolved:
        stmt = stmt.where(
            models.AthleteHypothesis.status.in_(_ATHLETE_HYPOTHESIS_OPEN_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthleteHypothesis.confidence.desc(),
            models.AthleteHypothesis.evidence_count.desc(),
            models.AthleteHypothesis.updated_at.desc(),
            models.AthleteHypothesis.id.desc(),
        )
    )
    return list(result)


async def get_prompt_athlete_hypotheses(
    db: AsyncSession,
    user_id: str,
    *,
    now: datetime | None = None,
    min_confidence: float = ATHLETE_HYPOTHESIS_PROMPT_MIN_CONFIDENCE,
    stale_after_days: int = ATHLETE_HYPOTHESIS_PROMPT_STALE_AFTER_DAYS,
    limit: int = ATHLETE_HYPOTHESIS_PROMPT_LIMIT,
) -> list[models.AthleteHypothesis]:
    """Return only the hypotheses worth spending coach-prompt tokens on (#512).

    :func:`list_athlete_hypotheses` stays unbounded for the API and the
    expert-mode UI, which are meant to show the athlete everything. A prompt is
    the opposite situation: it pays for every row on every message, so it takes
    the strongest few and lets the rest wait for their evidence. Ordering is
    inherited from ``list_athlete_hypotheses`` (confidence, then evidence), so
    the survivors are the ones the coach is most able to defend.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=stale_after_days)
    prompt_hypotheses: list[models.AthleteHypothesis] = []
    for hypothesis in await list_athlete_hypotheses(db, user_id):
        # A belief the coach barely holds is not worth asserting to the athlete,
        # however tentatively — and a hypothesis that has gone this long without
        # a single fresh observation is one the evidence stopped supporting.
        if hypothesis.confidence < min_confidence:
            continue
        if _as_aware_utc(hypothesis.updated_at) < cutoff:
            continue
        prompt_hypotheses.append(hypothesis)
        if len(prompt_hypotheses) >= limit:
            break
    return prompt_hypotheses


async def propose_athlete_hypothesis(
    db: AsyncSession,
    user_id: str,
    *,
    statement: str,
    category: str = "general",
    rationale: str = "",
    confidence: float | None = None,
    evidence: list[str] | None = None,
    alternative_explanations: list[str] | None = None,
    observed_at: datetime | None = None,
) -> models.AthleteHypothesis:
    """Create a hypothesis or strengthen an existing one with fresh evidence.

    A new statement is stored as ``proposed`` with one unit of evidence. When the
    same statement recurs its evidence count grows and its confidence ticks up (a
    growing body of support), and a hypothesis the athlete had ``refuted`` is
    revived to ``proposed`` — fresh evidence reopens the question. A ``confirmed``
    hypothesis keeps its status while still accruing evidence.

    ``evidence`` and ``alternative_explanations`` (#479) are the structured
    supporting observations and the competing explanations still to be ruled out;
    when supplied they replace the stored lists so a recurring hypothesis carries
    the latest evidence rather than a stale snapshot.
    """
    cleaned = statement.strip()
    if not cleaned:
        raise ValueError("statement must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    statement_key = _normalise_hypothesis_key(cleaned)

    existing = await db.scalar(
        select(models.AthleteHypothesis).where(
            models.AthleteHypothesis.user_id == user_id,
            models.AthleteHypothesis.category == normalized_category,
            models.AthleteHypothesis.statement_key == statement_key,
        )
    )
    if existing is None:
        existing = models.AthleteHypothesis(
            user_id=user_id,
            statement=cleaned,
            statement_key=statement_key,
            category=normalized_category,
            rationale=rationale.strip(),
            confidence=_clamp_confidence(confidence),
            evidence=list(evidence) if evidence else None,
            alternative_explanations=(
                list(alternative_explanations) if alternative_explanations else None
            ),
            evidence_count=1,
            status="proposed",
            first_proposed_at=now,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.evidence_count += 1
        existing.updated_at = now
        if rationale:
            existing.rationale = rationale.strip()
        if evidence:
            existing.evidence = list(evidence)
        if alternative_explanations:
            existing.alternative_explanations = list(alternative_explanations)
        if existing.status == "refuted":
            existing.status = "proposed"
        base_confidence = max(existing.confidence, _clamp_confidence(confidence))
        existing.confidence = min(
            1.0, base_confidence + ATHLETE_MEMORY_CONFIDENCE_STEP
        )
    await db.flush()
    return existing


async def decay_unsupported_model_hypotheses(
    db: AsyncSession,
    user_id: str,
    *,
    category: str,
    supported_keys: set[str],
    now: datetime | None = None,
) -> int:
    """Decay — and eventually retire — proposed hypotheses no longer supported.

    Called after regenerating the deterministic performance-model hypotheses
    (#479): any previously ``proposed`` hypothesis in ``category`` whose statement
    the model no longer produces has lost its supporting evidence, so its
    confidence is stepped down; once it drops below the retire floor it is deleted
    so the coach stops asserting a claim the model no longer backs. Confirmed or
    refuted hypotheses (the athlete has already ruled on them) are left untouched.
    Returns the number of hypotheses decayed or retired.
    """
    normalized_category = _normalise_athlete_memory_category(category)
    now = now or datetime.now(timezone.utc)
    rows = await db.scalars(
        select(models.AthleteHypothesis).where(
            models.AthleteHypothesis.user_id == user_id,
            models.AthleteHypothesis.category == normalized_category,
            models.AthleteHypothesis.status == "proposed",
        )
    )
    changed = 0
    for row in rows:
        if row.statement_key in supported_keys:
            continue
        row.confidence = max(0.0, row.confidence - ATHLETE_HYPOTHESIS_DECAY_STEP)
        row.updated_at = now
        if row.confidence < ATHLETE_HYPOTHESIS_RETIRE_CONFIDENCE:
            await db.delete(row)
        changed += 1
    await db.flush()
    return changed


async def update_athlete_hypothesis(
    db: AsyncSession,
    user_id: str,
    hypothesis_id: str,
    *,
    statement: str | None = None,
    category: str | None = None,
    rationale: str | None = None,
    confidence: float | None = None,
    status: str | None = None,
) -> models.AthleteHypothesis | None:
    """Apply an athlete's edit or verdict to a hypothesis and flush.

    Confirming a hypothesis floors its confidence and promotes it into a durable
    memory fact via :func:`observe_athlete_memory_fact`, so validated knowledge
    starts informing coach advice; refuting or deleting simply resolves it.
    """
    existing = await get_athlete_hypothesis(db, user_id, hypothesis_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    if statement is not None:
        cleaned = statement.strip()
        if not cleaned:
            raise ValueError("statement must not be empty")
        existing.statement = cleaned
        existing.statement_key = _normalise_hypothesis_key(cleaned)
    if category is not None:
        existing.category = _normalise_athlete_memory_category(category)
    if rationale is not None:
        existing.rationale = rationale.strip()
    if confidence is not None:
        existing.confidence = _clamp_confidence(confidence)
    if status is not None:
        existing.status = status
        if status == "confirmed":
            existing.confidence = max(
                existing.confidence, ATHLETE_HYPOTHESIS_CONFIRM_CONFIDENCE
            )
            # The athlete has vetted this claim, so promote it as trusted
            # knowledge: keep its confidence and let it inform coaching at once,
            # rather than re-earning trust through the evidence gate (#387).
            await observe_athlete_memory_fact(
                db,
                user_id,
                fact=existing.statement,
                category=existing.category,
                source_snippet=existing.rationale,
                confidence=existing.confidence,
                observed_at=now,
                trusted=True,
            )
    existing.updated_at = now
    await db.flush()
    return existing


async def delete_athlete_hypothesis(
    db: AsyncSession, user_id: str, hypothesis_id: str
) -> bool:
    """Permanently remove an athlete hypothesis owned by a user."""
    existing = await get_athlete_hypothesis(db, user_id, hypothesis_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# AthleteOpenQuestion (#385)
# ---------------------------------------------------------------------------

# Statuses shown by default: still-open questions. Answered/dismissed questions
# are resolved and only surfaced on request (management/export views).
_ATHLETE_OPEN_QUESTION_OPEN_STATUSES = ("open",)
# Independent observations at/above which an open question has "sufficient
# evidence" and auto-closes as answered (see issue #385).
ATHLETE_OPEN_QUESTION_AUTO_CLOSE_EVIDENCE = 3
# Prompt-facing cap (#512), for the same reason as the hypothesis cap above: 21
# open questions were 2,503 tokens (12 %) of every coach message, and a question
# only auto-closes once it reaches the evidence threshold, so a question nothing
# ever gathers evidence for is re-sent forever. Five is enough for the coach to
# recognise an opening in the conversation; more just crowds out the reply. There
# is no confidence to filter on here, so it is the best-evidenced five — the ones
# closest to being answerable.
ATHLETE_OPEN_QUESTION_PROMPT_LIMIT = 5
ATHLETE_OPEN_QUESTION_PROMPT_STALE_AFTER_DAYS = 56


def _normalise_open_question_key(question: str) -> str:
    return " ".join(question.casefold().split())[:255]


async def get_athlete_open_question(
    db: AsyncSession, user_id: str, question_id: str
) -> models.AthleteOpenQuestion | None:
    """Return one open question owned by a user."""
    return await db.scalar(
        select(models.AthleteOpenQuestion).where(
            models.AthleteOpenQuestion.id == question_id,
            models.AthleteOpenQuestion.user_id == user_id,
        )
    )


async def list_athlete_open_questions(
    db: AsyncSession,
    user_id: str,
    *,
    include_resolved: bool = False,
) -> list[models.AthleteOpenQuestion]:
    """Return tracked open questions for a user, best-evidenced first."""
    stmt = select(models.AthleteOpenQuestion).where(
        models.AthleteOpenQuestion.user_id == user_id
    )
    if not include_resolved:
        stmt = stmt.where(
            models.AthleteOpenQuestion.status.in_(
                _ATHLETE_OPEN_QUESTION_OPEN_STATUSES
            )
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthleteOpenQuestion.evidence_count.desc(),
            models.AthleteOpenQuestion.updated_at.desc(),
            models.AthleteOpenQuestion.id.desc(),
        )
    )
    return list(result)


async def get_prompt_athlete_open_questions(
    db: AsyncSession,
    user_id: str,
    *,
    now: datetime | None = None,
    stale_after_days: int = ATHLETE_OPEN_QUESTION_PROMPT_STALE_AFTER_DAYS,
    limit: int = ATHLETE_OPEN_QUESTION_PROMPT_LIMIT,
) -> list[models.AthleteOpenQuestion]:
    """Return only the open questions worth carrying in a coach prompt (#512).

    The athlete-facing list stays complete via
    :func:`list_athlete_open_questions`; this is the bounded view the coach
    reads on every message. Ordering is inherited, so the survivors are the
    best-evidenced ones — the questions closest to being answerable.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=stale_after_days)
    prompt_questions: list[models.AthleteOpenQuestion] = []
    for question in await list_athlete_open_questions(db, user_id):
        # A question that has gathered nothing new in this long is not one the
        # coach is still actively working on.
        if _as_aware_utc(question.updated_at) < cutoff:
            continue
        prompt_questions.append(question)
        if len(prompt_questions) >= limit:
            break
    return prompt_questions


async def record_athlete_open_question(
    db: AsyncSession,
    user_id: str,
    *,
    question: str,
    category: str = "general",
    evidence: str = "",
    needs: str = "",
    resolved: bool = False,
    resolution: str = "",
    observed_at: datetime | None = None,
) -> models.AthleteOpenQuestion:
    """Create an open question or accrue evidence on an existing one, then flush.

    A new question is stored ``open`` with one unit of evidence. When the same
    question recurs its evidence count grows and its ``evidence``/``needs`` are
    refreshed with the latest reading. Once the count reaches
    :data:`ATHLETE_OPEN_QUESTION_AUTO_CLOSE_EVIDENCE` — or the caller signals the
    record now answers it via ``resolved`` — the question auto-closes to
    ``answered`` with a short ``resolution``, matching the issue's requirement
    that questions close once sufficient evidence exists.
    """
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("question must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    question_key = _normalise_open_question_key(cleaned)

    existing = await db.scalar(
        select(models.AthleteOpenQuestion).where(
            models.AthleteOpenQuestion.user_id == user_id,
            models.AthleteOpenQuestion.category == normalized_category,
            models.AthleteOpenQuestion.question_key == question_key,
        )
    )
    if existing is None:
        existing = models.AthleteOpenQuestion(
            user_id=user_id,
            question=cleaned,
            question_key=question_key,
            category=normalized_category,
            evidence=evidence.strip(),
            needs=needs.strip(),
            evidence_count=1,
            status="open",
            first_asked_at=now,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.evidence_count += 1
        existing.updated_at = now
        if evidence:
            existing.evidence = evidence.strip()
        if needs:
            existing.needs = needs.strip()
        # Fresh evidence reopens a question the athlete had dismissed.
        if existing.status == "dismissed":
            existing.status = "open"
            existing.resolution = None

    if existing.status == "open" and (
        resolved
        or existing.evidence_count >= ATHLETE_OPEN_QUESTION_AUTO_CLOSE_EVIDENCE
    ):
        existing.status = "answered"
        existing.resolution = (resolution.strip() or existing.evidence) or None
    await db.flush()
    return existing


async def update_athlete_open_question(
    db: AsyncSession,
    user_id: str,
    question_id: str,
    *,
    question: str | None = None,
    category: str | None = None,
    evidence: str | None = None,
    needs: str | None = None,
    resolution: str | None = None,
    status: str | None = None,
) -> models.AthleteOpenQuestion | None:
    """Apply an athlete's edit or verdict to an open question and flush."""
    existing = await get_athlete_open_question(db, user_id, question_id)
    if existing is None:
        return None

    if question is not None:
        cleaned = question.strip()
        if not cleaned:
            raise ValueError("question must not be empty")
        existing.question = cleaned
        existing.question_key = _normalise_open_question_key(cleaned)
    if category is not None:
        existing.category = _normalise_athlete_memory_category(category)
    if evidence is not None:
        existing.evidence = evidence.strip()
    if needs is not None:
        existing.needs = needs.strip()
    if resolution is not None:
        existing.resolution = resolution.strip() or None
    if status is not None:
        existing.status = status
    existing.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return existing


async def delete_athlete_open_question(
    db: AsyncSession, user_id: str, question_id: str
) -> bool:
    """Permanently remove an open question owned by a user."""
    existing = await get_athlete_open_question(db, user_id, question_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# AthleteInquiry
# ---------------------------------------------------------------------------

# Statuses shown by default: inquiries still waiting on the athlete.
_ATHLETE_INQUIRY_OPEN_STATUSES = ("pending",)
# How many times one inquiry may be put to the athlete: the first ask plus a
# single kind rephrasing. After that the coach hands off to Settings rather than
# asking a third time (#506).
ATHLETE_INQUIRY_MAX_ASKS = 2
# Pending inquiries shown at once. The coach asks about a couple of things, not
# a questionnaire — extra candidates wait until these are dealt with.
ATHLETE_INQUIRY_MAX_PENDING = 2
# Confidence given to a fact the athlete stated in answer to a direct question.
# It is first-hand testimony, so it is trusted immediately, unlike an inferred
# observation that has to earn trust through recurrence.
ATHLETE_INQUIRY_ANSWER_CONFIDENCE = 0.9


def _normalise_inquiry_key(question: str) -> str:
    return " ".join(question.casefold().split())[:255]


async def get_athlete_inquiry(
    db: AsyncSession, user_id: str, inquiry_id: str
) -> models.AthleteInquiry | None:
    """Return one inquiry owned by a user."""
    return await db.scalar(
        select(models.AthleteInquiry).where(
            models.AthleteInquiry.id == inquiry_id,
            models.AthleteInquiry.user_id == user_id,
        )
    )


async def list_athlete_inquiries(
    db: AsyncSession,
    user_id: str,
    *,
    include_resolved: bool = False,
) -> list[models.AthleteInquiry]:
    """Return the athlete's inquiries, oldest ask first.

    Pending inquiries are returned in the order they were raised so the chat pins
    the question that has been waiting longest rather than the newest one.
    """
    stmt = select(models.AthleteInquiry).where(
        models.AthleteInquiry.user_id == user_id
    )
    if not include_resolved:
        stmt = stmt.where(
            models.AthleteInquiry.status.in_(_ATHLETE_INQUIRY_OPEN_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthleteInquiry.asked_at,
            models.AthleteInquiry.id,
        )
    )
    return list(result)


async def count_pending_athlete_inquiries(db: AsyncSession, user_id: str) -> int:
    """Return how many inquiries are currently waiting on the athlete."""
    result = await db.scalar(
        select(func.count())
        .select_from(models.AthleteInquiry)
        .where(
            models.AthleteInquiry.user_id == user_id,
            models.AthleteInquiry.status.in_(_ATHLETE_INQUIRY_OPEN_STATUSES),
        )
    )
    return int(result or 0)


async def record_athlete_inquiry(
    db: AsyncSession,
    user_id: str,
    *,
    question: str,
    category: str = "general",
    why_asking: str = "",
    settings_hint: str = "",
    observed_at: datetime | None = None,
) -> models.AthleteInquiry | None:
    """Raise a new inquiry, or return ``None`` when it is already on file.

    Unlike :func:`record_athlete_open_question`, re-raising does **not** accrue
    evidence: asking the athlete the same thing again is nagging, not learning.
    An inquiry the athlete already answered or that was handed off to Settings
    stays closed; only a dismissed one is reopened, since a skip means "not now"
    rather than "never".
    """
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("question must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    question_key = _normalise_inquiry_key(cleaned)

    # Matched on the question alone, deliberately ignoring the category: the model
    # files the same question under a different slug from one run to the next, and
    # "never asked twice" has to survive that. The unique index keeps the stricter
    # (category, question) pair as a backstop.
    existing = await db.scalar(
        select(models.AthleteInquiry).where(
            models.AthleteInquiry.user_id == user_id,
            models.AthleteInquiry.question_key == question_key,
        )
    )
    if existing is not None:
        if existing.status != "dismissed":
            return None
        existing.status = "pending"
        existing.question = cleaned
        existing.ask_count = 1
        existing.follow_up_note = None
        existing.asked_at = now
        existing.updated_at = now
        await db.flush()
        return existing

    inquiry = models.AthleteInquiry(
        user_id=user_id,
        question=cleaned,
        question_key=question_key,
        category=normalized_category,
        why_asking=why_asking.strip(),
        settings_hint=settings_hint.strip(),
        status="pending",
        ask_count=1,
        asked_at=now,
        updated_at=now,
    )
    db.add(inquiry)
    await db.flush()
    return inquiry


async def resolve_athlete_inquiry(
    db: AsyncSession,
    inquiry: models.AthleteInquiry,
    *,
    answer: str,
    now: datetime | None = None,
) -> models.AthleteInquiry:
    """Close an inquiry the athlete's answer resolved."""
    reference = now or datetime.now(timezone.utc)
    inquiry.answer = answer.strip()
    inquiry.status = "answered"
    inquiry.follow_up_note = None
    inquiry.answered_at = reference
    inquiry.updated_at = reference
    await db.flush()
    return inquiry


async def reask_athlete_inquiry(
    db: AsyncSession,
    inquiry: models.AthleteInquiry,
    *,
    answer: str,
    question: str,
    note: str,
    now: datetime | None = None,
) -> models.AthleteInquiry:
    """Put a rephrased version of the inquiry back to the athlete.

    Keeps the answer that missed — partial information is still worth having —
    and leaves ``question_key`` untouched so the rephrasing cannot be raised as a
    second, separate inquiry.
    """
    reference = now or datetime.now(timezone.utc)
    inquiry.answer = answer.strip()
    inquiry.question = question.strip() or inquiry.question
    inquiry.follow_up_note = note.strip() or None
    inquiry.ask_count += 1
    inquiry.asked_at = reference
    inquiry.updated_at = reference
    await db.flush()
    return inquiry


async def hand_off_athlete_inquiry(
    db: AsyncSession,
    inquiry: models.AthleteInquiry,
    *,
    answer: str,
    note: str,
    now: datetime | None = None,
) -> models.AthleteInquiry:
    """Stop asking and point the athlete at Settings instead (#506).

    Reached when the rephrased question still did not land. The inquiry leaves
    the chat pin for good; whatever the athlete did say is kept on the record.
    """
    reference = now or datetime.now(timezone.utc)
    inquiry.answer = answer.strip()
    inquiry.status = "needs_settings"
    inquiry.follow_up_note = note.strip() or None
    inquiry.answered_at = reference
    inquiry.updated_at = reference
    await db.flush()
    return inquiry


async def dismiss_athlete_inquiry(
    db: AsyncSession, user_id: str, inquiry_id: str, *, now: datetime | None = None
) -> models.AthleteInquiry | None:
    """Record that the athlete skipped an inquiry for now."""
    existing = await get_athlete_inquiry(db, user_id, inquiry_id)
    if existing is None:
        return None
    reference = now or datetime.now(timezone.utc)
    existing.status = "dismissed"
    existing.updated_at = reference
    await db.flush()
    return existing


async def delete_athlete_inquiry(
    db: AsyncSession, user_id: str, inquiry_id: str
) -> bool:
    """Permanently remove an inquiry owned by a user."""
    existing = await get_athlete_inquiry(db, user_id, inquiry_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# AthleteExperiment
# ---------------------------------------------------------------------------

# Statuses shown by default: experiments still awaiting the athlete to run them.
# Completed/dismissed experiments are resolved and only surfaced on request.
_ATHLETE_EXPERIMENT_OPEN_STATUSES = ("suggested",)


def _normalise_experiment_key(protocol: str) -> str:
    return " ".join(protocol.casefold().split())[:255]


async def get_athlete_experiment(
    db: AsyncSession, user_id: str, experiment_id: str
) -> models.AthleteExperiment | None:
    """Return one validation experiment owned by a user."""
    return await db.scalar(
        select(models.AthleteExperiment).where(
            models.AthleteExperiment.id == experiment_id,
            models.AthleteExperiment.user_id == user_id,
        )
    )


async def list_athlete_experiments(
    db: AsyncSession,
    user_id: str,
    *,
    include_resolved: bool = False,
) -> list[models.AthleteExperiment]:
    """Return suggested validation experiments for a user, newest first."""
    stmt = select(models.AthleteExperiment).where(
        models.AthleteExperiment.user_id == user_id
    )
    if not include_resolved:
        stmt = stmt.where(
            models.AthleteExperiment.status.in_(_ATHLETE_EXPERIMENT_OPEN_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthleteExperiment.updated_at.desc(),
            models.AthleteExperiment.id.desc(),
        )
    )
    return list(result)


async def suggest_athlete_experiment(
    db: AsyncSession,
    user_id: str,
    *,
    question: str,
    protocol: str,
    rationale: str = "",
    category: str = "general",
    hypothesis_id: str | None = None,
    observed_at: datetime | None = None,
) -> models.AthleteExperiment:
    """Create a validation experiment or refresh an existing one.

    Experiments are keyed on their normalised ``protocol`` so re-proposing the
    same experiment does not create duplicates: a matching row keeps its identity
    while its question/rationale are refreshed, and one the athlete had
    ``dismissed`` is revived to ``suggested`` so a recurring uncertainty resurfaces.
    A ``completed`` experiment is left untouched — the athlete has already run it.
    """
    cleaned_protocol = protocol.strip()
    if not cleaned_protocol:
        raise ValueError("protocol must not be empty")
    cleaned_question = question.strip()
    if not cleaned_question:
        raise ValueError("question must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    protocol_key = _normalise_experiment_key(cleaned_protocol)

    existing = await db.scalar(
        select(models.AthleteExperiment).where(
            models.AthleteExperiment.user_id == user_id,
            models.AthleteExperiment.protocol_key == protocol_key,
        )
    )
    if existing is None:
        existing = models.AthleteExperiment(
            user_id=user_id,
            hypothesis_id=hypothesis_id,
            question=cleaned_question,
            protocol=cleaned_protocol,
            protocol_key=protocol_key,
            rationale=rationale.strip(),
            category=normalized_category,
            status="suggested",
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
    elif existing.status != "completed":
        existing.question = cleaned_question
        if rationale:
            existing.rationale = rationale.strip()
        if hypothesis_id is not None:
            existing.hypothesis_id = hypothesis_id
        if existing.status == "dismissed":
            existing.status = "suggested"
        existing.updated_at = now
    await db.flush()
    return existing


async def update_athlete_experiment(
    db: AsyncSession,
    user_id: str,
    experiment_id: str,
    *,
    question: str | None = None,
    protocol: str | None = None,
    rationale: str | None = None,
    category: str | None = None,
    status: str | None = None,
) -> models.AthleteExperiment | None:
    """Apply an athlete's edit or verdict to a validation experiment and flush."""
    existing = await get_athlete_experiment(db, user_id, experiment_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    if question is not None:
        cleaned = question.strip()
        if not cleaned:
            raise ValueError("question must not be empty")
        existing.question = cleaned
    if protocol is not None:
        cleaned = protocol.strip()
        if not cleaned:
            raise ValueError("protocol must not be empty")
        existing.protocol = cleaned
        existing.protocol_key = _normalise_experiment_key(cleaned)
    if rationale is not None:
        existing.rationale = rationale.strip()
    if category is not None:
        existing.category = _normalise_athlete_memory_category(category)
    if status is not None:
        existing.status = status
    existing.updated_at = now
    await db.flush()
    return existing


async def delete_athlete_experiment(
    db: AsyncSession, user_id: str, experiment_id: str
) -> bool:
    """Permanently remove a validation experiment owned by a user."""
    existing = await get_athlete_experiment(db, user_id, experiment_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# AthletePrediction
# ---------------------------------------------------------------------------

# Statuses shown by default: predictions still awaiting an outcome. Evaluated
# (correct/incorrect) predictions are resolved and only surfaced on request
# (management/export views) or when computing accuracy.
_ATHLETE_PREDICTION_OPEN_STATUSES = ("pending",)
_ATHLETE_PREDICTION_RESOLVED_STATUSES = ("correct", "incorrect")
# How much a single evaluation moves the coach's confidence in a prediction: up
# on a correct call, down on a wrong one (see #383's "Confidence reduced").
ATHLETE_PREDICTION_CONFIDENCE_STEP = 0.2
ATHLETE_PREDICTION_DEFAULT_CONFIDENCE = 0.5


def _normalise_prediction_key(prediction: str) -> str:
    return " ".join(prediction.casefold().split())[:255]


async def get_athlete_prediction(
    db: AsyncSession, user_id: str, prediction_id: str
) -> models.AthletePrediction | None:
    """Return one stored prediction owned by a user."""
    return await db.scalar(
        select(models.AthletePrediction).where(
            models.AthletePrediction.id == prediction_id,
            models.AthletePrediction.user_id == user_id,
        )
    )


async def list_athlete_predictions(
    db: AsyncSession,
    user_id: str,
    *,
    include_resolved: bool = False,
) -> list[models.AthletePrediction]:
    """Return stored predictions for a user, newest first.

    By default only ``pending`` predictions (still awaiting an outcome) are
    returned; pass ``include_resolved`` to also include evaluated ones.
    """
    stmt = select(models.AthletePrediction).where(
        models.AthletePrediction.user_id == user_id
    )
    if not include_resolved:
        stmt = stmt.where(
            models.AthletePrediction.status.in_(_ATHLETE_PREDICTION_OPEN_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthletePrediction.created_at.desc(),
            models.AthletePrediction.id.desc(),
        )
    )
    return list(result)


async def get_athlete_prediction_accuracy(
    db: AsyncSession, user_id: str
) -> tuple[int, int]:
    """Return ``(evaluated, correct)`` counts for a user's predictions.

    This is the raw measure of coaching quality: how many predictions have been
    checked against reality and how many of those turned out right. The caller
    derives the hit-rate (``correct / evaluated``) from these counts.
    """
    rows = await db.execute(
        select(
            models.AthletePrediction.status,
            func.count(),
        )
        .where(
            models.AthletePrediction.user_id == user_id,
            models.AthletePrediction.status.in_(
                _ATHLETE_PREDICTION_RESOLVED_STATUSES
            ),
        )
        .group_by(models.AthletePrediction.status)
    )
    counts = {status: count for status, count in rows.all()}
    correct = counts.get("correct", 0)
    evaluated = correct + counts.get("incorrect", 0)
    return evaluated, correct


async def record_athlete_prediction(
    db: AsyncSession,
    user_id: str,
    *,
    prediction: str,
    expected_outcome: str,
    horizon: str = "",
    category: str = "general",
    confidence: float | None = None,
    observed_at: datetime | None = None,
) -> models.AthletePrediction:
    """Store a new coach prediction or refresh a still-pending duplicate.

    Predictions are keyed on their normalised text so re-recording the same
    claim while it is still ``pending`` refreshes its expected outcome/horizon
    rather than creating a duplicate. A prediction that has already been
    evaluated (``correct``/``incorrect``) is left untouched — it is a historical
    record of a call the coach already got right or wrong.
    """
    cleaned = prediction.strip()
    if not cleaned:
        raise ValueError("prediction must not be empty")
    cleaned_expected = expected_outcome.strip()
    if not cleaned_expected:
        raise ValueError("expected_outcome must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    prediction_key = _normalise_prediction_key(cleaned)
    resolved_confidence = (
        ATHLETE_PREDICTION_DEFAULT_CONFIDENCE
        if confidence is None
        else max(0.0, min(1.0, float(confidence)))
    )

    existing = await db.scalar(
        select(models.AthletePrediction).where(
            models.AthletePrediction.user_id == user_id,
            models.AthletePrediction.prediction_key == prediction_key,
        )
    )
    if existing is None:
        existing = models.AthletePrediction(
            user_id=user_id,
            prediction=cleaned,
            prediction_key=prediction_key,
            expected_outcome=cleaned_expected,
            horizon=horizon.strip(),
            category=normalized_category,
            confidence=resolved_confidence,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
    elif existing.status == "pending":
        existing.expected_outcome = cleaned_expected
        if horizon:
            existing.horizon = horizon.strip()
        existing.category = normalized_category
        existing.updated_at = now
    await db.flush()
    return existing


async def evaluate_athlete_prediction(
    db: AsyncSession,
    user_id: str,
    prediction_id: str,
    *,
    correct: bool,
    actual_outcome: str,
    evaluated_at: datetime | None = None,
) -> models.AthletePrediction | None:
    """Record the observed outcome of a pending prediction and adjust confidence.

    A correct call nudges the prediction's confidence up by
    :data:`ATHLETE_PREDICTION_CONFIDENCE_STEP`; a wrong one reduces it by the
    same step ("Confidence reduced"). Already-evaluated predictions are returned
    unchanged so a re-run never double-counts an outcome.
    """
    existing = await get_athlete_prediction(db, user_id, prediction_id)
    if existing is None:
        return None
    if existing.status != "pending":
        return existing

    now = evaluated_at or datetime.now(timezone.utc)
    existing.actual_outcome = actual_outcome.strip()
    existing.status = "correct" if correct else "incorrect"
    delta = (
        ATHLETE_PREDICTION_CONFIDENCE_STEP
        if correct
        else -ATHLETE_PREDICTION_CONFIDENCE_STEP
    )
    existing.confidence = max(0.0, min(1.0, existing.confidence + delta))
    existing.evaluated_at = now
    existing.updated_at = now
    await db.flush()
    return existing


async def update_athlete_prediction(
    db: AsyncSession,
    user_id: str,
    prediction_id: str,
    *,
    prediction: str | None = None,
    expected_outcome: str | None = None,
    actual_outcome: str | None = None,
    horizon: str | None = None,
    category: str | None = None,
    confidence: float | None = None,
    status: str | None = None,
) -> models.AthletePrediction | None:
    """Apply an athlete's edit or manual verdict to a prediction and flush.

    Marking a still-pending prediction ``correct`` or ``incorrect`` routes
    through :func:`evaluate_athlete_prediction` so the confidence adjustment and
    ``evaluated_at`` stamp match the automated evaluation path.
    """
    existing = await get_athlete_prediction(db, user_id, prediction_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    if prediction is not None:
        cleaned = prediction.strip()
        if not cleaned:
            raise ValueError("prediction must not be empty")
        existing.prediction = cleaned
        existing.prediction_key = _normalise_prediction_key(cleaned)
    if expected_outcome is not None:
        cleaned = expected_outcome.strip()
        if not cleaned:
            raise ValueError("expected_outcome must not be empty")
        existing.expected_outcome = cleaned
    if actual_outcome is not None:
        existing.actual_outcome = actual_outcome.strip()
    if horizon is not None:
        existing.horizon = horizon.strip()
    if category is not None:
        existing.category = _normalise_athlete_memory_category(category)
    if confidence is not None:
        existing.confidence = _clamp_confidence(confidence)

    if (
        status in _ATHLETE_PREDICTION_RESOLVED_STATUSES
        and existing.status == "pending"
    ):
        # Reuse the evaluation path for the confidence delta and timestamp; it
        # requires an outcome, so fall back to whatever the caller supplied.
        return await evaluate_athlete_prediction(
            db,
            user_id,
            prediction_id,
            correct=status == "correct",
            actual_outcome=existing.actual_outcome or "",
            evaluated_at=now,
        )
    if status is not None:
        existing.status = status
    existing.updated_at = now
    await db.flush()
    return existing


async def delete_athlete_prediction(
    db: AsyncSession, user_id: str, prediction_id: str
) -> bool:
    """Permanently remove a stored prediction owned by a user."""
    existing = await get_athlete_prediction(db, user_id, prediction_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


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
    required_workout: dict | None = None,
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
            required_workout=required_workout,
            active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.reason = reason.strip() or existing.reason
        existing.source = source.strip() or existing.source
        existing.expires_on = expires_on or existing.expires_on
        if required_workout is not None:
            existing.required_workout = required_workout
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


async def deactivate_availability_constraints_for_dates(
    db: AsyncSession,
    user_id: str,
    dates: list[str],
) -> list[models.AthleteAvailabilityConstraint]:
    """Mark active constraints on ``dates`` inactive; return the ones lifted.

    Used when the athlete asks to lift a constraint the coach's override note
    flagged (#437). Returns the deactivated rows (before the flush hides them
    from the active list) so the caller can honestly confirm what was lifted.
    """
    if not dates:
        return []
    result = await db.scalars(
        select(models.AthleteAvailabilityConstraint).where(
            models.AthleteAvailabilityConstraint.user_id == user_id,
            models.AthleteAvailabilityConstraint.active.is_(True),
            models.AthleteAvailabilityConstraint.constraint_date.in_(dates),
        )
    )
    lifted = list(result)
    now = datetime.now(timezone.utc)
    for constraint in lifted:
        constraint.active = False
        constraint.updated_at = now
    await db.flush()
    return lifted


async def get_last_flagged_constraint_dates(
    db: AsyncSession, user_id: str
) -> list[str]:
    """Return the constraint dates the most recent override note flagged (#437).

    Resolves a bare "lift that constraint" to the constraint(s) the previous
    assistant reply reported as blocking a coach-requested change. Returns an
    empty list when the last relevant reply flagged nothing.
    """
    # Filter in Python: ``is_not(None)`` on a JSON column matches SQL-NULL rows
    # too (SQLAlchemy JSON-null semantics), so a plain reply after the flagged
    # one would mask it. Scan newest-first and return the first real flag.
    messages = await db.scalars(
        select(models.ChatMessage)
        .where(
            models.ChatMessage.user_id == user_id,
            models.ChatMessage.role == "assistant",
        )
        .order_by(models.ChatMessage.created_at.desc())
        .limit(20)
    )
    for message in messages:
        dates = message.flagged_constraint_dates
        if dates:
            return [str(d) for d in dates]
    return []


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


async def invalidate_login_summary(db: AsyncSession, user_id: str) -> bool:
    """Clear the stored login summary so it is regenerated on next load.

    Returns True when an existing non-empty summary was cleared. Used by the
    summary pipeline when the training plan changes.
    """
    assessment = await get_rider_assessment(db, user_id)
    if assessment is None or assessment.login_summary is None:
        return False
    assessment.login_summary = None
    await db.flush()
    return True


async def set_training_status(
    db: AsyncSession,
    user_id: str,
    *,
    label: str | None,
    tone: str | None,
    rationale: str | None,
) -> models.RiderAssessment | None:
    """Persist the coach-authored dashboard status chip (#499).

    Deliberately *not* routed through :func:`upsert_rider_assessment`, which
    rewrites ``hr_zones``/``ride_insights`` from its arguments — a status-only
    write must not touch the assessment's other fields.
    """
    assessment = await get_rider_assessment(db, user_id)
    if assessment is None:
        return None
    assessment.training_status_label = label
    assessment.training_status_tone = tone
    assessment.training_status_rationale = rationale
    await db.flush()
    return assessment


async def invalidate_training_status(db: AsyncSession, user_id: str) -> bool:
    """Clear the stored status so it regenerates on the next dashboard load.

    Returns True when a stored status was actually cleared. Used by the status
    pipeline when the plan or the athlete's recorded activity changes.
    """
    assessment = await get_rider_assessment(db, user_id)
    if assessment is None or assessment.training_status_label is None:
        return False
    assessment.training_status_label = None
    assessment.training_status_tone = None
    assessment.training_status_rationale = None
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# AthleteMetricSnapshot
# ---------------------------------------------------------------------------


async def create_athlete_metric_snapshot(
    db: AsyncSession,
    user_id: str,
    *,
    ftp: int | None,
    threshold_hr: int | None = None,
    map_5min: int | None = None,
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
        map_5min=map_5min,
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


async def get_latest_map_5min(db: AsyncSession, user_id: str) -> int | None:
    """Return the most recently recorded best 5-minute power, if any.

    Used as the maximal aerobic power reference when sanity-checking a
    user-entered FTP.  Older snapshots predate the column and store ``NULL``,
    so rows without a value are skipped rather than treated as zero.
    """
    return await db.scalar(
        select(models.AthleteMetricSnapshot.map_5min)
        .where(
            models.AthleteMetricSnapshot.user_id == user_id,
            models.AthleteMetricSnapshot.map_5min.isnot(None),
        )
        .order_by(models.AthleteMetricSnapshot.recorded_at.desc())
        .limit(1)
    )


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
    perf_signals: dict | None = None,
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
        perf_signals=perf_signals,
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


async def get_recent_ride_start_locations(
    db: AsyncSession,
    user_id: str,
    limit: int = 120,
) -> list[tuple[float, float]]:
    """Return recent ride start coordinates, newest first (#495).

    The learning substrate for the inferred home location: clustering these is
    robust to travel and one-off rides in a way the single latest GPS ride never
    was (:func:`services.home_location.infer_home_location`).
    """
    rows = await db.execute(
        select(models.RideMetric.start_lat, models.RideMetric.start_lng)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.start_lat.is_not(None),
            models.RideMetric.start_lng.is_not(None),
        )
        .order_by(models.RideMetric.activity_date.desc())
        .limit(limit)
    )
    return [(float(lat), float(lng)) for lat, lng in rows.all()]


async def get_rides_with_weather(
    db: AsyncSession,
    user_id: str,
    limit: int = 200,
) -> list[models.RideMetric]:
    """Return recent rides that carry stored weather, newest first (#495).

    Feeds the weather-preference belief update: the per-ride conditions were
    already persisted at import, so learning tolerances needs no new storage.
    """
    result = await db.scalars(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.weather_temperature_c.is_not(None),
        )
        .order_by(models.RideMetric.activity_date.desc())
        .limit(limit)
    )
    return list(result)


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


async def get_unclassified_intervals_ride_metrics(
    db: AsyncSession,
    user_id: str,
    *,
    since_date: str,
    limit: int = 25,
) -> list[models.RideMetric]:
    """Return recent intervals.icu rides still classified ``unknown`` (or NULL).

    Backs the reclassification backfill: bounded by ``since_date`` and ``limit``
    so it only ever re-fetches a small, recent set of laps from the provider
    (#482).  Rows the provider has already answered 404 for are excluded — their
    id is permanently dead, and without this they would occupy the bounded
    candidate slots on every tick forever (#517).  Newest first.
    """
    result = await db.scalars(
        select(models.RideMetric)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_source == "intervals",
            or_(
                models.RideMetric.ride_purpose == "unknown",
                models.RideMetric.ride_purpose.is_(None),
            ),
            models.RideMetric.activity_date >= since_date,
            models.RideMetric.provider_unfetchable_at.is_(None),
        )
        .order_by(models.RideMetric.activity_date.desc())
        .limit(limit)
    )
    return list(result)


async def update_ride_metric_classification(
    row: models.RideMetric,
    *,
    ride_purpose: str,
    classification_confidence: str | None,
    classification_reason: str | None,
    summary: str | None = None,
) -> None:
    """Overwrite only the auto-classification fields on a RideMetric row.

    Leaves power/TSS/CTL/ATL and every athlete-edited field (label_override,
    coach_note, feel_legs, plan match, …) untouched, so a reclassification never
    clobbers user edits.
    """
    row.ride_purpose = ride_purpose
    row.classification_confidence = classification_confidence
    row.classification_reason = classification_reason
    if summary is not None:
        row.summary = summary


async def mark_ride_metric_unfetchable(row: models.RideMetric) -> None:
    """Record that the provider answered 404 for this row's activity id (#517).

    Touches nothing else: the ride's own metrics stay exactly as imported, only
    the "do not ask the provider about this id again" marker is set.
    """
    row.provider_unfetchable_at = datetime.now(timezone.utc)


async def set_ride_feel_legs(
    db: AsyncSession,
    user_id: str,
    strava_activity_id: int,
    legs: str | None,
    *,
    external_activity_id: str | None = None,
) -> models.RideMetric | None:
    """Set (or clear, when ``legs`` is None) the athlete's leg-freshness rating.

    Unlike :func:`update_ride_metric_notes`, this always assigns the value so a
    dashboard tap can clear a prior rating.  It never touches ``user_note`` or
    any other field, so it cannot clobber notes captured conversationally.
    Returns the updated row, or None if not found.

    ``external_activity_id`` is the precision-safe string identity carried for
    non-Strava rides (e.g. intervals.icu).  Their synthesized 63-bit
    ``strava_activity_id`` is float64-corrupted through the browser, so an exact
    int lookup misses (#441); prefer the string id when the frontend supplies it.
    """
    row = None
    if external_activity_id:
        row = await get_ride_metric_by_external_id(db, user_id, external_activity_id)
    if row is None:
        row = await get_ride_metric_by_strava_id(db, user_id, strava_activity_id)
    if row is None:
        return None
    row.feel_legs = legs
    return row


async def get_ride_metric_by_external_id(
    db: AsyncSession,
    user_id: str,
    external_activity_id: str,
) -> models.RideMetric | None:
    """Return the RideMetric for a provider's external activity id, or None.

    External ids are precision-safe strings (unlike the float64-corrupted
    synthesized ``strava_activity_id`` for non-Strava rides, #441).
    """
    return await db.scalar(
        select(models.RideMetric).where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.external_activity_id == external_activity_id,
        )
    )


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


async def get_recorded_activity_dates(
    db: AsyncSession, user_id: str, dates: list[str]
) -> set[str]:
    """Return the subset of ``dates`` on which the user has a recorded activity.

    Used by the plan pipeline to protect past days the athlete actually trained
    from being dropped by an automated full-plan regenerate, independent of the
    manual ``completed`` flag (which is only set when the athlete ticks a workout
    by hand). Every synced activity — ride, strength, yoga — is a ``RideMetric``,
    so its presence marks the date as historical fact.
    """
    if not dates:
        return set()
    result = await db.scalars(
        select(models.RideMetric.activity_date)
        .where(
            models.RideMetric.user_id == user_id,
            models.RideMetric.activity_date.in_(dates),
        )
        .distinct()
    )
    return set(result)


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
    matched_plan_slot: int | None = None,
    matched_plan_snapshot: dict | None = None,
    matched_at: datetime | None = None,
    label_override: str | None = None,
) -> models.RideMetric:
    """Update the plan-match fields on a RideMetric row and flush.

    ``matched_plan_slot`` names which session on ``matched_plan_date`` the ride
    belongs to (#496); ``None`` is the single-session day.
    """
    ride.plan_match_status = status
    ride.matched_plan_date = matched_plan_date
    ride.matched_plan_slot = matched_plan_slot
    ride.matched_plan_snapshot = matched_plan_snapshot
    ride.matched_at = matched_at
    ride.label_override = label_override
    await db.flush()
    return ride
