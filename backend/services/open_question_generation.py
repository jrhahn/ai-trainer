"""Backend-owned periodic maintenance of the athlete's open-questions list (#385).

Once a week this job reviews each onboarded athlete's accumulated training
history and asks the coach model to maintain an explicit OPEN QUESTIONS list —
the specific, coaching-relevant uncertainties the record cannot yet answer
(e.g. "Is FTP underestimated?"), each with the evidence that raises it and what
it still needs to be settled. Unlike a hypothesis (a tentative answer) or a
durable insight (a supported pattern), an open question is the coach openly
tracking what it does not yet know.

Questions are stored through :func:`crud.record_athlete_open_question`, so a
recurring question accrues evidence on later runs and auto-closes once enough
evidence exists. When the model judges the history now answers a tracked
question it is closed with a short resolution. The job runs after the weekly
hypothesis-generation pass so it works from a freshly reconciled knowledge base.
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

# Run after the hypothesis-generation window (Monday 05:00) so questions are
# maintained against a freshly reconciled knowledge base.
OPEN_QUESTION_GENERATION_WEEKDAY = 0  # Monday
OPEN_QUESTION_GENERATION_HOUR = 6
# The history window fed to the model, and the floor below which there is not
# enough signal to raise a meaningful open question.
OPEN_QUESTION_HISTORY_LIMIT = 60
OPEN_QUESTION_MIN_ACTIVITIES = 8


@dataclass(slots=True)
class OpenQuestionGenerationResult:
    checked: int = 0
    skipped: int = 0
    generated: int = 0
    failed: int = 0


async def generate_user_open_questions(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Maintain the open-questions list for one athlete.

    Returns the number of questions recorded (created, re-evidenced, or resolved).
    Returns 0 when the athlete has too little history or the model finds nothing
    worth asking.
    """
    if not user.is_onboarded:
        return 0

    metrics = await crud.get_ride_metrics_history(
        db, user.id, limit=OPEN_QUESTION_HISTORY_LIMIT
    )
    if len(metrics) < OPEN_QUESTION_MIN_ACTIVITIES:
        return 0

    metrics_section = ride_metrics_context_section(metrics, timezone_name)
    if not metrics_section.strip():
        return 0

    facts = await crud.list_athlete_memory_facts(db, user.id, now=now)
    existing_facts = [fact.fact for fact in facts]
    questions = await crud.list_athlete_open_questions(
        db, user.id, include_resolved=True
    )
    existing_questions = [question.question for question in questions]

    async with track_llm_usage(db, user, source="open-question-generation"):
        candidates = await ai_service.generate_open_questions(
            metrics_section,
            existing_facts=existing_facts,
            existing_questions=existing_questions,
            provider=resolve_user_provider(user),
        )

    recorded = 0
    for candidate in candidates:
        await crud.record_athlete_open_question(
            db,
            user.id,
            question=candidate["question"],
            category=candidate["category"],
            evidence=candidate["evidence"],
            needs=candidate["needs"],
            resolved=candidate["resolved"],
            resolution=candidate["resolution"],
            observed_at=now,
        )
        recorded += 1
    return recorded


async def run_open_question_generation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> OpenQuestionGenerationResult:
    started = datetime.now(timezone.utc)
    result = OpenQuestionGenerationResult()
    logger.info("Athlete open-question generation started")

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
                recorded = await generate_user_open_questions(
                    db,
                    fresh_user,
                    now=now,
                    timezone_name=timezone_name,
                )
                if recorded:
                    result.generated += recorded
                else:
                    result.skipped += 1
                await db.commit()
        except Exception:
            result.failed += 1
            logger.warning(
                "Athlete open-question generation failed for user_id=%s",
                user.id,
                exc_info=True,
            )

    duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "Athlete open-question generation finished checked=%s generated=%s "
        "skipped=%s failed=%s duration_ms=%s",
        result.checked,
        result.generated,
        result.skipped,
        result.failed,
        duration_ms,
    )
    return result


def athlete_open_question_generation_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> ScheduledJob:
    async def _run() -> object:
        return await run_open_question_generation(
            session_factory,
            timezone_name=settings.app_timezone,
        )

    return ScheduledJob(
        name="athlete-open-question-generation",
        run=_run,
        next_delay=lambda: seconds_until_next_weekly_run(
            timezone_name=settings.app_timezone,
            weekday=OPEN_QUESTION_GENERATION_WEEKDAY,
            hour=OPEN_QUESTION_GENERATION_HOUR,
        ),
    )
