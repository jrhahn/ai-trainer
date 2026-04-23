"""Training metrics recalculation service.

Extracts the CTL/ATL/TSB chain-rebuild logic from ``routers/users.py`` so it
can be tested and reused independently of the HTTP layer.
"""

from __future__ import annotations

import logging
from datetime import date as _date
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services.analysis import apply_ctl_atl_decay, compute_ride_tss

logger = logging.getLogger(__name__)


def get_effective_ftp(user: models.User, ftp_override: int | None = None) -> int | None:
    """Return the best available FTP value for *user*.

    Priority order: explicit *ftp_override* → rider assessment → manual profile.
    Returns ``None`` when no FTP is known.
    """
    if ftp_override is not None and ftp_override > 0:
        return ftp_override
    if user.rider_assessment and user.rider_assessment.estimated_ftp:
        return user.rider_assessment.estimated_ftp
    if user.current_ftp:
        return user.current_ftp
    return None


async def recalculate_metrics_for_user(
    db: AsyncSession,
    user: models.User,
    ftp_override: int | None = None,
) -> tuple[int, int]:
    """Recompute TSS/CTL/ATL/TSB for all stored rides.

    When *ftp_override* is given the user's ``current_ftp`` profile field is
    updated before the recalculation so subsequent analyses use the new value.

    Returns ``(updated_count, ftp_used)``.

    Raises ``ValueError`` when no FTP value is available.
    """
    ftp_value = get_effective_ftp(user, ftp_override)
    if not ftp_value or ftp_value <= 0:
        raise ValueError(
            "No FTP value available. Provide ftp_override or set current_ftp first."
        )

    if ftp_override is not None:
        user.current_ftp = ftp_override

    all_metrics = await crud.get_all_ride_metrics_ordered(db, user.id)
    if not all_metrics:
        await db.flush()
        return 0, ftp_value

    ftp_float = float(ftp_value)
    ctl = 0.0
    atl = 0.0
    prev_date_str: str | None = None
    updated = 0

    for metric in all_metrics:
        np_w = metric.normalized_power_w
        duration_s = metric.duration_seconds or 0
        new_tss: float | None = None
        new_if: float | None = None

        if np_w and duration_s > 0:
            new_tss = compute_ride_tss(float(duration_s), float(np_w), ftp_float)
            new_if = round(float(np_w) / ftp_float, 3)

        gap_days = 1
        if prev_date_str is not None:
            try:
                prev_d = _date.fromisoformat(prev_date_str)
                curr_d = _date.fromisoformat(metric.activity_date)
                gap_days = max(1, (curr_d - prev_d).days)
            except ValueError:
                gap_days = 1

        ride_tss = new_tss if new_tss is not None else 0.0
        ctl, atl = apply_ctl_atl_decay(ctl, atl, ride_tss, gap_days=gap_days)
        tsb = ctl - atl
        prev_date_str = metric.activity_date

        metric.tss = round(new_tss, 1) if new_tss is not None else None
        metric.intensity_factor = new_if
        metric.ftp_used = ftp_value
        metric.ctl_after = round(ctl, 2)
        metric.atl_after = round(atl, 2)
        metric.tsb_after = round(tsb, 2)
        updated += 1

    # Replace all snapshots with per-ride snapshots for time-series visualisation.
    await crud.delete_athlete_metric_snapshots(db, user.id)
    for metric in all_metrics:
        if metric.activity_date:
            try:
                ride_dt = datetime.fromisoformat(metric.activity_date).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                logger.warning(
                    "Could not parse activity_date %r for ride metric %s; "
                    "snapshot will use current timestamp.",
                    metric.activity_date,
                    getattr(metric, "strava_activity_id", "unknown"),
                )
                ride_dt = datetime.now(timezone.utc)
        else:
            ride_dt = datetime.now(timezone.utc)

        await crud.create_athlete_metric_snapshot(
            db,
            user.id,
            ftp=ftp_value,
            threshold_hr=user.threshold_heart_rate,
            ctl=round(metric.ctl_after, 1) if metric.ctl_after is not None else None,
            atl=round(metric.atl_after, 1) if metric.atl_after is not None else None,
            tsb=round(metric.tsb_after, 1) if metric.tsb_after is not None else None,
            source="manual_recalculate",
            recorded_at=ride_dt,
        )

    await db.flush()
    return updated, ftp_value
