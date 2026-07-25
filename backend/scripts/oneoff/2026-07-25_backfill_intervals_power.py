"""One-off backfill for #466: correct inflated/degenerate intervals power.

Intervals.icu rides had ``avg_power_w`` and ``normalized_power_w`` recomputed
from a lossy/downsampled stream, which inflated the sample-mean and collapsed
NP onto avg (``avg == NP``, ~20 W above the provider's own figure). The code
fix now prefers the provider's headline numbers, but rides already stored keep
their wrong values until re-fetched.

For each user with intervals credentials this script:
  1. loads their intervals-sourced ride metrics,
  2. re-fetches the provider's own average / weighted-average (NP) / training
     load from the intervals summary API for that date range,
  3. overwrites the stored ``avg_power_w`` / ``normalized_power_w``,
  4. rebuilds the whole chain (TSS / IF / CTL / ATL / TSB) from the corrected
     Normalized Power via ``metrics_service.recalculate_metrics_for_user``.

Metrics without a provider power figure (e.g. yoga, weights) are left untouched.

Usage (from ``backend/`` with the app venv; see CLAUDE.md for the NixOS env):
    uv run python scripts/oneoff/2026-07-25_backfill_intervals_power.py            # dry run
    uv run python scripts/oneoff/2026-07-25_backfill_intervals_power.py --commit   # apply
    uv run python scripts/oneoff/2026-07-25_backfill_intervals_power.py --email you@example.com --commit
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

import models
from database import async_session_maker
from services import crud
from services.intervals_service import (
    _first_float,
    _first_int,
    fetch_recent_activities,
)
from services.metrics_service import recalculate_metrics_for_user


def _provider_power(activity: dict) -> tuple[int | None, int | None, float | None]:
    """Extract (avg, NP, TSS) from an intervals activity summary.

    Mirrors ``map_activity_to_imported_activity`` field priority so the backfill
    and the live import path agree. Average must never come from a weighted
    field (that is NP).
    """
    avg = _first_int(activity, "icu_average_watts", "average_watts")
    np = _first_int(activity, "icu_weighted_avg_watts", "weighted_average_watts")
    tss = _first_float(activity, "icu_training_load", "training_load")
    return avg, np, tss


async def _backfill_user(session, user: models.User, *, commit: bool) -> tuple[int, int]:
    """Return (metrics_examined, metrics_corrected) for one user."""
    token = await crud.get_intervals_token(session, user.id)
    if token is None or not token.api_key:
        return 0, 0

    metrics = await crud.get_all_ride_metrics_ordered(session, user.id)
    intervals_metrics = [
        m
        for m in metrics
        if (m.activity_source or "strava") == "intervals" and m.external_activity_id
    ]
    if not intervals_metrics:
        return 0, 0

    dates = [
        date.fromisoformat(m.activity_date)
        for m in intervals_metrics
        if m.activity_date
    ]
    if not dates:
        return 0, 0

    activities = await fetch_recent_activities(
        token.api_key,
        token.athlete_id or "0",
        oldest=min(dates),
        newest=max(dates) + timedelta(days=1),
    )
    provider_by_id = {str(a.get("id")): a for a in activities if a.get("id") is not None}

    corrected = 0
    for metric in intervals_metrics:
        summary = provider_by_id.get(str(metric.external_activity_id))
        if summary is None:
            continue
        avg, np, _tss = _provider_power(summary)
        if np is None and avg is None:
            continue  # non-power activity — leave as-is
        before = (metric.avg_power_w, metric.normalized_power_w)
        if avg is not None:
            metric.avg_power_w = avg
        if np is not None:
            metric.normalized_power_w = np
        after = (metric.avg_power_w, metric.normalized_power_w)
        if before != after:
            corrected += 1
            print(
                f"    {metric.activity_date} {metric.external_activity_id}: "
                f"avg/NP {before[0]}/{before[1]} -> {after[0]}/{after[1]}"
            )

    if corrected:
        # Rebuild TSS/IF/CTL/ATL/TSB from the corrected Normalized Power.
        try:
            await recalculate_metrics_for_user(session, user)
        except ValueError as exc:
            print(f"    ! chain rebuild skipped ({exc}); power values still corrected")

    if commit:
        await session.commit()
    else:
        await session.rollback()

    return len(intervals_metrics), corrected


async def main(email: str | None, commit: bool) -> None:
    async with async_session_maker() as session:
        stmt = select(models.User).options(selectinload(models.User.rider_assessment))
        if email:
            stmt = stmt.where(models.User.email == email)
        users = (await session.scalars(stmt)).all()

        total_examined = 0
        total_corrected = 0
        for user in users:
            examined, corrected = await _backfill_user(session, user, commit=commit)
            if examined:
                print(
                    f"  user={user.email}: {corrected}/{examined} intervals rides corrected"
                )
            total_examined += examined
            total_corrected += corrected

    mode = "COMMITTED" if commit else "DRY RUN (no changes written)"
    print(
        f"\n{mode}: corrected {total_corrected} of {total_examined} intervals ride metrics"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", help="Limit to a single user by email")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write changes (default is a dry run that rolls back)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.email, args.commit))
