"""The validation experiments the coach suggests."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from domain import uncertainty_lifecycle
from crud.keys import normalise_key
from crud.memory import _normalise_athlete_memory_category
from crud.uncertainty import record_uncertainty_event


# ---------------------------------------------------------------------------
# AthleteExperiment
# ---------------------------------------------------------------------------

# Statuses shown by default: experiments still awaiting the athlete to run them.
# Completed/dismissed experiments are resolved and only surfaced on request.
_ATHLETE_EXPERIMENT_OPEN_STATUSES = ("suggested",)


async def get_athlete_experiment(
    db: AsyncSession, user_id: str, experiment_id: str
) -> models.AthleteExperiment | None:
    """Return one validation experiment owned by a user."""
    return await db.scalar(
        select(models.AthleteExperiment).where(
            models.AthleteExperiment.id == experiment_id,
            models.AthleteExperiment.user_id == user_id,
        )
    )


async def list_athlete_experiments(
    db: AsyncSession,
    user_id: str,
    *,
    include_resolved: bool = False,
) -> list[models.AthleteExperiment]:
    """Return suggested validation experiments for a user, newest first."""
    stmt = select(models.AthleteExperiment).where(
        models.AthleteExperiment.user_id == user_id
    )
    if not include_resolved:
        stmt = stmt.where(
            models.AthleteExperiment.status.in_(_ATHLETE_EXPERIMENT_OPEN_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthleteExperiment.updated_at.desc(),
            models.AthleteExperiment.id.desc(),
        )
    )
    return list(result)


async def suggest_athlete_experiment(
    db: AsyncSession,
    user_id: str,
    *,
    question: str,
    protocol: str,
    rationale: str = "",
    category: str = "general",
    hypothesis_id: str | None = None,
    observed_at: datetime | None = None,
    max_open: int | None = None,
) -> models.AthleteExperiment | None:
    """Create a validation experiment or refresh an existing one.

    Experiments are keyed on their normalised ``protocol`` so re-proposing the
    same experiment does not create duplicates: a matching row keeps its identity
    while its question/rationale are refreshed, and one the athlete had
    ``dismissed`` is revived to ``suggested`` so a recurring uncertainty resurfaces.
    A ``completed`` experiment is left untouched — the athlete has already run it.

    ``max_open`` is the channel ceiling (#581). An experiment asks the athlete to
    *do* something; more than a few outstanding is not a plan, it is homework,
    and 20 suggested with 0 run is what that looked like. A new protocol is
    declined once the list is full, with an audit line.
    """
    cleaned_protocol = protocol.strip()
    if not cleaned_protocol:
        raise ValueError("protocol must not be empty")
    cleaned_question = question.strip()
    if not cleaned_question:
        raise ValueError("question must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    protocol_key = normalise_key(cleaned_protocol)

    existing = await db.scalar(
        select(models.AthleteExperiment).where(
            models.AthleteExperiment.user_id == user_id,
            models.AthleteExperiment.protocol_key == protocol_key,
        )
    )
    if existing is None:
        if (
            max_open is not None
            and await count_open_athlete_experiments(db, user_id) >= max_open
        ):
            await record_uncertainty_event(
                db,
                user_id,
                channel=uncertainty_lifecycle.CHANNEL_EXPERIMENT,
                event=uncertainty_lifecycle.EVENT_DECLINED,
                statement=cleaned_protocol,
                reason=f"Channel already holds {max_open} suggested experiments.",
                now=now,
            )
            return None
        existing = models.AthleteExperiment(
            user_id=user_id,
            hypothesis_id=hypothesis_id,
            question=cleaned_question,
            protocol=cleaned_protocol,
            protocol_key=protocol_key,
            rationale=rationale.strip(),
            category=normalized_category,
            status="suggested",
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
    elif existing.status != "completed":
        existing.question = cleaned_question
        if rationale:
            existing.rationale = rationale.strip()
        if hypothesis_id is not None:
            existing.hypothesis_id = hypothesis_id
        if existing.status in ("dismissed", uncertainty_lifecycle.STATUS_EXPIRED):
            existing.status = "suggested"
        existing.updated_at = now
    await db.flush()
    return existing


async def update_athlete_experiment(
    db: AsyncSession,
    user_id: str,
    experiment_id: str,
    *,
    question: str | None = None,
    protocol: str | None = None,
    rationale: str | None = None,
    category: str | None = None,
    status: str | None = None,
) -> models.AthleteExperiment | None:
    """Apply an athlete's edit or verdict to a validation experiment and flush."""
    existing = await get_athlete_experiment(db, user_id, experiment_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    if question is not None:
        cleaned = question.strip()
        if not cleaned:
            raise ValueError("question must not be empty")
        existing.question = cleaned
    if protocol is not None:
        cleaned = protocol.strip()
        if not cleaned:
            raise ValueError("protocol must not be empty")
        existing.protocol = cleaned
        existing.protocol_key = normalise_key(cleaned)
    if rationale is not None:
        existing.rationale = rationale.strip()
    if category is not None:
        existing.category = _normalise_athlete_memory_category(category)
    if status is not None:
        existing.status = status
    existing.updated_at = now
    await db.flush()
    return existing


async def delete_athlete_experiment(
    db: AsyncSession, user_id: str, experiment_id: str
) -> bool:
    """Permanently remove a validation experiment owned by a user."""
    existing = await get_athlete_experiment(db, user_id, experiment_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


async def count_open_athlete_experiments(db: AsyncSession, user_id: str) -> int:
    result = await db.scalar(
        select(func.count())
        .select_from(models.AthleteExperiment)
        .where(
            models.AthleteExperiment.user_id == user_id,
            models.AthleteExperiment.status.in_(_ATHLETE_EXPERIMENT_OPEN_STATUSES),
        )
    )
    return int(result or 0)
