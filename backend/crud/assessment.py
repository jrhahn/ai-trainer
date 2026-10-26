"""The rider assessment, its login summary and training status, metric snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import models


# ---------------------------------------------------------------------------
# RiderAssessment
# ---------------------------------------------------------------------------


async def get_rider_assessment(
    db: AsyncSession, user_id: str
) -> models.RiderAssessment | None:
    """Return the RiderAssessment for a user, or None."""
    return await db.get(models.RiderAssessment, user_id)


async def upsert_rider_assessment(
    db: AsyncSession,
    user_id: str,
    *,
    estimated_ftp: int | None,
    rider_type: str | None = None,
    notes: str | None = None,
    hr_zones: Any | None = None,
    ride_insights: str | None = None,
    last_ride_feedback: str | None = None,
    login_summary: str | None = None,
) -> models.RiderAssessment:
    """Create or update the RiderAssessment for a user and flush.

    Write it through :func:`services.assessment_pipeline.record`, which also
    tells the summary and the badge (ai-trainer-ops#245).

    For updates, ``rider_type``, ``notes``, ``hr_zones`` and ``ride_insights``
    keep the existing values when *None* is passed. ``last_ride_feedback`` and
    ``login_summary`` are only written when the caller provides a non-empty
    value. ``hr_zones`` and ``ride_insights`` used to be rewritten from the
    arguments, so every write of the login summary, which passes neither,
    erased both, and the next summary was written without the ride insights.
    """
    assessment = await get_rider_assessment(db, user_id)
    if assessment is None:
        assessment = models.RiderAssessment(
            user_id=user_id,
            estimated_ftp=estimated_ftp,
            rider_type=rider_type if rider_type is not None else "allrounder",
            notes=notes if notes is not None else "",
            hr_zones=hr_zones,
            ride_insights=ride_insights,
            last_ride_feedback=last_ride_feedback,
            login_summary=login_summary,
        )
        db.add(assessment)
    else:
        assessment.estimated_ftp = estimated_ftp
        if rider_type is not None:
            assessment.rider_type = rider_type
        if notes is not None:
            assessment.notes = notes
        if hr_zones is not None:
            assessment.hr_zones = hr_zones
        if ride_insights is not None:
            assessment.ride_insights = ride_insights
        if last_ride_feedback:
            assessment.last_ride_feedback = last_ride_feedback
        if login_summary:
            assessment.login_summary = login_summary
    await db.flush()
    return assessment


async def invalidate_login_summary(db: AsyncSession, user_id: str) -> bool:
    """Clear the stored login summary so it is regenerated on next load.

    Returns True when an existing non-empty summary was cleared. Used by the
    summary pipeline when the training plan changes.
    """
    assessment = await get_rider_assessment(db, user_id)
    if assessment is None or assessment.login_summary is None:
        return False
    assessment.login_summary = None
    await db.flush()
    return True


async def set_training_status(
    db: AsyncSession,
    user_id: str,
    *,
    label: str | None,
    tone: str | None,
    rationale: str | None,
) -> models.RiderAssessment | None:
    """Persist the coach-authored dashboard status chip (#499).

    Deliberately *not* routed through :func:`upsert_rider_assessment`: the badge
    is the status pipeline's output, and a status-only write must neither touch
    the assessment's other fields nor signal an assessment change.
    """
    assessment = await get_rider_assessment(db, user_id)
    if assessment is None:
        return None
    assessment.training_status_label = label
    assessment.training_status_tone = tone
    assessment.training_status_rationale = rationale
    await db.flush()
    return assessment


async def invalidate_training_status(db: AsyncSession, user_id: str) -> bool:
    """Clear the stored status so it regenerates on the next dashboard load.

    Returns True when a stored status was actually cleared. Used by the status
    pipeline when the plan or the athlete's recorded activity changes.
    """
    assessment = await get_rider_assessment(db, user_id)
    if assessment is None or assessment.training_status_label is None:
        return False
    assessment.training_status_label = None
    assessment.training_status_tone = None
    assessment.training_status_rationale = None
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# AthleteMetricSnapshot
# ---------------------------------------------------------------------------


async def create_athlete_metric_snapshot(
    db: AsyncSession,
    user_id: str,
    *,
    ftp: int | None,
    threshold_hr: int | None = None,
    map_5min: int | None = None,
    ctl: float | None = None,
    atl: float | None = None,
    tsb: float | None = None,
    ctl_by_sport: dict[str, float] | None = None,
    source: str = "strava_analysis",
    recorded_at: datetime | None = None,
) -> models.AthleteMetricSnapshot:
    """Insert a new AthleteMetricSnapshot row and flush.

    ``recorded_at`` defaults to the current UTC time when not provided.  Pass
    an explicit value to back-date a snapshot to the originating ride date.
    """
    kwargs: dict = dict(
        user_id=user_id,
        ftp=ftp,
        map_5min=map_5min,
        ctl=ctl,
        atl=atl,
        tsb=tsb,
        ctl_by_sport=ctl_by_sport,
        source=source,
    )
    if recorded_at is not None:
        kwargs["recorded_at"] = recorded_at
    snapshot = models.AthleteMetricSnapshot(**kwargs)
    db.add(snapshot)
    await db.flush()
    return snapshot


async def get_athlete_metric_history(
    db: AsyncSession,
    user_id: str,
    limit: int = 90,
) -> list[models.AthleteMetricSnapshot]:
    """Return the most recent *limit* AthleteMetricSnapshot rows for a user, oldest first."""
    recent_ids = (
        select(models.AthleteMetricSnapshot.id)
        .where(models.AthleteMetricSnapshot.user_id == user_id)
        .order_by(models.AthleteMetricSnapshot.recorded_at.desc())
        .limit(limit)
        .scalar_subquery()
    )
    result = await db.scalars(
        select(models.AthleteMetricSnapshot)
        .where(models.AthleteMetricSnapshot.id.in_(recent_ids))
        .order_by(models.AthleteMetricSnapshot.recorded_at.asc())
    )
    return list(result)


async def get_latest_map_5min(db: AsyncSession, user_id: str) -> int | None:
    """Return the most recently recorded best 5-minute power, if any.

    Used as the maximal aerobic power reference when sanity-checking a
    user-entered FTP.  Older snapshots predate the column and store ``NULL``,
    so rows without a value are skipped rather than treated as zero.
    """
    return await db.scalar(
        select(models.AthleteMetricSnapshot.map_5min)
        .where(
            models.AthleteMetricSnapshot.user_id == user_id,
            models.AthleteMetricSnapshot.map_5min.isnot(None),
        )
        .order_by(models.AthleteMetricSnapshot.recorded_at.desc())
        .limit(1)
    )


async def delete_athlete_metric_snapshots(db: AsyncSession, user_id: str) -> None:
    """Delete all AthleteMetricSnapshot rows for a user and flush.

    Used when rebuilding the full metric history after an FTP recalculation so
    stale snapshots do not pollute the progression chart.
    """
    await db.execute(
        delete(models.AthleteMetricSnapshot).where(
            models.AthleteMetricSnapshot.user_id == user_id
        )
    )
    await db.flush()
