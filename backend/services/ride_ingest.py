"""One way in for rides, whichever door they arrive through (ai-trainer-ops#248).

The chain that turns fetched activities into stored rides was written out once
per door: the background sync, the activity analysis, a .fit upload and the two
history imports. The copies drifted. #714 (the athlete's logged effort), #716
(the threshold pace), #745 (hand-logged sessions) and #245 (the rides node) each
had to be applied to every copy. The analysis also missed the replay of a late
ride (#278), and it reviewed every matched ride against the plan as it stood
before the first review (#452).

A door now maps what it fetched to ride inputs and hands them to
:func:`ingest_rides`, which runs the steps in one fixed order. Which optional
steps a door runs is its :class:`IngestPolicy`. The policies are named below,
next to each other, so the differences between the doors are written down in
one place, and tests/test_ride_ingest_248.py pins them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from config import settings
from services import logged_sessions, metrics_service, reported_effort, rides_pipeline
from services.analysis import build_ride_metrics_chain
from services.fitness_ledger import ledger_from_metric
from services.intervals_service import apply_summary_fallback
from services.learning_pipeline import learn_from_completed_workouts
from services.llm import resolve_user_provider
from services.ride_matching import (
    apply_ride_plan_matches,
    mark_matched_days_completed,
    review_matched_ride_and_adapt,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestPolicy:
    """The optional steps a door runs. Their order is fixed in :func:`ingest_rides`."""

    # Replay the load chain when a ride arrived dated on or before the newest
    # stored one (#278). A .fit upload replays once per request instead, after
    # every file (``replay_load_chain``).
    replay_late: bool = True
    # Rewrite the last-ride feedback the summary reads, when the newest ride
    # changed. The analysis writes that feedback itself.
    refresh_last_ride_feedback: bool = False
    # Mark the plan days the rides matched as completed, which protects them
    # from an automated regenerate.
    complete_matched_days: bool = False
    # Review each matched ride against the plan and adapt it: a model call per ride.
    review: bool = False
    # One continuous-learning step (#388).
    learn: bool = False


SYNC = IngestPolicy(
    refresh_last_ride_feedback=True, complete_matched_days=True, review=True, learn=True
)
ANALYSIS = IngestPolicy(review=True)
FIT_UPLOAD = IngestPolicy(replay_late=False)


@dataclass
class IngestResult:
    stored: int = 0
    matched: list[models.RideMetric] = field(default_factory=list)
    adapted: int = 0


async def replay_load_chain(db: AsyncSession, user_id: str) -> None:
    """Rewrite every stored ride's fitness and fatigue in date order (#156, #278)."""
    metrics_service.replay_load_chain(await crud.get_all_ride_metrics_ordered(db, user_id))
    await db.flush()


async def ingest_rides(
    db: AsyncSession,
    user: models.User,
    rides: list[dict],
    *,
    ftp: float,
    policy: IngestPolicy,
) -> IngestResult:
    """Price and store ``rides``, then run what follows a new ride, in this order:

    1. annotate the rides with the effort the athlete logged for them (#714);
    2. price them into the load chain, seeded from the newest stored ride, with
       the athlete's heart-rate bounds and threshold pace (#579, #716);
    3. fill what a provider's summary has where the streams were missing;
    4. store them;
    5. replay the chain when one arrived late (policy);
    6. reconcile the sessions the athlete logged by hand (#745);
    7. rewrite the last-ride feedback when the newest ride changed (policy);
    8. match them to the plan;
    9. tell the rides node, so the summary and the badge go stale (#245);
    10. mark the matched days completed (policy, best-effort);
    11. review each matched ride and adapt the plan (policy);
    12. run one learning step (policy).
    """
    if not rides:
        return IngestResult()

    await reported_effort.annotate_from_logs(db, user.id, rides)

    latest = await crud.get_latest_ride_metric(db, user.id)
    metrics = build_ride_metrics_chain(
        rides,
        ftp,
        initial_ledger=ledger_from_metric(latest),
        max_heart_rate=user.max_heart_rate,
        resting_heart_rate=user.resting_heart_rate,
        threshold_pace_seconds_per_km=await metrics_service.get_effective_threshold_pace(
            db, user
        ),
    )
    rides_by_id = {ride["strava_activity_id"]: ride for ride in rides}
    for metric in metrics:
        ride = rides_by_id.get(metric["strava_activity_id"])
        if ride is not None:
            apply_summary_fallback(metric, ride)
        await crud.upsert_ride_metric(db, user.id, **metric)

    # The chain was seeded from the newest stored row. A ride dated on or before
    # that row was chained as if it came after it, and the rows after it never
    # counted it (#278).
    if (
        policy.replay_late
        and latest is not None
        and any(
            str(metric["activity_date"])[:10] <= str(latest.activity_date)[:10]
            for metric in metrics
        )
    ):
        await replay_load_chain(db, user.id)

    # Now that the rows exist: one of these may be the recording of a session the
    # athlete logged by hand, which has been standing in for itself until now.
    await logged_sessions.reconcile_logged_sessions(db, user)

    if not metrics:
        return IngestResult()

    if policy.refresh_last_ride_feedback:
        # The stored last-ride feedback is what the training summary reads as the
        # most recent activity. A newer ride left it describing the one before.
        newest = await crud.get_latest_ride_metric(db, user.id)
        if newest is not None and (latest is None or newest.id != latest.id):
            await metrics_service.refresh_rider_assessment_feedback(db, user, newest, int(ftp))

    plan_row = await crud.get_training_plan(db, user.id)
    training_plan = plan_row.plan if plan_row is not None else []
    matched = await apply_ride_plan_matches(
        db, user.id, training_plan, [metric["strava_activity_id"] for metric in metrics]
    )
    await rides_pipeline.notify_changed(db, user)

    if policy.complete_matched_days:
        # A matched ride is proof the athlete did that day. Nothing else sets the
        # flag automatically, and an unflagged day is not protected against an
        # automated regenerate. Best-effort: it must never undo the import.
        try:
            async with db.begin_nested():
                await mark_matched_days_completed(db, user, training_plan, matched)
        except Exception:
            logger.warning("Failed to mark matched plan days completed", exc_info=True)

    adapted = 0
    if policy.review:
        streams_by_id = {ride["strava_activity_id"]: ride.get("streams") or {} for ride in rides}
        for ride_metric in matched:
            # The plan as the previous review left it: the same snapshot for every
            # ride made a later adaptation to an already-changed day look like a
            # concurrent edit, and it was dropped (#452).
            current_row = await crud.get_training_plan(db, user.id)
            current_plan = current_row.plan if current_row is not None else training_plan
            await review_matched_ride_and_adapt(
                db,
                user,
                ride_metric,
                current_plan,
                provider=resolve_user_provider(user),
                streams=streams_by_id.get(ride_metric.strava_activity_id),
            )
            adapted += 1

    if policy.learn:
        # Continuous learning (#388): one step now that completed workouts are on
        # file, instead of waiting for the weekly batch. Best-effort by itself.
        await learn_from_completed_workouts(db, user, timezone_name=settings.app_timezone)

    return IngestResult(stored=len(metrics), matched=matched, adapted=adapted)
