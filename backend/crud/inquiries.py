"""The questions the coach puts to the athlete, and the answers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from crud.keys import normalise_key
from crud.memory import _normalise_athlete_memory_category


# ---------------------------------------------------------------------------
# AthleteInquiry
# ---------------------------------------------------------------------------

# Statuses shown by default: inquiries still waiting on the athlete.
_ATHLETE_INQUIRY_OPEN_STATUSES = ("pending",)


# How many times one inquiry may be put to the athlete: the first ask plus a
# single kind rephrasing. After that the coach hands off to Settings rather than
# asking a third time (#506).
ATHLETE_INQUIRY_MAX_ASKS = 2


# Pending inquiries shown at once. The coach asks about a couple of things, not
# a questionnaire — extra candidates wait until these are dealt with.
ATHLETE_INQUIRY_MAX_PENDING = 2


# Confidence given to a fact the athlete stated in answer to a direct question.
# It is first-hand testimony, so it is trusted immediately, unlike an inferred
# observation that has to earn trust through recurrence.
ATHLETE_INQUIRY_ANSWER_CONFIDENCE = 0.9


async def get_athlete_inquiry(
    db: AsyncSession, user_id: str, inquiry_id: str
) -> models.AthleteInquiry | None:
    """Return one inquiry owned by a user."""
    return await db.scalar(
        select(models.AthleteInquiry).where(
            models.AthleteInquiry.id == inquiry_id,
            models.AthleteInquiry.user_id == user_id,
        )
    )


async def list_athlete_inquiries(
    db: AsyncSession,
    user_id: str,
    *,
    include_resolved: bool = False,
) -> list[models.AthleteInquiry]:
    """Return the athlete's inquiries, oldest ask first.

    Pending inquiries are returned in the order they were raised so the chat pins
    the question that has been waiting longest rather than the newest one.
    """
    stmt = select(models.AthleteInquiry).where(models.AthleteInquiry.user_id == user_id)
    if not include_resolved:
        stmt = stmt.where(
            models.AthleteInquiry.status.in_(_ATHLETE_INQUIRY_OPEN_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthleteInquiry.asked_at,
            models.AthleteInquiry.id,
        )
    )
    return list(result)


async def count_pending_athlete_inquiries(db: AsyncSession, user_id: str) -> int:
    """Return how many inquiries are currently waiting on the athlete."""
    result = await db.scalar(
        select(func.count())
        .select_from(models.AthleteInquiry)
        .where(
            models.AthleteInquiry.user_id == user_id,
            models.AthleteInquiry.status.in_(_ATHLETE_INQUIRY_OPEN_STATUSES),
        )
    )
    return int(result or 0)


async def record_athlete_inquiry(
    db: AsyncSession,
    user_id: str,
    *,
    question: str,
    category: str = "general",
    why_asking: str = "",
    settings_hint: str = "",
    observed_at: datetime | None = None,
    options: list[dict] | None = None,
    hypothesis_id: str | None = None,
) -> models.AthleteInquiry | None:
    """Raise a new inquiry, or return ``None`` when it is already on file.

    ``options`` and ``hypothesis_id`` are a starting question's tap answers and
    the hypothesis it tests (ai-trainer-ops#154).

    Unlike :func:`record_athlete_open_question`, re-raising does **not** accrue
    evidence: asking the athlete the same thing again is nagging, not learning.
    An inquiry the athlete already answered or that was handed off to Settings
    stays closed; only a dismissed one is reopened, since a skip means "not now"
    rather than "never".
    """
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("question must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    question_key = normalise_key(cleaned)

    # Matched on the question alone, deliberately ignoring the category: the model
    # files the same question under a different slug from one run to the next, and
    # "never asked twice" has to survive that. The unique index keeps the stricter
    # (category, question) pair as a backstop.
    existing = await db.scalar(
        select(models.AthleteInquiry).where(
            models.AthleteInquiry.user_id == user_id,
            models.AthleteInquiry.question_key == question_key,
        )
    )
    if existing is not None:
        if existing.status != "dismissed":
            return None
        existing.status = "pending"
        existing.question = cleaned
        existing.ask_count = 1
        existing.follow_up_note = None
        existing.asked_at = now
        existing.updated_at = now
        await db.flush()
        return existing

    inquiry = models.AthleteInquiry(
        user_id=user_id,
        question=cleaned,
        question_key=question_key,
        category=normalized_category,
        why_asking=why_asking.strip(),
        settings_hint=settings_hint.strip(),
        status="pending",
        ask_count=1,
        asked_at=now,
        updated_at=now,
        options=options or None,
        hypothesis_id=hypothesis_id,
    )
    db.add(inquiry)
    await db.flush()
    return inquiry


async def resolve_athlete_inquiry(
    db: AsyncSession,
    inquiry: models.AthleteInquiry,
    *,
    answer: str,
    now: datetime | None = None,
) -> models.AthleteInquiry:
    """Close an inquiry the athlete's answer resolved."""
    reference = now or datetime.now(timezone.utc)
    inquiry.answer = answer.strip()
    inquiry.status = "answered"
    inquiry.follow_up_note = None
    inquiry.answered_at = reference
    inquiry.updated_at = reference
    await db.flush()
    return inquiry


async def reask_athlete_inquiry(
    db: AsyncSession,
    inquiry: models.AthleteInquiry,
    *,
    answer: str,
    question: str,
    note: str,
    now: datetime | None = None,
) -> models.AthleteInquiry:
    """Put a rephrased version of the inquiry back to the athlete.

    Keeps the answer that missed — partial information is still worth having —
    and leaves ``question_key`` untouched so the rephrasing cannot be raised as a
    second, separate inquiry.
    """
    reference = now or datetime.now(timezone.utc)
    inquiry.answer = answer.strip()
    inquiry.question = question.strip() or inquiry.question
    inquiry.follow_up_note = note.strip() or None
    inquiry.ask_count += 1
    inquiry.asked_at = reference
    inquiry.updated_at = reference
    await db.flush()
    return inquiry


async def hand_off_athlete_inquiry(
    db: AsyncSession,
    inquiry: models.AthleteInquiry,
    *,
    answer: str,
    note: str,
    now: datetime | None = None,
) -> models.AthleteInquiry:
    """Stop asking and point the athlete at Settings instead (#506).

    Reached when the rephrased question still did not land. The inquiry leaves
    the chat pin for good; whatever the athlete did say is kept on the record.
    """
    reference = now or datetime.now(timezone.utc)
    inquiry.answer = answer.strip()
    inquiry.status = "needs_settings"
    inquiry.follow_up_note = note.strip() or None
    inquiry.answered_at = reference
    inquiry.updated_at = reference
    await db.flush()
    return inquiry


async def dismiss_athlete_inquiry(
    db: AsyncSession, user_id: str, inquiry_id: str, *, now: datetime | None = None
) -> models.AthleteInquiry | None:
    """Record that the athlete skipped an inquiry for now."""
    existing = await get_athlete_inquiry(db, user_id, inquiry_id)
    if existing is None:
        return None
    reference = now or datetime.now(timezone.utc)
    existing.status = "dismissed"
    existing.updated_at = reference
    await db.flush()
    return existing


async def delete_athlete_inquiry(
    db: AsyncSession, user_id: str, inquiry_id: str
) -> bool:
    """Permanently remove an inquiry owned by a user."""
    existing = await get_athlete_inquiry(db, user_id, inquiry_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True
