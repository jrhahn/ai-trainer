"""One-off backfill of RideMetric.duration_seconds from moving_time (#427, #429).

Historic rides were stored with the wall-clock ``elapsed_time`` (incl. pauses)
instead of ``moving_time`` (pauses removed). Neither ``moving_time`` nor the raw
streams are persisted, so the correct duration can only come from the upstream
provider.

This is the whole-history CLI wrapper around the source-aware correction in
``services.duration_refresh`` (the same logic the daily ``duration-refresh``
scheduler job runs over a recent window). See that module for how Strava and
intervals.icu rides are matched (including the float-tolerant intervals join for
the float64-corrupted ids, #429 Bug B).

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
from pathlib import Path

# Allow ``python scripts/backfill_activity_moving_time.py`` from backend/ to
# import the top-level app modules (crud, models, ...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

import models
from database import async_session_maker
from services.duration_refresh import refresh_user_durations

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_moving_time")


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
                # since=None → correct the whole history, not just a recent window.
                changed, recalculated = await refresh_user_durations(
                    db, user, apply=args.apply, since=None
                )
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
