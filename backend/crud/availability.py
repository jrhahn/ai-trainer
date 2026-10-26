"""Availability constraints and plan commitments."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import models


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


async def list_active_plan_commitments(
    db: AsyncSession,
    user_id: str,
    *,
    today: str,
) -> list[models.PlanCommitment]:
    """Active commitments whose window has not passed, oldest first (#667).

    Filtered by ``end_date >= today`` rather than relying on the expiry sweep, so
    a commitment stops binding on the right day even if nothing has swept yet.
    """
    result = await db.scalars(
        select(models.PlanCommitment)
        .where(
            models.PlanCommitment.user_id == user_id,
            models.PlanCommitment.active.is_(True),
            models.PlanCommitment.end_date >= today,
        )
        .order_by(
            models.PlanCommitment.start_date.asc(),
            models.PlanCommitment.created_at.asc(),
        )
    )
    return list(result)


async def deactivate_expired_plan_commitments(
    db: AsyncSession,
    user_id: str,
    *,
    today: str,
) -> int:
    """Retire commitments whose window has passed."""
    result = await db.execute(
        update(models.PlanCommitment)
        .where(
            models.PlanCommitment.user_id == user_id,
            models.PlanCommitment.active.is_(True),
            models.PlanCommitment.end_date < today,
        )
        .values(active=False)
    )
    await db.flush()
    return int(result.rowcount or 0)


async def create_plan_commitment(
    db: AsyncSession,
    user_id: str,
    *,
    start_date: str,
    end_date: str,
    text: str,
    source: str = "coach_chat",
) -> models.PlanCommitment:
    """Record an arrangement, superseding any active one it overlaps (#667).

    Superseding rather than accumulating is the point. The athlete agreeing
    something new for a window they already had an arrangement for has *changed
    their mind*, and leaving both rows active would hand every plan writer two
    contradictory instructions with no way to tell which is current.
    """
    await db.execute(
        update(models.PlanCommitment)
        .where(
            models.PlanCommitment.user_id == user_id,
            models.PlanCommitment.active.is_(True),
            models.PlanCommitment.start_date <= end_date,
            models.PlanCommitment.end_date >= start_date,
        )
        .values(active=False)
    )
    commitment = models.PlanCommitment(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        text=text,
        source=source,
    )
    db.add(commitment)
    await db.flush()
    return commitment


async def deactivate_availability_constraint(
    db: AsyncSession, user_id: str, constraint_id: str
) -> bool:
    """Take back one constraint the chat captured (ai-trainer-ops#110).

    False for another athlete's row and for one already lifted or expired.
    """
    row = await db.get(models.AthleteAvailabilityConstraint, constraint_id)
    if row is None or row.user_id != user_id or not row.active:
        return False
    row.active = False
    row.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return True


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
