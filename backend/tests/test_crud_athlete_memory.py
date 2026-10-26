"""Unit tests for the crud data-access layer: the coach's memory, the athlete context and memory facts.

Split out of ``test_crud.py`` (ai-trainer-ops#259); its history is there.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from tests.conftest import TestSessionLocal


# ---------------------------------------------------------------------------
# Session fixture scoped to individual tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """Yield a fresh session that is rolled back after each test."""
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db: AsyncSession, email: str = "test@example.com") -> models.User:
    return await crud.create_user(
        db, email=email, name="Test User", hashed_password="hashed"
    )


# ---------------------------------------------------------------------------
# CoachMemory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_coach_memory_missing(db: AsyncSession) -> None:
    user = await _make_user(db)
    memory = await crud.get_coach_memory(db, user.id)
    assert memory is None


@pytest.mark.asyncio
async def test_upsert_coach_memory_creates_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    memory = await crud.upsert_coach_memory(db, user.id, "Prefers morning rides.")
    assert memory.memory == "Prefers morning rides."


@pytest.mark.asyncio
async def test_upsert_coach_memory_updates_existing(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.upsert_coach_memory(db, user.id, "Old memory.")
    updated = await crud.upsert_coach_memory(db, user.id, "New memory.")
    assert updated.memory == "New memory."


# ---------------------------------------------------------------------------
# AthleteContext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_athlete_context_missing(db: AsyncSession) -> None:
    user = await _make_user(db)
    context = await crud.get_athlete_context(db, user.id)
    assert context is None


@pytest.mark.asyncio
async def test_upsert_athlete_context_creates_new(db: AsyncSession) -> None:
    user = await _make_user(db)
    context = await crud.upsert_athlete_context(
        db,
        user.id,
        training_tendency="overtrains",
        rest_response="restless",
        adherence_pattern="adds_extra",
        strengths=["VO2max work"],
        weaknesses=["easy days"],
        preferred_terrain=["singletrack"],
        preferred_session_types=["VO2max", "MTB skills"],
        coaching_risks=["doing too much when fresh"],
        notes="Needs permission to rest.",
    )

    assert context.training_tendency == "overtrains"
    assert context.rest_response == "restless"
    assert context.adherence_pattern == "adds_extra"
    assert context.coaching_risks == ["doing too much when fresh"]


@pytest.mark.asyncio
async def test_upsert_athlete_context_updates_existing(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.upsert_athlete_context(
        db,
        user.id,
        training_tendency="overtrains",
    )
    updated = await crud.upsert_athlete_context(
        db,
        user.id,
        training_tendency="balanced",
        rest_response="calm",
        adherence_pattern="follows_plan",
    )

    assert updated.training_tendency == "balanced"
    assert updated.rest_response == "calm"
    assert updated.adherence_pattern == "follows_plan"


# ---------------------------------------------------------------------------
# AthleteMemoryFact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observe_athlete_memory_fact_repeated_observations_increase_confidence(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Does too much when fresh",
        category="training tendency",
        source_snippet="After a rest day I usually add extra intervals.",
    )
    first_confidence = first.confidence
    second = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="does   too much WHEN fresh",
        category="training tendency",
        source_snippet="I felt fresh so I added a second hard set.",
    )

    assert second.id == first.id
    assert second.category == "training_tendency"
    assert second.observation_count == 2
    assert second.confidence > first_confidence
    assert second.source_snippet == "I felt fresh so I added a second hard set."


@pytest.mark.asyncio
async def test_observe_athlete_memory_fact_persists_and_reclassifies_kind(
    db: AsyncSession,
) -> None:
    """kind is stored, defaults to observation, and re-observing can promote it (#386)."""
    user = await _make_user(db)

    # An unspecified kind defaults to the safer "observation".
    default = await crud.observe_athlete_memory_fact(
        db, user.id, fact="Prefers MTB", category="preference"
    )
    assert default.kind == "observation"

    # A stable value can be recorded explicitly as a fact.
    ftp = await crud.observe_athlete_memory_fact(
        db, user.id, fact="FTP is about 250 W", kind="fact", category="general"
    )
    assert ftp.kind == "fact"

    # An unrecognised label collapses to "observation".
    weird = await crud.observe_athlete_memory_fact(
        db, user.id, fact="Weird one", kind="hunch", category="general"
    )
    assert weird.kind == "observation"

    # Re-observing the same row with a new kind re-classifies it.
    promoted = await crud.observe_athlete_memory_fact(
        db, user.id, fact="Prefers MTB", kind="fact", category="preference"
    )
    assert promoted.id == default.id
    assert promoted.kind == "fact"

    # A direct update can reclassify too.
    updated = await crud.update_athlete_memory_fact(
        db, user.id, promoted.id, kind="observation"
    )
    assert updated is not None
    assert updated.kind == "observation"


@pytest.mark.asyncio
async def test_athlete_memory_confidence_decays_after_grace_period(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Prefers morning rides",
        category="preference",
        confidence=0.7,
        observed_at=observed_at,
        trusted=True,
    )

    # Within the grace window confidence is untouched.
    fresh = observed_at + timedelta(days=crud.ATHLETE_MEMORY_DECAY_GRACE_DAYS)
    assert await crud.apply_athlete_memory_confidence_decay(db, user.id, now=fresh) == 0
    assert fact.confidence == 0.7

    # Past the grace window it erodes linearly with idle days.
    later = observed_at + timedelta(days=crud.ATHLETE_MEMORY_DECAY_GRACE_DAYS + 20)
    assert await crud.apply_athlete_memory_confidence_decay(db, user.id, now=later) == 1
    assert fact.confidence == pytest.approx(
        0.7 - crud.ATHLETE_MEMORY_CONFIDENCE_DECAY_PER_DAY * 20
    )
    assert fact.status == "active"


@pytest.mark.asyncio
async def test_athlete_memory_decay_marks_fact_stale_and_observation_revives_it(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Skips long rides in winter",
        category="adherence",
        confidence=0.4,
        observed_at=observed_at,
    )

    # Enough idle days to fall below the stale threshold.
    later = observed_at + timedelta(days=crud.ATHLETE_MEMORY_DECAY_GRACE_DAYS + 40)
    await crud.apply_athlete_memory_confidence_decay(db, user.id, now=later)
    assert fact.status == "stale"
    assert fact.confidence < crud.ATHLETE_MEMORY_STALE_CONFIDENCE

    # A fresh observation revives the fact and rebuilds confidence.
    revived = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Skips long rides in winter",
        category="adherence",
        observed_at=later,
    )
    assert revived.id == fact.id
    assert revived.status == "active"
    assert revived.confidence > 0.0


@pytest.mark.asyncio
async def test_athlete_memory_confirmed_facts_do_not_decay(db: AsyncSession) -> None:
    user = await _make_user(db)
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Races cyclocross every autumn",
        category="motivation",
        confidence=0.7,
        observed_at=observed_at,
    )
    await crud.update_athlete_memory_fact(
        db, user.id, fact.id, status="user_confirmed"
    )
    confirmed_confidence = fact.confidence

    later = observed_at + timedelta(days=crud.ATHLETE_MEMORY_DECAY_GRACE_DAYS + 200)
    assert await crud.apply_athlete_memory_confidence_decay(db, user.id, now=later) == 0
    assert fact.status == "user_confirmed"
    assert fact.confidence == confirmed_confidence


@pytest.mark.asyncio
async def test_athlete_memory_archives_obsolete_observation(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Prefers gravel over road",
        category="preference",
        confidence=0.6,
        observed_at=observed_at,
    )

    # Just before the archive window the fact has decayed but is still on file.
    before = observed_at + timedelta(days=crud.ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS - 1)
    await crud.apply_athlete_memory_confidence_decay(db, user.id, now=before)
    assert fact.status in {"active", "stale"}

    # Past the archive window an unmentioned observation is archived.
    later = observed_at + timedelta(days=crud.ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS)
    assert await crud.apply_athlete_memory_confidence_decay(db, user.id, now=later) == 1
    assert fact.status == "archived"

    # Archiving is idempotent: a second pass reports no further change.
    assert await crud.apply_athlete_memory_confidence_decay(db, user.id, now=later) == 0

    # Archived facts drop out of the default management view but remain on file.
    active = await crud.list_athlete_memory_facts(db, user.id, now=later)
    assert fact.id not in {f.id for f in active}
    all_facts = await crud.list_athlete_memory_facts(
        db, user.id, include_inactive=True, now=later
    )
    assert fact.id in {f.id for f in all_facts}


@pytest.mark.asyncio
async def test_athlete_memory_archived_fact_revived_by_observation(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Trains best in the evening",
        category="preference",
        confidence=0.5,
        observed_at=observed_at,
    )

    later = observed_at + timedelta(days=crud.ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS + 10)
    await crud.apply_athlete_memory_confidence_decay(db, user.id, now=later)
    assert fact.status == "archived"

    revived = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Trains best in the evening",
        category="preference",
        observed_at=later,
    )
    assert revived.id == fact.id
    assert revived.status == "active"
    # Fresh evidence refreshes confidence back above the default floor.
    assert revived.confidence >= crud.ATHLETE_MEMORY_DEFAULT_CONFIDENCE


@pytest.mark.asyncio
async def test_athlete_memory_confirmed_facts_not_archived(db: AsyncSession) -> None:
    user = await _make_user(db)
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Aims for a sub-9-hour gran fondo",
        category="motivation",
        confidence=0.7,
        observed_at=observed_at,
    )
    await crud.update_athlete_memory_fact(
        db, user.id, fact.id, status="user_confirmed"
    )

    later = observed_at + timedelta(days=crud.ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS + 100)
    assert await crud.apply_athlete_memory_confidence_decay(db, user.id, now=later) == 0
    assert fact.status == "user_confirmed"


@pytest.mark.asyncio
async def test_prompt_athlete_memory_facts_omit_low_confidence_stale_and_rejected(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    # Corroborated by a second observation, so it clears the evidence bar (#387).
    included = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Responds well to clear recovery permission",
        category="coaching",
        confidence=0.6,
    )
    included = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Responds well to clear recovery permission",
        category="coaching",
        confidence=0.6,
    )
    low_confidence = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Maybe dislikes gym work",
        category="preference",
        confidence=0.2,
    )
    rejected = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Only trains indoors",
        category="preference",
        confidence=0.8,
    )
    stale = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Often skips Monday sessions",
        category="adherence",
        confidence=0.8,
        observed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    confirmed = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Uses MTB races as motivation",
        category="motivation",
        confidence=0.1,
        observed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    await crud.update_athlete_memory_fact(
        db, user.id, rejected.id, status="rejected"
    )
    await crud.update_athlete_memory_fact(
        db, user.id, confirmed.id, status="user_confirmed"
    )

    facts = await crud.get_prompt_athlete_memory_facts(
        db,
        user.id,
        now=datetime(2026, 6, 18, tzinfo=timezone.utc),
        stale_after_days=90,
    )
    fact_ids = {fact.id for fact in facts}

    assert included.id in fact_ids
    assert confirmed.id in fact_ids
    assert low_confidence.id not in fact_ids
    assert rejected.id not in fact_ids
    assert stale.id not in fact_ids


@pytest.mark.asyncio
async def test_single_observation_capped_and_withheld_until_corroborated(
    db: AsyncSession,
) -> None:
    """A single event never promotes itself into trusted, coach-visible memory (#387)."""
    user = await _make_user(db)

    # However confident the extraction claims to be, a first sighting is capped
    # below the prompt threshold and left out of coach prompts.
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Fades in the final interval of long sessions",
        category="fatigue_response",
        confidence=0.95,
    )
    assert fact.observation_count == 1
    assert fact.confidence <= crud.ATHLETE_MEMORY_INITIAL_CONFIDENCE_CAP
    assert fact.confidence < crud.ATHLETE_MEMORY_PROMPT_MIN_CONFIDENCE

    prompt_facts = await crud.get_prompt_athlete_memory_facts(db, user.id)
    assert fact.id not in {f.id for f in prompt_facts}

    # A second, corroborating observation grows confidence past the bar and,
    # having met the evidence minimum, the fact now informs coaching.
    corroborated = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Fades in the final interval of long sessions",
        category="fatigue_response",
        confidence=0.95,
    )
    assert corroborated.id == fact.id
    assert corroborated.observation_count == crud.ATHLETE_MEMORY_MIN_EVIDENCE
    assert corroborated.confidence >= crud.ATHLETE_MEMORY_PROMPT_MIN_CONFIDENCE

    prompt_facts = await crud.get_prompt_athlete_memory_facts(db, user.id)
    assert fact.id in {f.id for f in prompt_facts}


@pytest.mark.asyncio
async def test_trusted_observation_bypasses_evidence_gate(db: AsyncSession) -> None:
    """A vetted, trusted write is usable at once — no re-earning trust (#387)."""
    user = await _make_user(db)

    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Targets a sub-9-hour gran fondo",
        category="motivation",
        confidence=0.75,
        trusted=True,
    )
    # Trust is honoured directly and enough evidence is seeded to clear the bar.
    assert fact.confidence == pytest.approx(0.75)
    assert fact.observation_count >= crud.ATHLETE_MEMORY_MIN_EVIDENCE

    prompt_facts = await crud.get_prompt_athlete_memory_facts(db, user.id)
    assert fact.id in {f.id for f in prompt_facts}


@pytest.mark.asyncio
async def test_untrusted_observation_does_not_reclassify_confirmed_fact(
    db: AsyncSession,
) -> None:
    """One automated observation must not overwrite athlete-confirmed knowledge (#387)."""
    user = await _make_user(db)

    fact = await crud.observe_athlete_memory_fact(
        db, user.id, fact="FTP is 300 W", kind="fact", category="general"
    )
    confirmed = await crud.update_athlete_memory_fact(
        db, user.id, fact.id, status="user_confirmed"
    )
    assert confirmed is not None and confirmed.kind == "fact"

    # An automated re-observation classifying it as a mere observation is ignored;
    # the athlete-confirmed classification stands.
    reobserved = await crud.observe_athlete_memory_fact(
        db, user.id, fact="FTP is 300 W", kind="observation", category="general"
    )
    assert reobserved.id == fact.id
    assert reobserved.kind == "fact"


@pytest.mark.asyncio
async def test_update_athlete_memory_fact_edits_and_rejects_fact(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Does too much when fresh",
        category="training",
        confidence=0.7,
    )

    updated = await crud.update_athlete_memory_fact(
        db,
        user.id,
        fact.id,
        fact="Adds extra work after rest days",
        category="coaching risk",
        confidence=0.8,
        status="user_confirmed",
        source_snippet="User corrected the wording.",
    )
    assert updated is not None
    assert updated.fact == "Adds extra work after rest days"
    assert updated.category == "coaching_risk"
    assert updated.confidence >= 0.9
    assert updated.status == "user_confirmed"
    assert updated.source_snippet == "User corrected the wording."

    rejected = await crud.update_athlete_memory_fact(
        db, user.id, fact.id, status="rejected"
    )
    assert rejected is not None
    assert rejected.status == "rejected"


@pytest.mark.asyncio
async def test_record_contradiction_lowers_confidence_and_flags_for_validation(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="FTP is around 320 W",
        category="fitness",
        confidence=0.8,
    )
    before = fact.confidence
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)

    flagged = await crud.record_athlete_memory_fact_contradiction(
        db,
        fact,
        reason="Held 400 W for 5x4 min on 2026-07-05 — well above the stored 320 W.",
        now=now,
    )
    assert flagged.status == "needs_validation"
    assert flagged.confidence == pytest.approx(
        before - crud.ATHLETE_MEMORY_CONTRADICTION_PENALTY
    )
    assert "400 W" in (flagged.contradiction_note or "")
    assert flagged.updated_at == now


@pytest.mark.asyncio
async def test_needs_validation_fact_visible_but_out_of_prompts(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Struggles in the heat",
        category="fitness",
        confidence=0.8,
    )
    await crud.record_athlete_memory_fact_contradiction(
        db, fact, reason="Strong performances on three warm-weather rides."
    )

    # Surfaced in the default management view so the athlete can act on it.
    visible = await crud.list_athlete_memory_facts(db, user.id)
    assert fact.id in {f.id for f in visible}

    # But withheld from coach prompts until the athlete validates or corrects it.
    prompt_facts = await crud.get_prompt_athlete_memory_facts(db, user.id)
    assert fact.id not in {f.id for f in prompt_facts}


@pytest.mark.asyncio
async def test_needs_validation_does_not_decay_or_archive(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Rarely rides in the rain",
        category="preference",
        confidence=0.8,
        observed_at=observed_at,
    )
    await crud.record_athlete_memory_fact_contradiction(
        db, fact, reason="Several rainy rides logged.", now=observed_at
    )

    # Long after the archive window it holds its flagged state, awaiting the athlete.
    later = observed_at + timedelta(days=crud.ATHLETE_MEMORY_ARCHIVE_AFTER_DAYS + 30)
    assert await crud.apply_athlete_memory_confidence_decay(db, user.id, now=later) == 0
    assert fact.status == "needs_validation"


@pytest.mark.asyncio
async def test_fresh_observation_revives_contradicted_fact_and_clears_note(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Prefers short intense sessions",
        category="preference",
        confidence=0.7,
    )
    await crud.record_athlete_memory_fact_contradiction(
        db, fact, reason="Logged several long endurance rides."
    )
    assert fact.status == "needs_validation"

    revived = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Prefers short intense sessions",
        category="preference",
    )
    assert revived.id == fact.id
    assert revived.status == "active"
    assert revived.contradiction_note is None


@pytest.mark.asyncio
async def test_confirming_contradicted_fact_clears_note(db: AsyncSession) -> None:
    user = await _make_user(db)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Recovers slowly from hard blocks",
        category="fitness",
        confidence=0.7,
    )
    await crud.record_athlete_memory_fact_contradiction(
        db, fact, reason="Back-to-back hard weeks with strong numbers."
    )

    confirmed = await crud.update_athlete_memory_fact(
        db, user.id, fact.id, status="user_confirmed"
    )
    assert confirmed is not None
    assert confirmed.status == "user_confirmed"
    assert confirmed.contradiction_note is None


@pytest.mark.asyncio
async def test_delete_athlete_memory_fact_removes_fact(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    fact = await crud.observe_athlete_memory_fact(
        db,
        user.id,
        fact="Prefers long endurance rides",
        category="preference",
    )

    deleted = await crud.delete_athlete_memory_fact(db, user.id, fact.id)
    assert deleted is True
    assert await crud.get_athlete_memory_fact(db, user.id, fact.id) is None

    # Deleting an already-removed fact is a no-op that reports failure.
    assert await crud.delete_athlete_memory_fact(db, user.id, fact.id) is False


@pytest.mark.asyncio
async def test_delete_athlete_memory_fact_scoped_to_owner(
    db: AsyncSession,
) -> None:
    owner = await _make_user(db)
    other = await _make_user(db, email="other@example.com")
    fact = await crud.observe_athlete_memory_fact(
        db,
        owner.id,
        fact="Races best in cool weather",
        category="preference",
    )

    # A different user cannot delete someone else's fact.
    assert await crud.delete_athlete_memory_fact(db, other.id, fact.id) is False
    assert await crud.get_athlete_memory_fact(db, owner.id, fact.id) is not None


# ---------------------------------------------------------------------------
# CoachMemory compare-and-set (#346)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_coach_memory_if_unchanged_creates_when_absent(db: AsyncSession) -> None:
    user = await _make_user(db, "cas-create@example.com")
    applied = await crud.update_coach_memory_if_unchanged(
        db, user.id, expected="", new="fresh"
    )
    assert applied is True
    assert (await crud.get_coach_memory(db, user.id)).memory == "fresh"


@pytest.mark.asyncio
async def test_update_coach_memory_if_unchanged_applies_on_match(db: AsyncSession) -> None:
    user = await _make_user(db, "cas-match@example.com")
    await crud.upsert_coach_memory(db, user.id, "base")
    applied = await crud.update_coach_memory_if_unchanged(
        db, user.id, expected="base", new="updated"
    )
    assert applied is True
    assert (await crud.get_coach_memory(db, user.id)).memory == "updated"


@pytest.mark.asyncio
async def test_update_coach_memory_if_unchanged_rejects_on_conflict(db: AsyncSession) -> None:
    user = await _make_user(db, "cas-conflict@example.com")
    await crud.upsert_coach_memory(db, user.id, "current")
    applied = await crud.update_coach_memory_if_unchanged(
        db, user.id, expected="stale-base", new="overwrite"
    )
    assert applied is False
    assert (await crud.get_coach_memory(db, user.id)).memory == "current"


@pytest.mark.asyncio
async def test_update_coach_memory_if_unchanged_no_create_when_expected_nonempty(
    db: AsyncSession,
) -> None:
    user = await _make_user(db, "cas-nocreate@example.com")
    applied = await crud.update_coach_memory_if_unchanged(
        db, user.id, expected="something", new="new"
    )
    assert applied is False
    assert await crud.get_coach_memory(db, user.id) is None


@pytest.mark.asyncio
async def test_get_coach_memory_for_update_returns_row(db: AsyncSession) -> None:
    user = await _make_user(db, "cm-lock@example.com")
    await crud.upsert_coach_memory(db, user.id, "notes")

    locked = await crud.get_coach_memory(db, user.id, for_update=True)
    assert locked is not None
    assert locked.memory == "notes"


@pytest.mark.asyncio
async def test_get_coach_memory_for_update_missing_is_none(db: AsyncSession) -> None:
    user = await _make_user(db, "cm-lock-missing@example.com")
    assert await crud.get_coach_memory(db, user.id, for_update=True) is None
