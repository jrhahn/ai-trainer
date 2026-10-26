"""Uncertainty events and value decisions, and the athlete's utility weights."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from domain import uncertainty_lifecycle, uncertainty_value
from crud.athlete_models import get_athlete_motivation_model


# ---------------------------------------------------------------------------
# Uncertainty lifecycle (#581)
# ---------------------------------------------------------------------------


async def record_uncertainty_event(
    db: AsyncSession,
    user_id: str,
    *,
    channel: str,
    event: str,
    statement: str,
    record_id: str | None = None,
    reason: str = "",
    evidence_count: int | None = None,
    age_days: int | None = None,
    now: datetime | None = None,
) -> models.AthleteUncertaintyEvent:
    """Write one line of the uncertainty audit trail.

    A record that leaves the set should leave a trace (#581, same reasoning as
    the weight events in #566). ``statement`` is stored verbatim because the
    trace has to outlive the row it describes.
    """
    row = models.AthleteUncertaintyEvent(
        user_id=user_id,
        channel=channel,
        event=event,
        record_id=record_id,
        statement=statement[:2000],
        reason=reason[:2000],
        evidence_count=evidence_count,
        age_days=age_days,
        recorded_at=now or datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row


async def athlete_utility_weights(db: AsyncSession, user_id: str) -> dict:
    """The athlete's utility weights, for the value gate (#582).

    Empty when no motivation model has been built yet, which the gate reads as
    the cold-start vector — a brand-new athlete must not be gated by a model
    nobody has inferred for them.
    """
    row = await get_athlete_motivation_model(db, user_id)
    return getattr(row, "weights", None) or {}


async def record_value_decision(
    db: AsyncSession,
    user_id: str,
    decision: "uncertainty_value.ValueDecision",
    *,
    statement: str,
    now: datetime | None = None,
) -> models.AthleteUncertaintyEvent:
    """Log one verdict from the value gate, kept or dropped (#582).

    Both outcomes are recorded. A decline is the one that was invisible before —
    "we decided not to ask this" had nowhere to be legible — and the raise is
    what a later expiry or resolution has to be paired against to answer "what
    share of raised uncertainties actually resolve?".

    The rules that fired go into ``reason`` by name, the same way a weight change
    carries its rule activations (#566).
    """
    record = decision.as_record()
    rules = ", ".join(firing["rule"] for firing in record["rules"]) or "none"
    return await record_uncertainty_event(
        db,
        user_id,
        channel=decision.channel,
        event=(
            uncertainty_lifecycle.EVENT_RAISED
            if decision.should_raise
            else uncertainty_lifecycle.EVENT_DECLINED_LOW_VALUE
        ),
        statement=statement,
        reason=f"{decision.reason} Rules: {rules}.",
        now=now,
    )


async def list_uncertainty_events(
    db: AsyncSession,
    user_id: str,
    *,
    channel: str | None = None,
    limit: int = 100,
) -> list[models.AthleteUncertaintyEvent]:
    """Newest audit entries first. Never read on the hot path — for inspection."""
    stmt = select(models.AthleteUncertaintyEvent).where(
        models.AthleteUncertaintyEvent.user_id == user_id
    )
    if channel is not None:
        stmt = stmt.where(models.AthleteUncertaintyEvent.channel == channel)
    result = await db.scalars(
        stmt.order_by(
            models.AthleteUncertaintyEvent.recorded_at.desc(),
            models.AthleteUncertaintyEvent.id.desc(),
        ).limit(limit)
    )
    return list(result)
