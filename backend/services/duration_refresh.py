"""Re-derive ``RideMetric.duration_seconds`` from the source's ``moving_time``.

intervals.icu populates ``moving_time`` only a few minutes *after* upload, so an
early sync/analysis can store the wall-clock ``elapsed_time`` (incl. pauses)
instead. Because the ride is then considered "already imported" by id, it is
never corrected (#429 Bug A). Strava can drift for the same reason.

This module holds the source-aware correction used by two callers:

* the daily scheduler job (``duration_refresh_job``), which refreshes a recent
  window for every athlete and is the standing prevention for Bug A;
* the one-off backfill CLI (``scripts/backfill_activity_moving_time.py``), which
  runs it over the full history.

Matching:

* **Strava** — the activity *list* endpoint returns ``moving_time`` per summary,
  so a few paginated calls suffice. Rides match on the (precise) Strava id.
* **intervals.icu** — rides match on ``intervals_activity_id()`` compared **as
  float**, because stored intervals ids are float64-corrupted (#429 Bug B): a
  19-digit hash round-tripped through the frontend as a JS Number loses
  precision, so an exact int compare misses. ``float(a) == float(b)`` matches
  because both decimal renderings map to the same float64.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

import crud
import models
from config import settings
from services import metrics_service
from services.intervals_service import fetch_recent_activities, intervals_activity_id
from services.plan_maintenance import seconds_until_next_daily_run
from services.scheduler import ScheduledJob
from services.strava_service import STRAVA_OAUTH_BASE, ensure_fresh_strava_token

logger = logging.getLogger(__name__)

STRAVA_LIST_PER_PAGE = 200
# Widen the intervals fetch window slightly around the stored ride dates.
INTERVALS_WINDOW_PADDING = timedelta(days=2)
# Hour of day (app timezone) to run the daily refresh. Offset from the 2am plan
# maintenance so the two heavy jobs do not overlap.
DURATION_REFRESH_HOUR = 4


@dataclass(slots=True)
class DurationRefreshResult:
    users: int = 0
    changed: int = 0
    recalculated: int = 0
    failed: int = 0


async def fetch_strava_moving_times(
    access_token: str, *, after: datetime | None = None
) -> dict[int, int]:
    """Return ``{strava_activity_id: moving_time_seconds}``.

    When ``after`` is given only activities started after it are fetched, which
    bounds the daily refresh to a recent window instead of the whole history.
    """
    moving_by_id: dict[int, int] = {}
    page = 1
    params_base: dict[str, int] = {"per_page": STRAVA_LIST_PER_PAGE}
    if after is not None:
        params_base["after"] = int(after.timestamp())
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            resp = await client.get(
                f"{STRAVA_OAUTH_BASE}/api/v3/athlete/activities",
                params={**params_base, "page": page},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if not resp.is_success:
                raise RuntimeError(
                    f"Strava list error {resp.status_code} on page {page}"
                )
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            for activity in batch:
                activity_id = activity.get("id")
                moving = activity.get("moving_time")
                if isinstance(activity_id, int) and isinstance(moving, int):
                    moving_by_id[activity_id] = moving
            if len(batch) < STRAVA_LIST_PER_PAGE:
                break
            page += 1
            await asyncio.sleep(0.5)  # be polite to Strava's rate limiter
    return moving_by_id


async def fetch_intervals_moving_times(
    api_key: str, athlete_id: str, oldest: date, newest: date
) -> dict[float, int]:
    """Return ``{float(intervals_activity_id): moving_time}`` over the range.

    The key is a float so it matches the float64-corrupted ids stored on
    RideMetric (#429 Bug B).
    """
    activities = await fetch_recent_activities(
        api_key, athlete_id, oldest=oldest, newest=newest
    )
    moving_by_key: dict[float, int] = {}
    for activity in activities:
        moving = activity.get("moving_time")
        raw_id = activity.get("id")
        if isinstance(moving, int) and moving > 0 and raw_id is not None:
            moving_by_key[float(intervals_activity_id(raw_id))] = moving
    return moving_by_key


def _log_change(metric: models.RideMetric, old: int, new: int) -> None:
    logger.info(
        "  %s  %s: %ss -> %ss (%+ds)",
        metric.activity_date,
        metric.activity_name or metric.strava_activity_id,
        old,
        new,
        new - old,
    )


def _within_window(metric: models.RideMetric, since: date | None) -> bool:
    if since is None:
        return True
    if not metric.activity_date:
        return False
    # activity_date is an ISO ``YYYY-MM-DD`` string; lexical compare == date compare.
    return metric.activity_date[:10] >= since.isoformat()


async def corrected_durations(
    db: AsyncSession, user: models.User, *, since: date | None = None
) -> list[tuple[models.RideMetric, int, int]]:
    """Return ``(metric, old, new)`` for every ride whose duration should change.

    ``since`` restricts the work to rides on/after that date (used by the daily
    refresh); ``None`` covers the whole history (used by the backfill CLI).
    """
    metrics = await crud.get_all_ride_metrics_ordered(db, user.id)
    metrics = [m for m in metrics if _within_window(m, since)]
    strava_metrics = [m for m in metrics if (m.activity_source or "strava") == "strava"]
    intervals_metrics = [m for m in metrics if m.activity_source == "intervals"]

    changes: list[tuple[models.RideMetric, int, int]] = []

    if strava_metrics and user.strava_token is not None:
        access_token = await ensure_fresh_strava_token(user.strava_token, db)
        after = (
            datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
            - INTERVALS_WINDOW_PADDING
            if since is not None
            else None
        )
        moving_by_id = await fetch_strava_moving_times(access_token, after=after)
        for metric in strava_metrics:
            moving = moving_by_id.get(metric.strava_activity_id)
            if moving and moving > 0 and moving != (metric.duration_seconds or 0):
                changes.append((metric, metric.duration_seconds or 0, moving))

    if intervals_metrics and user.intervals_token is not None:
        dates = [
            datetime.fromisoformat(m.activity_date).date()
            for m in intervals_metrics
            if m.activity_date
        ]
        if dates:
            moving_by_key = await fetch_intervals_moving_times(
                user.intervals_token.api_key,
                user.intervals_token.athlete_id,
                oldest=min(dates) - INTERVALS_WINDOW_PADDING,
                newest=max(dates) + INTERVALS_WINDOW_PADDING,
            )
            for metric in intervals_metrics:
                moving = moving_by_key.get(float(metric.strava_activity_id))
                if moving and moving > 0 and moving != (metric.duration_seconds or 0):
                    changes.append((metric, metric.duration_seconds or 0, moving))

    return changes


async def refresh_user_durations(
    db: AsyncSession,
    user: models.User,
    *,
    apply: bool,
    since: date | None = None,
) -> tuple[int, int]:
    """Correct one user's durations. Returns ``(changed, chain_recalculated)``."""
    if user.strava_token is None and user.intervals_token is None:
        return 0, 0

    changes = await corrected_durations(db, user, since=since)
    for metric, old, new in changes:
        _log_change(metric, old, new)
        if apply:
            metric.duration_seconds = new

    recalculated = 0
    if changes and apply:
        try:
            recalculated, _ = await metrics_service.recalculate_metrics_for_user(
                db, user
            )
        except ValueError as exc:
            # No FTP available: durations are still fixed, chain left as-is.
            logger.warning("  chain not recalculated for %s: %s", user.email, exc)
        await db.commit()
    else:
        # Release any row lock / refreshed Strava token even on a dry run.
        await db.commit()

    return len(changes), recalculated


async def run_duration_refresh(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lookback_days: int,
) -> DurationRefreshResult:
    """Refresh recent durations for every connected athlete (daily job)."""
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()
    started = datetime.now(timezone.utc)
    result = DurationRefreshResult()

    async with session_factory() as db:
        users = list(
            await db.scalars(
                select(models.User).options(
                    selectinload(models.User.strava_token),
                    selectinload(models.User.intervals_token),
                    selectinload(models.User.rider_assessment),
                )
            )
        )

        for user in users:
            if user.strava_token is None and user.intervals_token is None:
                continue
            try:
                changed, recalculated = await refresh_user_durations(
                    db, user, apply=True, since=since
                )
            except Exception:
                result.failed += 1
                await db.rollback()
                logger.warning(
                    "Duration refresh failed user=%s", user.id, exc_info=True
                )
                continue
            if changed:
                result.users += 1
                result.changed += changed
                result.recalculated += recalculated

    duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "Duration refresh finished since=%s users_changed=%s rides_changed=%s "
        "chain_rows=%s failed=%s duration_ms=%s",
        since.isoformat(),
        result.users,
        result.changed,
        result.recalculated,
        result.failed,
        duration_ms,
    )
    return result


def duration_refresh_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> ScheduledJob:
    async def _run() -> object:
        return await run_duration_refresh(
            session_factory,
            lookback_days=settings.duration_refresh_lookback_days,
        )

    return ScheduledJob(
        name="duration-refresh",
        run=_run,
        next_delay=lambda: seconds_until_next_daily_run(
            timezone_name=settings.app_timezone,
            hour=DURATION_REFRESH_HOUR,
        ),
    )
