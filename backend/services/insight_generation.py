"""Backend-owned periodic generation of durable athlete insights.

Once a week this job reviews each onboarded athlete's accumulated training
history and asks the coach model to infer durable patterns — e.g. "performs
best after one recovery day" or "tolerates heat well". Inferred insights are
stored through the normal :func:`crud.observe_athlete_memory_fact` pipeline, so
they strengthen when re-observed on later runs, decay when they stop recurring,
and surface in the existing athlete-memory UI alongside conversation-extracted
facts.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import ai_service
from services.llm import resolve_user_provider
from schemas import AthleteModelSchema
from services.prompts import ride_metrics_context_section
from services.token_accounting import track_llm_usage
from services.per_user_jobs import PerUserJob

logger = logging.getLogger(__name__)

# Run early on Monday, after the nightly plan-maintenance window, so a fresh
# week of training history is available before insights are re-derived.
INSIGHT_GENERATION_WEEKDAY = 0  # Monday
INSIGHT_GENERATION_HOUR = 3
# The history window fed to the model, and the floor below which there is not
# enough signal to infer a durable pattern.
INSIGHT_HISTORY_LIMIT = 60
INSIGHT_MIN_ACTIVITIES = 8


async def generate_user_insights(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Infer and store durable insights for one athlete.

    Returns the number of insights observed (created or strengthened). Returns 0
    when the athlete has too little history or the model finds no durable
    pattern.
    """
    if not user.is_onboarded:
        return 0

    metrics = await crud.get_ride_metrics_history(
        db, user.id, limit=INSIGHT_HISTORY_LIMIT
    )
    if len(metrics) < INSIGHT_MIN_ACTIVITIES:
        return 0

    metrics_section = ride_metrics_context_section(metrics, timezone_name)
    if not metrics_section.strip():
        return 0

    existing = await crud.list_athlete_memory_facts(db, user.id, now=now)
    existing_facts = [fact.fact for fact in existing]

    provider = resolve_user_provider(user)
    async with track_llm_usage(db, user, source="step:insight-generation"):
        candidates = await ai_service.generate_athlete_insights(
            metrics_section,
            existing_facts=existing_facts,
            provider=provider,
        )

    observed = 0
    for candidate in candidates:
        await crud.observe_athlete_memory_fact(
            db,
            user.id,
            fact=candidate["fact"],
            kind=candidate.get("kind", "observation"),
            category=candidate["category"],
            source_snippet=candidate["source_snippet"],
            confidence=candidate["confidence"],
            observed_at=now,
        )
        observed += 1

    # Refresh the long-term structured athlete model (#384). Best-effort: a
    # failure here must never discard the insights observed above.
    await _refresh_athlete_model(db, user, metrics_section, provider)

    return observed


async def _refresh_athlete_model(
    db: AsyncSession,
    user: models.User,
    metrics_section: str,
    provider: str,
) -> None:
    """Derive and persist the long-term athlete model, swallowing failures."""
    existing_model_row = await crud.get_athlete_model(db, user.id)
    current_model = (
        AthleteModelSchema.model_validate(
            existing_model_row, from_attributes=True
        ).model_dump(by_alias=True, mode="json")
        if existing_model_row is not None
        else None
    )

    async with track_llm_usage(db, user, source="step:athlete-model-derivation"):
        try:
            derived_model = await ai_service.derive_athlete_model(
                metrics_section,
                current_model=current_model,
                provider=provider,
            )
        except Exception:
            logger.warning(
                "Athlete model derivation failed for user_id=%s", user.id, exc_info=True
            )
            return

    if derived_model is not None:
        await crud.upsert_athlete_model(db, user.id, **derived_model)


# Run for every athlete with a plan by the shared loop (ai-trainer-ops#252). The
# step is looked up when the job runs, so a test can replace it.
JOB = PerUserJob(
    name="athlete-insight-generation",
    label="Athlete insight generation",
    step=lambda db, user, **when: generate_user_insights(db, user, **when),
    counts=("generated",),
    weekday=INSIGHT_GENERATION_WEEKDAY,
    hour=INSIGHT_GENERATION_HOUR,
)
run_insight_generation = JOB.run
