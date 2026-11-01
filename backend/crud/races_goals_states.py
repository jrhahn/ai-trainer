"""Race events, goals, the athlete's states such as an illness, week goals."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from domain import athlete_states


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
# Measurable goals (ai-trainer-ops#65)
# ---------------------------------------------------------------------------


async def list_active_goals(db: AsyncSession, user_id: str) -> list[models.AthleteGoal]:
    result = await db.execute(
        select(models.AthleteGoal)
        .where(
            models.AthleteGoal.user_id == user_id,
            models.AthleteGoal.status == "active",
        )
        .order_by(models.AthleteGoal.created_at)
    )
    return list(result.scalars().all())


async def remove_goal(
    db: AsyncSession, user_id: str, goal_id: str
) -> models.AthleteGoal | None:
    """Mark the athlete's goal ``removed``; ``None`` if it is not theirs (ops#65).

    Kept as a row, like a replaced goal, so what was said stays traceable.
    """
    goal = await db.scalar(
        select(models.AthleteGoal).where(
            models.AthleteGoal.id == goal_id,
            models.AthleteGoal.user_id == user_id,
            # Only the goal in force: an achieved or replaced one is history.
            models.AthleteGoal.status == "active",
        )
    )
    if goal is None:
        return None
    goal.status = "removed"
    goal.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return goal


async def upsert_active_goal(
    db: AsyncSession,
    user_id: str,
    *,
    metric: str,
    scope: str,
    target: float,
    statement: str = "",
    deadline: str | None = None,
) -> models.AthleteGoal:
    """Set the athlete's goal for *metric* and *scope*.

    The previous active goal for the same measure is kept as ``replaced`` rather
    than overwritten, so a target that changed can still be traced to what the
    athlete said before.
    """
    existing = await db.scalar(
        select(models.AthleteGoal).where(
            models.AthleteGoal.user_id == user_id,
            models.AthleteGoal.metric == metric,
            models.AthleteGoal.scope == scope,
            models.AthleteGoal.status == "active",
        )
    )
    now = datetime.now(timezone.utc)
    if existing is not None:
        if existing.target == target and existing.deadline == deadline:
            return existing
        existing.status = "replaced"
        existing.updated_at = now
        # Before the insert: the partial unique index allows one active row.
        await db.flush()
    goal = models.AthleteGoal(
        user_id=user_id,
        metric=metric,
        scope=scope,
        target=target,
        statement=statement,
        deadline=deadline,
        created_at=now,
        updated_at=now,
    )
    db.add(goal)
    await db.flush()
    return goal


# ---------------------------------------------------------------------------
# Athlete states said in chat (ai-trainer-ops#18, #39 Q1)
# ---------------------------------------------------------------------------


def _state_dates(row: models.AthleteState) -> tuple[date, date | None]:
    return (
        date.fromisoformat(row.starts_on),
        date.fromisoformat(row.ends_on) if row.ends_on else None,
    )


def _stated_state(row: models.AthleteState) -> athlete_states.StatedState:
    return athlete_states.StatedState(
        row.kind,
        *_state_dates(row),
        date.fromisoformat(row.said_on) if row.said_on else None,
    )


async def _relevant_state_rows(
    db: AsyncSession, user_id: str, today: date
) -> list[models.AthleteState]:
    rows = await db.scalars(
        select(models.AthleteState)
        .where(models.AthleteState.user_id == user_id)
        .order_by(models.AthleteState.starts_on)
    )
    return [
        row for row in rows if athlete_states.still_relevant(_stated_state(row), today)
    ]


async def list_relevant_states(
    db: AsyncSession, user_id: str, today: date
) -> list[athlete_states.StatedState]:
    """The athlete's states that still say something about the plan from *today*."""
    return [
        _stated_state(row) for row in await _relevant_state_rows(db, user_id, today)
    ]


async def record_state(
    db: AsyncSession, user_id: str, state: athlete_states.StatedState, today: date
) -> models.AthleteState:
    """Store a stated state. A second "I'm ill" updates the illness in force, and
    a second "still tired" the tiredness."""
    now = datetime.now(timezone.utc)
    for row in await _relevant_state_rows(db, user_id, today):
        same_illness = athlete_states.is_ill(row.kind) and athlete_states.is_ill(
            state.kind
        )
        if row.kind != state.kind and not same_illness:
            continue
        if same_illness:
            # A fever said later makes the illness in force one with fever; a
            # plain "still ill" does not take the fever back.
            if state.kind == athlete_states.KIND_FEVER:
                row.kind = athlete_states.KIND_FEVER
            if state.ends_on is not None:
                row.ends_on = state.ends_on.isoformat()
            elif row.ends_on is not None and date.fromisoformat(row.ends_on) < today:
                # Ill again after saying they were well: the end no longer holds.
                row.ends_on = None
            # Still ill: an open illness expires from the last time it was said.
            row.said_on = today.isoformat()
            row.updated_at = now
            await db.flush()
            return row
        if state.kind == athlete_states.KIND_CLEARANCE:
            # A warning sign said again: the wait for the doctor runs from now,
            # also after an all-clear earlier the same day (review of #1253).
            row.ends_on = None
            row.said_on = today.isoformat()
            row.updated_at = now
            await db.flush()
            return row
        if state.kind == athlete_states.KIND_TIRED:
            # Still tired: the easy days count from the last time it was said.
            # ``starts_on`` keeps the first mention; the prior's window starts
            # at today anyway, and the states block reads ``said_on``.
            row.ends_on = max(row.ends_on or "", state.ends_on.isoformat())
            row.said_on = today.isoformat()
            row.updated_at = now
            await db.flush()
            return row
        if row.starts_on == state.starts_on.isoformat():
            return row
    row = models.AthleteState(
        user_id=user_id,
        kind=state.kind,
        starts_on=state.starts_on.isoformat(),
        ends_on=state.ends_on.isoformat() if state.ends_on else None,
        said_on=today.isoformat(),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    return row


async def end_illness(
    db: AsyncSession, user_id: str, today: date
) -> models.AthleteState | None:
    """The athlete says they are well: the illness in force ended yesterday."""
    for row in await _relevant_state_rows(db, user_id, today):
        if not athlete_states.is_ill(row.kind):
            continue
        if athlete_states.effective_end(_stated_state(row)) < today:
            continue
        starts_on, _ = _state_dates(row)
        row.ends_on = max(starts_on, today - timedelta(days=1)).isoformat()
        row.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return row
    return None


async def end_clearance(
    db: AsyncSession, user_id: str, today: date
) -> models.AthleteState | None:
    """The athlete says a doctor has checked the warning sign: it held until yesterday."""
    for row in await _relevant_state_rows(db, user_id, today):
        if row.kind != athlete_states.KIND_CLEARANCE:
            continue
        if athlete_states.effective_end(_stated_state(row)) < today:
            continue
        starts_on, _ = _state_dates(row)
        row.ends_on = max(starts_on, today - timedelta(days=1)).isoformat()
        row.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return row
    return None


async def retract_illness(db: AsyncSession, user_id: str, today: date) -> str | None:
    """The athlete takes the illness in force back: it never was (ai-trainer-ops#115).

    Deleted rather than ended: an ended illness keeps the easy days of a return,
    and there is nothing to return from. Returns the deleted row's id.
    """
    for row in await _relevant_state_rows(db, user_id, today):
        if not athlete_states.is_ill(row.kind):
            continue
        if athlete_states.effective_end(_stated_state(row)) < today:
            continue
        state_id = row.id
        await db.delete(row)
        await db.flush()
        return state_id
    return None


async def delete_state(db: AsyncSession, user_id: str, state_id: str) -> bool:
    """Delete a state the athlete took back. Deleted, not flagged: it is health data."""
    row = await db.scalar(
        select(models.AthleteState).where(
            models.AthleteState.id == state_id,
            models.AthleteState.user_id == user_id,
        )
    )
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    return True


async def get_week_goals(
    db: AsyncSession, user_id: str, week_starts: Sequence[str]
) -> dict[str, models.WeekGoal]:
    """The stored season-plan goals of these weeks, by ISO Monday (ai-trainer-ops#98)."""
    if not week_starts:
        return {}
    rows = await db.scalars(
        select(models.WeekGoal).where(
            models.WeekGoal.user_id == user_id,
            models.WeekGoal.week_start.in_(list(week_starts)),
        )
    )
    return {row.week_start: row for row in rows}


async def save_week_goal(
    db: AsyncSession, user_id: str, week_start: str, *, fingerprint: str, goal: str
) -> models.WeekGoal:
    """Store a week's goal. A new week counts as fitted to the days as they are:
    the plan writers were handed its targets when they wrote them. A rewritten
    goal keeps the old ``fitted_fingerprint``, so the nightly run sees the move.
    """
    now = datetime.now(timezone.utc)
    row = (await get_week_goals(db, user_id, [week_start])).get(week_start)
    if row is None:
        row = models.WeekGoal(
            user_id=user_id,
            week_start=week_start,
            fitted_fingerprint=fingerprint,
            created_at=now,
        )
        db.add(row)
    row.fingerprint = fingerprint
    row.goal = goal
    row.updated_at = now
    await db.flush()
    return row
