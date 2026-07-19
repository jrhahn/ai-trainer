"""Backfill RideMetric.duration_seconds from the source's moving_time (#427, #429).

Historic rides were stored with the wall-clock ``elapsed_time`` (incl. pauses)
instead of ``moving_time`` (pauses removed). Neither ``moving_time`` nor the raw
streams are persisted, so the correct duration can only come from the upstream
provider.

Two sources are handled:

* **Strava** — the activity *list* endpoint returns ``moving_time`` in each
  summary, so a few paginated calls per user suffice (no per-activity stream
  requests). Rides are matched by the (precise) Strava activity id.

* **intervals.icu** — activities are fetched over the date range of the user's
  stored intervals rides. intervals.icu populates ``moving_time`` a few minutes
  after upload, so an early sync often stored ``elapsed_time`` (#429 Bug A).
  Rides are matched on ``intervals_activity_id()`` compared **as float**,
  because stored intervals ids are float64-corrupted (#429 Bug B): a 19-digit
  hash round-tripped through the frontend as a JS Number loses precision, so an
  exact int compare misses. ``float(a) == float(b)`` still matches because both
  decimal renderings map to the same float64.

For every ride whose stored ``duration_seconds`` differs from the source's
``moving_time`` this rewrites ``duration_seconds`` and then re-derives the
TSS/CTL/ATL/TSB chain via ``metrics_service.recalculate_metrics_for_user``.

Usage (from backend/):
    python scripts/backfill_activity_moving_time.py               # dry run
    python scripts/backfill_activity_moving_time.py --apply       # persist
    python scripts/backfill_activity_moving_time.py --apply --user-email a@b.com

Environment:
    DATABASE_URL  — required (same as the app)
    Strava client credentials must be configured for Strava token refresh.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Allow ``python scripts/backfill_activity_moving_time.py`` from backend/ to
# import the top-level app modules (crud, models, ...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

import crud
import models
from database import async_session_maker
from services import metrics_service
from services.intervals_service import fetch_recent_activities, intervals_activity_id
from services.strava_service import STRAVA_OAUTH_BASE, ensure_fresh_strava_token

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_moving_time")

STRAVA_LIST_PER_PAGE = 200
# Widen the intervals fetch window slightly around the stored ride dates.
INTERVALS_WINDOW_PADDING = timedelta(days=2)


async def _fetch_all_strava_moving_times(access_token: str) -> dict[int, int]:
    """Return {strava_activity_id: moving_time_seconds} for the whole history."""
    moving_by_id: dict[int, int] = {}
    page = 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            resp = await client.get(
                f"{STRAVA_OAUTH_BASE}/api/v3/athlete/activities",
                params={"per_page": STRAVA_LIST_PER_PAGE, "page": page},
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


async def _fetch_intervals_moving_times(
    api_key: str, athlete_id: str, oldest: date, newest: date
) -> dict[float, int]:
    """Return {float(intervals_activity_id): moving_time} over the date range.

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


async def _corrected_durations(
    db, user: models.User
) -> list[tuple[models.RideMetric, int, int]]:
    """Return ``(metric, old, new)`` for every ride whose duration should change."""
    metrics = await crud.get_all_ride_metrics_ordered(db, user.id)
    strava_metrics = [m for m in metrics if (m.activity_source or "strava") == "strava"]
    intervals_metrics = [m for m in metrics if m.activity_source == "intervals"]

    changes: list[tuple[models.RideMetric, int, int]] = []

    if strava_metrics and user.strava_token is not None:
        access_token = await ensure_fresh_strava_token(user.strava_token, db)
        moving_by_id = await _fetch_all_strava_moving_times(access_token)
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
            moving_by_key = await _fetch_intervals_moving_times(
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


async def _backfill_user(db, user: models.User, apply: bool) -> tuple[int, int]:
    """Return ``(changed, chain_recalculated)`` for one user."""
    if user.strava_token is None and user.intervals_token is None:
        return 0, 0

    changes = await _corrected_durations(db, user)
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


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Without this flag the script only reports.",
    )
    parser.add_argument(
        "--user-email",
        default=None,
        help="Limit the backfill to a single athlete by email.",
    )
    args = parser.parse_args()

    if not args.apply:
        logger.info("DRY RUN — no changes will be written. Pass --apply to commit.\n")

    total_changed = 0
    total_users = 0
    async with async_session_maker() as db:
        query = select(models.User).options(
            selectinload(models.User.strava_token),
            selectinload(models.User.intervals_token),
            selectinload(models.User.rider_assessment),
        )
        if args.user_email:
            query = query.where(models.User.email == args.user_email)
        users = list(await db.scalars(query))

        for user in users:
            if user.strava_token is None and user.intervals_token is None:
                continue
            logger.info("User %s:", user.email)
            try:
                changed, recalculated = await _backfill_user(db, user, args.apply)
            except Exception as exc:  # noqa: BLE001
                logger.error("  failed: %s", exc)
                await db.rollback()
                continue
            if changed:
                total_changed += changed
                total_users += 1
                logger.info(
                    "  %d ride(s) %s%s",
                    changed,
                    "updated" if args.apply else "would change",
                    f", {recalculated} chain rows recalculated" if recalculated else "",
                )
            else:
                logger.info("  nothing to change")

    verb = "updated" if args.apply else "would be updated"
    logger.info(
        "\nDone: %d ride(s) across %d user(s) %s.", total_changed, total_users, verb
    )


if __name__ == "__main__":
    asyncio.run(main())
