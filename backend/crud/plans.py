"""The training plan, the history of its days and the summaries of its changes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import models


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


def locked_training_plan_select(user_id: str):
    """The plan row, locked and read fresh: what a read-merge-write needs (#282).

    ``FOR UPDATE`` makes a second writer of the same plan (the nightly job, a
    chat edit, a sync) wait until this transaction ends instead of merging into
    a snapshot the first one is about to replace. ``populate_existing``
    matters as much: a request that loaded the plan before its model call holds
    that object in the identity map, and a plain select hands back the stale
    copy even after another writer committed. SQLite ignores the lock.

    Without a row yet (the first plan) there is nothing to lock: two first
    writes at once still race on the insert, where the unique ``user_id``
    turns the second into an integrity error rather than a silent overwrite.
    """
    return (
        select(models.TrainingPlan)
        .where(models.TrainingPlan.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def get_training_plan_for_update(
    db: AsyncSession, user_id: str
) -> models.TrainingPlan | None:
    """Return the user's TrainingPlan locked until the transaction ends, or None."""
    return await db.scalar(locked_training_plan_select(user_id))


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

    An optional ``reason`` is the rationale of a guard that made the change
    itself — the interference reverts (#715). It is written with the row rather
    than backfilled, because the guard's reason is a fact it already knows and
    must survive the narrator not running at all.

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
            reason=change.get("reason"),
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
    stmt = select(models.PlanDayHistory).where(models.PlanDayHistory.user_id == user_id)
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

    A row that already carries a reason is left alone. That is a guard stating
    why it corrected the plan (#715) — a deterministic fact about the rule it
    applied — and the narrator's retelling of a *different* change on the same
    date must not overwrite it. ``reasons`` is keyed by date rather than by
    session, so without this a two-a-day whose AM session was narrated would
    replace the rule recorded against its PM session.
    """
    if not reasons:
        return
    rows = await db.scalars(
        select(models.PlanDayHistory).where(models.PlanDayHistory.batch_id == batch_id)
    )
    for row in rows:
        reason = reasons.get(row.date)
        if reason and not row.reason:
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
