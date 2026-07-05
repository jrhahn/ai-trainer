"""Ride↔plan match pipeline: refreshes ride snapshots when the plan changes.

The dashboard renders a *past* ride's planned workout from the ride's
``matched_plan_snapshot`` — the live plan no longer contains days that have
rolled off the rolling window, so the snapshot is the only source. That snapshot
is captured at ride-import time and, before #364, was never refreshed when the
plan later changed: a coach edit to a past day never reached the already-imported
ride, so the dashboard showed "No planned workout found".

This node depends on the ``plan`` pipeline (symmetric with
``services/summary_pipeline.py``). On every plan change it re-runs matching for
the changed dates, so each ride on those dates picks up the latest planned
workout *before* the day drops out of the window.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services.pipeline_graph import graph

logger = logging.getLogger(__name__)

PIPELINE_NAME = "ride_match"


async def refresh(
    db: AsyncSession, user: models.User, *, dates: list[str] | None = None
) -> list[models.RideMetric]:
    """Re-match rides on ``dates`` against the current plan, refreshing snapshots."""
    if not dates:
        return []
    # Imported lazily: ride_matching imports plan_pipeline at module load and
    # plan_pipeline imports this module to register the node, so a top-level
    # import here would close an import cycle.
    from services import ride_matching

    plan_row = await crud.get_training_plan(db, user.id)
    plan = plan_row.plan if plan_row is not None else None
    return await ride_matching.refresh_matches_for_dates(db, user.id, plan, dates)


async def _on_plan_changed(
    *, db: AsyncSession, user: models.User, dates: list[str] | None = None, **_: object
) -> None:
    """Pipeline hook: the plan changed upstream; refresh affected ride snapshots."""
    await refresh(db, user, dates=dates)


graph.register(
    PIPELINE_NAME,
    depends_on=("plan",),
    on_upstream_changed=_on_plan_changed,
)
