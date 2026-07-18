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
# Importing these registers the downstream nodes on the "plan" node, so any plan
# write — wherever it originates — fans out to them: summary_pipeline invalidates
# the login summary, ride_match_pipeline refreshes ride↔plan snapshots for the
# changed dates (#364). The assessment_pipeline import registers the "assessment"
# source node that summary_pipeline also depends on, so validate() at startup can
# resolve every edge. Neither module imports this one at load time, so the
# imports stay acyclic (ride_match_pipeline defers its ride_matching import).
from services import assessment_pipeline  # noqa: F401
from services import ride_match_pipeline  # noqa: F401
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

    ``trigger`` is the call-site identifier, recorded verbatim in the plan-day
    history (#343). ``name`` is stamped onto the days this trigger actually
    changes (so future triggers can see who last set a day) and pins the day when
    it equals ``"user"``. ``respect_pins`` is True for automated triggers, which
    must never overwrite a user-pinned, not-yet-completed day.
    """

    trigger: str
    name: str
    respect_pins: bool


# (stamp name, respect_pins) per trigger; the trigger key is injected below so it
# is recorded in history without being duplicated. Keeping this in one place makes
# the pin policy for all eight triggers auditable at a glance. See #342 / #343.
_SOURCE_POLICY: dict[str, tuple[str, bool]] = {
    # User-authored: stamp days "user" (pinning them). They created the intent.
    "user_edit": (USER_SOURCE, False),
    "coach_chat": (USER_SOURCE, False),
    # User-requested, on-demand: authoritative, may reset existing pins. Fired only
    # by an explicit user request for a recommendation ("recommend my next ride").
    "next_ride": ("next_ride", False),
    # Background/automatic: must respect user pins, never themselves pinned. These
    # are the triggers that caused the silent revert in #342. Note generate/adapt live
    # here despite once being labelled "user-requested": they have no manual UI caller —
    # the frontend fires them automatically (adapt on stale-plan dashboard load, generate
    # during activity sync), so treating them as authoritative let an on-load/on-sync
    # write clobber coach-chat/manual pins. See #359.
    "generate": ("generate", True),
    "adapt": ("adapt", True),
    "auto_adapt": ("auto_adapt", True),
    "nightly_maintenance": ("nightly_maintenance", True),
    "ride_review": ("ride_review", True),
}
PLAN_SOURCES: dict[str, PlanSource] = {
    key: PlanSource(trigger=key, name=name, respect_pins=respect)
    for key, (name, respect) in _SOURCE_POLICY.items()
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


def _preserve_completed_days(
    plan: list[dict], current_plan: list[dict]
) -> list[dict]:
    """Restore every completed day from the current plan, unchanged.

    A completed day is historical fact and must never be rewritten or dropped by
    an automated trigger (#345). The per-day update path already skips completed
    days (see ``apply_plan_updates``); this closes the same gap on the full-plan
    path, where the LLM can return a different version of a completed day, or omit
    it entirely (which ``merge_preserving_user_edits`` would then drop). User edits
    are not routed through here, so they can still set/correct completed days.
    """
    completed_by_date = {
        d["date"]: d for d in current_plan if d.get("completed") and d.get("date")
    }
    if not completed_by_date:
        return plan
    result: list[dict] = []
    seen: set[str] = set()
    for day in plan:
        date = day.get("date")
        result.append(completed_by_date[date] if date in completed_by_date else day)
        if date is not None:
            seen.add(date)
    for date, day in completed_by_date.items():
        if date not in seen:
            result.append(day)
    result.sort(key=lambda d: d["date"])
    return result


def _preserve_pinned_days(
    plan: list[dict], current_plan: list[dict]
) -> list[dict]:
    """Restore user-pinned, not-yet-completed days the proposal dropped entirely.

    ``_protect_user_pinned_days`` only guards pinned days the proposal still
    *contains*; a full regenerate can shift the date window and omit a pinned
    future day altogether (#359). This closes that gap on the full-plan path by
    re-appending any ``source == "user"``, not-completed day that ``plan`` lost.
    Completed pinned days are already covered by ``_preserve_completed_days``.
    """
    pinned_by_date = {
        d["date"]: d
        for d in current_plan
        if d.get("source") == USER_SOURCE
        and not d.get("completed")
        and d.get("date")
    }
    if not pinned_by_date:
        return plan
    present = {d.get("date") for d in plan}
    missing = [day for date, day in pinned_by_date.items() if date not in present]
    if not missing:
        return plan
    result = plan + missing
    result.sort(key=lambda d: d["date"])
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
    """Merge per-day ``plan_updates`` into ``plan`` through the canonical model.

    Each changed day is validated as a ``PlanDay``, the update applied via
    ``schemas.merge_update`` (which keeps duration coherent and drops stale
    duration siblings, #422), and dumped back to a canonical camelCase dict.
    Completed days are never modified. Returns ``None`` when there is nothing to
    apply, so callers can cheaply detect a no-op.
    """
    if not plan_updates:
        return None
    updates_by_date = {u["date"]: u for u in plan_updates if u.get("date")}
    if not updates_by_date:
        return None
    result: list[dict] = []
    for day in plan:
        date = day.get("date")
        if date in updates_by_date and not day.get("completed"):
            try:
                merged = schemas.merge_update(
                    schemas.PlanDay.model_validate(day),
                    schemas.PlanDayUpdateSchema.model_validate(updates_by_date[date]),
                )
            except Exception:  # noqa: BLE001 — drop one bad update, keep the day
                logger.warning(
                    "dropping invalid plan update for %s; keeping current day",
                    date, exc_info=True,
                )
                result.append(day)
            else:
                result.append(merged.model_dump(by_alias=True, exclude_none=True))
        else:
            result.append(day)
    return result


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


def _content_differs(a: dict | None, b: dict | None) -> bool:
    """Whether two day dicts differ in workout content (ignoring ``source``).

    One side missing counts as a change (a day added or removed).
    """
    if a is None or b is None:
        return a is not b
    return _day_content(a) != _day_content(b)


def _plan_day_changes(
    current_plan: list[dict],
    merged: list[dict],
    proposal: list[dict],
    source: PlanSource,
) -> list[dict]:
    """Build per-day history records for one plan write (#343).

    Emits an ``applied=True`` record for every date whose content actually
    changed, and — for pin-respecting (automated) triggers — an
    ``applied=False`` record for every existing day the proposal wanted to
    change but that was kept unchanged (blocked by a user pin or a completed
    day). ``source`` re-stamps alone never count as a change.
    """
    current_by = {d["date"]: d for d in current_plan if d.get("date")}
    merged_by = {d["date"]: d for d in merged if d.get("date")}
    changes: list[dict] = []
    for date in sorted(set(current_by) | set(merged_by)):
        cur, new = current_by.get(date), merged_by.get(date)
        if _content_differs(cur, new):
            changes.append(
                {"date": date, "old_day": cur, "new_day": new, "applied": True}
            )
    if source.respect_pins:
        for day in proposal:
            date = day.get("date")
            cur = current_by.get(date) if date else None
            if cur is None:
                continue
            if _content_differs(cur, day) and not _content_differs(
                cur, merged_by.get(date)
            ):
                changes.append(
                    {"date": date, "old_day": cur, "new_day": day, "applied": False}
                )
    return changes


def _to_canonical_day(day: dict) -> dict:
    """Validate + normalize one day through the canonical ``PlanDay`` model.

    This is the persist gate: it guarantees a coherent duration (scalar vs
    min/max window) and, because ``PlanDay`` uses ``extra="allow"``, preserves
    any unmodelled key rather than dropping it. A day that fails validation
    (rare malformed legacy/LLM data) passes through unchanged and is logged,
    so one bad day never aborts a whole plan write (#422 follow-up).
    """
    if not isinstance(day, dict):
        return day
    try:
        return schemas.PlanDay.model_validate(day).model_dump(
            by_alias=True, exclude_none=True, mode="json"
        )
    except Exception:  # noqa: BLE001 — never let one bad day block a write
        logger.warning(
            "plan day failed PlanDay validation; passing through unchanged",
            exc_info=True,
        )
        return day


async def _enforce_and_persist(
    db: AsyncSession,
    user: models.User,
    proposed_plan: list[dict],
    *,
    base_plan: list[dict],
    constraints: list[dict],
    source: PlanSource,
) -> list[dict]:
    # Give every proposed day coherent, canonical fields before anything reads
    # them: the PlanDay gate reconciles the duration scalar vs min/max window and
    # preserves unknown keys (#368, #422).
    proposed_plan = [_to_canonical_day(day) for day in proposed_plan]
    enforced = sanitize_plan_for_constraints(proposed_plan, constraints)
    current_row = await crud.get_training_plan(db, user.id)
    current_plan = current_row.plan if current_row is not None else []
    # The sanitized proposal before edit-protection — what the trigger "wanted",
    # used to log automated changes that a pin/completed-day then blocked.
    proposal = enforced
    if source.respect_pins:
        enforced = _protect_user_pinned_days(enforced, current_plan)
    merged = merge_preserving_user_edits(base_plan, enforced, current_plan)
    if source.respect_pins:
        merged = _preserve_completed_days(merged, current_plan)
        merged = _preserve_pinned_days(merged, current_plan)
    merged = _stamp_source(merged, current_plan, source)
    # Final canonicalization: preserved pins / completed days re-inject *stored*
    # days that bypassed the gate above, so run every day through PlanDay once
    # more before persist. Comparing against the raw ``current_plan`` means a
    # previously-incoherent stored day is rewritten here (lazy backfill) instead
    # of being masked as a no-op.
    merged = [_to_canonical_day(day) for day in merged]
    # Record per-day history (append-only) before the early no-op return so that
    # a fully-blocked automated write still logs its attempted corrections.
    changes = _plan_day_changes(current_plan, merged, proposal, source)
    if changes:
        await crud.record_plan_day_changes(db, user.id, changes, source.trigger)
    if current_row is not None and merged == current_plan:
        return current_plan
    await crud.upsert_training_plan(db, user.id, merged)
    # The plan changed: cascade to downstream pipelines (invalidate the login
    # summary so it regenerates from the new plan, and refresh ride↔plan snapshots
    # for the days whose content actually changed). ``changes`` also carries
    # applied=False (blocked) records, which are not real content changes, so
    # filter to applied ones.
    changed_dates = sorted(
        {c["date"] for c in changes if c.get("applied") and c.get("date")}
    )
    await pipeline_graph.notify_changed(
        "plan", db=db, user=user, dates=changed_dates
    )
    return merged


def _as_dicts(items) -> list[dict]:
    """Coerce a list of typed models (PlanDay / PlanDayUpdateSchema) or dicts to
    plain camelCase dicts, so builders may pass either into the commit API."""
    coerced: list[dict] = []
    for item in items or []:
        if isinstance(item, schemas.CamelModel):
            coerced.append(item.model_dump(by_alias=True, exclude_none=True))
        else:
            coerced.append(item)
    return coerced


async def commit_plan(
    db: AsyncSession,
    user: models.User,
    proposed_plan: "list[schemas.PlanDay | dict]",
    *,
    base_plan: "list[schemas.PlanDay | dict]",
    source: str,
    now=None,
    timezone_name: str | None = None,
) -> list[dict]:
    """Enforce constraints, protect user edits, and persist a full proposed plan.

    ``source`` names the trigger (see ``PLAN_SOURCES``); it decides whether the
    change is pinned as a user edit and whether it must respect existing pins.
    Accepts typed ``PlanDay`` days or raw dicts. Returns the plan that was
    actually persisted (or the unchanged current plan when the proposal collapses
    to a no-op).
    """
    resolved = _resolve_source(source)
    today = app_today_iso(now, timezone_name)
    constraints = await load_active_constraints(db, user.id, today=today)
    return await _enforce_and_persist(
        db, user, _as_dicts(proposed_plan), base_plan=_as_dicts(base_plan),
        constraints=constraints, source=resolved,
    )


async def commit_plan_updates(
    db: AsyncSession,
    user: models.User,
    plan_updates: "list[schemas.PlanDayUpdateSchema | dict] | None",
    *,
    base_plan: "list[schemas.PlanDay | dict]",
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
    base_dicts = _as_dicts(base_plan)
    filtered = filter_plan_updates_for_constraints(_as_dicts(plan_updates), constraints)
    proposed = apply_plan_updates(base_dicts, filtered)
    if proposed is None:
        return base_dicts
    return await _enforce_and_persist(
        db, user, proposed, base_plan=base_dicts, constraints=constraints,
        source=resolved,
    )
