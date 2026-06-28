"""Single constraint-respecting pipeline for all training-plan mutations.

Every code path that changes a user's training plan must route through
``commit_plan`` (a full proposed plan) or ``commit_plan_updates`` (a list of
per-day updates). Centralising the commit half of plan generation guarantees
that, no matter which trigger fired (nightly maintenance, coach chat, ride
review, activity sync, manual edit):

1. hard availability constraints are loaded from the dedicated store,
2. the proposed plan is checked against those constraints,
3. constraints are enforced deterministically (non-negotiable),
4. concurrent user edits are preserved (fixes the reload-revert, #339),
5. the result is persisted exactly once.

The "plan the available days optimally" step (the AI call) stays in the
individual triggers — they have different inputs — but they all hand the
result to this module, which owns enforcement and persistence. See #340.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from services.dates import app_today_iso
from services.plan_constraints import (
    filter_plan_updates_for_constraints,
    sanitize_plan_for_constraints,
)

logger = logging.getLogger(__name__)


def apply_plan_updates(
    plan: list[dict], plan_updates: list[dict] | None
) -> list[dict] | None:
    """Merge per-day ``plan_updates`` into ``plan``.

    Completed days are never modified. Returns ``None`` when there is nothing
    to apply, so callers can cheaply detect a no-op.
    """
    if not plan_updates:
        return None
    updates_by_date = {u["date"]: u for u in plan_updates if u.get("date")}
    if not updates_by_date:
        return None
    return [
        {**day, **{k: v for k, v in updates_by_date[day["date"]].items() if v is not None}}
        if day.get("date") in updates_by_date and not day.get("completed")
        else day
        for day in plan
    ]


def merge_preserving_user_edits(
    base_plan: list[dict],
    proposed_plan: list[dict],
    current_plan: list[dict],
) -> list[dict]:
    """Combine a proposed plan with the current DB plan, protecting user edits.

    ``base_plan`` is the plan the trigger started from; ``proposed_plan`` is the
    (already constraint-enforced) plan it wants to write; ``current_plan`` is a
    fresh read from the database. A day the user changed concurrently — i.e. the
    current DB value differs from ``base_plan`` — is kept as-is and never
    overwritten by the proposal.
    """
    original_by_date = {d["date"]: d for d in base_plan}
    current_by_date = {d["date"]: d for d in current_plan}

    merged: list[dict] = []
    proposed_dates: set[str] = set()
    for day in proposed_plan:
        date = day["date"]
        proposed_dates.add(date)
        original = original_by_date.get(date)
        current = current_by_date.get(date)
        if original is not None and current is not None and original == current:
            # Unchanged since the trigger read it — apply the proposal.
            merged.append(day)
        elif current is not None and original != current:
            # User edited this day concurrently — keep their version.
            merged.append(current)
        else:
            # Proposal added a brand-new date, or user deleted the day — apply.
            merged.append(day)

    for date, current_day in current_by_date.items():
        if date not in proposed_dates:
            original_day = original_by_date.get(date)
            if original_day is None:
                # User added a day the proposal doesn't know about — keep it.
                merged.append(current_day)
            elif current_day != original_day:
                # User edited a day the proposal removed/rescheduled — keep it.
                merged.append(current_day)
            # else: unchanged and dropped by the proposal — let the proposal win.

    merged.sort(key=lambda d: d["date"])
    return merged


async def load_active_constraints(
    db: AsyncSession, user_id: str, *, today: str
) -> list[dict]:
    """Expire stale constraints and return the active hard constraints as dicts."""
    await crud.deactivate_expired_availability_constraints(db, user_id, today=today)
    rows = await crud.list_active_availability_constraints(db, user_id, today=today)
    return [
        schemas.AthleteAvailabilityConstraintSchema.model_validate(
            row, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        for row in rows
    ]


async def _enforce_and_persist(
    db: AsyncSession,
    user: models.User,
    proposed_plan: list[dict],
    *,
    base_plan: list[dict],
    constraints: list[dict],
) -> list[dict]:
    enforced = sanitize_plan_for_constraints(proposed_plan, constraints)
    current_row = await crud.get_training_plan(db, user.id)
    current_plan = current_row.plan if current_row is not None else []
    merged = merge_preserving_user_edits(base_plan, enforced, current_plan)
    if current_row is not None and merged == current_plan:
        return current_plan
    await crud.upsert_training_plan(db, user.id, merged)
    return merged


async def commit_plan(
    db: AsyncSession,
    user: models.User,
    proposed_plan: list[dict],
    *,
    base_plan: list[dict],
    now=None,
    timezone_name: str | None = None,
) -> list[dict]:
    """Enforce constraints, protect user edits, and persist a full proposed plan.

    Returns the plan that was actually persisted (or the unchanged current plan
    when the proposal collapses to a no-op).
    """
    today = app_today_iso(now, timezone_name)
    constraints = await load_active_constraints(db, user.id, today=today)
    return await _enforce_and_persist(
        db, user, proposed_plan, base_plan=base_plan, constraints=constraints
    )


async def commit_plan_updates(
    db: AsyncSession,
    user: models.User,
    plan_updates: list[dict] | None,
    *,
    base_plan: list[dict],
    now=None,
    timezone_name: str | None = None,
) -> list[dict]:
    """Apply per-day updates through the same constraint-respecting pipeline.

    Updates that would violate a hard constraint are dropped before they are
    applied; completed days are never touched.
    """
    today = app_today_iso(now, timezone_name)
    constraints = await load_active_constraints(db, user.id, today=today)
    filtered = filter_plan_updates_for_constraints(plan_updates or [], constraints)
    proposed = apply_plan_updates(base_plan, filtered)
    if proposed is None:
        return base_plan
    return await _enforce_and_persist(
        db, user, proposed, base_plan=base_plan, constraints=constraints
    )
