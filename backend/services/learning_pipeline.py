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
2. **Refresh the weather model** — re-cluster the athlete's ride starts into their
   training location (:func:`services.home_location.infer_home_location`) and
   update the confidence-scored weather tolerances derived from the conditions
   stored on each ride
   (:func:`services.weather_preference.refresh_weather_preferences`).
3. **Detect anomalies** — flag stored knowledge the new evidence contradicts
   (:func:`services.contradiction_detection.detect_user_contradictions`).
4. **Create new hypotheses** — form tentative, testable ideas, both the
   deterministic performance-model hypotheses derived from the freshly refreshed
   model (:func:`services.hypothesis_engine.refresh_performance_hypotheses`) and
   the free-form LLM ones
   (:func:`services.hypothesis_generation.generate_user_hypotheses`).
5. **Resolve open questions** — record new coaching uncertainties and close the
   ones the new data now answers
   (:func:`services.open_question_generation.generate_user_open_questions`).
6. **Ask the athlete** — raise the questions none of the steps above could ever
   settle from data, pinned in the chat for the athlete to answer
   (:func:`services.athlete_inquiry.generate_user_inquiries`).

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
    athlete_inquiry,
    athlete_model_inference,
    contradiction_detection,
    home_location,
    hypothesis_engine,
    hypothesis_generation,
    insight_generation,
    open_question_generation,
    weather_preference,
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
    performance_hypotheses: int = 0
    open_questions: int = 0
    inquiries: int = 0
    performance_model: int = 0
    home_location: int = 0
    weather_preferences: int = 0
    failed_steps: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.observations
            or self.contradictions
            or self.hypotheses
            or self.performance_hypotheses
            or self.open_questions
            or self.inquiries
            or self.performance_model
            or self.home_location
            or self.weather_preferences
        )


async def _refresh_performance_model_step(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Learning-step adapter around the deterministic inference engine (#476).

    Returns 1 when a model was (re)derived, 0 otherwise, so it slots into the
    common ``-> int`` step contract.
    """
    row = await athlete_model_inference.refresh_performance_model(db, user, now=now)
    return 1 if row is not None else 0


async def _refresh_performance_hypotheses_step(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Learning-step adapter around the deterministic hypothesis engine (#479).

    Runs right after the model refresh so it works from the freshly derived model,
    and returns the number of hypotheses asserted so it slots into the common
    ``-> int`` step contract.
    """
    return await hypothesis_engine.refresh_performance_hypotheses(db, user, now=now)


async def _infer_home_location_step(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Learning-step adapter around the ride-start clustering (#495).

    Runs before the weather-preference step so an indoor ride imported in the same
    batch can be weather-tagged from a freshly seeded location. An athlete-set
    location is protected by the crud write gate, so this is safe every sync.
    """
    row = await home_location.infer_home_location(db, user.id, now=now)
    return 1 if row is not None else 0


async def _refresh_weather_preferences_step(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Learning-step adapter around the weather-preference engine (#495)."""
    return await weather_preference.refresh_weather_preferences(db, user, now=now)


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
    result.performance_model = await _run_step(
        "performance_model",
        _refresh_performance_model_step,
        db,
        user,
        now,
        timezone_name,
        result,
    )
    result.performance_hypotheses = await _run_step(
        "performance_hypotheses",
        _refresh_performance_hypotheses_step,
        db,
        user,
        now,
        timezone_name,
        result,
    )
    result.home_location = await _run_step(
        "home_location",
        _infer_home_location_step,
        db,
        user,
        now,
        timezone_name,
        result,
    )
    result.weather_preferences = await _run_step(
        "weather_preferences",
        _refresh_weather_preferences_step,
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
    # Last: what none of the passes above could ever infer is what is left to ask
    # the athlete about, so this runs against a fully reconciled knowledge base
    # and does not ask about something the same sync just worked out (#506).
    result.inquiries = await _run_step(
        "inquiries",
        athlete_inquiry.generate_user_inquiries,
        db,
        user,
        now,
        timezone_name,
        result,
    )

    logger.info(
        "Continuous learning step user_id=%s observations=%s contradictions=%s "
        "hypotheses=%s performance_hypotheses=%s open_questions=%s inquiries=%s "
        "performance_model=%s home_location=%s weather_preferences=%s failed=%s",
        user.id,
        result.observations,
        result.contradictions,
        result.hypotheses,
        result.performance_hypotheses,
        result.open_questions,
        result.inquiries,
        result.performance_model,
        result.home_location,
        result.weather_preferences,
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
