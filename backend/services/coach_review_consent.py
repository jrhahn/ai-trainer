"""The athlete's consent to the weekly coach review (ai-trainer-ops#136).

Phase 2 of the coach review (#143) reads other athletes' weeks only with
their explicit consent: the chat and the training are health data (Art. 9
GDPR), and the judge runs at Anthropic in the USA. Each consent is a row of
``models.CoachReviewConsent``; a withdrawal ends it but keeps it, so it stays
demonstrable (Art. 7(1)).

``VERSION`` is the date of the consent text in the settings and on /privacy.
A new text needs a new consent: a row of an older version is not valid.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models

VERSION = "2026-10-20"


def _open(user_id: str):
    return select(models.CoachReviewConsent).where(
        models.CoachReviewConsent.user_id == user_id,
        models.CoachReviewConsent.withdrawn_at.is_(None),
    )


async def valid(db: AsyncSession, user_id: str) -> models.CoachReviewConsent | None:
    """The athlete's consent to the current text, or None."""
    return await db.scalar(
        _open(user_id)
        .where(models.CoachReviewConsent.version == VERSION)
        .order_by(models.CoachReviewConsent.given_at.desc())
        .limit(1)
    )


async def give(
    db: AsyncSession, user_id: str, now: datetime | None = None
) -> models.CoachReviewConsent:
    """Consent to the current text. An existing valid consent stays as it was."""
    consent = await valid(db, user_id)
    if consent is not None:
        return consent
    # A consent to an older text ends with the new one.
    await withdraw(db, user_id, now)
    consent = models.CoachReviewConsent(
        user_id=user_id, version=VERSION, given_at=now or datetime.now(timezone.utc)
    )
    db.add(consent)
    await db.flush()
    return consent


async def withdraw(db: AsyncSession, user_id: str, now: datetime | None = None) -> None:
    """End every open consent; the rows stay."""
    for consent in (await db.scalars(_open(user_id))).all():
        consent.withdrawn_at = now or datetime.now(timezone.utc)
    await db.flush()
