"""The athlete's motivation, athlete and performance models, with history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from domain import motivation_model


# ---------------------------------------------------------------------------
# AthleteMotivationModel (what the athlete is optimizing for, #562)
# ---------------------------------------------------------------------------


async def get_athlete_motivation_model(
    db: AsyncSession, user_id: str
) -> models.AthleteMotivationModel | None:
    """Return the athlete's stored motivation model, or None (#562)."""
    return await db.get(models.AthleteMotivationModel, user_id)


def motivation_model_as_dict(
    row: models.AthleteMotivationModel | None,
) -> dict[str, Any]:
    """A stored motivation model as a normalized dict, defaults when absent.

    Consumers — the planner (#564), the coach prompt (#565) — always get the
    full shape, so none of them has to special-case an athlete who has no row
    yet. Normalizing on read as well as on write also repairs a row written
    before an invariant existed.
    """
    if row is None:
        return motivation_model.default_model()
    return motivation_model.normalize_model(
        {
            "primary_objective": row.primary_objective,
            "primary_objective_source": row.primary_objective_source,
            "primary_objective_confidence": row.primary_objective_confidence,
            "primary_objective_snippet": row.primary_objective_snippet,
            "secondary_objectives": row.secondary_objectives,
            "constraints": row.constraints,
            "utility_weights": row.utility_weights,
            "pinned_weights": row.pinned_weights,
            "modality_affinity": row.modality_affinity,
        }
    )


def _record_weight_event(
    db: AsyncSession,
    user_id: str,
    *,
    before: dict[str, float],
    after: dict[str, float],
    source: str,
    rules: list[dict[str, Any]] | None,
    evidence: dict[str, Any] | None,
    pinned: list[str],
    now: datetime,
) -> None:
    """Log a utility weight change, if there was one (#566).

    Silent when the vector did not move, which is the common case: most writes to
    the motivation model touch an objective or a constraint and leave the weights
    alone, and a trail full of "nothing changed" is a trail nobody reads. Weights
    are stored rounded to four decimals, so equality here is exact rather than
    approximate.
    """
    if before == after:
        return
    db.add(
        models.AthleteMotivationWeightEvent(
            user_id=user_id,
            source=source,
            weights_before=dict(before),
            weights_after=dict(after),
            deltas={
                component: round(after[component] - before.get(component, 0.0), 4)
                for component in after
                if round(after[component] - before.get(component, 0.0), 4)
            },
            rules=rules or None,
            evidence=evidence or None,
            pinned=list(pinned),
            recorded_at=now,
        )
    )


async def get_motivation_weight_history(
    db: AsyncSession, user_id: str, *, limit: int = 20
) -> list[models.AthleteMotivationWeightEvent]:
    """The athlete's weight changes, newest first (#566)."""
    result = await db.execute(
        select(models.AthleteMotivationWeightEvent)
        .where(models.AthleteMotivationWeightEvent.user_id == user_id)
        .order_by(models.AthleteMotivationWeightEvent.recorded_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def upsert_athlete_motivation_model(
    db: AsyncSession,
    user_id: str,
    *,
    updates: dict[str, Any],
    source: str = motivation_model.SOURCE_INFERRED,
    accrue: bool = False,
    promote: bool = False,
    now: datetime | None = None,
    rules: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
) -> models.AthleteMotivationModel:
    """Merge an update into the athlete's motivation model and flush (#562).

    ``updates`` is a partial model in the shape
    :func:`domain.motivation_model.normalize_model` accepts. The merge — and
    with it the user-set override that stops an inference pass from overwriting
    a hand-stated objective (#342/#345/#346) — happens in that module, so this
    function stays the persistence half and every writer gets the same guard
    whether or not it remembered to ask for one.

    ``accrue`` and ``promote`` are what an inference pass (#563) sets: repeated
    evidence strengthens an entry rather than replacing it, and an entry that has
    earned enough confidence may become the primary objective. A direct athlete
    edit uses neither — it is a statement, not evidence.

    ``rules`` and ``evidence`` are the learning run's account of itself (#566).
    Recording the weight change *here* rather than at the caller is what makes the
    audit trail complete: every writer goes through this function, so no weight
    can move without a row explaining it — including the athlete's own edit, which
    records no rules because nothing was inferred.
    """
    timestamp = now or datetime.now(timezone.utc)
    existing = await get_athlete_motivation_model(db, user_id)
    before = motivation_model_as_dict(existing)
    merged = motivation_model.merge_model(
        motivation_model_as_dict(existing) if existing is not None else None,
        updates,
        source=source,
        accrue=accrue,
        now=timestamp,
    )
    if promote:
        merged = motivation_model.promote_primary_objective(merged, now=timestamp)
    merged["updated_at"] = timestamp
    _record_weight_event(
        db,
        user_id,
        before=before["utility_weights"],
        after=merged["utility_weights"],
        source=source,
        rules=rules,
        evidence=evidence,
        pinned=merged["pinned_weights"],
        now=timestamp,
    )

    if existing is None:
        existing = models.AthleteMotivationModel(user_id=user_id, **merged)
        db.add(existing)
    else:
        for attr, value in merged.items():
            setattr(existing, attr, value)
    await db.flush()
    return existing


# ---------------------------------------------------------------------------
# AthleteModel (long-term structured athlete model, #384)
# ---------------------------------------------------------------------------


async def get_athlete_model(
    db: AsyncSession, user_id: str
) -> models.AthleteModel | None:
    """Return the long-term AthleteModel for a user, or None."""
    return await db.get(models.AthleteModel, user_id)


async def upsert_athlete_model(
    db: AsyncSession,
    user_id: str,
    *,
    ftp_watts: int | None = None,
    vo2max: float | None = None,
    pacing_quality: str = "",
    recovery_ability: str = "",
    threshold_durability: str = "",
    heat_tolerance: str = "",
    preferred_training_style: str = "",
    strengths: list[str] | None = None,
    weaknesses: list[str] | None = None,
    risk_factors: list[str] | None = None,
    summary: str = "",
    confidence: float | None = None,
) -> models.AthleteModel:
    """Create or update a user's long-term athlete model and flush.

    ``confidence`` is only written when explicitly provided so that an athlete's
    manual edit (which omits it) preserves the coach's last confidence score.
    """
    values: dict[str, object] = {
        "ftp_watts": ftp_watts,
        "vo2max": vo2max,
        "pacing_quality": pacing_quality,
        "recovery_ability": recovery_ability,
        "threshold_durability": threshold_durability,
        "heat_tolerance": heat_tolerance,
        "preferred_training_style": preferred_training_style,
        "strengths": list(strengths or []),
        "weaknesses": list(weaknesses or []),
        "risk_factors": list(risk_factors or []),
        "summary": summary,
        "updated_at": datetime.now(timezone.utc),
    }
    if confidence is not None:
        values["confidence"] = confidence

    existing = await get_athlete_model(db, user_id)
    if existing is None:
        existing = models.AthleteModel(user_id=user_id, **values)
        db.add(existing)
    else:
        for attr, value in values.items():
            setattr(existing, attr, value)
    await db.flush()
    return existing


# ---------------------------------------------------------------------------
# AthletePerformanceModel (#475)
# ---------------------------------------------------------------------------


async def get_athlete_performance_model(
    db: AsyncSession, user_id: str
) -> models.AthletePerformanceModel | None:
    """Return the deterministic Athlete Performance Model for a user, or None."""
    return await db.get(models.AthletePerformanceModel, user_id)


async def upsert_athlete_performance_model(
    db: AsyncSession,
    user_id: str,
    *,
    attributes: dict[str, object],
    likely_limiter: str | None = None,
    limiters: list[object] | None = None,
    source_window_days: int | None = None,
    derived_from_rides: int = 0,
) -> models.AthletePerformanceModel:
    """Create or update a user's Athlete Performance Model and flush."""
    values: dict[str, object] = {
        "attributes": attributes,
        "likely_limiter": likely_limiter,
        "limiters": limiters,
        "source_window_days": source_window_days,
        "derived_from_rides": derived_from_rides,
        "updated_at": datetime.now(timezone.utc),
    }
    existing = await get_athlete_performance_model(db, user_id)
    if existing is None:
        existing = models.AthletePerformanceModel(user_id=user_id, **values)
        db.add(existing)
    else:
        for attr, value in values.items():
            setattr(existing, attr, value)
    await db.flush()
    return existing


async def create_athlete_performance_snapshot(
    db: AsyncSession,
    user_id: str,
    *,
    attributes: dict[str, object],
    likely_limiter: str | None = None,
    limiters: list[object] | None = None,
    recorded_at: datetime | None = None,
) -> models.AthletePerformanceSnapshot:
    """Insert a new AthletePerformanceSnapshot row and flush."""
    kwargs: dict = dict(
        user_id=user_id,
        attributes=attributes,
        likely_limiter=likely_limiter,
        limiters=limiters,
    )
    if recorded_at is not None:
        kwargs["recorded_at"] = recorded_at
    snapshot = models.AthletePerformanceSnapshot(**kwargs)
    db.add(snapshot)
    await db.flush()
    return snapshot


async def get_athlete_performance_snapshots(
    db: AsyncSession,
    user_id: str,
    limit: int = 90,
) -> list[models.AthletePerformanceSnapshot]:
    """Return the most recent *limit* performance snapshots for a user, oldest first."""
    recent_ids = (
        select(models.AthletePerformanceSnapshot.id)
        .where(models.AthletePerformanceSnapshot.user_id == user_id)
        .order_by(models.AthletePerformanceSnapshot.recorded_at.desc())
        .limit(limit)
        .scalar_subquery()
    )
    result = await db.scalars(
        select(models.AthletePerformanceSnapshot)
        .where(models.AthletePerformanceSnapshot.id.in_(recent_ids))
        .order_by(models.AthletePerformanceSnapshot.recorded_at.asc())
    )
    return list(result)
