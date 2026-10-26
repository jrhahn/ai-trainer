"""The coach's open questions about the athlete."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from domain import uncertainty_lifecycle
from crud.keys import normalise_key
from crud.memory import _as_aware_utc, _normalise_athlete_memory_category
from crud.uncertainty import record_uncertainty_event


# ---------------------------------------------------------------------------
# AthleteOpenQuestion (#385)
# ---------------------------------------------------------------------------

# Statuses shown by default: still-open questions. Answered/dismissed questions
# are resolved and only surfaced on request (management/export views).
_ATHLETE_OPEN_QUESTION_OPEN_STATUSES = ("open",)


# Independent observations at/above which an open question has "sufficient
# evidence" and auto-closes as answered (see issue #385).
ATHLETE_OPEN_QUESTION_AUTO_CLOSE_EVIDENCE = 3


# Prompt-facing cap (#512), for the same reason as the hypothesis cap above: 21
# open questions were 2,503 tokens (12 %) of every coach message, and a question
# only auto-closes once it reaches the evidence threshold, so a question nothing
# ever gathers evidence for is re-sent forever. Five is enough for the coach to
# recognise an opening in the conversation; more just crowds out the reply. There
# is no confidence to filter on here, so it is the best-evidenced five — the ones
# closest to being answerable.
ATHLETE_OPEN_QUESTION_PROMPT_LIMIT = 5


ATHLETE_OPEN_QUESTION_PROMPT_STALE_AFTER_DAYS = 56


async def get_athlete_open_question(
    db: AsyncSession, user_id: str, question_id: str
) -> models.AthleteOpenQuestion | None:
    """Return one open question owned by a user."""
    return await db.scalar(
        select(models.AthleteOpenQuestion).where(
            models.AthleteOpenQuestion.id == question_id,
            models.AthleteOpenQuestion.user_id == user_id,
        )
    )


async def list_athlete_open_questions(
    db: AsyncSession,
    user_id: str,
    *,
    include_resolved: bool = False,
) -> list[models.AthleteOpenQuestion]:
    """Return tracked open questions for a user, best-evidenced first."""
    stmt = select(models.AthleteOpenQuestion).where(
        models.AthleteOpenQuestion.user_id == user_id
    )
    if not include_resolved:
        stmt = stmt.where(
            models.AthleteOpenQuestion.status.in_(_ATHLETE_OPEN_QUESTION_OPEN_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthleteOpenQuestion.evidence_count.desc(),
            models.AthleteOpenQuestion.updated_at.desc(),
            models.AthleteOpenQuestion.id.desc(),
        )
    )
    return list(result)


async def get_prompt_athlete_open_questions(
    db: AsyncSession,
    user_id: str,
    *,
    now: datetime | None = None,
    stale_after_days: int = ATHLETE_OPEN_QUESTION_PROMPT_STALE_AFTER_DAYS,
    limit: int = ATHLETE_OPEN_QUESTION_PROMPT_LIMIT,
) -> list[models.AthleteOpenQuestion]:
    """Return only the open questions worth carrying in a coach prompt (#512).

    The athlete-facing list stays complete via
    :func:`list_athlete_open_questions`; this is the bounded view the coach
    reads on every message. Ordering is inherited, so the survivors are the
    best-evidenced ones — the questions closest to being answerable.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=stale_after_days)
    prompt_questions: list[models.AthleteOpenQuestion] = []
    for question in await list_athlete_open_questions(db, user_id):
        # A question that has gathered nothing new in this long is not one the
        # coach is still actively working on.
        if _as_aware_utc(question.updated_at) < cutoff:
            continue
        prompt_questions.append(question)
        if len(prompt_questions) >= limit:
            break
    return prompt_questions


async def record_athlete_open_question(
    db: AsyncSession,
    user_id: str,
    *,
    question: str,
    category: str = "general",
    evidence: str = "",
    needs: str = "",
    resolved: bool = False,
    resolution: str = "",
    observed_at: datetime | None = None,
    max_open: int | None = None,
) -> models.AthleteOpenQuestion | None:
    """Create an open question or accrue evidence on an existing one, then flush.

    A new question is stored ``open`` with one unit of evidence. When the same
    question recurs its evidence count grows and its ``evidence``/``needs`` are
    refreshed with the latest reading. Once the count reaches
    :data:`ATHLETE_OPEN_QUESTION_AUTO_CLOSE_EVIDENCE` — or the caller signals the
    record now answers it via ``resolved`` — the question auto-closes to
    ``answered`` with a short ``resolution``, matching the issue's requirement
    that questions close once sufficient evidence exists.

    ``max_open`` is the channel ceiling (#581): a *new* question is declined and
    ``None`` returned once the list is full, with an audit line. Accruing
    evidence on an existing question is never declined — that is how a question
    reaches an answer.
    """
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("question must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    question_key = normalise_key(cleaned)

    existing = await db.scalar(
        select(models.AthleteOpenQuestion).where(
            models.AthleteOpenQuestion.user_id == user_id,
            models.AthleteOpenQuestion.category == normalized_category,
            models.AthleteOpenQuestion.question_key == question_key,
        )
    )
    if existing is None:
        if (
            max_open is not None
            and await count_open_athlete_open_questions(db, user_id) >= max_open
        ):
            await record_uncertainty_event(
                db,
                user_id,
                channel=uncertainty_lifecycle.CHANNEL_OPEN_QUESTION,
                event=uncertainty_lifecycle.EVENT_DECLINED,
                statement=cleaned,
                reason=f"Channel already holds {max_open} open questions.",
                now=now,
            )
            return None
        existing = models.AthleteOpenQuestion(
            user_id=user_id,
            question=cleaned,
            question_key=question_key,
            category=normalized_category,
            evidence=evidence.strip(),
            needs=needs.strip(),
            evidence_count=1,
            status="open",
            first_asked_at=now,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.evidence_count += 1
        existing.updated_at = now
        if evidence:
            existing.evidence = evidence.strip()
        if needs:
            existing.needs = needs.strip()
        # Fresh evidence reopens a question the athlete had dismissed, or one
        # that expired unanswered — recurring is the signal that it mattered.
        if existing.status in ("dismissed", uncertainty_lifecycle.STATUS_EXPIRED):
            existing.status = "open"
            existing.resolution = None

    if existing.status == "open" and (
        resolved or existing.evidence_count >= ATHLETE_OPEN_QUESTION_AUTO_CLOSE_EVIDENCE
    ):
        existing.status = "answered"
        existing.resolution = (resolution.strip() or existing.evidence) or None
    await db.flush()
    return existing


async def update_athlete_open_question(
    db: AsyncSession,
    user_id: str,
    question_id: str,
    *,
    question: str | None = None,
    category: str | None = None,
    evidence: str | None = None,
    needs: str | None = None,
    resolution: str | None = None,
    status: str | None = None,
) -> models.AthleteOpenQuestion | None:
    """Apply an athlete's edit or verdict to an open question and flush."""
    existing = await get_athlete_open_question(db, user_id, question_id)
    if existing is None:
        return None

    if question is not None:
        cleaned = question.strip()
        if not cleaned:
            raise ValueError("question must not be empty")
        existing.question = cleaned
        existing.question_key = normalise_key(cleaned)
    if category is not None:
        existing.category = _normalise_athlete_memory_category(category)
    if evidence is not None:
        existing.evidence = evidence.strip()
    if needs is not None:
        existing.needs = needs.strip()
    if resolution is not None:
        existing.resolution = resolution.strip() or None
    if status is not None:
        existing.status = status
    existing.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return existing


async def delete_athlete_open_question(
    db: AsyncSession, user_id: str, question_id: str
) -> bool:
    """Permanently remove an open question owned by a user."""
    existing = await get_athlete_open_question(db, user_id, question_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


async def count_open_athlete_open_questions(db: AsyncSession, user_id: str) -> int:
    result = await db.scalar(
        select(func.count())
        .select_from(models.AthleteOpenQuestion)
        .where(
            models.AthleteOpenQuestion.user_id == user_id,
            models.AthleteOpenQuestion.status.in_(_ATHLETE_OPEN_QUESTION_OPEN_STATUSES),
        )
    )
    return int(result or 0)
