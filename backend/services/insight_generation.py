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
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import crud
import models
from config import settings
from services import ai_service
from services.dates import app_timezone
from services.llm import resolve_user_provider
from schemas import AthleteModelSchema
from services.prompts import ride_metrics_context_section
from services.scheduler import ScheduledJob
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)

# Run early on Monday, after the nightly plan-maintenance window, so a fresh
# week of training history is available before insights are re-derived.
INSIGHT_GENERATION_WEEKDAY = 0  # Monday
INSIGHT_GENERATION_HOUR = 3
# The history window fed to the model, and the floor below which there is not
# enough signal to infer a durable pattern.
INSIGHT_HISTORY_LIMIT = 60
INSIGHT_MIN_ACTIVITIES = 8


@dataclass(slots=True)
class InsightGenerationResult:
    checked: int = 0
    skipped: int = 0
    generated: int = 0
    failed: int = 0


def seconds_until_next_weekly_run(
    now: datetime | None = None,
    *,
    timezone_name: str | None = None,
    weekday: int = INSIGHT_GENERATION_WEEKDAY,
    hour: int = INSIGHT_GENERATION_HOUR,
) -> float:
    tz = app_timezone(timezone_name)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(tz)
    next_run = datetime.combine(local_now.date(), time(hour=hour), tzinfo=tz)
    days_ahead = (weekday - local_now.weekday()) % 7
    next_run += timedelta(days=days_ahead)
    if local_now >= next_run:
        next_run += timedelta(days=7)
    return max(0.0, (next_run - local_now).total_seconds())


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
    async with track_llm_usage(db, user, source="insight-generation"):
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

    async with track_llm_usage(db, user, source="athlete-model-derivation"):
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


async def run_insight_generation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> InsightGenerationResult:
    started = datetime.now(timezone.utc)
    result = InsightGenerationResult()
    logger.info("Athlete insight generation started")

    async with session_factory() as db:
        users = await crud.get_users_with_training_plans(db)

    for user in users:
        result.checked += 1
        try:
            async with session_factory() as db:
                fresh_user = await crud.get_user_by_id(db, user.id)
                if fresh_user is None:
                    result.skipped += 1
                    continue
                observed = await generate_user_insights(
                    db,
                    fresh_user,
                    now=now,
                    timezone_name=timezone_name,
                )
                if observed:
                    result.generated += observed
                else:
                    result.skipped += 1
                await db.commit()
        except Exception:
            result.failed += 1
            logger.warning(
                "Athlete insight generation failed for user_id=%s",
                user.id,
                exc_info=True,
            )

    duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "Athlete insight generation finished checked=%s generated=%s skipped=%s "
        "failed=%s duration_ms=%s",
        result.checked,
        result.generated,
        result.skipped,
        result.failed,
        duration_ms,
    )
    return result


def athlete_insight_generation_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> ScheduledJob:
    async def _run() -> object:
        return await run_insight_generation(
            session_factory,
            timezone_name=settings.app_timezone,
        )

    return ScheduledJob(
        name="athlete-insight-generation",
        run=_run,
        next_delay=lambda: seconds_until_next_weekly_run(
            timezone_name=settings.app_timezone
        ),
    )
