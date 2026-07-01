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
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
import schemas
from services.dates import app_today_iso
from services.pipeline_graph import graph as pipeline_graph
from services.plan_constraints import (
    filter_plan_updates_for_constraints,
    sanitize_plan_for_constraints,
)
# Importing summary_pipeline registers the downstream "summary" node, so any plan
# write — wherever it originates — invalidates the login summary. The import is
# acyclic: summary_pipeline does not depend on this module.
from services import summary_pipeline  # noqa: F401

logger = logging.getLogger(__name__)

# Register this pipeline as the root "plan" node in the DAG.
pipeline_graph.register("plan")


# Value stamped onto a day the user set directly (manual edit or coach chat).
# A day carrying this source is "pinned": automated triggers must not overwrite it.
USER_SOURCE = "user"


@dataclass(frozen=True)
class PlanSource:
    """Describes the behaviour of one trigger that mutates the plan.

    ``name`` is stamped onto the days this trigger actually changes (so future
    triggers can see who last set a day). ``respect_pins`` is True for automated
    triggers, which must never overwrite a user-pinned, not-yet-completed day.
    """

    name: str
    respect_pins: bool


# Every call site passes one of these keys. Keeping the mapping here (rather than
# a bare string per caller) makes the pin policy for all eight triggers auditable
# in one place. See #342.
PLAN_SOURCES: dict[str, PlanSource] = {
    # User-authored: stamp days "user" (pinning them). They created the intent.
    "user_edit": PlanSource(USER_SOURCE, respect_pins=False),
    "coach_chat": PlanSource(USER_SOURCE, respect_pins=False),
    # User-requested adaptations: authoritative, may reset existing pins. These
    # are explicit user actions ("(re)generate my plan", "recommend my next
    # ride"), so overwriting a prior pin is the expected outcome.
    "generate": PlanSource("generate", respect_pins=False),
    "adapt": PlanSource("adapt", respect_pins=False),
    "next_ride": PlanSource("next_ride", respect_pins=False),
    # Background/automatic: must respect user pins, and are never themselves
    # pinned. These are the triggers that caused the silent revert in #342.
    "auto_adapt": PlanSource("auto_adapt", respect_pins=True),
    "nightly_maintenance": PlanSource("nightly_maintenance", respect_pins=True),
    "ride_review": PlanSource("ride_review", respect_pins=True),
}


def _resolve_source(source: str) -> PlanSource:
    try:
        return PLAN_SOURCES[source]
    except KeyError:
        raise ValueError(
            f"Unknown plan-change source {source!r}; expected one of "
            f"{sorted(PLAN_SOURCES)}"
        ) from None


def _day_content(day: dict) -> dict:
    """A day's content without the ``source`` marker, for change detection.

    ``source`` is metadata about *who* set the day, not part of the workout, so
    it must be excluded when deciding whether a day actually changed (otherwise
    re-stamping would look like a change and cause needless churn).
    """
    return {k: v for k, v in day.items() if k != "source"}


def _protect_user_pinned_days(
    proposed: list[dict], current_plan: list[dict]
) -> list[dict]:
    """Revert automated changes to user-pinned, not-yet-completed days.

    For any day whose *current* value is ``source == "user"`` and not completed,
    an automated proposal that would change its content is dropped in favour of
    the user's version. This is the core fix for the post-ride revert (#342).
    """
    current_by_date = {d["date"]: d for d in current_plan}
    result: list[dict] = []
    for day in proposed:
        current = current_by_date.get(day.get("date"))
        if (
            current is not None
            and current.get("source") == USER_SOURCE
            and not current.get("completed")
            and _day_content(day) != _day_content(current)
        ):
            result.append(current)
        else:
            result.append(day)
    return result


def _stamp_source(
    plan: list[dict], current_plan: list[dict], source: PlanSource
) -> list[dict]:
    """Mark days whose content changed with this trigger's source.

    Unchanged days keep their existing source so the no-op check and the
    concurrent-edit merge stay stable (no spurious writes / summary refreshes).
    """
    current_by_date = {d["date"]: d for d in current_plan}
    stamped: list[dict] = []
    for day in plan:
        current = current_by_date.get(day.get("date"))
        if current is not None and _day_content(day) == _day_content(current):
            preserved = current.get("source")
            new_day = {k: v for k, v in day.items() if k != "source"}
            if preserved is not None:
                new_day["source"] = preserved
            stamped.append(new_day)
        else:
            stamped.append({**day, "source": source.name})
    return stamped


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
    source: PlanSource,
) -> list[dict]:
    enforced = sanitize_plan_for_constraints(proposed_plan, constraints)
    current_row = await crud.get_training_plan(db, user.id)
    current_plan = current_row.plan if current_row is not None else []
    if source.respect_pins:
        enforced = _protect_user_pinned_days(enforced, current_plan)
    merged = merge_preserving_user_edits(base_plan, enforced, current_plan)
    merged = _stamp_source(merged, current_plan, source)
    if current_row is not None and merged == current_plan:
        return current_plan
    await crud.upsert_training_plan(db, user.id, merged)
    # The plan changed: cascade to downstream pipelines (e.g. invalidate the
    # login summary so it is regenerated from the new plan on next load).
    await pipeline_graph.notify_changed("plan", db=db, user=user)
    return merged


async def commit_plan(
    db: AsyncSession,
    user: models.User,
    proposed_plan: list[dict],
    *,
    base_plan: list[dict],
    source: str,
    now=None,
    timezone_name: str | None = None,
) -> list[dict]:
    """Enforce constraints, protect user edits, and persist a full proposed plan.

    ``source`` names the trigger (see ``PLAN_SOURCES``); it decides whether the
    change is pinned as a user edit and whether it must respect existing pins.
    Returns the plan that was actually persisted (or the unchanged current plan
    when the proposal collapses to a no-op).
    """
    resolved = _resolve_source(source)
    today = app_today_iso(now, timezone_name)
    constraints = await load_active_constraints(db, user.id, today=today)
    return await _enforce_and_persist(
        db, user, proposed_plan, base_plan=base_plan, constraints=constraints,
        source=resolved,
    )


async def commit_plan_updates(
    db: AsyncSession,
    user: models.User,
    plan_updates: list[dict] | None,
    *,
    base_plan: list[dict],
    source: str,
    now=None,
    timezone_name: str | None = None,
) -> list[dict]:
    """Apply per-day updates through the same constraint-respecting pipeline.

    Updates that would violate a hard constraint are dropped before they are
    applied; completed days are never touched. ``source`` names the trigger
    (see ``PLAN_SOURCES``).
    """
    resolved = _resolve_source(source)
    today = app_today_iso(now, timezone_name)
    constraints = await load_active_constraints(db, user.id, today=today)
    filtered = filter_plan_updates_for_constraints(plan_updates or [], constraints)
    proposed = apply_plan_updates(base_plan, filtered)
    if proposed is None:
        return base_plan
    return await _enforce_and_persist(
        db, user, proposed, base_plan=base_plan, constraints=constraints,
        source=resolved,
    )
