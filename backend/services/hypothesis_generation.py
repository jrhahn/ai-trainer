"""Backend-owned periodic generation of explicit athlete hypotheses.

Once a week this job reviews each onboarded athlete's accumulated training
history and asks the coach model to form explicit HYPOTHESES — tentative,
testable ideas about cause and effect (e.g. "upper-body strength training
suppresses heart-rate response the following day") that are worth tracking but
not yet trusted enough to act on. Unlike durable insights (see
:mod:`services.insight_generation`), a hypothesis is stored ``proposed`` and
surfaced to the athlete for validation; confirming one promotes it into a memory
fact so it can inform coaching.

Hypotheses are stored through :func:`crud.propose_athlete_hypothesis`, so a
recurring idea accrues evidence and grows in confidence on later runs. The job
runs after the weekly contradiction-detection pass so it works from a freshly
reconciled knowledge base.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import crud
import models
from config import settings
from services import ai_service
from services.insight_generation import seconds_until_next_weekly_run
from services.llm import resolve_user_provider
from services.prompts import ride_metrics_context_section
from services.scheduler import ScheduledJob
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)

# Run after the contradiction-detection window (Monday 04:00) so hypotheses are
# formed against a freshly reconciled knowledge base.
HYPOTHESIS_GENERATION_WEEKDAY = 0  # Monday
HYPOTHESIS_GENERATION_HOUR = 5
# The history window fed to the model, and the floor below which there is not
# enough signal to form a testable hypothesis.
HYPOTHESIS_HISTORY_LIMIT = 60
HYPOTHESIS_MIN_ACTIVITIES = 8


@dataclass(slots=True)
class HypothesisGenerationResult:
    checked: int = 0
    skipped: int = 0
    generated: int = 0
    failed: int = 0


async def generate_user_hypotheses(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Form and store explicit hypotheses for one athlete.

    Returns the number of hypotheses proposed (created or strengthened). Returns
    0 when the athlete has too little history or the model finds no idea worth
    testing.
    """
    if not user.is_onboarded:
        return 0

    metrics = await crud.get_ride_metrics_history(
        db, user.id, limit=HYPOTHESIS_HISTORY_LIMIT
    )
    if len(metrics) < HYPOTHESIS_MIN_ACTIVITIES:
        return 0

    metrics_section = ride_metrics_context_section(metrics, timezone_name)
    if not metrics_section.strip():
        return 0

    facts = await crud.list_athlete_memory_facts(db, user.id, now=now)
    existing_facts = [fact.fact for fact in facts]
    hypotheses = await crud.list_athlete_hypotheses(
        db, user.id, include_resolved=True
    )
    existing_hypotheses = [hypothesis.statement for hypothesis in hypotheses]

    async with track_llm_usage(db, user, source="step:hypothesis-generation"):
        candidates = await ai_service.generate_athlete_hypotheses(
            metrics_section,
            existing_facts=existing_facts,
            existing_hypotheses=existing_hypotheses,
            provider=resolve_user_provider(user),
        )

    proposed = 0
    for candidate in candidates:
        await crud.propose_athlete_hypothesis(
            db,
            user.id,
            statement=candidate["statement"],
            category=candidate["category"],
            rationale=candidate["rationale"],
            confidence=candidate["confidence"],
            observed_at=now,
        )
        proposed += 1
    return proposed


async def run_hypothesis_generation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> HypothesisGenerationResult:
    started = datetime.now(timezone.utc)
    result = HypothesisGenerationResult()
    logger.info("Athlete hypothesis generation started")

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
                proposed = await generate_user_hypotheses(
                    db,
                    fresh_user,
                    now=now,
                    timezone_name=timezone_name,
                )
                if proposed:
                    result.generated += proposed
                else:
                    result.skipped += 1
                await db.commit()
        except Exception:
            result.failed += 1
            logger.warning(
                "Athlete hypothesis generation failed for user_id=%s",
                user.id,
                exc_info=True,
            )

    duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "Athlete hypothesis generation finished checked=%s generated=%s skipped=%s "
        "failed=%s duration_ms=%s",
        result.checked,
        result.generated,
        result.skipped,
        result.failed,
        duration_ms,
    )
    return result


def athlete_hypothesis_generation_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> ScheduledJob:
    async def _run() -> object:
        return await run_hypothesis_generation(
            session_factory,
            timezone_name=settings.app_timezone,
        )

    return ScheduledJob(
        name="athlete-hypothesis-generation",
        run=_run,
        next_delay=lambda: seconds_until_next_weekly_run(
            timezone_name=settings.app_timezone,
            weekday=HYPOTHESIS_GENERATION_WEEKDAY,
            hour=HYPOTHESIS_GENERATION_HOUR,
        ),
    )
