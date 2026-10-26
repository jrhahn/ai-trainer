"""Retiring the hypotheses, open questions and experiments that went stale."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from domain import uncertainty_lifecycle
from crud.experiments import _ATHLETE_EXPERIMENT_OPEN_STATUSES
from crud.hypotheses import _ATHLETE_HYPOTHESIS_OPEN_STATUSES
from crud.open_questions import _ATHLETE_OPEN_QUESTION_OPEN_STATUSES
from crud.uncertainty import record_uncertainty_event


async def expire_stale_uncertainties(
    db: AsyncSession,
    user_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Retire uncertainty records nothing ever came back to (#581).

    Returns ``{channel: expired_count}``. Records are moved to
    ``uncertainty_lifecycle.STATUS_EXPIRED`` rather than deleted: they drop out
    of every list and prompt because those filter on the open statuses, while the
    row itself stays available for the "which uncertainties actually resolved?"
    question a value gate would need. Every expiry writes an audit line.

    Only records the athlete never ruled on are touched — a ``confirmed``,
    ``refuted``, ``answered``, ``completed`` or ``dismissed`` record is a verdict
    someone reached, and verdicts do not expire.
    """
    reference = now or datetime.now(timezone.utc)
    expired: dict[str, int] = {}

    async def _sweep(rows, *, channel, policy, text_of, evidence_of) -> None:
        count = 0
        for row in rows:
            evidence = evidence_of(row)
            if not uncertainty_lifecycle.has_expired(
                last_seen=row.updated_at,
                evidence_count=evidence,
                policy=policy,
                now=reference,
            ):
                continue
            reason = uncertainty_lifecycle.expiry_reason(
                last_seen=row.updated_at,
                evidence_count=evidence,
                policy=policy,
                now=reference,
            )
            age_days = uncertainty_lifecycle.age_in_days(row.updated_at, reference)
            row.status = uncertainty_lifecycle.STATUS_EXPIRED
            row.updated_at = reference
            await record_uncertainty_event(
                db,
                user_id,
                channel=channel,
                event=uncertainty_lifecycle.EVENT_EXPIRED,
                statement=text_of(row),
                record_id=row.id,
                reason=reason,
                evidence_count=evidence,
                age_days=age_days,
                now=reference,
            )
            count += 1
        if count:
            expired[channel] = count

    hypotheses = await db.scalars(
        select(models.AthleteHypothesis).where(
            models.AthleteHypothesis.user_id == user_id,
            models.AthleteHypothesis.status.in_(_ATHLETE_HYPOTHESIS_OPEN_STATUSES),
            models.AthleteHypothesis.category.notin_(
                tuple(uncertainty_lifecycle.DETERMINISTIC_HYPOTHESIS_CATEGORIES)
            ),
        )
    )
    await _sweep(
        list(hypotheses),
        channel=uncertainty_lifecycle.CHANNEL_HYPOTHESIS,
        policy=uncertainty_lifecycle.HYPOTHESIS_POLICY,
        text_of=lambda row: row.statement,
        evidence_of=lambda row: row.evidence_count,
    )

    questions = await db.scalars(
        select(models.AthleteOpenQuestion).where(
            models.AthleteOpenQuestion.user_id == user_id,
            models.AthleteOpenQuestion.status.in_(_ATHLETE_OPEN_QUESTION_OPEN_STATUSES),
        )
    )
    await _sweep(
        list(questions),
        channel=uncertainty_lifecycle.CHANNEL_OPEN_QUESTION,
        policy=uncertainty_lifecycle.OPEN_QUESTION_POLICY,
        text_of=lambda row: row.question,
        evidence_of=lambda row: row.evidence_count,
    )

    experiments = await db.scalars(
        select(models.AthleteExperiment).where(
            models.AthleteExperiment.user_id == user_id,
            models.AthleteExperiment.status.in_(_ATHLETE_EXPERIMENT_OPEN_STATUSES),
        )
    )
    # An experiment has no evidence count — it was either run or it was not — so
    # it is always measured against the unsupported window.
    await _sweep(
        list(experiments),
        channel=uncertainty_lifecycle.CHANNEL_EXPERIMENT,
        policy=uncertainty_lifecycle.EXPERIMENT_POLICY,
        text_of=lambda row: row.protocol,
        evidence_of=lambda _row: 1,
    )

    await db.flush()
    return expired
