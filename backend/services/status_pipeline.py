"""Training-status pipeline: the coach owns the dashboard's status badge.

The badge under the dashboard greeting ("On track" / "Slightly behind" / …) was
computed in the browser from a bare adherence ratio and never left it, so the
coach had never seen the word it was being asked about and invented explanations
for it (#499). This node moves the badge server-side: the deterministic audit in
:mod:`services.training_status` establishes the facts, the coach phrases them,
and the stored result feeds *both* the chip and the ask-trainer prompt — so the
label and the coach's account of it come from the same place and cannot drift.

Structurally this mirrors :mod:`services.summary_pipeline`: it depends on the
plan and on the athlete's recorded activity, invalidation is a cheap clear, and
the expensive LLM call is deferred to the next dashboard load. ``ride_match`` is
an upstream dependency as well as ``plan``, because whether a session counts as
done is decided by ride↔plan matching — re-running matches can flip the badge
even when the plan itself did not change.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import training_status
from services.dates import app_today
from services.llm import resolve_user_provider
from services.pipeline_graph import graph

logger = logging.getLogger(__name__)

PIPELINE_NAME = "training_status"


async def build_facts(
    db: AsyncSession, user: models.User, *, timezone_name: str | None = None
) -> dict:
    """Assemble the deterministic status audit from the stored plan and rides."""
    plan_row = await crud.get_training_plan(db, user.id)
    plan = plan_row.plan if plan_row is not None else None

    today = app_today(timezone_name=timezone_name)
    # Deduplicated by activity identity, so a re-imported ride cannot be credited
    # twice against the same planned session. 60 rows covers the 7-day window
    # comfortably even for an athlete logging several sessions a day.
    rides = await crud.get_ride_metrics_history(db, user.id, limit=60)
    return training_status.build_status_facts(plan, rides, today)


async def regenerate(
    db: AsyncSession,
    user: models.User,
    *,
    provider: str | None = None,
    timezone_name: str | None = None,
) -> tuple[str, str, str]:
    """Recompute and persist the badge, returning ``(label, tone, rationale)``.

    Never returns empty: if the provider call fails or produces something the
    chip cannot render, the deterministic fallback stands in, because the badge
    is part of the dashboard's first paint.
    """
    # Imported lazily: ai_service pulls in a large prompt/provider surface, and
    # this module is imported from plan_pipeline purely to register the node.
    from services import ai_service

    facts = await build_facts(db, user, timezone_name=timezone_name)

    result = None
    try:
        result = await ai_service.generate_training_status(
            facts,
            training_plan=None,
            provider=provider or resolve_user_provider(user),
            timezone_name=timezone_name,
        )
    except Exception:
        # A status badge must never be the reason a dashboard load fails; the
        # deterministic fallback below is always renderable.
        logger.warning("Training-status generation failed", exc_info=True)

    label, tone, rationale = result or training_status.fallback_status(facts)
    await crud.set_training_status(
        db, user.id, label=label, tone=tone, rationale=rationale
    )
    return label, tone, rationale


async def invalidate(db: AsyncSession, user: models.User) -> bool:
    """Mark the badge stale by clearing it (lazy regeneration on next load)."""
    return await crud.invalidate_training_status(db, user.id)


async def _on_upstream_changed(
    *, db: AsyncSession, user: models.User, **_: object
) -> None:
    """Pipeline hook: the plan, the assessment or a ride match changed, so the
    stored badge no longer reflects the athlete's week."""
    await invalidate(db, user)


graph.register(
    PIPELINE_NAME,
    depends_on=("plan", "assessment", "ride_match"),
    on_upstream_changed=_on_upstream_changed,
)
