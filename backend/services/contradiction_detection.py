"""Backend-owned periodic detection of contradicted athlete knowledge.

Once a week this job checks each onboarded athlete's stored memory facts against
their recent training data and asks the coach model to flag facts the new
evidence contradicts — e.g. a stored FTP of 320 W while the athlete repeatedly
holds 400 W for 4-minute intervals. Contradicted facts are demoted through
:func:`crud.record_athlete_memory_fact_contradiction`, which lowers their
confidence, moves them to ``needs_validation`` (pulling them from coach prompts),
and records why — so the athlete can confirm or correct them. A later fresh,
consistent observation revives the fact and clears the flag.

Runs just after the weekly insight-generation pass (see
:mod:`services.insight_generation`), so freshly generated insights are on file
before stored knowledge is re-checked.
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
from services.llm import (
    begin_token_usage_collection,
    finish_token_usage_collection,
    resolve_user_provider,
)
from services.prompts import ride_metrics_context_section
from services.scheduler import ScheduledJob

logger = logging.getLogger(__name__)

# Run after the insight-generation window (Monday 03:00) so newly inferred
# insights are already on file when stored knowledge is re-checked.
CONTRADICTION_DETECTION_WEEKDAY = 0  # Monday
CONTRADICTION_DETECTION_HOUR = 4
# The history window fed to the model, and the floor below which there is not
# enough recent evidence to trust a contradiction.
CONTRADICTION_HISTORY_LIMIT = 60
CONTRADICTION_MIN_ACTIVITIES = 3

# Facts eligible to be checked: currently trusted knowledge. Facts already flagged
# for validation are skipped so a lingering disagreement is not re-penalised on
# every run.
_CHECKABLE_STATUSES = ("active", "user_confirmed")


@dataclass(slots=True)
class ContradictionDetectionResult:
    checked: int = 0
    skipped: int = 0
    flagged: int = 0
    failed: int = 0


async def detect_user_contradictions(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Flag stored facts recent training data contradicts, for one athlete.

    Returns the number of facts flagged for validation. Returns 0 when the
    athlete has too little recent history, has no checkable stored facts, or the
    model finds no contradiction.
    """
    if not user.is_onboarded:
        return 0

    facts = await crud.list_athlete_memory_facts(db, user.id, now=now)
    checkable = [fact for fact in facts if fact.status in _CHECKABLE_STATUSES]
    if not checkable:
        return 0

    metrics = await crud.get_ride_metrics_history(
        db, user.id, limit=CONTRADICTION_HISTORY_LIMIT
    )
    if len(metrics) < CONTRADICTION_MIN_ACTIVITIES:
        return 0

    metrics_section = ride_metrics_context_section(metrics, timezone_name)
    if not metrics_section.strip():
        return 0

    usage_token = begin_token_usage_collection()
    try:
        contradictions = await ai_service.detect_athlete_fact_contradictions(
            metrics_section,
            [fact.fact for fact in checkable],
            provider=resolve_user_provider(user),
        )
    finally:
        consumed = finish_token_usage_collection(usage_token)
        if consumed:
            await crud.increment_user_consumed_tokens(db, user, consumed)

    flagged = 0
    for contradiction in contradictions:
        index = contradiction["factIndex"]
        if not (0 <= index < len(checkable)):
            continue
        await crud.record_athlete_memory_fact_contradiction(
            db,
            checkable[index],
            reason=contradiction["reason"],
            now=now,
        )
        flagged += 1
    return flagged


async def run_contradiction_detection(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> ContradictionDetectionResult:
    started = datetime.now(timezone.utc)
    result = ContradictionDetectionResult()
    logger.info("Athlete contradiction detection started")

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
                flagged = await detect_user_contradictions(
                    db,
                    fresh_user,
                    now=now,
                    timezone_name=timezone_name,
                )
                if flagged:
                    result.flagged += flagged
                else:
                    result.skipped += 1
                await db.commit()
        except Exception:
            result.failed += 1
            logger.warning(
                "Athlete contradiction detection failed for user_id=%s",
                user.id,
                exc_info=True,
            )

    duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "Athlete contradiction detection finished checked=%s flagged=%s skipped=%s "
        "failed=%s duration_ms=%s",
        result.checked,
        result.flagged,
        result.skipped,
        result.failed,
        duration_ms,
    )
    return result


def athlete_contradiction_detection_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> ScheduledJob:
    async def _run() -> object:
        return await run_contradiction_detection(
            session_factory,
            timezone_name=settings.app_timezone,
        )

    return ScheduledJob(
        name="athlete-contradiction-detection",
        run=_run,
        next_delay=lambda: seconds_until_next_weekly_run(
            timezone_name=settings.app_timezone,
            weekday=CONTRADICTION_DETECTION_WEEKDAY,
            hour=CONTRADICTION_DETECTION_HOUR,
        ),
    )
