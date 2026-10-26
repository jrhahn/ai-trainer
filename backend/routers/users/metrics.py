"""Fitness and ride metrics, and what the athlete says about a ride.

Also recalculating the metrics and estimating the FTP.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from database import async_session_maker, get_db
from services import metrics_service
from services import rides_pipeline
from services.ride_matching import apply_ride_plan_matches
from services.weather_service import backfill_missing_ride_weather

router = APIRouter(prefix="/users/me", tags=["users"])

logger = logging.getLogger(__name__)


_weather_backfill_users_in_progress: set[str] = set()


def _ride_metric_log_sample(
    rides: list[models.RideMetric], limit: int = 10
) -> list[dict[str, object]]:
    return [
        {
            "id": ride.strava_activity_id,
            "name": ride.activity_name,
            "date": ride.activity_date,
            "sport_type": ride.sport_type,
        }
        for ride in rides[:limit]
    ]


async def _backfill_ride_weather_bg(user_id: str, access_token: str | None) -> None:
    """Background weather backfill so dashboard hydration reads stored data immediately."""
    try:
        async with async_session_maker() as session:
            await backfill_missing_ride_weather(
                session, user_id, access_token, limit=90
            )
            await session.commit()
    except Exception:
        logger.info("Ride weather backfill skipped", exc_info=True)
    finally:
        _weather_backfill_users_in_progress.discard(user_id)


@router.get("/metrics-history", response_model=schemas.MetricsHistoryResponse)
async def get_metrics_history(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.MetricsHistoryResponse:
    snapshots = await crud.get_athlete_metric_history(db, current_user.id)
    return schemas.MetricsHistoryResponse(
        snapshots=[
            schemas.AthleteMetricSnapshotSchema(
                recorded_at=s.recorded_at.isoformat(),
                ftp=s.ftp,
                map_5min=s.map_5min,
                ctl=s.ctl,
                atl=s.atl,
                tsb=s.tsb,
                ctl_by_sport=s.ctl_by_sport,
                source=s.source,
            )
            for s in snapshots
        ]
    )


@router.get("/ride-metrics-history", response_model=schemas.RideMetricHistoryResponse)
async def get_ride_metrics_history(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RideMetricHistoryResponse:
    """Return the most recent 90 per-ride CTL/ATL/TSB records in chronological order.

    Used by the expert-mode time-series charts to show how training load metrics
    develop over time with one data point per ride.
    """
    if (
        current_user.strava_token is not None
        and current_user.id not in _weather_backfill_users_in_progress
    ):
        _weather_backfill_users_in_progress.add(current_user.id)
        background_tasks.add_task(
            _backfill_ride_weather_bg,
            current_user.id,
            current_user.strava_token.access_token,
        )

    rides = await crud.get_ride_metrics_history(db, current_user.id, limit=90)
    existing_plan = await crud.get_training_plan(db, current_user.id)
    if rides and existing_plan is not None:
        await apply_ride_plan_matches(
            db,
            current_user.id,
            existing_plan.plan,
            [ride.strava_activity_id for ride in rides],
        )

    # get_ride_metrics_history returns newest-first; reverse for chronological charting
    rides = list(reversed(rides))
    logger.info(
        "Ride metrics history response user=%s count=%s sample=%s",
        current_user.id,
        len(rides),
        _ride_metric_log_sample(rides),
    )
    return schemas.RideMetricHistoryResponse(
        rides=[
            schemas.RideMetricSchema.model_validate(r, from_attributes=True)
            for r in rides
        ]
    )


@router.patch(
    "/ride-feedback/{strava_activity_id}",
    response_model=schemas.RideFeedbackResponse,
)
async def save_ride_feedback(
    strava_activity_id: int,
    body: schemas.RideFeedbackRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RideFeedbackResponse:
    """Set the athlete's quick "how the legs felt" rating for a Strava activity.

    This is the one-tap dashboard signal.  It writes only the ``feel_legs``
    column (``None`` clears it), so it never clobbers notes captured
    conversationally via the coach chat.  When set, ``feel_legs`` is surfaced to
    every AI coach prompt via ``ride_metrics_context_section()``.  Richer
    feedback — perceived effort, notes, plan-match corrections — is captured in
    conversation with the coach, not here.
    """
    row = await crud.set_ride_feel_legs(
        db,
        current_user.id,
        strava_activity_id,
        body.legs,
        external_activity_id=body.external_activity_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found",
        )

    # The leg-feel rating feeds the login summary, so mark it stale to regenerate.
    await rides_pipeline.notify_changed(db, current_user)

    return schemas.RideFeedbackResponse(
        strava_activity_id=strava_activity_id,
        ride=schemas.RideMetricSchema.model_validate(row, from_attributes=True),
    )


@router.patch(
    "/ride-purpose/{strava_activity_id}",
    response_model=schemas.RidePurposeAnswerResponse,
)
async def answer_ride_purpose(
    strava_activity_id: int,
    body: schemas.RidePurposeAnswerRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RidePurposeAnswerResponse:
    """Record what the athlete says an unclassified session actually was (#580).

    The coach used to ask this in prose inside the login summary, where there
    was nothing to answer with and nowhere for the answer to go. It writes the
    classification itself — so every prompt that already reads ``ride_purpose``
    sees the answer — with a confidence that names the athlete as its source and
    protects it from the next re-import.

    A ``purpose`` of ``None`` is a skip: the question stops without anything
    being asserted about the session.
    """
    row = await crud.answer_ride_purpose_question(
        db,
        current_user.id,
        strava_activity_id,
        body.purpose,
        external_activity_id=body.external_activity_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ride not found",
        )

    # The classification feeds the login summary, so mark it stale to regenerate
    # — otherwise the card keeps describing a session the athlete just named.
    await rides_pipeline.notify_changed(db, current_user)

    return schemas.RidePurposeAnswerResponse(
        strava_activity_id=strava_activity_id,
        ride=schemas.RideMetricSchema.model_validate(row, from_attributes=True),
    )


@router.post("/recalculate-metrics", response_model=schemas.RecalculateMetricsResponse)
async def recalculate_metrics(
    body: schemas.RecalculateMetricsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.RecalculateMetricsResponse:
    """Recompute TSS, CTL, ATL, and TSB for all stored rides using a given FTP."""
    try:
        updated, ftp_used = await metrics_service.recalculate_metrics_for_user(
            db, current_user, body.ftp_override
        )
    except ValueError as exc:
        request_id = getattr(request.state, "request_id", None)
        logger.warning(
            "recalculate-metrics rejected for user %s (request_id=%s): %s",
            current_user.id,
            request_id,
            exc,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return schemas.RecalculateMetricsResponse(updated=updated, ftp_used=ftp_used)


@router.post("/estimate-ftp", response_model=schemas.EstimateFTPResponse)
async def estimate_ftp(
    body: schemas.EstimateFTPRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.EstimateFTPResponse:
    """Save updated heart-rate values and return the user-entered FTP.

    Persists ``max_heart_rate`` to the user profile when supplied so that
    subsequent Strava imports and analyses automatically use the new values.

    Returns the FTP value set directly on the user profile (``current_ftp``).
    FTP is never estimated or derived from activity data.
    """
    # --- Persist new HR values when provided ---
    if body.max_heart_rate is not None:
        current_user.max_heart_rate = body.max_heart_rate
    if body.resting_heart_rate is not None:
        current_user.resting_heart_rate = body.resting_heart_rate
    await db.flush()

    # Return the user-entered FTP directly from the profile
    if current_user.current_ftp:
        return schemas.EstimateFTPResponse(
            estimated_ftp=current_user.current_ftp,
            source="profile",
        )

    return schemas.EstimateFTPResponse(estimated_ftp=None, source="none")
