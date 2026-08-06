"""The coach's direct line to the athlete for what data cannot reveal (#506).

Every other athlete-learning pass in this package works the same way: read the
training history, infer something, wait for more rides to confirm it. That
covers everything the athlete's power file can express — and nothing it cannot.
Why a session was skipped, whether a niggle has cleared, what the athlete wants
from the season, whether the power meter was swapped: no volume of future data
answers these, so a coach that only infers stays quietly wrong about them.

This module closes that gap in two halves:

* :func:`generate_user_inquiries` raises questions, but only after the model has
  reasoned about what the incoming data stream *will* tell it — the prompt makes
  that reasoning step mandatory, because an ungated coach model asks about FTP
  and fatigue, which the next fortnight of riding answers for free. At most
  :data:`crud.ATHLETE_INQUIRY_MAX_PENDING` questions are ever pending, so the
  athlete meets a question or two rather than a questionnaire.
* :func:`submit_inquiry_answer` handles the reply. An answer that resolves the
  question becomes a trusted :class:`models.AthleteMemoryFact` and immediately
  informs coaching. One that misses buys a single kind rephrasing; if that also
  misses, the coach stops asking and hands off to Settings.

Both halves are best-effort in the same sense as the surrounding learning steps:
generation failures are logged and never break a sync, and a failed answer
judgement accepts the answer rather than re-asking the athlete (see
:func:`services.ai_service.evaluate_inquiry_answer`).
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import ai_service
from services.llm import resolve_user_provider
from services.prompts import ride_metrics_context_section
from services.token_accounting import track_llm_usage

logger = logging.getLogger(__name__)

# The history window the model reads to work out what data already answers.
INQUIRY_HISTORY_LIMIT = 60
# Below this there is not enough training to tell an unanswerable question from
# one the next few rides would settle, so the coach stays quiet.
INQUIRY_MIN_ACTIVITIES = 8


async def generate_user_inquiries(
    db: AsyncSession,
    user: models.User,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Raise the questions only this athlete can answer.

    Returns the number of new inquiries pinned. Returns 0 when the athlete has
    too little history, already has the maximum pending, or the coach finds
    nothing worth asking.
    """
    if not user.is_onboarded:
        return 0

    pending = await crud.count_pending_athlete_inquiries(db, user.id)
    capacity = crud.ATHLETE_INQUIRY_MAX_PENDING - pending
    if capacity <= 0:
        return 0

    metrics = await crud.get_ride_metrics_history(
        db, user.id, limit=INQUIRY_HISTORY_LIMIT
    )
    if len(metrics) < INQUIRY_MIN_ACTIVITIES:
        return 0

    metrics_section = ride_metrics_context_section(metrics, timezone_name)
    if not metrics_section.strip():
        return 0

    facts = await crud.list_athlete_memory_facts(db, user.id, now=now)
    existing_facts = [fact.fact for fact in facts]
    # Every inquiry ever raised, answered and dismissed alike: the point of the
    # list is that the athlete is never asked the same thing twice.
    previous = await crud.list_athlete_inquiries(db, user.id, include_resolved=True)
    asked_questions = [inquiry.question for inquiry in previous]

    # Distinct from answering one below: this is the nightly generation, that
    # is the athlete replying. Sharing one label meant the dashboard could not
    # tell scheduled machinery from something a person did (#549).
    async with track_llm_usage(db, user, source="step:athlete-inquiry-generation"):
        candidates = await ai_service.generate_athlete_inquiries(
            metrics_section,
            existing_facts=existing_facts,
            asked_questions=asked_questions,
            max_candidates=capacity,
            provider=resolve_user_provider(user),
        )

    recorded = 0
    for candidate in candidates:
        inquiry = await crud.record_athlete_inquiry(
            db,
            user.id,
            question=candidate["question"],
            category=candidate["category"],
            why_asking=candidate["why_asking"],
            settings_hint=candidate["settings_hint"],
            observed_at=now,
        )
        # ``None`` means the question is already on file under another status —
        # asking it again would be exactly the nagging this design avoids.
        if inquiry is None:
            continue
        recorded += 1
        if recorded >= capacity:
            break
    return recorded


async def submit_inquiry_answer(
    db: AsyncSession,
    user: models.User,
    inquiry: models.AthleteInquiry,
    answer: str,
    *,
    now: datetime | None = None,
) -> tuple[models.AthleteInquiry, bool, str]:
    """Judge an answer to a pinned inquiry and advance its lifecycle.

    Returns ``(inquiry, accepted, coach_reply)``. An accepted answer closes the
    inquiry and is stored as a trusted memory fact. A rejected one is rephrased
    and re-pinned while attempts remain, and otherwise handed off to Settings —
    the coach asks at most :data:`crud.ATHLETE_INQUIRY_MAX_ASKS` times.
    """
    cleaned = answer.strip()
    if not cleaned:
        raise ValueError("answer must not be empty")

    is_final_attempt = inquiry.ask_count >= crud.ATHLETE_INQUIRY_MAX_ASKS

    async with track_llm_usage(db, user, source="step:athlete-inquiry-answer"):
        verdict = await ai_service.evaluate_inquiry_answer(
            inquiry.question,
            cleaned,
            why_asking=inquiry.why_asking,
            settings_hint=inquiry.settings_hint,
            is_final_attempt=is_final_attempt,
            provider=resolve_user_provider(user),
        )

    coach_reply = verdict.get("reply", "")

    if verdict.get("answered"):
        # The athlete stating something about themselves is first-hand testimony,
        # so it is trusted straight away rather than having to recur to be
        # believed. Losing the fact must not lose the answer, hence the guard.
        fact = verdict.get("fact") or ""
        if fact.strip():
            try:
                await crud.observe_athlete_memory_fact(
                    db,
                    user.id,
                    fact=fact,
                    kind="fact",
                    category=inquiry.category,
                    source_snippet=f"Answered when asked: {inquiry.question}"[:500],
                    confidence=crud.ATHLETE_INQUIRY_ANSWER_CONFIDENCE,
                    observed_at=now,
                    trusted=True,
                )
            except Exception:
                logger.warning(
                    "Storing inquiry answer as a memory fact failed for user_id=%s "
                    "inquiry_id=%s",
                    user.id,
                    inquiry.id,
                    exc_info=True,
                )
        await crud.resolve_athlete_inquiry(db, inquiry, answer=cleaned, now=now)
        return inquiry, True, coach_reply

    if is_final_attempt:
        await crud.hand_off_athlete_inquiry(
            db, inquiry, answer=cleaned, note=coach_reply, now=now
        )
        return inquiry, False, coach_reply

    await crud.reask_athlete_inquiry(
        db,
        inquiry,
        answer=cleaned,
        question=verdict.get("question", ""),
        note=coach_reply,
        now=now,
    )
    return inquiry, False, coach_reply
