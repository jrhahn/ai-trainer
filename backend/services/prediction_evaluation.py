"""Backend-owned periodic evaluation of the coach's own predictions.

Once a week this job closes the coaching-quality loop for each onboarded athlete
(#383). It does two things against a fresh view of the athlete's training data:

1. **Evaluate** the predictions the coach made earlier that are still ``pending``
   — scoring each ``correct`` or ``incorrect`` where the data can now settle it,
   filling in what actually happened, and nudging the prediction's confidence up
   on a hit or down on a miss ("Confidence reduced").
2. **Make** new, checkable predictions from the latest history so there is always
   something to score on the next pass.

Predictions are stored through :func:`crud.record_athlete_prediction` (keyed on
their text so a recurring claim is refreshed, not duplicated) and resolved
through :func:`crud.evaluate_athlete_prediction`. The job runs after the weekly
validation-experiment pass so it works from the athlete's freshest data.
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

# Run after the validation-experiment window (Monday 06:00) so predictions are
# scored and made against the athlete's freshest training data.
PREDICTION_EVALUATION_WEEKDAY = 0  # Monday
PREDICTION_EVALUATION_HOUR = 7
# The history window fed to the model, and the floor below which there is not
# enough signal to make or judge a prediction.
PREDICTION_HISTORY_LIMIT = 60
PREDICTION_MIN_ACTIVITIES = 8


@dataclass(slots=True)
class PredictionEvaluationResult:
    checked: int = 0
    skipped: int = 0
    evaluated: int = 0
    generated: int = 0
    failed: int = 0


async def evaluate_user_predictions(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> tuple[int, int]:
    """Evaluate pending predictions and make new ones for one athlete.

    Returns ``(evaluated, generated)`` — how many pending predictions were
    scored and how many new predictions were recorded. Returns ``(0, 0)`` when
    the athlete has too little history to work from.
    """
    if not user.is_onboarded:
        return 0, 0

    metrics = await crud.get_ride_metrics_history(
        db, user.id, limit=PREDICTION_HISTORY_LIMIT
    )
    if len(metrics) < PREDICTION_MIN_ACTIVITIES:
        return 0, 0

    metrics_section = ride_metrics_context_section(metrics, timezone_name)
    if not metrics_section.strip():
        return 0, 0

    provider = resolve_user_provider(user)
    evaluated = 0
    generated = 0

    async with track_llm_usage(db, user, source="prediction-evaluation"):
        pending = await crud.list_athlete_predictions(db, user.id)
        if pending:
            payload = [
                {
                    "prediction": prediction.prediction,
                    "expected_outcome": prediction.expected_outcome,
                    "horizon": prediction.horizon,
                }
                for prediction in pending
            ]
            verdicts = await ai_service.evaluate_athlete_predictions(
                metrics_section,
                payload,
                provider=provider,
            )
            for verdict in verdicts:
                prediction = pending[verdict["index"]]
                resolved = await crud.evaluate_athlete_prediction(
                    db,
                    user.id,
                    prediction.id,
                    correct=verdict["correct"],
                    actual_outcome=verdict["actual_outcome"],
                    evaluated_at=now,
                )
                if resolved is not None:
                    evaluated += 1

        existing = await crud.list_athlete_predictions(
            db, user.id, include_resolved=True
        )
        existing_texts = [prediction.prediction for prediction in existing]
        candidates = await ai_service.generate_athlete_predictions(
            metrics_section,
            existing_predictions=existing_texts,
            provider=provider,
        )
        for candidate in candidates:
            await crud.record_athlete_prediction(
                db,
                user.id,
                prediction=candidate["prediction"],
                expected_outcome=candidate["expected_outcome"],
                horizon=candidate["horizon"],
                category=candidate["category"],
                confidence=candidate["confidence"],
                observed_at=now,
            )
            generated += 1

    return evaluated, generated


async def run_prediction_evaluation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> PredictionEvaluationResult:
    started = datetime.now(timezone.utc)
    result = PredictionEvaluationResult()
    logger.info("Prediction evaluation started")

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
                evaluated, generated = await evaluate_user_predictions(
                    db,
                    fresh_user,
                    now=now,
                    timezone_name=timezone_name,
                )
                if evaluated or generated:
                    result.evaluated += evaluated
                    result.generated += generated
                else:
                    result.skipped += 1
                await db.commit()
        except Exception:
            result.failed += 1
            logger.warning(
                "Prediction evaluation failed for user_id=%s",
                user.id,
                exc_info=True,
            )

    duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "Prediction evaluation finished checked=%s evaluated=%s generated=%s "
        "skipped=%s failed=%s duration_ms=%s",
        result.checked,
        result.evaluated,
        result.generated,
        result.skipped,
        result.failed,
        duration_ms,
    )
    return result


def prediction_evaluation_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> ScheduledJob:
    async def _run() -> object:
        return await run_prediction_evaluation(
            session_factory,
            timezone_name=settings.app_timezone,
        )

    return ScheduledJob(
        name="prediction-evaluation",
        run=_run,
        next_delay=lambda: seconds_until_next_weekly_run(
            timezone_name=settings.app_timezone,
            weekday=PREDICTION_EVALUATION_WEEKDAY,
            hour=PREDICTION_EVALUATION_HOUR,
        ),
    )
