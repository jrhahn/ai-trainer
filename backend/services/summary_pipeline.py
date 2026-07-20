"""Login-summary pipeline: keeps the dashboard "recent training summary" fresh.

The login summary is produced by an LLM from the rider assessment *and the
current training plan*, so it goes stale whenever either input changes. This
module is a pipeline node that depends on the ``plan`` and ``assessment``
pipelines: when an upstream input changes the summary is invalidated (cleared),
and it is lazily regenerated on the next dashboard load (the frontend
re-requests it when it is missing).

Regeneration is an LLM call, so invalidation is intentionally cheap and the
expensive work is deferred to load time — this debounces the many rapid plan
edits that can happen during a coach conversation.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import ai_service
from services.llm import resolve_user_provider
from services.pipeline_graph import graph

logger = logging.getLogger(__name__)

PIPELINE_NAME = "summary"


async def regenerate(
    db: AsyncSession, user: models.User, *, provider: str | None = None
) -> str | None:
    """Regenerate and persist the login summary from current assessment + plan.

    Returns the new summary, or ``None`` when the user has no rider assessment.
    May raise ``AIRateLimitError`` from the provider; callers map that to HTTP.
    """
    assessment = await crud.get_rider_assessment(db, user.id)
    if assessment is None:
        return None

    existing_plan = await crud.get_training_plan(db, user.id)
    training_plan = existing_plan.plan if existing_plan is not None else None

    # The athlete's one-tap "how the legs felt" rating lives on the ride, not the
    # assessment. Read it at generation time so the summary reflects the real
    # signal rather than an inferred effort number.
    latest_ride = await crud.get_latest_ride_metric(db, user.id)
    feel_legs = latest_ride.feel_legs if latest_ride is not None else None

    login_summary = await ai_service.generate_login_summary(
        ride_insights=assessment.ride_insights,
        last_ride_feedback=assessment.last_ride_feedback,
        notes=assessment.notes,
        estimated_ftp=assessment.estimated_ftp,
        training_plan=training_plan or None,
        provider=provider or resolve_user_provider(user),
        feel_legs=feel_legs,
    )
    if login_summary:
        await crud.upsert_rider_assessment(
            db,
            user.id,
            estimated_ftp=assessment.estimated_ftp,
            login_summary=login_summary,
        )
    return login_summary


async def invalidate(db: AsyncSession, user: models.User) -> bool:
    """Mark the login summary stale by clearing it (lazy regeneration on load)."""
    return await crud.invalidate_login_summary(db, user.id)


async def _on_upstream_changed(
    *, db: AsyncSession, user: models.User, **_: object
) -> None:
    """Pipeline hook: an upstream input (plan or assessment) changed, so the
    summary is now stale."""
    await invalidate(db, user)


graph.register(
    PIPELINE_NAME,
    depends_on=("plan", "assessment"),
    on_upstream_changed=_on_upstream_changed,
)
