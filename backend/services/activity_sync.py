"""Backend-owned activity sync and plan adaptation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import crud
import models
from config import settings
from services import summary_pipeline
from services.analysis import (
    build_ride_metrics_chain,
    build_rule_based_summary,
    classify_from_provider_intervals,
    classify_ride_confidence_and_reason,
)
from services.activity_imports import (
    ImportedActivity,
    find_existing_import,
    to_ride_inputs,
)
from services.dates import app_today
from services.intervals_service import (
    IntervalsActivityNotFound,
    IntervalsDataUnavailable,
    fetch_activity_detail as fetch_intervals_activity_detail,
    fetch_activity_streams as fetch_intervals_activity_streams,
    fetch_recent_activities as fetch_recent_intervals_activities,
    intervals_activity_id,
    map_activity_to_imported_activity,
    normalize_provider_intervals,
    sanitize_intervals_streams,
    apply_summary_fallback,
)
from services.learning_pipeline import learn_from_completed_workouts
from services.token_accounting import flush_deferred_usage, track_llm_usage
from services.ride_matching import (
    apply_ride_plan_matches,
    mark_matched_days_completed,
    review_matched_ride_and_adapt,
)
from services.llm import resolve_user_provider
from services.scheduler import ScheduledJob
from services.strava_service import (
    STRAVA_OAUTH_BASE,
    StravaStreamUnavailable,
    ensure_fresh_strava_token,
    fetch_activity_streams_strict as fetch_strava_activity_streams,
)
from services.weather_service import (
    enrich_activity_weather,
    home_coordinates_for_user,
)

logger = logging.getLogger(__name__)

INTERVALS_CURSOR_TOLERANCE = 2048


@dataclass(slots=True)
class SourceSyncResult:
    source: str
    checked: int = 0
    imported: int = 0
    skipped: int = 0
    adapted: int = 0
    failed: int = 0
    reclassified: int = 0


@dataclass(slots=True)
class ActivitySyncResult:
    users: int = 0
    source_checks: int = 0
    imported: int = 0
    skipped: int = 0
    adapted: int = 0
    failed: int = 0
    reclassified: int = 0

    def add(self, source: SourceSyncResult) -> None:
        self.source_checks += source.checked
        self.imported += source.imported
        self.skipped += source.skipped
        self.adapted += source.adapted
        self.failed += source.failed
        self.reclassified += source.reclassified



def _intervals_cursor_matches(activity_id: int, cursor: int) -> bool:
    return (
        activity_id == cursor or abs(activity_id - cursor) <= INTERVALS_CURSOR_TOLERANCE
    )


def _safe_intervals_cursor(
    outcomes: list[tuple[int, bool]], current_cursor: int
) -> int:
    """Return the furthest cursor position that does not skip a failed import.

    ``outcomes`` lists ``(activity_id, handled_ok)`` for the new activities in
    newest-first order (the order intervals.icu returns them).  Intervals ids
    are content hashes, not monotonic counters, so the cursor can only move by
    list position, not by id magnitude.

    Starting from the oldest new activity (the one adjacent to the current
    cursor) and moving toward the newest, the cursor advances across an
    unbroken run of successfully-handled activities and stops at the first
    failure — so that activity, and everything newer than it, is retried on
    the next sync instead of being silently skipped (regression guard, #322).
    """
    new_cursor = current_cursor
    for activity_id, handled_ok in reversed(outcomes):
        if not handled_ok:
            break
        new_cursor = activity_id
    return new_cursor


def _sanitize_strava_streams(streams: object) -> dict:
    if not isinstance(streams, dict):
        return {}
    cleaned: dict[str, dict[str, list]] = {}
    for key in ("watts", "heartrate", "cadence", "velocity_smooth", "altitude", "time"):
        stream_obj = streams.get(key)
        if not isinstance(stream_obj, dict):
            continue
        data = stream_obj.get("data")
        if not isinstance(data, list):
            continue
        numeric = [float(v) for v in data if isinstance(v, (int, float))]
        cleaned[key] = {"data": numeric}
    latlng_obj = streams.get("latlng")
    if isinstance(latlng_obj, dict):
        data = latlng_obj.get("data")
        if isinstance(data, list):
            points = [
                [float(p[0]), float(p[1])]
                for p in data
                if isinstance(p, (list, tuple))
                and len(p) >= 2
                and isinstance(p[0], (int, float))
                and isinstance(p[1], (int, float))
            ]
            cleaned["latlng"] = {"data": points}
    return cleaned


async def fetch_recent_strava_activities(
    access_token: str, per_page: int = 10
) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{STRAVA_OAUTH_BASE}/api/v3/athlete/activities",
            params={"per_page": per_page},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if not resp.is_success:
        raise RuntimeError(f"Strava list error {resp.status_code}")
    data = resp.json()
    return data if isinstance(data, list) else []


def _strava_activity_to_imported_activity(
    activity: dict, streams: dict, weather: dict
) -> ImportedActivity | None:
    activity_id = activity.get("id")
    if not isinstance(activity_id, int):
        return None
    start_date: str = activity.get("start_date", "")
    start_date_local: str = activity.get("start_date_local", "")
    activity_date_source = start_date_local or start_date
    activity_date = activity_date_source[:10] if activity_date_source else ""
    if not activity_date:
        return None
    return ImportedActivity(
        source="strava",
        external_activity_id=str(activity_id),
        name=activity.get("name") if isinstance(activity.get("name"), str) else None,
        start_datetime=start_date_local or start_date or None,
        activity_date=activity_date,
        sport_type=activity.get("sport_type") or activity.get("type") or "cycling",
        duration_seconds=int(
            activity.get("moving_time") or activity.get("elapsed_time") or 0
        ),
        streams=streams,
        start_lat=(
            float(activity["start_latlng"][0])
            if isinstance(activity.get("start_latlng"), list)
            and len(activity["start_latlng"]) >= 2
            and isinstance(activity["start_latlng"][0], (int, float))
            else None
        ),
        start_lng=(
            float(activity["start_latlng"][1])
            if isinstance(activity.get("start_latlng"), list)
            and len(activity["start_latlng"]) >= 2
            and isinstance(activity["start_latlng"][1], (int, float))
            else None
        ),
        weather=weather,
        metadata={"strava_activity_id": activity_id},
    )


async def _persist_and_adapt(
    db: AsyncSession,
    user: models.User,
    activities: list[ImportedActivity],
) -> tuple[int, int]:
    if not activities:
        return 0, 0
    rides = to_ride_inputs(activities)

    latest_metric = await crud.get_latest_ride_metric(db, user.id)
    seed_ctl = (
        latest_metric.ctl_after if latest_metric and latest_metric.ctl_after else 0.0
    )
    seed_atl = (
        latest_metric.atl_after if latest_metric and latest_metric.atl_after else 0.0
    )
    ftp = float(user.current_ftp or 0)
    if (
        ftp <= 0
        and user.rider_assessment is not None
        and user.rider_assessment.estimated_ftp
    ):
        ftp = float(user.rider_assessment.estimated_ftp)

    metrics_chain = build_ride_metrics_chain(rides, ftp, seed_ctl, seed_atl)
    rides_by_id = {ride["strava_activity_id"]: ride for ride in rides}
    for metric in metrics_chain:
        ride = rides_by_id.get(metric["strava_activity_id"])
        if ride is not None:
            apply_summary_fallback(metric, ride)
        await crud.upsert_ride_metric(db, user.id, **metric)

    if not metrics_chain:
        return 0, 0

    existing_plan = await crud.get_training_plan(db, user.id)
    training_plan = existing_plan.plan if existing_plan is not None else []
    imported_ids = [metric["strava_activity_id"] for metric in metrics_chain]
    auto_matched = await apply_ride_plan_matches(
        db, user.id, training_plan, imported_ids
    )
    # A synced ride is proof the athlete did that day, so mark its matched plan day
    # completed — nothing else sets that flag automatically, which left auto-matched
    # days unprotected against an automated regenerate dropping them. Best-effort:
    # it must never break the sync that triggered it.
    try:
        await mark_matched_days_completed(db, user, training_plan, auto_matched)
    except Exception:
        logger.warning("Failed to mark matched plan days completed", exc_info=True)
    streams_by_id = {
        ride["strava_activity_id"]: ride.get("streams") or {} for ride in rides
    }
    adapted = 0
    for ride_metric in auto_matched:
        # Re-read the plan before each adaptation so an earlier ride's committed
        # changes are visible to the next one. Passing the same pre-loop snapshot
        # as every ride's base_plan made a later adaptation to an already-changed
        # day look like a concurrent user edit and get dropped (stale-snapshot
        # clobber, #452).
        current_plan_row = await crud.get_training_plan(db, user.id)
        current_plan = (
            current_plan_row.plan if current_plan_row is not None else training_plan
        )
        await review_matched_ride_and_adapt(
            db,
            user,
            ride_metric,
            current_plan,
            provider=resolve_user_provider(user),
            streams=streams_by_id.get(ride_metric.strava_activity_id),
        )
        adapted += 1

    # Continuous athlete learning (#388): now that completed workouts are on
    # file, run one learning step so the coach evolves from the new evidence
    # immediately instead of waiting for the weekly batch jobs. Best-effort —
    # it must never break the sync that triggered it.
    await learn_from_completed_workouts(
        db, user, timezone_name=settings.app_timezone
    )

    return len(metrics_chain), adapted


async def sync_strava_for_user(db: AsyncSession, user: models.User) -> SourceSyncResult:
    result = SourceSyncResult(source="strava", checked=1)
    if user.strava_token is None or not user.strava_auto_sync_enabled:
        result.skipped = 1
        return result

    access_token = await ensure_fresh_strava_token(user.strava_token, db)
    activities = await fetch_recent_strava_activities(access_token)
    valid_ids = [
        activity.get("id")
        for activity in activities
        if isinstance(activity.get("id"), int)
    ]
    if not valid_ids:
        return result

    latest_seen = max(valid_ids)
    if user.last_strava_activity_id is None:
        user.last_strava_activity_id = latest_seen
        result.skipped = len(valid_ids)
        logger.info(
            "Strava activity sync initialized cursor user=%s cursor=%s",
            user.id,
            latest_seen,
        )
        return result

    new_activities = [
        activity
        for activity in activities
        if isinstance(activity.get("id"), int)
        and activity["id"] > int(user.last_strava_activity_id)
    ]
    imported_ids: list[int] = []
    imported_activities: list[ImportedActivity] = []
    retry_barrier: int | None = None  # smallest id we must not advance past (#325)
    # Resolved once for the batch: indoor rides have no GPS, so their conditions
    # come from the athlete's training location (#495).
    home_coordinates = await home_coordinates_for_user(db, user.id)
    for activity in new_activities:
        activity_id = int(activity["id"])
        try:
            raw_streams = await fetch_strava_activity_streams(access_token, activity_id)
        except StravaStreamUnavailable:
            # Transient Strava failure: don't import degraded (stream-less) data
            # and don't advance the cursor past this activity, so it is retried
            # on a later tick instead of being silently lost.
            logger.warning(
                "Strava streams unavailable (transient) user=%s activity=%s; "
                "leaving for retry",
                user.id,
                activity_id,
            )
            result.skipped += 1
            retry_barrier = (
                activity_id if retry_barrier is None else min(retry_barrier, activity_id)
            )
            continue
        streams = _sanitize_strava_streams(raw_streams)
        weather = await enrich_activity_weather(
            activity, streams=streams, fallback_coordinates=home_coordinates
        )
        imported = _strava_activity_to_imported_activity(activity, streams, weather)
        if imported is None:
            result.skipped += 1
            continue
        if await find_existing_import(db, user.id, imported) is not None:
            result.skipped += 1
            continue
        imported_activities.append(imported)
        imported_ids.append(activity_id)

    imported, adapted = await _persist_and_adapt(db, user, imported_activities)
    result.imported = imported
    result.adapted = adapted
    # Advance the cursor, but never to or past an activity still awaiting retry.
    advanceable = (
        [i for i in imported_ids if i < retry_barrier]
        if retry_barrier is not None
        else imported_ids
    )
    if advanceable:
        max_imported_id = max(advanceable)
        if max_imported_id > int(user.last_strava_activity_id or 0):
            user.last_strava_activity_id = max_imported_id
    logger.info(
        "Strava activity sync user=%s fetched=%s new=%s imported=%s skipped=%s adapted=%s",
        user.id,
        len(activities),
        len(new_activities),
        result.imported,
        result.skipped,
        result.adapted,
    )
    return result


# Bounds for the unknown-ride reclassification backfill: only recent rides are
# retried, and only a few per tick, so it never fans out into a large re-fetch
# of the whole history (#482).
INTERVALS_RECLASSIFY_WINDOW_DAYS = 45
INTERVALS_RECLASSIFY_LIMIT = 25


def _resolve_user_ftp(user: models.User) -> float:
    ftp = float(user.current_ftp or 0)
    if (
        ftp <= 0
        and user.rider_assessment is not None
        and user.rider_assessment.estimated_ftp
    ):
        ftp = float(user.rider_assessment.estimated_ftp)
    return ftp


async def _reclassify_unknown_intervals_rides(
    db: AsyncSession, user: models.User
) -> int:
    """Re-fetch provider laps for still-``unknown`` intervals rides and reclassify.

    The nightly sync skips activities that were already imported, so a ride
    imported before provider-interval classification existed (or before its
    per-second stream was available) stays ``unknown`` forever. This bounded
    backfill re-fetches only those rows' structured intervals and upgrades the
    classification in place via :func:`crud.update_ride_metric_classification`,
    which leaves power/load and every athlete-edited field untouched (#482).

    Returns the number of rides whose classification changed.
    """
    token = user.intervals_token
    if token is None:
        return 0
    ftp = _resolve_user_ftp(user)
    if ftp <= 0:
        return 0

    since = (
        app_today() - timedelta(days=INTERVALS_RECLASSIFY_WINDOW_DAYS)
    ).isoformat()
    candidates = await crud.get_unclassified_intervals_ride_metrics(
        db, user.id, since_date=since, limit=INTERVALS_RECLASSIFY_LIMIT
    )

    changed = 0
    retired = 0
    for row in candidates:
        activity_id = row.external_activity_id
        if not activity_id:
            continue
        try:
            detail = await fetch_intervals_activity_detail(token.api_key, activity_id)
        except IntervalsDataUnavailable:
            continue  # transient — retry on a later tick
        except IntervalsActivityNotFound:
            # Permanent: the id is gone (or was never valid — see the corrupted
            # ids from #427). Mark the row so it stops consuming a candidate
            # slot and a provider request on every tick (#517).
            await crud.mark_ride_metric_unfetchable(row)
            retired += 1
            continue
        provider_intervals = normalize_provider_intervals(detail)
        if not provider_intervals:
            continue
        purpose, work = classify_from_provider_intervals(provider_intervals, ftp)
        if purpose == "unknown":
            continue
        duration_s = row.duration_seconds or 0
        confidence, reason = classify_ride_confidence_and_reason(
            purpose, duration_s, work
        )
        summary = build_rule_based_summary(
            purpose,
            duration_s,
            float(row.normalized_power_w) if row.normalized_power_w else None,
            row.tss,
            work,
        )
        await crud.update_ride_metric_classification(
            row,
            ride_purpose=purpose,
            classification_confidence=confidence,
            classification_reason=reason,
            summary=summary,
        )
        changed += 1

    if changed:
        # Reclassification changes what the dashboard summary should say.
        await summary_pipeline.invalidate(db, user)
        logger.info(
            "Intervals reclassify backfill user=%s upgraded=%s of candidates=%s",
            user.id,
            changed,
            len(candidates),
        )
    if retired:
        # Persist the markers now rather than with the rest of the tick: they
        # are facts about the provider, and a later failure in this same sync
        # (a list-endpoint error, say) must not roll them back into another
        # round of the very 404s this is meant to stop (#517).
        await db.commit()
        logger.info(
            "Intervals reclassify backfill user=%s retired=%s dead activity ids",
            user.id,
            retired,
        )
    return changed


async def sync_intervals_for_user(
    db: AsyncSession, user: models.User
) -> SourceSyncResult:
    result = SourceSyncResult(source="intervals", checked=1)
    if user.intervals_token is None or not user.intervals_auto_sync_enabled:
        result.skipped = 1
        return result

    # Reclassify any still-``unknown`` intervals rides first — this runs every
    # tick independent of whether there are new activities, since the fix targets
    # already-imported rides the new-activity loop skips (#482). Best-effort: a
    # provider hiccup here must not abort the rest of the sync.
    try:
        result.reclassified = await _reclassify_unknown_intervals_rides(db, user)
    except Exception:
        logger.warning(
            "Intervals reclassify backfill failed user=%s", user.id, exc_info=True
        )

    today = app_today()
    activities = await fetch_recent_intervals_activities(
        user.intervals_token.api_key,
        user.intervals_token.athlete_id,
        oldest=today - timedelta(days=30),
        newest=today + timedelta(days=1),
    )
    valid = [
        (activity, intervals_activity_id(activity.get("id")))
        for activity in activities
        if activity.get("id") is not None
    ]
    if not valid:
        return result

    latest_seen = valid[0][1]
    if user.last_intervals_activity_id is None:
        user.last_intervals_activity_id = latest_seen
        result.skipped = len(valid)
        logger.info(
            "Intervals activity sync initialized cursor user=%s cursor=%s",
            user.id,
            latest_seen,
        )
        return result

    new_activities: list[tuple[dict, int]] = []
    cursor_found = False
    for activity, activity_id in valid:
        if _intervals_cursor_matches(activity_id, int(user.last_intervals_activity_id)):
            cursor_found = True
            break
        new_activities.append((activity, activity_id))
    if not cursor_found:
        logger.info(
            "Intervals activity sync cursor not in recent window user=%s cursor=%s fetched=%s",
            user.id,
            user.last_intervals_activity_id,
            len(valid),
        )

    imported_activities: list[ImportedActivity] = []
    # Per-activity outcome in newest-first order so the cursor only advances
    # across activities that were actually handled (imported or already
    # present), never past a transient failure — see _safe_intervals_cursor.
    outcomes: list[tuple[int, bool]] = []
    for activity, activity_id in new_activities:
        summary_imported = map_activity_to_imported_activity(activity, None, {})
        if summary_imported is None:
            result.skipped += 1
            outcomes.append((activity_id, False))
            continue
        if await find_existing_import(db, user.id, summary_imported) is not None:
            result.skipped += 1
            outcomes.append((activity_id, True))
            continue
        try:
            detail = await fetch_intervals_activity_detail(
                user.intervals_token.api_key, activity.get("id")
            )
            streams = sanitize_intervals_streams(
                await fetch_intervals_activity_streams(
                    user.intervals_token.api_key, activity.get("id")
                )
            )
        except IntervalsDataUnavailable:
            # Transient intervals.icu failure: don't import degraded data, and
            # mark this activity unhandled so the cursor stops before it and it
            # is retried on a later tick (#352).
            logger.warning(
                "Intervals streams/detail unavailable (transient) user=%s "
                "activity=%s; leaving for retry",
                user.id,
                activity_id,
            )
            result.skipped += 1
            outcomes.append((activity_id, False))
            continue
        except IntervalsActivityNotFound:
            # The listing offered an activity that no longer exists. Permanent,
            # so count it as handled and let the cursor move past it — holding
            # the cursor here would re-request the same dead id forever (#517).
            logger.warning(
                "Intervals activity gone from provider user=%s activity=%s; skipping",
                user.id,
                activity_id,
            )
            result.skipped += 1
            outcomes.append((activity_id, True))
            continue
        imported = map_activity_to_imported_activity(activity, detail, streams)
        if imported is None:
            result.skipped += 1
            outcomes.append((activity_id, False))
            continue
        imported_activities.append(imported)
        outcomes.append((activity_id, True))

    imported, adapted = await _persist_and_adapt(db, user, imported_activities)
    result.imported = imported
    result.adapted = adapted
    safe_cursor = _safe_intervals_cursor(
        outcomes, int(user.last_intervals_activity_id)
    )
    if safe_cursor != user.last_intervals_activity_id:
        user.last_intervals_activity_id = safe_cursor
    logger.info(
        "Intervals activity sync user=%s fetched=%s new=%s imported=%s skipped=%s adapted=%s cursor_found=%s",
        user.id,
        len(activities),
        len(new_activities),
        result.imported,
        result.skipped,
        result.adapted,
        cursor_found,
    )
    return result


async def _sync_one_source(
    db: AsyncSession,
    user_id: str,
    source: str,
    usage_source: str,
    sync_fn,
) -> SourceSyncResult:
    """One provider's sync for one user, inside its own usage scope."""
    user = await crud.get_user_by_id(db, user_id)
    if user is None:
        return SourceSyncResult(source=source, checked=1, skipped=1)
    # Wrapped here rather than inside each helper because a synced ride triggers
    # coach work — the per-ride review in review_matched_ride_and_adapt is two
    # LLM calls — that ran outside every collection scope and was therefore
    # billed to nobody (#516). Steps that open their own scope (the learning
    # chain) still report under their own source; nesting means their tokens are
    # persisted once, by the inner scope.
    async with track_llm_usage(db, user, source=usage_source):
        return await sync_fn(db, user)


async def run_activity_sync(
    session_factory: async_sessionmaker[AsyncSession],
) -> ActivitySyncResult:
    started = datetime.now(timezone.utc)
    result = ActivitySyncResult()
    logger.info("Activity sync started")

    async with session_factory() as db:
        users = await crud.get_users_with_training_plans(db)

    result.users = len(users)
    for user_ref in users:
        # The usage label is spelled out rather than built from `source`, so
        # every label in the app can be found by grepping for the string that
        # appears in Grafana (#549).
        for source, usage_source, sync_fn in (
            ("strava", "job:strava-sync", sync_strava_for_user),
            ("intervals", "job:intervals-sync", sync_intervals_for_user),
        ):
            try:
                async with session_factory() as db:
                    # Same shape as the request session dependency: whatever this
                    # sync spent is written once the transaction has resolved,
                    # because a sync that raises rolls back the rows recording
                    # its spend along with everything else (#560).
                    try:
                        source_result = await _sync_one_source(
                            db, user_ref.id, source, usage_source, sync_fn
                        )
                        result.add(source_result)
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise
                    finally:
                        await flush_deferred_usage(db)
            except Exception:
                result.failed += 1
                logger.warning(
                    "Activity sync failed user=%s source=%s",
                    user_ref.id,
                    source,
                    exc_info=True,
                )

    duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "Activity sync finished users=%s source_checks=%s imported=%s skipped=%s adapted=%s reclassified=%s failed=%s duration_ms=%s",
        result.users,
        result.source_checks,
        result.imported,
        result.skipped,
        result.adapted,
        result.reclassified,
        result.failed,
        duration_ms,
    )
    return result


def activity_sync_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> ScheduledJob:
    async def _run() -> object:
        return await run_activity_sync(session_factory)

    return ScheduledJob(
        name="activity-sync",
        run=_run,
        next_delay=lambda: max(60, int(settings.activity_sync_interval_seconds)),
    )
