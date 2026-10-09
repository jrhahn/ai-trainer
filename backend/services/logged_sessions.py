"""A session only the athlete recorded still happened (#745).

The load ladder's bottom two rungs — session-RPE (#712/#714) and time on task
(#579) — exist for sessions that nothing measured. Both are reached by walking
``RideMetric`` rows, and until now every path that created one needed an
*imported* activity: a Strava or intervals.icu sync, or an uploaded ``.fit``
file. So the two rungs built for unmeasured sessions were unreachable by any
session that was never recorded on a device — and a gym session is the least
likely session in the athlete's week to be recorded on one.

The athlete who logged twelve sets and RPE 4 therefore got tonnage and e1RM
trends (#714), got the interference guards, which read the plan (#715), and got
*zero* contribution to fatigue. The day read as rest, and TSB rose across it:
exactly the failure ``services.training_load`` opens its docstring with.

This closes it by giving such a session a row of its own, through the gates that
already exist rather than beside them:

**It is priced by the ladder, not here.** ``session_load`` decides the rung and
labels the provenance (#712). Nothing in this module knows a load formula.

**It is booked by the one ledger loop.** ``metrics_service.replay_load_chain``
(#713), so an hour in the gym raises aggregate fatigue and its own sport's
fitness, and never cycling's.

**Its identity is the session's identity.** ``(date, slot)`` from #496, as the
``external_activity_id`` of an ``activity_imports`` source — so re-saving the
form corrects the row instead of appending a second session.

**It is retired the moment a recording turns up**, matched on date and
``training_sport`` the way ``reported_effort._candidate`` matches. A placeholder
exists only in the absence of a recording, so the recording's arrival is
precisely the event that ends it.

Two signals are deliberately *not* offered to the ladder here:

- **Average power.** A logged average is not a normalised power, and converting
  one to the other is a formula this app does not have — inventing it at a new
  site is what the single load gate exists to prevent. A logged ride therefore
  prices from heart rate, effort or time, which are honest about being estimates.
- **Tonnage.** Volume load is the gym's own currency and is deliberately not
  convertible to TSS (#714).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import metrics_service
from services.activity_identity import training_sport
from services.activity_imports import ImportedActivity
from services.training_load import LoadSignals, TrainingLoad, session_load

logger = logging.getLogger(__name__)

#: The ``activity_source`` that marks a row as standing in for a session nobody
#: recorded. Distinct from the three import sources so the row is always
#: recognisable as the athlete's own account of the session rather than a
#: measurement of it — in the database, in the coach's context, and to the
#: retirement rule below.
LOGGED_SOURCE = "logged"

#: What the row is called where an activity name is shown. Deliberately not a
#: provider-shaped name: the athlete should be able to tell at a glance which
#: sessions in their history were recorded and which they typed in.
LOGGED_ACTIVITY_NAME = "Logged session"


def session_external_id(date: str, slot: int | None) -> str:
    """The stable external id of a logged session: its ``(date, slot)``.

    Named for the column it fills rather than for the identity it encodes, so it
    is not mistaken for ``schemas.session_key``, which answers the same question
    about a *plan day* and returns a tuple.

    #496's session identity, used verbatim. Keying on the date alone would make
    a two-a-day collapse into one row, so the morning gym session and the
    evening ride would take turns overwriting each other's load.
    """
    return f"{date}#{int(slot or 0)}"


def _duration_seconds(log: models.WorkoutLog) -> int:
    """The session's duration in whole seconds, or ``0`` if it claims none.

    Whole seconds because that is the unit the ladder reads, and the two must not
    disagree about what counts as a session — see :func:`asserts_a_session`.
    """
    try:
        minutes = float(getattr(log, "actual_duration_minutes", None) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, int(round(minutes * 60)))


def asserts_a_session(log: models.WorkoutLog) -> bool:
    """Whether this log claims a session happened at all.

    A duration the ladder can price is the whole test, and deliberately not a
    reported effort: an hour in the gym with no effort recorded is still an hour,
    and the duration rung is there to price it. Requiring an effort would leave
    the commonest log — saved without touching the RPE control — contributing
    nothing, which is the bug this module exists to fix.

    Measured in whole seconds rather than "a positive number of minutes", found
    in review: a quarter of a second is positive minutes but zero seconds, so the
    two tests disagreed. A log corrected to such a duration was still *wanted*,
    which kept the retirement rule from removing the row it had already written —
    so the athlete's correction was ignored and a stale load stood. One test for
    both, rather than a second rule to patch the first.
    """
    return _duration_seconds(log) > 0


# Sessions entered outside any plan day live in a slot range of their own
# (ai-trainer-ops#47). A planned session's slot is its position on the plan day
# (0, 1, … for a two-a-day). If an entered ride took slot 1 and the next plan put
# a second session on that day, the plan would read the athlete's ride as
# feedback on a session they never saw. No plan writes a day with a hundred.
UNPLANNED_SLOT_BASE = 100


def is_unplanned_slot(slot: int | None) -> bool:
    return int(slot or 0) >= UNPLANNED_SLOT_BASE


def is_unplanned_entry(metric: models.RideMetric) -> bool:
    """A load row for a session the athlete entered as *not* the plan's (#47).

    It must never be matched to a plan session: the athlete chose "Add
    activity" over "Log Completed Workout", which is the statement that this
    was something else.
    """
    if not is_logged_placeholder(metric):
        return False
    _, _, slot = str(getattr(metric, "external_activity_id", "") or "").partition("#")
    return slot.isdigit() and is_unplanned_slot(int(slot))


def is_logged_placeholder(metric: models.RideMetric) -> bool:
    """Whether this row stands in for a session nobody recorded."""
    return getattr(metric, "activity_source", None) == LOGGED_SOURCE


def logged_activity(log: models.WorkoutLog) -> ImportedActivity | None:
    """The logged session as an importable activity, or ``None`` if it claims none.

    Reusing ``ImportedActivity`` rather than building a row by hand so the
    identity rules — ``source_key``, ``legacy_metric_id``, the ride-input shape
    every chain caller already reads — stay in the one place that owns them.
    """
    if not asserts_a_session(log):
        return None
    date = str(getattr(log, "date", "") or "")
    if not date:
        return None
    slot = getattr(log, "slot", 0)
    return ImportedActivity(
        source=LOGGED_SOURCE,
        external_activity_id=session_external_id(date, slot),
        name=LOGGED_ACTIVITY_NAME,
        # No start time: the athlete told us the day and the duration, not the
        # clock. ``None`` rather than a guessed midnight, which would read as a
        # fact about when they trained.
        start_datetime=None,
        activity_date=date,
        sport_type=str(getattr(log, "sport_type", "") or "cycling"),
        duration_seconds=_duration_seconds(log),
        metadata={"workout_log_slot": int(slot or 0)},
    )


def load_for_log(
    log: models.WorkoutLog,
    *,
    max_heart_rate: int | None = None,
    resting_heart_rate: int | None = None,
) -> TrainingLoad | None:
    """Price a logged session through the ladder (#712).

    Every signal the log actually carries is offered and the ladder decides which
    one answers — that is its design, and the reason a caller does not get to
    pick a rung. Average power is the one exception, for the reason in the module
    docstring.
    """
    activity = logged_activity(log)
    if activity is None:
        return None
    return session_load(
        LoadSignals(
            sport_type=activity.sport_type,
            duration_seconds=activity.duration_seconds,
            avg_hr_bpm=getattr(log, "average_heart_rate", None),
            max_heart_rate=max_heart_rate,
            resting_heart_rate=resting_heart_rate,
            perceived_effort=getattr(log, "perceived_effort", None),
        )
    )


def _recorded_counts(
    metrics: list[models.RideMetric],
) -> dict[tuple[str, str], int]:
    """How many *recorded* activities exist per ``(date, sport)``.

    Counted from the stored rows alone, which is why
    :func:`reconcile_logged_sessions` has to run *after* an import has written
    its rows — see its docstring.
    """
    counts: dict[tuple[str, str], int] = {}
    for metric in metrics:
        if is_logged_placeholder(metric):
            continue
        key = (
            str(getattr(metric, "activity_date", "") or ""),
            training_sport(getattr(metric, "sport_type", None)) or "",
        )
        counts[key] = counts.get(key, 0) + 1
    return counts


def _already_stored(
    row: models.RideMetric, activity: ImportedActivity, load: TrainingLoad
) -> bool:
    """Whether the stored placeholder already says exactly this.

    The reason the common path is cheap. Without it every import of an athlete
    who has *any* hand-logged session re-writes that row and replays the whole
    ledger to arrive back where it started — correct, but paid for on every sync
    forever.
    """
    return (
        row.activity_date == activity.activity_date
        and row.sport_type == activity.sport_type
        and (row.duration_seconds or 0) == (activity.duration_seconds or 0)
        and row.tss == round(load.tss, 1)
        and row.tss_source == load.source
    )


def _placeholders_by_id(
    metrics: list[models.RideMetric],
) -> dict[str, models.RideMetric]:
    return {
        str(getattr(metric, "external_activity_id", "") or ""): metric
        for metric in metrics
        if is_logged_placeholder(metric)
    }


@dataclass(frozen=True)
class Reconciliation:
    """Which placeholders should exist, and which should not any more."""

    write: tuple[models.WorkoutLog, ...] = ()
    retire: tuple[models.RideMetric, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.write or self.retire)


def reconcile(
    logs: list[models.WorkoutLog],
    metrics: list[models.RideMetric],
) -> Reconciliation:
    """Decide which logged sessions need a row of their own, and which do not.

    Pure: the whole rule in one testable place, with the database work left to
    :func:`reconcile_logged_sessions`.

    A log needs a row when it claims a session and no recorded activity of the
    same sport covers it. Where a date carries more logged sessions of one sport
    than recordings — a logged two-a-day of which one was recorded — the
    recordings are paired off against the lower slots first, slot order standing
    in for time order. That pairing can only be wrong about *which* of two
    same-sport sessions on one day was recorded, never about how many sessions
    the day held, which is the number the load chain reads.
    """
    recorded = _recorded_counts(metrics)

    existing = _placeholders_by_id(metrics)

    wanted: set[str] = set()
    write: list[models.WorkoutLog] = []
    by_sport_and_date: dict[tuple[str, str], list[models.WorkoutLog]] = {}
    for log in logs:
        if not asserts_a_session(log):
            continue
        key = (
            str(getattr(log, "date", "") or ""),
            training_sport(getattr(log, "sport_type", None)) or "",
        )
        by_sport_and_date.setdefault(key, []).append(log)

    for key, same in sorted(by_sport_and_date.items()):
        covered = recorded.get(key, 0)
        for log in sorted(same, key=lambda row: int(getattr(row, "slot", 0) or 0)):
            if covered > 0:
                covered -= 1
                continue
            wanted.add(session_external_id(log.date, getattr(log, "slot", 0)))
            write.append(log)

    retire = tuple(
        metric for ident, metric in sorted(existing.items()) if ident not in wanted
    )
    return Reconciliation(write=tuple(write), retire=retire)


async def reconcile_logged_sessions(db: AsyncSession, user: models.User) -> int:
    """Make the load chain reflect the sessions only the athlete recorded.

    Returns how many rows were written or retired — ``0`` on the common path,
    which is not a failure: most sessions were recorded by something, and the
    placeholder is for the ones that were not.

    **Order matters at an import.** This reads which activities exist from the
    database, so it has to run *after* the import has written its rows and
    before that transaction commits. Run before them, and the arriving activity
    is not visible yet, so the placeholder it supersedes survives and the
    session counts twice until the next sync.

    **It is deliberately not fail-safe at an import.** The import paths let an
    exception here abort their transaction, because a retirement has to be atomic
    with the rows that caused it: swallowing the error would commit the new
    activity *and* keep the placeholder it supersedes, leaving that hour priced
    twice with nothing left to notice. The workout-log route is the one caller
    that guards it, for the opposite reason — there the athlete's own log is
    already written and must still save.

    Wired into every path that builds the load chain, for the reason
    ``reported_effort.annotate_from_logs`` gives: a feeder in one of five callers
    is a feeder that reverts. Running it on every import is also what reconciles
    history, so no backfill migration is needed — a session logged before this
    existed gets its row the next time anything syncs.
    """
    logs = await crud.get_workout_logs(db, user.id)
    metrics = await crud.get_all_ride_metrics_ordered(db, user.id)
    plan = reconcile(logs, metrics)
    if not plan:
        return 0

    stored = _placeholders_by_id(metrics)
    changed = 0
    for log in plan.write:
        activity = logged_activity(log)
        if activity is None:  # pragma: no cover - reconcile already filtered these
            continue
        load = load_for_log(
            log,
            max_heart_rate=user.max_heart_rate,
            resting_heart_rate=user.resting_heart_rate,
        )
        if load is None:  # pragma: no cover - see asserts_a_session
            # Unreachable: a log that reaches here has a duration the ladder can
            # price, because that is now what ``asserts_a_session`` tests. Kept
            # because the alternative to this branch is an AttributeError on
            # ``load.tss`` if the two ever drift again, and because writing a
            # zero would be the assertion that the athlete rested (#579).
            continue
        was = stored.get(activity.source_key)
        if was is not None and _already_stored(was, activity, load):
            continue
        ride = activity.to_ride_input()
        await crud.upsert_ride_metric(
            db,
            user.id,
            strava_activity_id=ride["strava_activity_id"],
            activity_source=ride["activity_source"],
            external_activity_id=ride["external_activity_id"],
            source_metadata=ride["source_metadata"],
            activity_date=ride["activity_date"],
            sport_type=ride["sport_type"],
            activity_name=ride["activity_name"],
            duration_seconds=ride["duration_seconds"],
            tss=round(load.tss, 1),
            tss_source=load.source,
        )
        changed += 1

    for metric in plan.retire:
        await db.delete(metric)
        changed += 1

    if not changed:
        # Every wanted row was already stored exactly as it stands. Replaying the
        # ledger to write back the numbers already there would be work for
        # nothing, and this runs on every import.
        return 0

    # Both halves invalidate every chain value after the row they touched, so the
    # ledger is replayed rather than patched in place (#713). Flushed first and
    # then re-read, so the replay walks the rows as they now stand rather than a
    # list still holding the ones just deleted.
    await db.flush()
    metrics_service.replay_load_chain(
        await crud.get_all_ride_metrics_ordered(db, user.id)
    )
    await db.flush()

    logger.info(
        "Reconciled logged sessions for user %s: %d written, %d retired",
        user.id,
        len(plan.write),
        len(plan.retire),
    )
    return changed
