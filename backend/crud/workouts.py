"""Logged workouts and their strength sets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from domain import strength_model


# ---------------------------------------------------------------------------
# WorkoutLog
# ---------------------------------------------------------------------------


async def get_workout_logs(db: AsyncSession, user_id: str) -> list[models.WorkoutLog]:
    """Return all WorkoutLog rows for a user."""
    result = await db.scalars(
        select(models.WorkoutLog).where(models.WorkoutLog.user_id == user_id)
    )
    return list(result)


async def delete_workout_log(
    db: AsyncSession, user_id: str, date: str, slot: int
) -> bool:
    """Delete one session's log and its strength sets; report whether it existed."""
    log = await get_workout_log_by_date(db, user_id, date, slot)
    if log is None:
        return False
    await db.execute(
        delete(models.StrengthSet).where(
            models.StrengthSet.user_id == user_id,
            models.StrengthSet.date == date,
            models.StrengthSet.slot == slot,
        )
    )
    await db.delete(log)
    await db.flush()
    return True


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


# "Say nothing about this field", as distinct from "set it to None". Only
# ``distance_km`` needs it today; see :func:`upsert_workout_log`.
_KEEP: Any = object()


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
    distance_km: Any = _KEEP,
    normalized_power: Any = _KEEP,
) -> models.WorkoutLog:
    """Create or update a WorkoutLog for one user/date/session and flush (#496).

    Every other field here is a straight replace, which is right: the callers
    that pass them always know all of them. ``distance_km`` is not, and defaults
    to the ``_KEEP`` sentinel instead of to ``None`` — three of the four callers
    predate the column and say nothing about distance, and with a ``None``
    default each of them would silently blank a distance the athlete had entered.
    That is the same shape as the heart-rate loss review found on PR #795, so it
    is closed here rather than left to depend on planned and unplanned slots
    never colliding.

    ``None`` passed explicitly still clears it, which is what an athlete emptying
    the field means. ``normalized_power`` follows the same rule (#169): the FIT
    and import paths that write a log know nothing about it.
    """
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
            distance_km=None if distance_km is _KEEP else distance_km,
            normalized_power=None if normalized_power is _KEEP else normalized_power,
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
        if distance_km is not _KEEP:
            existing.distance_km = distance_km
        if normalized_power is not _KEEP:
            existing.normalized_power = normalized_power
    await db.flush()
    return existing


# ---------------------------------------------------------------------------
# StrengthSet (#714)
# ---------------------------------------------------------------------------


async def get_strength_sets(
    db: AsyncSession, user_id: str, date: str, slot: int = 0
) -> list[models.StrengthSet]:
    """The sets logged for one gym session, in the order they were performed."""
    result = await db.scalars(
        select(models.StrengthSet)
        .where(
            models.StrengthSet.user_id == user_id,
            models.StrengthSet.date == date,
            models.StrengthSet.slot == slot,
        )
        .order_by(models.StrengthSet.exercise, models.StrengthSet.set_index)
    )
    return list(result)


async def replace_strength_sets(
    db: AsyncSession,
    user_id: str,
    date: str,
    slot: int,
    sets: Sequence[strength_model.StrengthSet],
) -> list[models.StrengthSet]:
    """Make the stored session exactly *sets* and return the stored rows.

    Replace rather than upsert-in-place. A re-log is the athlete correcting the
    session — dropping a set they did not do, reordering two exercises — and an
    upsert keyed on ``set_index`` would leave the removed tail behind, so the
    session would silently grow every time it was edited down. Deleting first
    makes the stored session equal to what was submitted, which is what the
    athlete means by saving the form.

    The sets are grouped per exercise and numbered from 1 in submission order, so
    ``set_index`` states the order they were performed in rather than a position
    in whatever order the client happened to send.
    """
    await db.execute(
        delete(models.StrengthSet).where(
            models.StrengthSet.user_id == user_id,
            models.StrengthSet.date == date,
            models.StrengthSet.slot == slot,
        )
    )

    seen_per_exercise: dict[str, int] = {}
    rows: list[models.StrengthSet] = []
    for one_set in sets:
        index = seen_per_exercise.get(one_set.exercise, 0) + 1
        seen_per_exercise[one_set.exercise] = index
        row = models.StrengthSet(
            user_id=user_id,
            date=date,
            slot=slot,
            exercise=one_set.exercise,
            set_index=index,
            reps=one_set.reps,
            weight_kg=one_set.weight_kg,
            rir=one_set.rir,
            rpe=one_set.rpe,
        )
        db.add(row)
        rows.append(row)

    await db.flush()
    return rows


async def get_strength_sets_for_exercise(
    db: AsyncSession,
    user_id: str,
    exercise: str,
    *,
    limit_days: int = 365,
) -> list[models.StrengthSet]:
    """Every set of one exercise, oldest first — the input to an e1RM trend.

    Returns the *sets*, not an e1RM series, because the formula choice belongs to
    the caller and ``domain.strength_model`` owns the arithmetic. A crud
    function that returned estimates would be a second place the formula lives.
    """
    cutoff = (
        (datetime.now(timezone.utc) - timedelta(days=limit_days)).date().isoformat()
    )
    result = await db.scalars(
        select(models.StrengthSet)
        .where(
            models.StrengthSet.user_id == user_id,
            models.StrengthSet.exercise == exercise,
            models.StrengthSet.date >= cutoff,
        )
        .order_by(models.StrengthSet.date, models.StrengthSet.set_index)
    )
    return list(result)


async def get_logged_exercises(db: AsyncSession, user_id: str) -> list[str]:
    """Which exercises this athlete has ever logged, alphabetically.

    So the trend endpoint and the log form can offer the athlete's own lifts
    rather than a hardcoded list of exercises someone else thought they should
    be doing.
    """
    result = await db.scalars(
        select(models.StrengthSet.exercise)
        .where(models.StrengthSet.user_id == user_id)
        .distinct()
        .order_by(models.StrengthSet.exercise)
    )
    return list(result)
