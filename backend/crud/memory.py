"""What the coach remembers: its memory text, the athlete context, the facts."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from crud.keys import normalise_key


ATHLETE_MEMORY_DEFAULT_CONFIDENCE = 0.35


ATHLETE_MEMORY_CONFIDENCE_STEP = 0.2


ATHLETE_MEMORY_PROMPT_MIN_CONFIDENCE = 0.5


ATHLETE_MEMORY_STALE_AFTER_DAYS = 90


ATHLETE_MEMORY_PROMPT_LIMIT = 12


# Evidence-based promotion (#387). The coach must not turn a single event into
# trusted knowledge: an inferred observation has to recur before it earns the
# coach's trust. A first sighting is seeded as a low-confidence candidate whose
# stored confidence is capped at the default floor (below the prompt threshold),
# and only repeated confirmation grows both its ``observation_count`` and its
# confidence past the bar. ``get_prompt_athlete_memory_facts`` then requires at
# least ``ATHLETE_MEMORY_MIN_EVIDENCE`` observations before an unconfirmed fact
# reaches a coach prompt. Deliberate, athlete-driven writes (confirming a
# hypothesis) bypass the cap via ``trusted=True``.
ATHLETE_MEMORY_MIN_EVIDENCE = 2


ATHLETE_MEMORY_INITIAL_CONFIDENCE_CAP = ATHLETE_MEMORY_DEFAULT_CONFIDENCE


# Confidence in an unconfirmed observation erodes once it has gone unmentioned
# for a grace period, so the coach trusts fresh evidence over old assumptions.
ATHLETE_MEMORY_DECAY_GRACE_DAYS = 30


ATHLETE_MEMORY_CONFIDENCE_DECAY_PER_DAY = 0.01


ATHLETE_MEMORY_STALE_CONFIDENCE = 0.3


# Once an unconfirmed observation has gone unmentioned this long it is archived:
# retained for audit and revival, but hidden from coach prompts and the default
# management view. A fresh observation revives it.
ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS = 180


# How much confidence a fact loses when fresh training evidence contradicts it.
# Larger than a single decay day so a real contradiction meaningfully demotes the
# fact and, together with the ``needs_validation`` flag, prompts the athlete to
# confirm or correct it.
ATHLETE_MEMORY_CONTRADICTION_PENALTY = 0.25


_ATHLETE_MEMORY_ACTIVE_STATUSES = ("active", "user_confirmed")


# Statuses shown in the default management view: currently trusted facts plus
# facts flagged for the athlete's attention (contradicted by fresh evidence).
_ATHLETE_MEMORY_VISIBLE_STATUSES = ("active", "user_confirmed", "needs_validation")


# Unconfirmed statuses whose confidence still decays / that can be archived.
_ATHLETE_MEMORY_DECAYABLE_STATUSES = ("active", "stale")


# Statuses a fresh observation revives back to ``active``.
_ATHLETE_MEMORY_REVIVABLE_STATUSES = ("stale", "archived", "needs_validation")


# ---------------------------------------------------------------------------
# CoachMemory
# ---------------------------------------------------------------------------


async def get_coach_memory(
    db: AsyncSession, user_id: str, *, for_update: bool = False
) -> models.CoachMemory | None:
    """Return the CoachMemory for a user, or None.

    Set ``for_update`` to lock the row (``SELECT … FOR UPDATE``) so a
    read-modify-write of the memory serialises against concurrent writers and
    cannot lose a committed edit (#346).
    """
    if for_update:
        return await db.get(models.CoachMemory, user_id, with_for_update=True)
    return await db.get(models.CoachMemory, user_id)


async def upsert_coach_memory(
    db: AsyncSession, user_id: str, memory: str
) -> models.CoachMemory:
    """Create or update the CoachMemory for a user and flush."""
    existing = await get_coach_memory(db, user_id)
    if existing is None:
        existing = models.CoachMemory(user_id=user_id, memory=memory)
        db.add(existing)
    else:
        existing.memory = memory
    await db.flush()
    return existing


async def update_coach_memory_if_unchanged(
    db: AsyncSession, user_id: str, *, expected: str, new: str
) -> bool:
    """Set memory to ``new`` only if it still equals ``expected``.

    Returns True when applied, False when the stored value has diverged from
    ``expected`` (a concurrent edit) so the caller can retry against a fresh
    base. The row is locked ``FOR UPDATE`` for the read-check-write, so a
    concurrent write from another transaction is never silently overwritten
    (#346). Handles the create case (no row yet) only when ``expected`` is empty.
    """
    existing = await db.get(models.CoachMemory, user_id, with_for_update=True)
    if existing is None:
        if expected:
            # We based our update on a non-empty memory that no longer exists —
            # treat as a conflict rather than resurrecting stale content.
            return False
        db.add(models.CoachMemory(user_id=user_id, memory=new))
        await db.flush()
        return True
    if existing.memory != expected:
        return False
    existing.memory = new
    existing.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# AthleteContext
# ---------------------------------------------------------------------------


async def get_athlete_context(
    db: AsyncSession, user_id: str
) -> models.AthleteContext | None:
    """Return the structured AthleteContext for a user, or None."""
    return await db.get(models.AthleteContext, user_id)


async def upsert_athlete_context(
    db: AsyncSession,
    user_id: str,
    *,
    training_tendency: str = "unknown",
    rest_response: str = "unknown",
    adherence_pattern: str = "unknown",
    strengths: list[str] | None = None,
    weaknesses: list[str] | None = None,
    preferred_terrain: list[str] | None = None,
    preferred_session_types: list[str] | None = None,
    coaching_risks: list[str] | None = None,
    notes: str = "",
) -> models.AthleteContext:
    """Create or update a user's structured athlete context and flush."""
    values = {
        "training_tendency": training_tendency,
        "rest_response": rest_response,
        "adherence_pattern": adherence_pattern,
        "strengths": list(strengths or []),
        "weaknesses": list(weaknesses or []),
        "preferred_terrain": list(preferred_terrain or []),
        "preferred_session_types": list(preferred_session_types or []),
        "coaching_risks": list(coaching_risks or []),
        "notes": notes,
        "updated_at": datetime.now(timezone.utc),
    }
    existing = await get_athlete_context(db, user_id)
    if existing is None:
        existing = models.AthleteContext(user_id=user_id, **values)
        db.add(existing)
    else:
        for attr, value in values.items():
            setattr(existing, attr, value)
    await db.flush()
    return existing


# ---------------------------------------------------------------------------
# AthleteMemoryFact
# ---------------------------------------------------------------------------


# Near-duplicate memory facts (ai-trainer-ops#73). The extractor rewords what
# it already knows on every pass: one athlete's memory held "FTP is
# approximately 285W", "The athlete has a typical functional threshold power of
# 285W" and eleven more, each a new row because the exact key differed, and all
# of them competing for the prompt's few memory slots. Two facts are the same
# when they carry the same figures and most of the same words once the
# extractor's habitual filler is dropped; without figures, nearly all words.
_MEMORY_FACT_FILLER = frozenset(
    """the a an athlete athlete's athletes has have had is are was be been being of to in
    on at for with and or by maintains maintain maintained consistently consistent highly
    high stable reliable reliably typical typically approximately around about roughly
    estimated estimate generally usually frequently regularly often tends tend their
    his her its this that these those which who across during while when""".split()
)


_MEMORY_FACT_SYNONYMS = (
    (re.compile(r"functional threshold power(?:\s*\(ftp\))?"), "ftp"),
    (re.compile(r"\bwatts?\b"), "w"),
)


_MEMORY_FACT_FIGURE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:w|bpm|h|min|km|hm|m|%)?\b")


MEMORY_FACT_SAME_WITH_FIGURES = 0.4


MEMORY_FACT_SAME_WITHOUT_FIGURES = 0.7


def _memory_fact_signature(fact: str) -> tuple[frozenset[str], frozenset[str]]:
    text = fact.casefold()
    for pattern, replacement in _MEMORY_FACT_SYNONYMS:
        text = pattern.sub(replacement, text)
    figures = frozenset(
        re.sub(r"\s+", "", figure) for figure in _MEMORY_FACT_FIGURE.findall(text)
    )
    words = frozenset(
        word
        for word in re.findall(r"[a-zäöüß]+", _MEMORY_FACT_FIGURE.sub(" ", text))
        if len(word) > 2 and word not in _MEMORY_FACT_FILLER
    )
    return figures, words


def _memory_fact_overlap(a: str, b: str) -> float:
    """How alike two facts are, 0 to 1; 0 when their figures differ."""
    (figures_a, words_a), (figures_b, words_b) = (
        _memory_fact_signature(a),
        _memory_fact_signature(b),
    )
    if not words_a or not words_b or figures_a != figures_b:
        return 0.0
    overlap = len(words_a & words_b) / len(words_a | words_b)
    floor = (
        MEMORY_FACT_SAME_WITH_FIGURES if figures_a else MEMORY_FACT_SAME_WITHOUT_FIGURES
    )
    return overlap if overlap >= floor else 0.0


def _normalise_athlete_memory_category(category: str | None) -> str:
    value = (category or "general").strip().lower().replace(" ", "_")
    return value[:50] or "general"


def _normalise_athlete_memory_kind(kind: str | None) -> str:
    """Coerce a knowledge-type label to ``fact`` or ``observation`` (#386).

    Anything that is not an explicit stable ``fact`` falls back to
    ``observation`` — the safer default, since an inferred pattern carries less
    authority than a stated value.
    """
    return "fact" if (kind or "").strip().lower() == "fact" else "observation"


def _clamp_confidence(value: float | None) -> float:
    if value is None:
        return ATHLETE_MEMORY_DEFAULT_CONFIDENCE
    return max(0.0, min(1.0, float(value)))


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def get_athlete_memory_fact(
    db: AsyncSession, user_id: str, fact_id: str
) -> models.AthleteMemoryFact | None:
    """Return one athlete memory fact owned by a user."""
    return await db.scalar(
        select(models.AthleteMemoryFact).where(
            models.AthleteMemoryFact.id == fact_id,
            models.AthleteMemoryFact.user_id == user_id,
        )
    )


async def apply_athlete_memory_confidence_decay(
    db: AsyncSession, user_id: str, *, now: datetime | None = None
) -> int:
    """Erode confidence in stale observations and archive obsolete ones.

    Active facts that have not been re-observed within
    ``ATHLETE_MEMORY_DECAY_GRACE_DAYS`` lose confidence linearly for each
    additional day of silence. Once confidence falls below
    ``ATHLETE_MEMORY_STALE_CONFIDENCE`` the fact is marked ``stale`` so it drops
    out of coach prompts. If it then stays unmentioned until
    ``ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS`` have passed since its last
    confirmation, it is ``archived`` — retained for audit and revival but hidden
    from the default management view. A fresh observation revives a stale or
    archived fact. ``user_confirmed`` facts are vetted by the athlete and never
    decay or archive. Returns the number of facts whose status or confidence
    changed.
    """
    reference = now or datetime.now(timezone.utc)
    facts = await db.scalars(
        select(models.AthleteMemoryFact).where(
            models.AthleteMemoryFact.user_id == user_id,
            models.AthleteMemoryFact.status.in_(_ATHLETE_MEMORY_DECAYABLE_STATUSES),
        )
    )
    changed = 0
    for fact in facts:
        idle_days = (reference - _as_aware_utc(fact.last_confirmed_at)).days
        # Archive observations that have gone unmentioned for a long time. This
        # is keyed purely on elapsed time, so it is deterministic regardless of
        # how often decay is applied.
        if idle_days >= ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS:
            fact.status = "archived"
            fact.updated_at = reference
            changed += 1
            continue
        # Stale facts hold their eroded confidence until they are archived.
        if fact.status != "active":
            continue
        decay_days = idle_days - ATHLETE_MEMORY_DECAY_GRACE_DAYS
        if decay_days <= 0:
            continue
        decayed = _clamp_confidence(
            fact.confidence - ATHLETE_MEMORY_CONFIDENCE_DECAY_PER_DAY * decay_days
        )
        if decayed >= fact.confidence:
            continue
        fact.confidence = decayed
        fact.updated_at = reference
        if decayed < ATHLETE_MEMORY_STALE_CONFIDENCE:
            fact.status = "stale"
        changed += 1
    if changed:
        await db.flush()
    return changed


async def list_athlete_memory_facts(
    db: AsyncSession,
    user_id: str,
    *,
    include_inactive: bool = False,
    now: datetime | None = None,
) -> list[models.AthleteMemoryFact]:
    """Return stored athlete memory facts for management views."""
    await apply_athlete_memory_confidence_decay(db, user_id, now=now)
    stmt = select(models.AthleteMemoryFact).where(
        models.AthleteMemoryFact.user_id == user_id
    )
    if not include_inactive:
        stmt = stmt.where(
            models.AthleteMemoryFact.status.in_(_ATHLETE_MEMORY_VISIBLE_STATUSES)
        )
    result = await db.scalars(
        stmt.order_by(
            models.AthleteMemoryFact.status.desc(),
            models.AthleteMemoryFact.confidence.desc(),
            models.AthleteMemoryFact.last_confirmed_at.desc(),
            models.AthleteMemoryFact.id.desc(),
        )
    )
    return list(result)


async def observe_athlete_memory_fact(
    db: AsyncSession,
    user_id: str,
    *,
    fact: str,
    kind: str = "observation",
    category: str = "general",
    source_snippet: str = "",
    source_exchange_id: str | None = None,
    confidence: float | None = None,
    observed_at: datetime | None = None,
    trusted: bool = False,
) -> models.AthleteMemoryFact:
    """Create a fact or strengthen an existing fact from a new observation.

    An ordinary (``trusted=False``) observation is treated as tentative evidence
    (#387): its first sighting is stored below the prompt-trust threshold and its
    confidence can only climb through the per-observation step, so it takes
    repeated confirmation before the coach relies on it. A ``trusted`` write —
    the athlete confirming a hypothesis — records the supplied confidence as-is
    and seeds enough evidence to be usable immediately.
    """
    cleaned_fact = fact.strip()
    if not cleaned_fact:
        raise ValueError("fact must not be empty")
    now = observed_at or datetime.now(timezone.utc)
    normalized_category = _normalise_athlete_memory_category(category)
    normalized_kind = _normalise_athlete_memory_kind(kind)
    fact_key = normalise_key(cleaned_fact)
    # Untrusted evidence can only claim up to the candidate cap; trust is earned
    # by recurrence, not asserted in a single extraction.
    contributed_confidence = _clamp_confidence(confidence)
    if not trusted:
        contributed_confidence = min(
            contributed_confidence, ATHLETE_MEMORY_INITIAL_CONFIDENCE_CAP
        )

    existing = await db.scalar(
        select(models.AthleteMemoryFact).where(
            models.AthleteMemoryFact.user_id == user_id,
            models.AthleteMemoryFact.category == normalized_category,
            models.AthleteMemoryFact.fact_key == fact_key,
        )
    )
    if existing is None:
        # A reworded restatement strengthens the fact it restates, in any
        # category: the extractor files the same FTP under "fact" one day and
        # "general" the next (ai-trainer-ops#73).
        # ponytail: scans every fact of the athlete (a few hundred at most); an
        # index on the figures would be needed for thousands.
        candidates = await db.scalars(
            select(models.AthleteMemoryFact).where(
                models.AthleteMemoryFact.user_id == user_id
            )
        )
        scored = [
            (_memory_fact_overlap(candidate.fact, cleaned_fact), candidate)
            for candidate in candidates
        ]
        best = max(scored, key=lambda pair: pair[0], default=(0.0, None))
        existing = best[1] if best[0] > 0 else None
    if existing is None:
        existing = models.AthleteMemoryFact(
            user_id=user_id,
            fact=cleaned_fact,
            fact_key=fact_key,
            kind=normalized_kind,
            category=normalized_category,
            source_snippet=source_snippet.strip(),
            source_exchange_id=source_exchange_id,
            first_observed_at=now,
            last_confirmed_at=now,
            confidence=contributed_confidence,
            status="active",
            # A confirmed hypothesis arrives already backed by evidence, so it
            # meets the promotion bar at once; a plain observation starts at one.
            observation_count=ATHLETE_MEMORY_MIN_EVIDENCE if trusted else 1,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.observation_count += 1
        existing.last_confirmed_at = now
        existing.updated_at = now
        # Fresh evidence can re-classify a row (e.g. a value the coach once
        # inferred is later stated outright), but a single automated observation
        # must not overwrite knowledge the athlete has confirmed (#387).
        if trusted or existing.status != "user_confirmed":
            existing.kind = normalized_kind
        if source_snippet:
            existing.source_snippet = source_snippet.strip()
        if source_exchange_id is not None:
            existing.source_exchange_id = source_exchange_id
        revived = existing.status in _ATHLETE_MEMORY_REVIVABLE_STATUSES
        if revived:
            existing.status = "active"
            # Fresh evidence re-confirms the fact, so any pending contradiction is
            # resolved and the validation prompt is cleared.
            existing.contradiction_note = None
        base_confidence = max(existing.confidence, contributed_confidence)
        # A revived observation may have decayed to near-zero; refresh it to at
        # least the default so fresh evidence is trusted again.
        if revived:
            base_confidence = max(base_confidence, ATHLETE_MEMORY_DEFAULT_CONFIDENCE)
        existing.confidence = min(1.0, base_confidence + ATHLETE_MEMORY_CONFIDENCE_STEP)
    await db.flush()
    return existing


async def athlete_memory_observation_counts(
    db: AsyncSession,
    user_id: str,
    facts: Sequence[str],
    *,
    category: str,
) -> dict[str, int]:
    """How often each of these observations has already been recorded (#593).

    Keyed through the same normalisation the write uses, so a caller asking "have
    we seen this before?" cannot drift away from what ``observe_athlete_memory_fact``
    actually stored. Counts every row, including the decayed and archived ones:
    the question is what has been observed, not what the coach currently believes.
    """
    wanted = {normalise_key(fact): fact for fact in facts if fact}
    if not wanted:
        return {}
    normalized_category = _normalise_athlete_memory_category(category)
    rows = await db.scalars(
        select(models.AthleteMemoryFact).where(
            models.AthleteMemoryFact.user_id == user_id,
            models.AthleteMemoryFact.category == normalized_category,
            models.AthleteMemoryFact.fact_key.in_(wanted),
        )
    )
    counts = {fact: 0 for fact in wanted.values()}
    for row in rows:
        original = wanted.get(row.fact_key)
        if original is not None:
            counts[original] = row.observation_count
    return counts


async def record_athlete_memory_fact_contradiction(
    db: AsyncSession,
    fact: models.AthleteMemoryFact,
    *,
    reason: str,
    now: datetime | None = None,
) -> models.AthleteMemoryFact:
    """Flag a fact that fresh training evidence contradicts.

    Lowers the fact's confidence by ``ATHLETE_MEMORY_CONTRADICTION_PENALTY`` and
    moves it to ``needs_validation`` so it is pulled from coach prompts and
    surfaced to the athlete to confirm or correct. ``reason`` explains, in the
    coach's words, why the evidence disagrees with the stored fact. This is a
    deliberate, evidence-driven demotion, so it applies to ``user_confirmed``
    facts too — even a vetted value is worth re-checking when the numbers
    disagree. A later fresh, consistent observation revives the fact and clears
    the flag (see :func:`observe_athlete_memory_fact`).
    """
    reference = now or datetime.now(timezone.utc)
    fact.confidence = _clamp_confidence(
        fact.confidence - ATHLETE_MEMORY_CONTRADICTION_PENALTY
    )
    fact.status = "needs_validation"
    fact.contradiction_note = reason.strip()[:500] or None
    fact.updated_at = reference
    await db.flush()
    return fact


async def update_athlete_memory_fact(
    db: AsyncSession,
    user_id: str,
    fact_id: str,
    *,
    fact: str | None = None,
    kind: str | None = None,
    category: str | None = None,
    source_snippet: str | None = None,
    source_exchange_id: str | None = None,
    confidence: float | None = None,
    status: str | None = None,
) -> models.AthleteMemoryFact | None:
    """Apply a user correction to an athlete memory fact and flush."""
    existing = await get_athlete_memory_fact(db, user_id, fact_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    if fact is not None:
        cleaned_fact = fact.strip()
        if not cleaned_fact:
            raise ValueError("fact must not be empty")
        existing.fact = cleaned_fact
        existing.fact_key = normalise_key(cleaned_fact)
        # Editing the fact text resolves any pending contradiction.
        existing.contradiction_note = None
    if kind is not None:
        existing.kind = _normalise_athlete_memory_kind(kind)
    if category is not None:
        existing.category = _normalise_athlete_memory_category(category)
    if source_snippet is not None:
        existing.source_snippet = source_snippet.strip()
    if source_exchange_id is not None:
        existing.source_exchange_id = source_exchange_id
    if confidence is not None:
        existing.confidence = _clamp_confidence(confidence)
    if status is not None:
        existing.status = status
        # Any move off ``needs_validation`` means the athlete has acted on the
        # validation prompt, so the contradiction note no longer applies.
        if status != "needs_validation":
            existing.contradiction_note = None
        if status == "user_confirmed":
            existing.confidence = max(existing.confidence, 0.9)
            existing.last_confirmed_at = now
    existing.updated_at = now
    await db.flush()
    return existing


async def delete_athlete_memory_fact(
    db: AsyncSession, user_id: str, fact_id: str
) -> bool:
    """Permanently remove an athlete memory fact owned by a user.

    Returns ``True`` when a row was deleted, ``False`` when no matching fact
    exists for the user.
    """
    existing = await get_athlete_memory_fact(db, user_id, fact_id)
    if existing is None:
        return False
    await db.delete(existing)
    await db.flush()
    return True


async def get_prompt_athlete_memory_facts(
    db: AsyncSession,
    user_id: str,
    *,
    now: datetime | None = None,
    min_confidence: float = ATHLETE_MEMORY_PROMPT_MIN_CONFIDENCE,
    min_evidence: int = ATHLETE_MEMORY_MIN_EVIDENCE,
    stale_after_days: int = ATHLETE_MEMORY_STALE_AFTER_DAYS,
    limit: int = ATHLETE_MEMORY_PROMPT_LIMIT,
) -> list[models.AthleteMemoryFact]:
    """Return only memory facts safe enough to include in coach prompts."""
    reference = now or datetime.now(timezone.utc)
    facts = await list_athlete_memory_facts(
        db, user_id, include_inactive=False, now=reference
    )
    cutoff = reference - timedelta(days=stale_after_days)
    prompt_facts: list[models.AthleteMemoryFact] = []
    for fact in facts:
        # Facts contradicted by fresh evidence are withheld from the coach until
        # the athlete validates or corrects them.
        if fact.status == "needs_validation":
            continue
        if fact.status == "user_confirmed":
            prompt_facts.append(fact)
        # An unconfirmed observation must clear both the confidence and the
        # evidence bar before it informs coaching, so a single event never
        # reaches the coach (#387).
        elif (
            fact.confidence >= min_confidence
            and fact.observation_count >= min_evidence
            and _as_aware_utc(fact.last_confirmed_at) >= cutoff
        ):
            prompt_facts.append(fact)
        if len(prompt_facts) >= limit:
            break
    return prompt_facts


async def clear_athlete_memory(db: AsyncSession, user_id: str) -> None:
    """Delete all memory facts and clear coach memory text for a user."""
    await db.execute(
        delete(models.AthleteMemoryFact).where(
            models.AthleteMemoryFact.user_id == user_id
        )
    )
    await db.execute(
        delete(models.AthleteHypothesis).where(
            models.AthleteHypothesis.user_id == user_id
        )
    )
    await db.execute(
        delete(models.AthleteOpenQuestion).where(
            models.AthleteOpenQuestion.user_id == user_id
        )
    )
    await db.execute(
        delete(models.AthleteExperiment).where(
            models.AthleteExperiment.user_id == user_id
        )
    )
    await db.execute(
        delete(models.AthletePrediction).where(
            models.AthletePrediction.user_id == user_id
        )
    )
    # Inquiries carry the athlete's own words in their answers, so a memory wipe
    # has to take them too (#506).
    await db.execute(
        delete(models.AthleteInquiry).where(models.AthleteInquiry.user_id == user_id)
    )
    coach_memory_row = await get_coach_memory(db, user_id)
    if coach_memory_row is not None:
        coach_memory_row.memory = ""
        coach_memory_row.updated_at = datetime.now(timezone.utc)
    await db.flush()


async def visible_athlete_memory_fact_texts(
    db: AsyncSession, user_id: str
) -> list[str]:
    """The texts of the learned traits Settings shows, with their contradiction
    notes. Read only: unlike :func:`list_athlete_memory_facts`, no confidence
    decay runs (ai-trainer-ops#215)."""
    rows = await db.execute(
        select(
            models.AthleteMemoryFact.fact, models.AthleteMemoryFact.contradiction_note
        ).where(
            models.AthleteMemoryFact.user_id == user_id,
            models.AthleteMemoryFact.status.in_(_ATHLETE_MEMORY_VISIBLE_STATUSES),
        )
    )
    return [text for row in rows for text in row if text]
