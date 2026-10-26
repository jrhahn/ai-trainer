"""The coach's predictions, and how they turned out."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from crud.keys import normalise_key
from crud.memory import _clamp_confidence, _normalise_athlete_memory_category


# ---------------------------------------------------------------------------
# AthletePrediction
# ---------------------------------------------------------------------------

# Statuses shown by default: predictions still awaiting an outcome. Evaluated
# (correct/incorrect) predictions are resolved and only surfaced on request
# (management/export views) or when computing accuracy.
_ATHLETE_PREDICTION_OPEN_STATUSES = ("pending",)


_ATHLETE_PREDICTION_RESOLVED_STATUSES = ("correct", "incorrect")


# How much a single evaluation moves the coach's confidence in a prediction: up
# on a correct call, down on a wrong one (see #383's "Confidence reduced").
ATHLETE_PREDICTION_CONFIDENCE_STEP = 0.2


ATHLETE_PREDICTION_DEFAULT_CONFIDENCE = 0.5


async def get_athlete_prediction(
    db: AsyncSession, user_id: str, prediction_id: str
) -> models.AthletePrediction | None:
    """Return one stored prediction owned by a user."""
    return await db.scalar(
        select(models.AthletePrediction).where(
            models.AthletePrediction.id == prediction_id,
            models.AthletePrediction.user_id == user_id,
        )
    )


async def list_athlete_predictions(
    db: AsyncSession,
    user_id: str,
    *,
    include_resolved: bool = False,
) -> list[models.AthletePrediction]:
    """Return stored predictions for a user, newest first.

    By default only ``pending`` predictions (still awaiting an outcome) are
    returned; pass ``include_resolved`` to also include evaluated ones.
    """
    stmt = select(models.AthletePrediction).where(
        models.AthletePrediction.user_id == user_id
    )
    if not include_resolved:
        stmt = stmt.where(
            models.AthletePrediction.status.in_(_ATHLETE_PREDICTION_OPEN_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthletePrediction.created_at.desc(),
            models.AthletePrediction.id.desc(),
        )
    )
    return list(result)


async def get_athlete_prediction_accuracy(
    db: AsyncSession, user_id: str
) -> tuple[int, int]:
    """Return ``(evaluated, correct)`` counts for a user's predictions.

    This is the raw measure of coaching quality: how many predictions have been
    checked against reality and how many of those turned out right. The caller
    derives the hit-rate (``correct / evaluated``) from these counts.
    """
    rows = await db.execute(
        select(
            models.AthletePrediction.status,
            func.count(),
        )
        .where(
            models.AthletePrediction.user_id == user_id,
            models.AthletePrediction.status.in_(_ATHLETE_PREDICTION_RESOLVED_STATUSES),
        )
        .group_by(models.AthletePrediction.status)
    )
    counts = {status: count for status, count in rows.all()}
    correct = counts.get("correct", 0)
    evaluated = correct + counts.get("incorrect", 0)
    return evaluated, correct


async def record_athlete_prediction(
    db: AsyncSession,
    user_id: str,
    *,
    prediction: str,
    expected_outcome: str,
    horizon: str = "",
    category: str = "general",
    confidence: float | None = None,
    observed_at: datetime | None = None,
) -> models.AthletePrediction:
    """Store a new coach prediction or refresh a still-pending duplicate.

    Predictions are keyed on their normalised text so re-recording the same
    claim while it is still ``pending`` refreshes its expected outcome/horizon
    rather than creating a duplicate. A prediction that has already been
    evaluated (``correct``/``incorrect``) is left untouched — it is a historical
    record of a call the coach already got right or wrong.
    """
    cleaned = prediction.strip()
    if not cleaned:
        raise ValueError("prediction must not be empty")
    cleaned_expected = expected_outcome.strip()
    if not cleaned_expected:
        raise ValueError("expected_outcome must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    prediction_key = normalise_key(cleaned)
    resolved_confidence = (
        ATHLETE_PREDICTION_DEFAULT_CONFIDENCE
        if confidence is None
        else _clamp_confidence(confidence)
    )

    existing = await db.scalar(
        select(models.AthletePrediction).where(
            models.AthletePrediction.user_id == user_id,
            models.AthletePrediction.prediction_key == prediction_key,
        )
    )
    if existing is None:
        existing = models.AthletePrediction(
            user_id=user_id,
            prediction=cleaned,
            prediction_key=prediction_key,
            expected_outcome=cleaned_expected,
            horizon=horizon.strip(),
            category=normalized_category,
            confidence=resolved_confidence,
            # Written here and nowhere else: a refresh of a pending duplicate,
            # an evaluation and an athlete's edit all leave it alone (#27).
            stated_confidence=resolved_confidence,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
    elif existing.status == "pending":
        existing.expected_outcome = cleaned_expected
        if horizon:
            existing.horizon = horizon.strip()
        existing.category = normalized_category
        existing.updated_at = now
    await db.flush()
    return existing


async def evaluate_athlete_prediction(
    db: AsyncSession,
    user_id: str,
    prediction_id: str,
    *,
    correct: bool,
    actual_outcome: str,
    evaluated_at: datetime | None = None,
) -> models.AthletePrediction | None:
    """Record the observed outcome of a pending prediction and adjust confidence.

    A correct call nudges the prediction's confidence up by
    :data:`ATHLETE_PREDICTION_CONFIDENCE_STEP`; a wrong one reduces it by the
    same step ("Confidence reduced"). Already-evaluated predictions are returned
    unchanged so a re-run never double-counts an outcome.
    """
    existing = await get_athlete_prediction(db, user_id, prediction_id)
    if existing is None:
        return None
    if existing.status != "pending":
        return existing

    now = evaluated_at or datetime.now(timezone.utc)
    existing.actual_outcome = actual_outcome.strip()
    existing.status = "correct" if correct else "incorrect"
    delta = (
        ATHLETE_PREDICTION_CONFIDENCE_STEP
        if correct
        else -ATHLETE_PREDICTION_CONFIDENCE_STEP
    )
    existing.confidence = _clamp_confidence(existing.confidence + delta)
    existing.evaluated_at = now
    existing.updated_at = now
    await db.flush()
    return existing


async def update_athlete_prediction(
    db: AsyncSession,
    user_id: str,
    prediction_id: str,
    *,
    prediction: str | None = None,
    expected_outcome: str | None = None,
    actual_outcome: str | None = None,
    horizon: str | None = None,
    category: str | None = None,
    confidence: float | None = None,
    status: str | None = None,
) -> models.AthletePrediction | None:
    """Apply an athlete's edit or manual verdict to a prediction and flush.

    Marking a still-pending prediction ``correct`` or ``incorrect`` routes
    through :func:`evaluate_athlete_prediction` so the confidence adjustment and
    ``evaluated_at`` stamp match the automated evaluation path.
    """
    existing = await get_athlete_prediction(db, user_id, prediction_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    if prediction is not None:
        cleaned = prediction.strip()
        if not cleaned:
            raise ValueError("prediction must not be empty")
        existing.prediction = cleaned
        existing.prediction_key = normalise_key(cleaned)
    if expected_outcome is not None:
        cleaned = expected_outcome.strip()
        if not cleaned:
            raise ValueError("expected_outcome must not be empty")
        existing.expected_outcome = cleaned
    if actual_outcome is not None:
        existing.actual_outcome = actual_outcome.strip()
    if horizon is not None:
        existing.horizon = horizon.strip()
    if category is not None:
        existing.category = _normalise_athlete_memory_category(category)
    if confidence is not None:
        existing.confidence = _clamp_confidence(confidence)

    if status in _ATHLETE_PREDICTION_RESOLVED_STATUSES and existing.status == "pending":
        # Reuse the evaluation path for the confidence delta and timestamp; it
        # requires an outcome, so fall back to whatever the caller supplied.
        return await evaluate_athlete_prediction(
            db,
            user_id,
            prediction_id,
            correct=status == "correct",
            actual_outcome=existing.actual_outcome or "",
            evaluated_at=now,
        )
    if status is not None:
        existing.status = status
    existing.updated_at = now
    await db.flush()
    return existing


async def delete_athlete_prediction(
    db: AsyncSession, user_id: str, prediction_id: str
) -> bool:
    """Permanently remove a stored prediction owned by a user."""
    existing = await get_athlete_prediction(db, user_id, prediction_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True
