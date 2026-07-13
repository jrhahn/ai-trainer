"""Continuous athlete-learning pipeline (#388).

After every completed workout is imported, run one learning step so the coach
evolves from the new evidence immediately rather than waiting for the weekly
batch jobs. This is the primary mechanism through which the coach evolves over
time; the weekly jobs (insight/contradiction/hypothesis/open-question
generation) remain registered as a periodic backstop.

The step composes the existing per-athlete learning passes in the same
dependency order the weekly schedule uses, so a single sync leaves the athlete's
knowledge in the same reconciled state a full weekly cycle would:

1. **Analyse + compare + update observations/confidence** — infer durable
   patterns from the new training, compare them against the stored athlete model
   and refresh it (:func:`services.insight_generation.generate_user_insights`).
   Covers issue steps 1, 2, 4 and 5.
2. **Detect anomalies** — flag stored knowledge the new evidence contradicts
   (:func:`services.contradiction_detection.detect_user_contradictions`).
3. **Create new hypotheses** — form tentative, testable ideas
   (:func:`services.hypothesis_generation.generate_user_hypotheses`).
4. **Resolve open questions** — record new coaching uncertainties and close the
   ones the new data now answers
   (:func:`services.open_question_generation.generate_user_open_questions`).

Each step is best-effort: a failure in one is logged and never aborts the others
or the surrounding activity sync. The persisted observations, hypotheses and
open questions *are* the coach's evolving notes (issue step 8) — they surface in
the coach prompts — so the step emits a structured audit line summarising what
was learned rather than clobbering the athlete-editable free-text coach memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

import models
from config import settings
from services import (
    contradiction_detection,
    hypothesis_generation,
    insight_generation,
    open_question_generation,
)

logger = logging.getLogger(__name__)

# A learning step is one per-athlete pass with the common
# ``(db, user, *, now, timezone_name) -> int`` shape shared by the weekly jobs.
LearningStep = Callable[..., Awaitable[int]]


@dataclass(slots=True)
class LearningStepResult:
    """What one continuous-learning pass changed for a single athlete."""

    observations: int = 0
    contradictions: int = 0
    hypotheses: int = 0
    open_questions: int = 0
    failed_steps: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.observations
            or self.contradictions
            or self.hypotheses
            or self.open_questions
        )


async def _run_step(
    name: str,
    step: LearningStep,
    db: AsyncSession,
    user: models.User,
    now: datetime | None,
    timezone_name: str | None,
    result: LearningStepResult,
) -> int:
    """Run one learning step, recording (not raising) any failure."""
    try:
        return await step(db, user, now=now, timezone_name=timezone_name)
    except Exception:
        logger.warning(
            "Continuous learning step %r failed for user_id=%s",
            name,
            user.id,
            exc_info=True,
        )
        result.failed_steps.append(name)
        return 0


async def run_learning_step(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> LearningStepResult:
    """Run one continuous-learning pass for ``user`` and return what changed.

    Writes are flushed into ``db`` but not committed — the caller (activity sync)
    owns the surrounding transaction, matching how the weekly jobs defer the
    commit to their run wrapper. A non-onboarded athlete is skipped.
    """
    result = LearningStepResult()
    if not user.is_onboarded:
        return result

    result.observations = await _run_step(
        "insights",
        insight_generation.generate_user_insights,
        db,
        user,
        now,
        timezone_name,
        result,
    )
    result.contradictions = await _run_step(
        "contradictions",
        contradiction_detection.detect_user_contradictions,
        db,
        user,
        now,
        timezone_name,
        result,
    )
    result.hypotheses = await _run_step(
        "hypotheses",
        hypothesis_generation.generate_user_hypotheses,
        db,
        user,
        now,
        timezone_name,
        result,
    )
    result.open_questions = await _run_step(
        "open_questions",
        open_question_generation.generate_user_open_questions,
        db,
        user,
        now,
        timezone_name,
        result,
    )

    logger.info(
        "Continuous learning step user_id=%s observations=%s contradictions=%s "
        "hypotheses=%s open_questions=%s failed=%s",
        user.id,
        result.observations,
        result.contradictions,
        result.hypotheses,
        result.open_questions,
        ",".join(result.failed_steps) or "none",
    )
    return result


async def learn_from_completed_workouts(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> LearningStepResult | None:
    """Sync-facing entry point: learn from a just-imported workout batch.

    Returns ``None`` when continuous learning is disabled. Guards against any
    unexpected failure so the athlete-learning pass can never break the activity
    sync that triggered it.
    """
    if not settings.continuous_learning_enabled:
        return None
    try:
        return await run_learning_step(
            db, user, now=now, timezone_name=timezone_name
        )
    except Exception:  # pragma: no cover - defensive; steps already self-guard
        logger.warning(
            "Continuous learning aborted for user_id=%s", user.id, exc_info=True
        )
        return None
