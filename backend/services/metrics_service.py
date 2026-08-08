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
from services import assessment_pipeline, plan_compliance
from services.analysis import apply_ctl_atl_decay, compute_ride_tss
from services.training_load import LOAD_SOURCE_POWER

logger = logging.getLogger(__name__)


def get_effective_ftp(user: models.User, ftp_override: int | None = None) -> int | None:
    """Return the best available FTP value for *user*.

    Priority order: explicit *ftp_override* → manual profile.
    Returns ``None`` when no FTP is known.
    """
    if ftp_override is not None and ftp_override > 0:
        return ftp_override
    if user.current_ftp:
        return user.current_ftp
    return None


def _last_ride_recommendation(ride_purpose: str | None, tsb_after: float | None) -> str:
    if ride_purpose == "recovery":
        return "Keep the next session easy so the recovery intent stays intact."
    if ride_purpose in {"interval_threshold", "interval_vo2max", "interval_sprints", "mixed"}:
        return "Plan the next hard session only once your legs feel fresh again."
    if ride_purpose == "interval_sweetspot":
        return "Use the next ride to absorb the work before stacking more tempo or threshold time."
    if tsb_after is not None and tsb_after < -10:
        return "Fatigue is elevated after this ride, so prioritise recovery before adding intensity."
    if tsb_after is not None and tsb_after > 10:
        return "You are carrying good freshness, so a quality session next would be well timed."
    return "Follow up with an aerobic ride or rest day depending on how your legs feel."


def build_last_ride_feedback(metric: models.RideMetric, ftp_value: int) -> str:
    parts: list[str] = []

    if metric.summary:
        parts.append(f"{metric.summary}.")
    else:
        parts.append("Most recent ride recalculated.")

    details: list[str] = []
    if metric.avg_power_w is not None:
        details.append(f"avg power {metric.avg_power_w} W")
    if metric.normalized_power_w is not None:
        details.append(f"NP {metric.normalized_power_w} W")
    if metric.intensity_factor is not None:
        details.append(f"IF {metric.intensity_factor:.2f}")
    if metric.tss is not None:
        details.append(f"TSS {round(metric.tss)}")

    if details:
        parts.append(
            f"Recalculated with FTP {ftp_value} W, this ride now scores "
            f"{' · '.join(details)}."
        )
    else:
        parts.append(f"Recalculated with FTP {ftp_value} W.")

    load_bits: list[str] = []
    if metric.ctl_after is not None:
        load_bits.append(f"CTL {metric.ctl_after:.1f}")
    if metric.atl_after is not None:
        load_bits.append(f"ATL {metric.atl_after:.1f}")
    if metric.tsb_after is not None:
        load_bits.append(f"TSB {metric.tsb_after:.1f}")
    if load_bits:
        parts.append(f"Post-ride load is {' · '.join(load_bits)}.")

    parts.append(_last_ride_recommendation(metric.ride_purpose, metric.tsb_after))
    return " ".join(parts)


async def _rebuild_metric_snapshots(
    db: AsyncSession,
    user: models.User,
    all_metrics: list[models.RideMetric],
    ftp_value: int,
) -> None:
    # RideMetric rows carry no power-duration data, so a rebuild cannot
    # recompute the MAP proxy.  Carry the last known value across the wipe and
    # re-attach it to the newest snapshot, otherwise a manual recalculation
    # would silently disable the FTP-vs-MAP plausibility check until the next
    # Strava import.
    preserved_map_5min = await crud.get_latest_map_5min(db, user.id)

    await crud.delete_athlete_metric_snapshots(db, user.id)
    newest_snapshot: models.AthleteMetricSnapshot | None = None
    newest_recorded_at: datetime | None = None
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

        snapshot = await crud.create_athlete_metric_snapshot(
            db,
            user.id,
            ftp=ftp_value,
            ctl=round(metric.ctl_after, 1) if metric.ctl_after is not None else None,
            atl=round(metric.atl_after, 1) if metric.atl_after is not None else None,
            tsb=round(metric.tsb_after, 1) if metric.tsb_after is not None else None,
            source="manual_recalculate",
            recorded_at=ride_dt,
        )
        # ``all_metrics`` is not guaranteed to be date-ordered, so track the
        # newest by timestamp rather than by iteration order.
        if newest_recorded_at is None or ride_dt > newest_recorded_at:
            newest_snapshot = snapshot
            newest_recorded_at = ride_dt

    if newest_snapshot is not None and preserved_map_5min is not None:
        newest_snapshot.map_5min = preserved_map_5min
        await db.flush()


async def _refresh_rider_assessment_feedback(
    db: AsyncSession,
    user: models.User,
    latest_metric: models.RideMetric,
    ftp_value: int,
) -> None:
    if user.rider_assessment is None:
        return

    await crud.upsert_rider_assessment(
        db,
        user.id,
        estimated_ftp=user.rider_assessment.estimated_ftp,
        rider_type=user.rider_assessment.rider_type,
        notes=user.rider_assessment.notes,
        hr_zones=user.rider_assessment.hr_zones,
        ride_insights=user.rider_assessment.ride_insights,
        last_ride_feedback=build_last_ride_feedback(latest_metric, ftp_value),
    )
    # last_ride_feedback feeds the login summary; mark it stale so it regenerates.
    await assessment_pipeline.notify_changed(db, user)


def _recalculate_metric_chain(
    all_metrics: list[models.RideMetric],
    ftp_value: int,
) -> int:
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
        new_source: str | None = metric.tss_source

        if np_w and duration_s > 0:
            new_if = round(float(np_w) / ftp_float, 3)

        # A new FTP moves power-derived load and nothing else. Recomputing every
        # row from power alone would silently delete the loads the ladder
        # established for sessions that never had a power meter, and put those
        # activities back to entering the chain as rest days (#579).
        if metric.tss_source in (None, LOAD_SOURCE_POWER) and np_w and duration_s > 0:
            new_tss = compute_ride_tss(float(duration_s), float(np_w), ftp_float)
            new_source = LOAD_SOURCE_POWER if new_tss is not None else None
        else:
            new_tss = metric.tss

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
        metric.tss_source = new_source if new_tss is not None else None
        metric.intensity_factor = new_if
        metric.ftp_used = ftp_value
        metric.ctl_after = round(ctl, 2)
        metric.atl_after = round(atl, 2)
        metric.tsb_after = round(tsb, 2)
        updated += 1

    return updated


def _rescore_compliance_badges(all_metrics: list[models.RideMetric]) -> None:
    """Re-derive each matched ride's badge from its freshly recomputed metrics.

    An FTP change or a duration correction moves TSS and intensity factor, which
    are exactly what the badge is scored from. Without this the stored badge —
    and therefore what the coach is told the athlete is looking at — would keep
    describing the old numbers (#551).
    """
    for metric in all_metrics:
        snapshot = metric.matched_plan_snapshot
        if not isinstance(snapshot, dict):
            continue
        metric.match_score, metric.match_label = plan_compliance.score_and_label(
            metric, snapshot
        )


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

    updated = _recalculate_metric_chain(all_metrics, ftp_value)
    _rescore_compliance_badges(all_metrics)

    await _rebuild_metric_snapshots(db, user, all_metrics, ftp_value)
    await _refresh_rider_assessment_feedback(db, user, all_metrics[-1], ftp_value)

    await db.flush()
    return updated, ftp_value
