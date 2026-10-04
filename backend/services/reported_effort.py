"""Turn the athlete's own session-RPE into training load (#714).

#712 built the sRPE rung of the load ladder and nothing ever fed it. The athlete
would log "that gym session was a 4 out of 5", and the app would go on pricing
the session from a flat per-hour assumption about every gym session anybody has
ever done — while the one person who was actually there had already said how hard
it was.

This closes that. Logging effort is *new evidence about a session's load*, so it
re-prices the session and replays the chain after it, rather than waiting for the
next import or an FTP change to notice.

Two rules keep it from doing damage:

**It only ever improves the rung.** The ladder's order is the whole statement of
which evidence beats which (#712): a provider figure, a power figure and a
heart-rate estimate all outrank a reported effort, and a session that already has
one of those keeps it. Only a session priced from time on task — the weakest
rung, a guess about the sport rather than about the session — is re-priced.
Letting sRPE overwrite an HR estimate would be the #579 mistake with the signs
reversed: replacing a measurement with an opinion.

**It never touches fitness.** The re-priced load enters the aggregate ATL and the
session's *own* sport's CTL via ``services.fitness_ledger`` (#713), which is why
an hour in the gym can make the athlete's fatigue rise without making them a
better cyclist. Nothing here knows what tonnage is; see ``services.strength_model``
for why that separation is the point.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import metrics_service
from services.activity_identity import activity_family, training_sport
from services.training_load import (
    LOAD_SOURCE_DURATION,
    LOAD_SOURCE_RPE,
    LoadSignals,
    session_load,
)

logger = logging.getLogger(__name__)

# The stored provenances a reported effort is allowed to replace. ``None`` is
# included because a row with a load but no recorded source predates #579's
# backfill; a row with no load *at all* is covered too — that is a session the
# ladder could not price, and an athlete's answer is strictly better than
# nothing.
#
# ``LOAD_SOURCE_RPE`` is in here so that *correcting* an effort works. An athlete
# who saves RPE 4 and then changes it to 2 has corrected the only input this row
# has, and without this the row would keep the first answer forever: the second
# save would find a source outside the set and decline. The re-log is a path this
# feature explicitly supports, so it has to be a path that works.
_REPLACEABLE_SOURCES = frozenset({LOAD_SOURCE_DURATION, LOAD_SOURCE_RPE, None})

# What the ladder landing *here* means: no gain. Deliberately not the set above —
# now that sRPE is replaceable, reusing one set for both questions would make a
# fresh sRPE answer read as "nothing to gain" and decline every re-price,
# including the first one.
_NO_GAIN_SOURCES = frozenset({LOAD_SOURCE_DURATION})


def _same_sport(metric_sport: str | None, logged_sport: str | None) -> bool:
    """Whether a stored activity and a logged session are the same sport.

    Compared through ``training_sport`` so "WeightTraining" and "strength" match,
    then through ``activity_family`` as a fallback so two sports outside the
    plannable three (two different hikes, say) are not silently treated as one.
    An unreadable sport on either side matches nothing: guessing would attach the
    athlete's gym effort to their evening ride.
    """
    logged = training_sport(logged_sport)
    stored = training_sport(metric_sport)
    if logged is not None and stored is not None:
        return logged == stored
    if not (logged_sport or "").strip() or not (metric_sport or "").strip():
        return False
    return activity_family(logged_sport) == activity_family(metric_sport)


def _candidate(
    metrics: list[models.RideMetric], logged_sport: str | None
) -> models.RideMetric | None:
    """Which activity on that date the logged effort describes, if any.

    Matched on sport rather than taken as "the only activity that day", because
    the two-a-day this app already models (#496) is precisely a gym session and a
    ride on one date — and attaching a gym session's RPE to the ride would price
    the ride from how hard the lifting felt.

    ``None`` when the sport is ambiguous: two gym sessions on one date cannot be
    told apart by date and sport alone, and silently picking one would make the
    stored load depend on row order.
    """
    same_sport = [m for m in metrics if _same_sport(m.sport_type, logged_sport)]
    if len(same_sport) != 1:
        return None
    return same_sport[0]


def annotate_rides_with_reported_effort(
    rides: list[dict],
    logs: list[models.WorkoutLog],
) -> int:
    """Attach each ride's own logged session-RPE, returning how many matched.

    The other half of the feeder. :func:`apply_reported_effort` handles "the
    athlete logged effort for a session already imported"; this handles the
    reverse order, which is just as common — the athlete logs the gym session in
    the app on Monday evening and the provider sync brings the activity in on
    Tuesday. Without this the import would price Monday from time on task and the
    answer the athlete had already given would sit unused until something
    re-priced it.

    Matched on date *and* sport for the same reason as
    :func:`_candidate`: a gym session and an evening ride on one date are two
    sessions (#496), and attaching the gym RPE to the ride would price the ride
    from how hard the lifting felt. An ambiguous date — two logged sessions of
    the same sport — is skipped rather than guessed.
    """
    by_sport_and_date: dict[tuple[str, str], list[models.WorkoutLog]] = {}
    for log in logs:
        if not log.perceived_effort:
            continue
        key = (log.date, training_sport(log.sport_type) or "")
        by_sport_and_date.setdefault(key, []).append(log)

    matched = 0
    for ride in rides:
        if ride.get("_reported_effort") is not None:
            continue
        key = (
            str(ride.get("activity_date") or ""),
            training_sport(ride.get("sport_type")) or "",
        )
        candidates = by_sport_and_date.get(key) or []
        if len(candidates) != 1:
            continue
        ride["_reported_effort"] = candidates[0].perceived_effort
        matched += 1
    return matched


async def annotate_from_logs(
    db: AsyncSession, user_id: str, rides: list[dict]
) -> int:
    """Fetch the athlete's logs and annotate *rides* with their efforts.

    The form every ``build_ride_metrics_chain`` caller uses, because there are
    five of them and a feeder wired into one is a feeder that reverts. Any path
    that rebuilds or extends the chain re-derives each session's load from the
    ride inputs it is given, so a rebuild without this prices a gym session from
    time on task again and silently discards a re-price
    :func:`apply_reported_effort` had already made.
    """
    if not rides:
        return 0
    return annotate_rides_with_reported_effort(
        rides, await crud.get_workout_logs(db, user_id)
    )


async def apply_reported_effort(
    db: AsyncSession,
    user: models.User,
    *,
    date: str,
    perceived_effort: int | float | None,
    sport_type: str | None,
    duration_minutes: int | float | None = None,
) -> models.RideMetric | None:
    """Re-price the session on *date* from the athlete's reported effort.

    Returns the row whose load changed, or ``None`` when nothing did — which is
    the common case and not a failure: most sessions already carry a better
    number than a reported effort, and that is the ladder working.
    """
    if not perceived_effort:
        return None

    metrics = await crud.get_ride_metrics_by_date(db, user.id, date)
    if not metrics:
        # No imported activity for that date. The log still stands on its own —
        # it is what the coach and the strength model read — but there is no row
        # for the load chain to hang a figure on, and inventing a synthetic
        # activity is a larger decision than this function should make.
        return None

    target = _candidate(metrics, sport_type)
    if target is None or target.tss_source not in _REPLACEABLE_SOURCES:
        return None

    duration_seconds = target.duration_seconds
    if not duration_seconds and duration_minutes:
        duration_seconds = float(duration_minutes) * 60.0

    load = session_load(
        LoadSignals(
            sport_type=target.sport_type,
            duration_seconds=duration_seconds,
            # Deliberately not re-offering the provider/power/HR signals: this
            # row is here *because* none of them priced it, and reconstructing
            # them from the stored row would just re-derive the same answer.
            perceived_effort=perceived_effort,
        )
    )
    if load is None or load.source in _NO_GAIN_SOURCES:
        # The ladder still landed on time-on-task, so there is nothing to gain
        # and a replay to avoid.
        return None

    priced = round(load.tss, 1)
    if target.tss == priced and target.tss_source == load.source:
        # Re-saving the form without changing the effort. Replaying the whole
        # history to write back the number already there would be work for
        # nothing, and this path runs on every save.
        return None

    target.tss = priced
    target.tss_source = load.source

    # A changed load invalidates every chain value after it, so the chain is
    # replayed rather than patched at this one row (#713).
    all_metrics = await crud.get_all_ride_metrics_ordered(db, user.id)
    metrics_service.replay_load_chain(all_metrics)
    await db.flush()

    logger.info(
        "Re-priced %s on %s for user %s from reported effort %s: load %.1f (%s)",
        target.sport_type,
        date,
        user.id,
        perceived_effort,
        target.tss,
        target.tss_source,
    )
    return target
