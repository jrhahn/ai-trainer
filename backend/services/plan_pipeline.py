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
from services import plan_coherence
from services import plan_commitments
from services.dates import app_today_iso
from services.pipeline_graph import graph as pipeline_graph
from services.plan_constraints import (
    day_violates_constraint,
    filter_plan_updates_for_constraints,
    sanitize_plan_for_constraints,
)
# Importing these registers the downstream nodes on the "plan" node, so any plan
# write — wherever it originates — fans out to them: summary_pipeline invalidates
# the login summary, ride_match_pipeline refreshes ride↔plan snapshots for the
# changed dates (#364), status_pipeline invalidates the dashboard status badge
# (#499). The assessment_pipeline import registers the "assessment" source node
# that summary_pipeline also depends on, so validate() at startup can resolve
# every edge. Neither module imports this one at load time, so the imports stay
# acyclic (ride_match_pipeline defers its ride_matching import, and
# status_pipeline defers its ai_service import).
from services import assessment_pipeline  # noqa: F401
from services import ride_match_pipeline  # noqa: F401
from services import status_pipeline  # noqa: F401
from services import summary_pipeline  # noqa: F401

logger = logging.getLogger(__name__)

# Register this pipeline as the root "plan" node in the DAG.
pipeline_graph.register("plan")


# Value stamped onto a day the user set directly (manual edit or coach chat).
# A day carrying this source is "pinned": automated triggers must not overwrite it.
USER_SOURCE = "user"


class PlanCommitResult(list):
    """The persisted plan, augmented with the run's batch metadata (#439).

    Subclasses ``list`` (of plan-day dicts) so every existing caller that treats
    a commit's result as the plan keeps working unchanged, while narrated
    triggers additionally read ``batch_id`` and ``applied_changes`` to explain
    the run in one chat message. ``batch_id`` identifies the ``PlanDayHistory``
    rows written by this commit (``None`` when nothing was recorded);
    ``applied_changes`` is the ``applied=True`` subset as
    ``{"date", "old_day", "new_day"}`` dicts — what actually changed for the
    athlete. ``plan`` is an explicit alias for the list payload.
    """

    def __init__(
        self,
        plan: list[dict],
        batch_id: str | None,
        applied_changes: list[dict],
    ) -> None:
        super().__init__(plan)
        self.batch_id = batch_id
        self.applied_changes = applied_changes

    @property
    def plan(self) -> list[dict]:
        return list(self)


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
# the pin policy for every trigger auditable at a glance. See #342 / #343.
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
    # Activity sync marking an auto-matched day completed. Respects pins like any
    # automated trigger, so it never touches a user-owned day.
    "activity_import": ("activity_import", True),
    # The athlete resolving an ambiguous ride↔session match, which marks the session
    # they named completed (#574). Athlete-initiated, but it only ever sets that one
    # flag — so it respects pins rather than claiming the day, and reads back in the
    # change history as itself instead of hiding behind "activity_import".
    "manual_match": ("manual_match", True),
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


# A plan session's identity, ``(date, slot)`` — date alone stopped being unique
# when a day gained the ability to hold two-a-days (#496). Every map, merge and
# sort in this module keys on this, so a pin protects one *session* and the
# morning yoga can be completed while the evening ride stays pending.
_key = schemas.session_key


def _sorted_by_session(plan: list[dict]) -> list[dict]:
    """Plan ordered by ``(date, slot)`` — chronological, AM before PM."""
    return sorted(plan, key=_key)


def _day_content(day: dict) -> dict:
    """A day's content without the ``source`` / ``slot`` markers, for change detection.

    ``source`` is metadata about *who* set the day, not part of the workout, so
    it must be excluded when deciding whether a day actually changed (otherwise
    re-stamping would look like a change and cause needless churn). ``slot`` is
    excluded for a stronger reason: it is half the session *key*, so two days
    being compared already share it — and leaving it in would make the canonical
    gate's newly-emitted ``slot: 0`` look like a content change on every legacy
    day the first time #496 ships.
    """
    return {k: v for k, v in day.items() if k not in ("source", "slot")}


def _protect_user_pinned_days(
    proposed: list[dict], current_plan: list[dict]
) -> list[dict]:
    """Revert automated changes to user-pinned, not-yet-completed sessions.

    For any session whose *current* value is ``source == "user"`` and not completed,
    an automated proposal that would change its content is dropped in favour of
    the user's version. This is the core fix for the post-ride revert (#342).
    """
    current_by_date = {_key(d): d for d in current_plan}
    result: list[dict] = []
    for day in proposed:
        current = current_by_date.get(_key(day))
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
    """Restore every completed session from the current plan, unchanged.

    A completed session is historical fact and must never be rewritten or dropped
    by an automated trigger (#345). The per-day update path already skips completed
    sessions (see ``apply_plan_updates``); this closes the same gap on the full-plan
    path, where the LLM can return a different version of a completed session, or omit
    it entirely (which ``merge_preserving_user_edits`` would then drop). User edits
    are not routed through here, so they can still set/correct completed sessions.

    Keyed per session, so ticking the morning yoga freezes *that* session while the
    evening ride stays open to automated adjustment (#496).
    """
    completed_by_date = {
        _key(d): d for d in current_plan if d.get("completed") and d.get("date")
    }
    if not completed_by_date:
        return plan
    result: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for day in plan:
        key = _key(day)
        result.append(completed_by_date[key] if key in completed_by_date else day)
        if day.get("date") is not None:
            seen.add(key)
    for key, day in completed_by_date.items():
        if key not in seen:
            result.append(day)
    return _sorted_by_session(result)


def _preserve_pinned_days(
    plan: list[dict], current_plan: list[dict]
) -> list[dict]:
    """Restore user-pinned, not-yet-completed sessions the proposal dropped entirely.

    ``_protect_user_pinned_days`` only guards pinned sessions the proposal still
    *contains*; a full regenerate can shift the date window and omit a pinned
    future session altogether (#359). This closes that gap on the full-plan path by
    re-appending any ``source == "user"``, not-completed session that ``plan`` lost.
    Completed pinned sessions are already covered by ``_preserve_completed_days``.
    """
    pinned_by_date = {
        _key(d): d
        for d in current_plan
        if d.get("source") == USER_SOURCE
        and not d.get("completed")
        and d.get("date")
    }
    if not pinned_by_date:
        return plan
    present = {_key(d) for d in plan if d.get("date") is not None}
    missing = [day for key, day in pinned_by_date.items() if key not in present]
    if not missing:
        return plan
    return _sorted_by_session(plan + missing)


def _preserve_activity_days(
    plan: list[dict], current_plan: list[dict], activity_dates: set[str]
) -> list[dict]:
    """Restore current-plan days the athlete actually trained, unchanged.

    ``_preserve_completed_days`` only guards days carrying the ``completed`` flag,
    but that flag is set solely when the athlete ticks a workout by hand — a synced
    activity never sets it. So a day the athlete rode but never ticked is protected
    by neither the pin guard nor the completed guard, and a full regenerate whose
    window starts today drops yesterday's ridden day entirely. The ride↔plan
    snapshot then has nothing to point at and the dashboard shows "No planned
    workout found" for a completed ride. Keying on recorded activity dates closes
    that gap without depending on the manual flag; the matched day's *workout* already
    belongs to the completed ride and must not be rewritten or dropped by an automated
    trigger (mirrors ``_preserve_completed_days``).

    A protected day's ``completed`` / ``feedback`` markers are the one legitimate
    post-activity change, so an update that sets them is carried through onto the
    preserved workout — otherwise this guard would revert the very completion that
    activity sync marks on the matched day (the ``activity_import`` trigger).

    ``activity_dates`` is a set of *dates* (an activity is recorded against a date,
    not a slot), so every session on a trained date is protected — the athlete may
    have ridden either of a two-a-day's sessions, and neither should be dropped.
    """
    return _preserve_dated_workouts(plan, current_plan, activity_dates)


def _preserve_dated_workouts(
    plan: list[dict], current_plan: list[dict], dates: set[str]
) -> list[dict]:
    """Freeze the workouts on *dates* against an automated rewrite.

    The shared body of :func:`_preserve_activity_days` and
    :func:`_preserve_today`: restore each protected session's workout from the
    current plan, re-append it if the proposal dropped it, and let completion
    markers through (see :func:`_preserve_workout_carry_completion`).
    """
    protected = {
        _key(d): d
        for d in current_plan
        if d.get("date") and d["date"] in dates
    }
    if not protected:
        return plan
    result: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for day in plan:
        key = _key(day)
        result.append(_preserve_workout_carry_completion(protected[key], day)
                      if key in protected else day)
        if day.get("date") is not None:
            seen.add(key)
    for key, day in protected.items():
        if key not in seen:
            result.append(day)
    return _sorted_by_session(result)


def _preserve_today(
    plan: list[dict],
    current_plan: list[dict],
    today: str,
    constraints: list[dict],
) -> list[dict]:
    """Freeze today's sessions against unattended automated rewrites (#651).

    The existing guards protect days that are pinned, completed, or already
    ridden. Today at 02:00 is none of those by definition, so it was the one day
    maximally exposed to the trigger the athlete can least see coming: the
    nightly job rewrote a Saturday long ride into a 45-minute recovery spin at
    02:00 that same Saturday, hours after the coach had confirmed the ride and
    hours before the athlete got up.

    A plan the athlete read last night and shaped their morning around must not
    change under them while they sleep. Only ``respect_pins`` (automated)
    triggers are held back — the athlete asking the coach to change today still
    works, because ``coach_chat`` and ``user_edit`` do not respect pins.

    An active hard constraint still wins: if the athlete is unavailable today,
    the constraint sanitizer's rest day is the correct outcome and restoring the
    session would reschedule training they already said they cannot do.
    """
    unconstrained = {
        d["date"]
        for d in current_plan
        if d.get("date") == today and not day_violates_constraint(d, constraints)
    }
    return _preserve_dated_workouts(plan, current_plan, unconstrained)


def _preserve_committed_days(
    plan: list[dict],
    current_plan: list[dict],
    commitments: list[dict],
    constraints: list[dict],
) -> list[dict]:
    """Hold automated writes out of a window the athlete agreed to (#667).

    Every guard above protects a day *somebody touched*: pinned, completed,
    ridden, today. An arrangement covers days nobody touched — the coach set
    Wednesday to strength precisely so that Thursday could hold the intervals,
    and Thursday, unclaimed, was rewritten into a second gym day. The pin worked
    exactly as designed and the athlete still lost the arrangement.

    Only ``respect_pins`` (automated) triggers are held back, like
    :func:`_preserve_today`: the athlete asking the coach to change a committed
    day still works, and the coach replaces the commitment when it does.

    An active hard constraint still wins. If the athlete has since said they are
    unavailable, the sanitizer's rest day is correct and restoring the agreed
    session would reschedule training they already ruled out.
    """
    if not commitments:
        return plan
    committed = plan_commitments.committed_dates(commitments)
    if not committed:
        return plan
    protected = {
        d["date"]
        for d in current_plan
        if d.get("date") in committed and not day_violates_constraint(d, constraints)
    }
    return _preserve_dated_workouts(plan, current_plan, protected)


def _preserve_workout_carry_completion(current: dict, proposed: dict) -> dict:
    """Keep ``current``'s workout but adopt ``proposed``'s completion markers.

    Freezes the trained day's workout content against an automated rewrite while
    letting a completion/feedback update land — the only change that legitimately
    happens to a day after the athlete trained it.
    """
    restored = dict(current)
    for marker in ("completed", "feedback"):
        if proposed.get(marker) is not None:
            restored[marker] = proposed[marker]
    return restored


def _stamp_source(
    plan: list[dict], current_plan: list[dict], source: PlanSource
) -> list[dict]:
    """Mark days whose content changed with this trigger's source.

    Unchanged days keep their existing source so the no-op check and the
    concurrent-edit merge stay stable (no spurious writes / summary refreshes).
    """
    current_by_date = {_key(d): d for d in current_plan}
    stamped: list[dict] = []
    for day in plan:
        current = current_by_date.get(_key(day))
        if current is not None and _day_content(day) == _day_content(current):
            preserved = current.get("source")
            new_day = {k: v for k, v in day.items() if k != "source"}
            if preserved is not None:
                new_day["source"] = preserved
            stamped.append(new_day)
        else:
            stamped.append({**day, "source": source.name})
    return stamped


def _default_slot_for_date(plan: list[dict], date: str) -> int:
    """The slot a slot-less update for ``date`` targets: the day's first session.

    Every caller that predates two-a-days (and every single-session day) resolves
    to the legacy slot 0 through this. Picking the *lowest existing* slot rather
    than a hardcoded 0 means an update still lands when a day's only session sits
    at a higher slot — e.g. after the morning session was deleted (#496).
    """
    slots = [
        schemas.day_slot(d) for d in plan if str(d.get("date") or "") == date
    ]
    return min(slots) if slots else 0


def _updates_by_session(
    plan: list[dict], plan_updates: list[dict]
) -> dict[tuple[str, int], dict]:
    """Key ``plan_updates`` by the session ``(date, slot)`` each one targets."""
    keyed: dict[tuple[str, int], dict] = {}
    for update in plan_updates:
        date = update.get("date")
        if not date:
            continue
        date = str(date)
        slot = update.get("slot")
        resolved = (
            schemas.day_slot(update)
            if slot is not None
            else _default_slot_for_date(plan, date)
        )
        keyed[(date, resolved)] = update
    return keyed


def apply_plan_updates(
    plan: list[dict], plan_updates: list[dict] | None
) -> list[dict] | None:
    """Merge per-session ``plan_updates`` into ``plan`` through the canonical model.

    Each changed session is validated as a ``PlanDay``, the update applied via
    ``schemas.merge_update`` (which keeps duration coherent and drops stale
    duration siblings, #422), and dumped back to a canonical camelCase dict.
    Completed sessions are never modified. Returns ``None`` when there is nothing
    to apply, so callers can cheaply detect a no-op.

    An update carrying a ``slot`` targets exactly that session; one without a slot
    targets the date's first session, which is how every pre-#496 caller keeps
    behaving unchanged on a single-session day.
    """
    if not plan_updates:
        return None
    updates_by_date = _updates_by_session(plan, plan_updates)
    if not updates_by_date:
        return None
    result: list[dict] = []
    for day in plan:
        key = schemas.session_key(day)
        if key in updates_by_date and not day.get("completed"):
            try:
                merged = schemas.merge_update(
                    schemas.PlanDay.model_validate(day),
                    schemas.PlanDayUpdateSchema.model_validate(updates_by_date[key]),
                )
            except Exception:  # noqa: BLE001 — drop one bad update, keep the day
                logger.warning(
                    "dropping invalid plan update for %s; keeping current day",
                    key, exc_info=True,
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
    fresh read from the database. A session the user changed concurrently — i.e.
    the current DB value differs from ``base_plan`` — is kept as-is and never
    overwritten by the proposal. Comparison is per session ``(date, slot)``, so a
    concurrent edit to the evening ride does not freeze the morning yoga (#496).
    """
    original_by_date = {_key(d): d for d in base_plan}
    current_by_date = {_key(d): d for d in current_plan}

    merged: list[dict] = []
    proposed_dates: set[tuple[str, int]] = set()
    for day in proposed_plan:
        key = _key(day)
        proposed_dates.add(key)
        original = original_by_date.get(key)
        current = current_by_date.get(key)
        if original is not None and current is not None and original == current:
            # Unchanged since the trigger read it — apply the proposal.
            merged.append(day)
        elif current is not None and original != current:
            # User edited this session concurrently — keep their version.
            merged.append(current)
        else:
            # Proposal added a brand-new session, or user deleted it — apply.
            merged.append(day)

    for key, current_day in current_by_date.items():
        if key not in proposed_dates:
            original_day = original_by_date.get(key)
            if original_day is None:
                # User added a session the proposal doesn't know about — keep it.
                merged.append(current_day)
            elif current_day != original_day:
                # User edited a session the proposal removed/rescheduled — keep it.
                merged.append(current_day)
            # else: unchanged and dropped by the proposal — let the proposal win.

    return _sorted_by_session(merged)


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
    """Build per-session history records for one plan write (#343).

    Emits an ``applied=True`` record for every session whose content actually
    changed, and — for pin-respecting (automated) triggers — an
    ``applied=False`` record for every existing session the proposal wanted to
    change but that was kept unchanged (blocked by a user pin or a completed
    session). ``source`` re-stamps alone never count as a change.

    Records are per session, not per date: a two-a-day whose PM ride was retuned
    logs one row for that session rather than one conflated row for the date, so
    the history stays a faithful diff of what the athlete actually saw (#496).
    """
    current_by = {_key(d): d for d in current_plan if d.get("date")}
    merged_by = {_key(d): d for d in merged if d.get("date")}
    changes: list[dict] = []
    for date, slot in sorted(set(current_by) | set(merged_by)):
        cur, new = current_by.get((date, slot)), merged_by.get((date, slot))
        if _content_differs(cur, new):
            changes.append(
                {
                    "date": date,
                    "slot": slot,
                    "old_day": cur,
                    "new_day": new,
                    "applied": True,
                }
            )
    if source.respect_pins:
        for day in proposal:
            if not day.get("date"):
                continue
            key = _key(day)
            cur = current_by.get(key)
            if cur is None:
                continue
            if _content_differs(cur, day) and not _content_differs(
                cur, merged_by.get(key)
            ):
                changes.append(
                    {
                        "date": key[0],
                        "slot": key[1],
                        "old_day": cur,
                        "new_day": day,
                        "applied": False,
                    }
                )
    return changes


def _move_signature(day: dict | None) -> tuple | None:
    """Identify a workout well enough to recognise it on another date.

    Deliberately narrow — title, type and duration must *all* be present and
    equal. A looser match (title alone) would start reverting unrelated edits
    that happen to share a name, and this guard's whole value is that it fires
    only when a session genuinely relocated.
    """
    if not day:
        return None
    title = str(day.get("title") or "").strip().lower()
    workout_type = str(day.get("workoutType") or "").strip().lower()
    duration = day.get("durationMinutes")
    if not title or not workout_type or duration is None:
        return None
    return (title, workout_type, duration)


def _revert_orphaned_moves(
    merged: list[dict], current_plan: list[dict], proposal: list[dict]
) -> list[dict]:
    """Undo the surviving half of a move whose destination was blocked (#651).

    The guards above filter session by session, with no notion that two changes
    in one batch belong together. So when the nightly job moved a long ride from
    Saturday to Sunday, the Sunday half hit the athlete's pinned rest day and was
    correctly reverted — while the Saturday half applied anyway, and the ride
    existed on neither day. The pin did its job and still produced the worst
    possible outcome.

    A blocked destination means the move did not happen, so the source keeps its
    session. Matching is by :func:`_move_signature`, so this only fires when the
    content the batch wanted to write onto the blocked day is recognisably the
    same workout it took off another day.
    """
    current_by = {_key(d): d for d in current_plan if d.get("date")}
    merged_by = {_key(d): d for d in merged if d.get("date")}
    blocked = [
        day
        for day in proposal
        if day.get("date")
        and _key(day) in current_by
        and _content_differs(current_by[_key(day)], day)
        and not _content_differs(current_by[_key(day)], merged_by.get(_key(day)))
    ]
    if not blocked:
        return merged
    wanted = {sig for sig in map(_move_signature, blocked) if sig is not None}
    if not wanted:
        return merged
    # Sources this batch emptied whose workout is exactly what a blocked day was
    # meant to receive.
    orphaned = {
        key
        for key, current in current_by.items()
        if _move_signature(current) in wanted
        and _content_differs(current, merged_by.get(key))
    }
    if not orphaned:
        return merged
    restored = [
        current_by[_key(day)] if _key(day) in orphaned else day for day in merged
    ]
    present = {_key(d) for d in restored if d.get("date") is not None}
    restored.extend(current_by[key] for key in orphaned if key not in present)
    return _sorted_by_session(restored)


# Reverting one day can expose a collision with its other neighbour, so the
# check repeats. Bounded rather than looped to convergence: a plan that still
# collides after three passes is not one more revert away from being coherent,
# and grinding through the whole week day by day would be an automated writer
# quietly deleting a fortnight of training.
_MAX_COHERENCE_PASSES = 3


def _revert_new_incoherences(
    merged: list[dict], current_plan: list[dict], today: str
) -> list[dict]:
    """Undo an automated write that put colliding sessions on adjacent days (#665).

    Every other guard here asks whether *this* day may be written. None asks what
    the write did to the day next to it, which is how 2026-09-08 ended with
    strength on Wednesday and Thursday: the coach had pinned Wednesday and said
    in the day itself that it was protecting Thursday's intervals, then an
    automated write turned Thursday into a second gym day. The pin worked. Nobody
    had claimed Thursday, so nothing else looked.

    Only collisions this write *created* are reverted — one already present in
    ``current_plan`` is pre-existing, and undoing an unrelated edit over it would
    punish the write for someone else's mess. Of the two days, the later one is
    given back: the earlier is nearer to being ridden and more likely the one the
    athlete and coach just agreed on.

    A day with no previous version cannot be restored, so a collision created by
    *appending* to the plan is left alone and reported by the prompt instead.
    Deleting it would cost the athlete a session, which is the #651 mistake.
    """
    current_by = {_key(d): d for d in current_plan if d.get("date")}
    if not current_by:
        return merged
    before = plan_coherence.collision_keys(current_plan, today)
    result = merged
    for _ in range(_MAX_COHERENCE_PASSES):
        created = plan_coherence.collision_keys(result, today) - before
        if not created:
            break
        result_by = {_key(d): d for d in result if d.get("date")}
        # Only days this write actually changed can be handed back, and only to
        # the content that was there before it ran.
        changed = {
            key
            for key, day in result_by.items()
            if key in current_by and _content_differs(current_by[key], day)
        }
        restore: set[tuple] = set()
        for key in sorted(created):
            dates = plan_coherence.collision_dates(key)
            candidates = [k for k in changed if k[0] in dates]
            if candidates:
                restore.add(max(candidates))
        if not restore:
            break
        result = [
            current_by[_key(day)] if _key(day) in restore else day for day in result
        ]
    return result


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
    commitments: list[dict],
    source: PlanSource,
    today: str,
) -> PlanCommitResult:
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
        # A synced activity never sets the manual ``completed`` flag, so protect
        # every current-plan day the athlete actually trained from being dropped
        # or rewritten by this automated trigger (#468 follow-up).
        activity_dates = await crud.get_recorded_activity_dates(
            db, user.id, [d["date"] for d in current_plan if d.get("date")]
        )
        merged = _preserve_activity_days(merged, current_plan, activity_dates)
        # Today is not pinned, completed or ridden at 02:00, so none of the
        # guards above covered the day the athlete is about to ride (#651).
        merged = _preserve_today(merged, current_plan, today, constraints)
        # An arrangement covers days nobody pinned — that is the point of it,
        # and why every guard above missed it (#667).
        merged = _preserve_committed_days(
            merged, current_plan, commitments, constraints
        )
        # A move is one decision across two days; applying only the half that
        # cleared the guards deletes the session outright (#651).
        merged = _revert_orphaned_moves(merged, current_plan, proposal)
        # Last, because it reads the finished week: every guard above protects a
        # day in isolation, and a write can satisfy all of them while still
        # stacking two gym days back to back (#665).
        merged = _revert_new_incoherences(merged, current_plan, today)
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
    batch_id: str | None = None
    if changes:
        rows = await crud.record_plan_day_changes(db, user.id, changes, source.trigger)
        batch_id = rows[0].batch_id if rows else None
    # The applied=True subset is what actually changed for the athlete; the coach
    # narrator turns it into one summary + per-day reasons (#439).
    applied_changes = [c for c in changes if c.get("applied")]
    if current_row is not None and merged == current_plan:
        return PlanCommitResult(current_plan, batch_id, applied_changes)
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
    return PlanCommitResult(merged, batch_id, applied_changes)


async def _load_active_commitments(
    db: AsyncSession, user_id: str, *, today: str
) -> list[dict]:
    """Active arrangements for this athlete, as the plain dicts the guard wants.

    Failure is non-fatal on purpose: commitments make plan writes *safer*, and a
    read error here must not be able to block one. The deploy of #667 also runs
    with the table absent until the migration lands, which this covers.
    """
    try:
        rows = await crud.list_active_plan_commitments(db, user_id, today=today)
    except Exception:  # noqa: BLE001 — never let this block a plan write
        logger.warning("could not load plan commitments; proceeding without them",
                       exc_info=True)
        return []
    return [plan_commitments.commitment_to_dict(row) for row in rows]


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
) -> PlanCommitResult:
    """Enforce constraints, protect user edits, and persist a full proposed plan.

    ``source`` names the trigger (see ``PLAN_SOURCES``); it decides whether the
    change is pinned as a user edit and whether it must respect existing pins.
    Accepts typed ``PlanDay`` days or raw dicts. Returns a ``PlanCommitResult``
    whose ``plan`` is what was actually persisted (or the unchanged current plan
    when the proposal collapses to a no-op).
    """
    resolved = _resolve_source(source)
    today = app_today_iso(now, timezone_name)
    constraints = await load_active_constraints(db, user.id, today=today)
    commitments = await _load_active_commitments(db, user.id, today=today)
    return await _enforce_and_persist(
        db, user, _as_dicts(proposed_plan), base_plan=_as_dicts(base_plan),
        constraints=constraints, commitments=commitments, source=resolved,
        today=today,
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
) -> PlanCommitResult:
    """Apply per-day updates through the same constraint-respecting pipeline.

    Updates that would violate a hard constraint are dropped before they are
    applied; completed days are never touched. ``source`` names the trigger
    (see ``PLAN_SOURCES``). Returns a ``PlanCommitResult`` (see ``commit_plan``).
    """
    resolved = _resolve_source(source)
    today = app_today_iso(now, timezone_name)
    constraints = await load_active_constraints(db, user.id, today=today)
    base_dicts = _as_dicts(base_plan)
    filtered = filter_plan_updates_for_constraints(_as_dicts(plan_updates), constraints)
    proposed = apply_plan_updates(base_dicts, filtered)
    if proposed is None:
        return PlanCommitResult(base_dicts, None, [])
    commitments = await _load_active_commitments(db, user.id, today=today)
    return await _enforce_and_persist(
        db, user, proposed, base_plan=base_dicts, constraints=constraints,
        commitments=commitments, source=resolved, today=today,
    )
