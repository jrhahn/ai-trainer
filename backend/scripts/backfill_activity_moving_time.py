"""Backfill RideMetric.duration_seconds from Strava moving_time (#427).

Historic rides were stored with Strava's ``elapsed_time`` (wall-clock incl.
pauses) instead of ``moving_time`` (pauses removed). Neither ``moving_time``
nor the raw streams are persisted, so the only source of truth for the correct
duration is Strava itself. The Strava activity *list* endpoint returns both
``moving_time`` and ``elapsed_time`` in each summary, so a few paginated calls
per user are enough — no per-activity stream/detail requests needed.

For every Strava-sourced RideMetric whose stored ``duration_seconds`` differs
from the activity's ``moving_time``, this script rewrites ``duration_seconds``
and then re-derives the TSS/CTL/ATL/TSB chain via
``metrics_service.recalculate_metrics_for_user``.

Intervals.icu rides are skipped: their ids are content hashes (never match a
Strava activity id) and that import path already preferred ``moving_time``.

Usage (from backend/):
    # Dry run — report what would change, write nothing:
    python scripts/backfill_activity_moving_time.py

    # Apply the changes:
    python scripts/backfill_activity_moving_time.py --apply

    # Limit to one athlete:
    python scripts/backfill_activity_moving_time.py --apply --user-email a@b.com

Environment:
    DATABASE_URL  — required (same as the app)
    Strava client credentials must be configured for token refresh.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
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
from services.strava_service import STRAVA_OAUTH_BASE, ensure_fresh_strava_token

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_moving_time")

STRAVA_LIST_PER_PAGE = 200


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
            # Be polite to Strava's rate limiter between pages.
            await asyncio.sleep(0.5)
    return moving_by_id


async def _backfill_user(
    db, user: models.User, apply: bool
) -> tuple[int, int]:
    """Return ``(changed, chain_recalculated)`` for one user."""
    if user.strava_token is None:
        return 0, 0

    metrics = await crud.get_all_ride_metrics_ordered(db, user.id)
    strava_metrics = [
        m for m in metrics if (m.activity_source or "strava") == "strava"
    ]
    if not strava_metrics:
        return 0, 0

    access_token = await ensure_fresh_strava_token(user.strava_token, db)
    moving_by_id = await _fetch_all_strava_moving_times(access_token)

    changed = 0
    for metric in strava_metrics:
        moving = moving_by_id.get(metric.strava_activity_id)
        if not moving or moving <= 0:
            continue
        old = metric.duration_seconds or 0
        if moving == old:
            continue
        logger.info(
            "  %s  %s: %ss -> %ss (%+ds)",
            metric.activity_date,
            metric.activity_name or metric.strava_activity_id,
            old,
            moving,
            moving - old,
        )
        changed += 1
        if apply:
            metric.duration_seconds = moving

    recalculated = 0
    if changed and apply:
        try:
            recalculated, _ = await metrics_service.recalculate_metrics_for_user(
                db, user
            )
        except ValueError as exc:
            # No FTP available: duration is still fixed, chain left as-is.
            logger.warning("  chain not recalculated for %s: %s", user.email, exc)
        await db.commit()
    else:
        # Release the row lock / refreshed token even on a dry run.
        await db.commit()

    return changed, recalculated


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
            selectinload(models.User.strava_token)
        )
        if args.user_email:
            query = query.where(models.User.email == args.user_email)
        users = list(await db.scalars(query))

        for user in users:
            if user.strava_token is None:
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
