"""The coach's hypotheses about the athlete."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from domain import uncertainty_lifecycle
from crud.keys import normalise_key
from crud.memory import (
    ATHLETE_MEMORY_CONFIDENCE_STEP,
    ATHLETE_MEMORY_DEFAULT_CONFIDENCE,
    _as_aware_utc,
    _clamp_confidence,
    _normalise_athlete_memory_category,
    observe_athlete_memory_fact,
)
from crud.uncertainty import record_uncertainty_event


# ---------------------------------------------------------------------------
# AthleteHypothesis
# ---------------------------------------------------------------------------

# Statuses shown by default: open hypotheses still awaiting the athlete's
# judgement. Confirmed/refuted hypotheses are resolved and only surfaced on
# request (management/export views).
_ATHLETE_HYPOTHESIS_OPEN_STATUSES = ("proposed",)


# Confidence a hypothesis is floored to when the athlete confirms it, before it
# is promoted into a memory fact.
ATHLETE_HYPOTHESIS_CONFIRM_CONFIDENCE = 0.75


# When the performance model stops supporting a deterministic hypothesis (#479),
# its confidence steps down each pass; once it falls below the retire floor the
# hypothesis is deleted so the coach stops asserting a claim the model no longer
# backs, rather than leaving a stale duplicate around.
ATHLETE_HYPOTHESIS_DECAY_STEP = 0.2


ATHLETE_HYPOTHESIS_RETIRE_CONFIDENCE = 0.2


# Prompt-facing cap (#512). This table only grows: a weekly generator keeps
# proposing, and in production all 62 rows were still ``proposed`` — nothing had
# ever retired one — so the section was 3,560 tokens (17 %) of every coach
# message and got more expensive each week no matter what the athlete did. The
# coach does not need to recite every idea it has ever had; it needs the ones it
# can actually defend. Eight is roughly what fits in a conversation before the
# hypotheses drown out the answer. The floor is the confidence a hypothesis is
# seeded with, so only one actively knocked down by
# :func:`decay_unsupported_model_hypotheses` falls below it.
ATHLETE_HYPOTHESIS_PROMPT_LIMIT = 8


ATHLETE_HYPOTHESIS_PROMPT_MIN_CONFIDENCE = ATHLETE_MEMORY_DEFAULT_CONFIDENCE


# The generators run weekly and re-propose what the data still supports, which
# bumps ``updated_at``. Surviving eight such passes without one fresh
# observation means the evidence has moved on.
ATHLETE_HYPOTHESIS_PROMPT_STALE_AFTER_DAYS = 56


async def get_athlete_hypothesis(
    db: AsyncSession, user_id: str, hypothesis_id: str
) -> models.AthleteHypothesis | None:
    """Return one athlete hypothesis owned by a user."""
    return await db.scalar(
        select(models.AthleteHypothesis).where(
            models.AthleteHypothesis.id == hypothesis_id,
            models.AthleteHypothesis.user_id == user_id,
        )
    )


async def list_athlete_hypotheses(
    db: AsyncSession,
    user_id: str,
    *,
    include_resolved: bool = False,
) -> list[models.AthleteHypothesis]:
    """Return stored hypotheses for a user, strongest evidence first."""
    stmt = select(models.AthleteHypothesis).where(
        models.AthleteHypothesis.user_id == user_id
    )
    if not include_resolved:
        stmt = stmt.where(
            models.AthleteHypothesis.status.in_(_ATHLETE_HYPOTHESIS_OPEN_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthleteHypothesis.confidence.desc(),
            models.AthleteHypothesis.evidence_count.desc(),
            models.AthleteHypothesis.updated_at.desc(),
            models.AthleteHypothesis.id.desc(),
        )
    )
    return list(result)


async def get_prompt_athlete_hypotheses(
    db: AsyncSession,
    user_id: str,
    *,
    now: datetime | None = None,
    min_confidence: float = ATHLETE_HYPOTHESIS_PROMPT_MIN_CONFIDENCE,
    stale_after_days: int = ATHLETE_HYPOTHESIS_PROMPT_STALE_AFTER_DAYS,
    limit: int = ATHLETE_HYPOTHESIS_PROMPT_LIMIT,
) -> list[models.AthleteHypothesis]:
    """Return only the hypotheses worth spending coach-prompt tokens on (#512).

    :func:`list_athlete_hypotheses` stays unbounded for the API and the
    expert-mode UI, which are meant to show the athlete everything. A prompt is
    the opposite situation: it pays for every row on every message, so it takes
    the strongest few and lets the rest wait for their evidence. Ordering is
    inherited from ``list_athlete_hypotheses`` (confidence, then evidence), so
    the survivors are the ones the coach is most able to defend.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=stale_after_days)
    prompt_hypotheses: list[models.AthleteHypothesis] = []
    for hypothesis in await list_athlete_hypotheses(db, user_id):
        # A belief the coach barely holds is not worth asserting to the athlete,
        # however tentatively — and a hypothesis that has gone this long without
        # a single fresh observation is one the evidence stopped supporting.
        if hypothesis.confidence < min_confidence:
            continue
        if _as_aware_utc(hypothesis.updated_at) < cutoff:
            continue
        prompt_hypotheses.append(hypothesis)
        if len(prompt_hypotheses) >= limit:
            break
    return prompt_hypotheses


async def propose_athlete_hypothesis(
    db: AsyncSession,
    user_id: str,
    *,
    statement: str,
    category: str = "general",
    rationale: str = "",
    confidence: float | None = None,
    evidence: list[str] | None = None,
    alternative_explanations: list[str] | None = None,
    observed_at: datetime | None = None,
    max_open: int | None = None,
    sport: str | None = None,
) -> models.AthleteHypothesis | None:
    """Create a hypothesis or strengthen an existing one with fresh evidence.

    A new statement is stored as ``proposed`` with one unit of evidence. When the
    same statement recurs its evidence count grows and its confidence ticks up (a
    growing body of support), and a hypothesis the athlete had ``refuted`` is
    revived to ``proposed`` — fresh evidence reopens the question. A ``confirmed``
    hypothesis keeps its status while still accruing evidence.

    ``evidence`` and ``alternative_explanations`` (#479) are the structured
    supporting observations and the competing explanations still to be ruled out;
    when supplied they replace the stored lists so a recurring hypothesis carries
    the latest evidence rather than a stale snapshot.

    ``max_open`` is the channel ceiling (#581). When the channel is full a *new*
    statement is declined and ``None`` returned, with an audit line saying the
    coach had something to add and was not allowed to. Strengthening an existing
    hypothesis is never declined: making evidence accrue on what is already there
    is the behaviour the ceiling exists to encourage. Deterministic writers pass
    no ceiling — they re-derive and retire their own set.
    """
    cleaned = statement.strip()
    if not cleaned:
        raise ValueError("statement must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    statement_key = normalise_key(cleaned)

    existing = await db.scalar(
        select(models.AthleteHypothesis).where(
            models.AthleteHypothesis.user_id == user_id,
            models.AthleteHypothesis.category == normalized_category,
            models.AthleteHypothesis.statement_key == statement_key,
        )
    )
    if existing is None:
        if (
            max_open is not None
            and await count_open_hypotheses(db, user_id) >= max_open
        ):
            await record_uncertainty_event(
                db,
                user_id,
                channel=uncertainty_lifecycle.CHANNEL_HYPOTHESIS,
                event=uncertainty_lifecycle.EVENT_DECLINED,
                statement=cleaned,
                reason=f"Channel already holds {max_open} open hypotheses.",
                now=now,
            )
            return None
        existing = models.AthleteHypothesis(
            user_id=user_id,
            statement=cleaned,
            statement_key=statement_key,
            category=normalized_category,
            rationale=rationale.strip(),
            confidence=_clamp_confidence(confidence),
            evidence=list(evidence) if evidence else None,
            alternative_explanations=(
                list(alternative_explanations) if alternative_explanations else None
            ),
            evidence_count=1,
            status="proposed",
            sport=sport,
            first_proposed_at=now,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.evidence_count += 1
        existing.updated_at = now
        if sport:
            # Set, never cleared. A caller that does not state a sport is not
            # asserting the claim has none — the LLM writer never states one —
            # and letting it blank the field would mean a weekly LLM pass erasing
            # what the deterministic engine knew (#718).
            existing.sport = sport
        if rationale:
            existing.rationale = rationale.strip()
        if evidence:
            existing.evidence = list(evidence)
        if alternative_explanations:
            existing.alternative_explanations = list(alternative_explanations)
        if existing.status in ("refuted", uncertainty_lifecycle.STATUS_EXPIRED):
            # Fresh evidence reopens the question — and a claim that recurred
            # after expiring is precisely the kind the ceiling exists to make
            # room for.
            existing.status = "proposed"
        base_confidence = max(existing.confidence, _clamp_confidence(confidence))
        existing.confidence = min(1.0, base_confidence + ATHLETE_MEMORY_CONFIDENCE_STEP)
    await db.flush()
    return existing


async def decay_unsupported_model_hypotheses(
    db: AsyncSession,
    user_id: str,
    *,
    category: str,
    supported_keys: set[str],
    now: datetime | None = None,
) -> int:
    """Decay — and eventually retire — proposed hypotheses no longer supported.

    Called after regenerating the deterministic performance-model hypotheses
    (#479): any previously ``proposed`` hypothesis in ``category`` whose statement
    the model no longer produces has lost its supporting evidence, so its
    confidence is stepped down; once it drops below the retire floor it is deleted
    so the coach stops asserting a claim the model no longer backs. Confirmed or
    refuted hypotheses (the athlete has already ruled on them) are left untouched.
    Returns the number of hypotheses decayed or retired.
    """
    normalized_category = _normalise_athlete_memory_category(category)
    now = now or datetime.now(timezone.utc)
    rows = await db.scalars(
        select(models.AthleteHypothesis).where(
            models.AthleteHypothesis.user_id == user_id,
            models.AthleteHypothesis.category == normalized_category,
            models.AthleteHypothesis.status == "proposed",
        )
    )
    changed = 0
    for row in rows:
        if row.statement_key in supported_keys:
            continue
        row.confidence = max(0.0, row.confidence - ATHLETE_HYPOTHESIS_DECAY_STEP)
        row.updated_at = now
        if row.confidence < ATHLETE_HYPOTHESIS_RETIRE_CONFIDENCE:
            await db.delete(row)
        changed += 1
    await db.flush()
    return changed


async def update_athlete_hypothesis(
    db: AsyncSession,
    user_id: str,
    hypothesis_id: str,
    *,
    statement: str | None = None,
    category: str | None = None,
    rationale: str | None = None,
    confidence: float | None = None,
    status: str | None = None,
) -> models.AthleteHypothesis | None:
    """Apply an athlete's edit or verdict to a hypothesis and flush.

    Confirming a hypothesis floors its confidence and promotes it into a durable
    memory fact via :func:`observe_athlete_memory_fact`, so validated knowledge
    starts informing coach advice; refuting or deleting simply resolves it.
    """
    existing = await get_athlete_hypothesis(db, user_id, hypothesis_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    if statement is not None:
        cleaned = statement.strip()
        if not cleaned:
            raise ValueError("statement must not be empty")
        existing.statement = cleaned
        existing.statement_key = normalise_key(cleaned)
    if category is not None:
        existing.category = _normalise_athlete_memory_category(category)
    if rationale is not None:
        existing.rationale = rationale.strip()
    if confidence is not None:
        existing.confidence = _clamp_confidence(confidence)
    if status is not None:
        existing.status = status
        if status == "confirmed":
            existing.confidence = max(
                existing.confidence, ATHLETE_HYPOTHESIS_CONFIRM_CONFIDENCE
            )
            # The athlete has vetted this claim, so promote it as trusted
            # knowledge: keep its confidence and let it inform coaching at once,
            # rather than re-earning trust through the evidence gate (#387).
            await observe_athlete_memory_fact(
                db,
                user_id,
                fact=existing.statement,
                category=existing.category,
                source_snippet=existing.rationale,
                confidence=existing.confidence,
                observed_at=now,
                trusted=True,
            )
    existing.updated_at = now
    await db.flush()
    return existing


async def delete_athlete_hypothesis(
    db: AsyncSession, user_id: str, hypothesis_id: str
) -> bool:
    """Permanently remove an athlete hypothesis owned by a user."""
    existing = await get_athlete_hypothesis(db, user_id, hypothesis_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


async def count_open_hypotheses(db: AsyncSession, user_id: str) -> int:
    """Open hypotheses the lifecycle governs — the free-form, LLM-formed ones.

    The deterministic writers are excluded on purpose; see
    :data:`domain.uncertainty_lifecycle.DETERMINISTIC_HYPOTHESIS_CATEGORIES`.
    """
    result = await db.scalar(
        select(func.count())
        .select_from(models.AthleteHypothesis)
        .where(
            models.AthleteHypothesis.user_id == user_id,
            models.AthleteHypothesis.status.in_(_ATHLETE_HYPOTHESIS_OPEN_STATUSES),
            models.AthleteHypothesis.category.notin_(
                tuple(uncertainty_lifecycle.DETERMINISTIC_HYPOTHESIS_CATEGORIES)
            ),
        )
    )
    return int(result or 0)
