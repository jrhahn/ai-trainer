"""Unit tests for the crud data-access layer: hypotheses, experiments, predictions, the performance model.

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
# AthleteHypothesis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_athlete_hypothesis_creates_proposed(db: AsyncSession) -> None:
    user = await _make_user(db)
    hypothesis = await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="Upper-body strength training suppresses next-day HR response",
        category="fatigue response",
        rationale="HR ~8 bpm low on the two days after gym sessions.",
        confidence=0.38,
    )

    assert hypothesis.id is not None
    assert hypothesis.status == "proposed"
    assert hypothesis.category == "fatigue_response"
    assert hypothesis.evidence_count == 1
    assert hypothesis.confidence == pytest.approx(0.38)


@pytest.mark.asyncio
async def test_propose_athlete_hypothesis_strengthens_on_repeat(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="Rides stronger in the second half of a block",
        category="general",
        confidence=0.3,
    )
    first_confidence = first.confidence
    second = await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="rides   STRONGER in the second half of a block",
        category="general",
        rationale="Two more blocks show the same rising trend.",
    )

    assert second.id == first.id
    assert second.evidence_count == 2
    assert second.confidence > first_confidence
    assert second.rationale == "Two more blocks show the same rising trend."


@pytest.mark.asyncio
async def test_confirm_hypothesis_promotes_to_memory_fact(db: AsyncSession) -> None:
    user = await _make_user(db)
    hypothesis = await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="Performs best with two recovery days before a race",
        category="fatigue_response",
        rationale="Best results followed a two-day taper twice.",
        confidence=0.4,
    )

    updated = await crud.update_athlete_hypothesis(
        db, user.id, hypothesis.id, status="confirmed"
    )
    assert updated is not None
    assert updated.status == "confirmed"
    assert updated.confidence >= crud.ATHLETE_HYPOTHESIS_CONFIRM_CONFIDENCE

    # Confirming promotes the statement into a durable memory fact.
    facts = await crud.list_athlete_memory_facts(db, user.id)
    assert any(
        fact.fact == "Performs best with two recovery days before a race"
        for fact in facts
    )


@pytest.mark.asyncio
async def test_refuted_hypothesis_hidden_then_revived_by_evidence(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    hypothesis = await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="Struggles on back-to-back hard days",
        category="fatigue_response",
    )
    await crud.update_athlete_hypothesis(
        db, user.id, hypothesis.id, status="refuted"
    )

    # Refuted hypotheses drop out of the default (open) list.
    open_only = await crud.list_athlete_hypotheses(db, user.id)
    assert open_only == []
    assert len(await crud.list_athlete_hypotheses(db, user.id, include_resolved=True)) == 1

    # Fresh supporting evidence reopens the question.
    revived = await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="Struggles on back-to-back hard days",
        category="fatigue_response",
    )
    assert revived.id == hypothesis.id
    assert revived.status == "proposed"
    assert revived.evidence_count == 2


@pytest.mark.asyncio
async def test_delete_athlete_hypothesis_scoped_to_owner(db: AsyncSession) -> None:
    owner = await _make_user(db)
    other = await _make_user(db, email="other@example.com")
    hypothesis = await crud.propose_athlete_hypothesis(
        db, owner.id, statement="Fuels poorly on long rides", category="fueling"
    )

    assert await crud.delete_athlete_hypothesis(db, other.id, hypothesis.id) is False
    assert await crud.delete_athlete_hypothesis(db, owner.id, hypothesis.id) is True
    assert await crud.get_athlete_hypothesis(db, owner.id, hypothesis.id) is None


@pytest.mark.asyncio
async def test_clear_athlete_memory_removes_hypotheses(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.propose_athlete_hypothesis(
        db, user.id, statement="Prefers hilly terrain", category="preference"
    )

    await crud.clear_athlete_memory(db, user.id)
    assert await crud.list_athlete_hypotheses(db, user.id, include_resolved=True) == []


# ---------------------------------------------------------------------------
# Prompt-facing caps for hypotheses and open questions (#512)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_hypotheses_are_capped_while_the_list_stays_complete(
    db: AsyncSession,
) -> None:
    """The coach reads the strongest few; the athlete-facing list stays whole."""
    user = await _make_user(db)
    # Confidence descends with the index, so the expected survivors are known.
    for index in range(crud.ATHLETE_HYPOTHESIS_PROMPT_LIMIT + 5):
        await crud.propose_athlete_hypothesis(
            db,
            user.id,
            statement=f"Hypothesis number {index}",
            category="fatigue_response",
            confidence=0.9 - index * 0.01,
        )

    prompt = await crud.get_prompt_athlete_hypotheses(db, user.id)
    assert len(prompt) == crud.ATHLETE_HYPOTHESIS_PROMPT_LIMIT
    assert [h.statement for h in prompt] == [
        f"Hypothesis number {index}"
        for index in range(crud.ATHLETE_HYPOTHESIS_PROMPT_LIMIT)
    ]

    # The API/UI path is untouched — capping is a prompt concern only.
    assert (
        len(await crud.list_athlete_hypotheses(db, user.id))
        == crud.ATHLETE_HYPOTHESIS_PROMPT_LIMIT + 5
    )


@pytest.mark.asyncio
async def test_prompt_hypotheses_grow_no_further_once_capped(
    db: AsyncSession,
) -> None:
    """Adding another hypothesis must not make the prompt any bigger (#512)."""
    user = await _make_user(db)
    for index in range(crud.ATHLETE_HYPOTHESIS_PROMPT_LIMIT):
        await crud.propose_athlete_hypothesis(
            db, user.id, statement=f"Baseline {index}", category="general"
        )
    before = await crud.get_prompt_athlete_hypotheses(db, user.id)

    await crud.propose_athlete_hypothesis(
        db, user.id, statement="One more idea", category="general"
    )
    after = await crud.get_prompt_athlete_hypotheses(db, user.id)

    assert len(after) == len(before) == crud.ATHLETE_HYPOTHESIS_PROMPT_LIMIT


@pytest.mark.asyncio
async def test_prompt_hypotheses_drop_low_confidence_and_stale_ones(
    db: AsyncSession,
) -> None:
    """A barely-held or long-unsupported belief is not worth prompt tokens."""
    user = await _make_user(db)
    now = datetime.now(timezone.utc)
    await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="Still actively supported",
        category="general",
        confidence=0.6,
        observed_at=now,
    )
    await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="Decayed to near nothing",
        category="general",
        confidence=0.1,
        observed_at=now,
    )
    await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement="No fresh evidence in months",
        category="general",
        confidence=0.6,
        observed_at=now
        - timedelta(days=crud.ATHLETE_HYPOTHESIS_PROMPT_STALE_AFTER_DAYS + 1),
    )

    prompt = await crud.get_prompt_athlete_hypotheses(db, user.id, now=now)
    assert [h.statement for h in prompt] == ["Still actively supported"]


@pytest.mark.asyncio
async def test_prompt_open_questions_are_capped_by_evidence(
    db: AsyncSession,
) -> None:
    """The best-evidenced questions survive; the rest wait their turn."""
    user = await _make_user(db)
    for index in range(crud.ATHLETE_OPEN_QUESTION_PROMPT_LIMIT + 4):
        await crud.record_athlete_open_question(
            db, user.id, question=f"Question number {index}", category="general"
        )
    # One extra observation lifts a single question above the rest without
    # reaching the auto-close threshold.
    await crud.record_athlete_open_question(
        db, user.id, question="Question number 7", category="general"
    )

    prompt = await crud.get_prompt_athlete_open_questions(db, user.id)
    assert len(prompt) == crud.ATHLETE_OPEN_QUESTION_PROMPT_LIMIT
    assert prompt[0].question == "Question number 7"
    assert all(question.status == "open" for question in prompt)

    assert (
        len(await crud.list_athlete_open_questions(db, user.id))
        == crud.ATHLETE_OPEN_QUESTION_PROMPT_LIMIT + 4
    )


@pytest.mark.asyncio
async def test_prompt_open_questions_drop_stale_ones(db: AsyncSession) -> None:
    """A question nothing has added to in months is no longer being worked on."""
    user = await _make_user(db)
    now = datetime.now(timezone.utc)
    await crud.record_athlete_open_question(
        db, user.id, question="Is FTP underestimated?", category="general",
        observed_at=now,
    )
    await crud.record_athlete_open_question(
        db,
        user.id,
        question="Does altitude blunt recovery?",
        category="general",
        observed_at=now
        - timedelta(days=crud.ATHLETE_OPEN_QUESTION_PROMPT_STALE_AFTER_DAYS + 1),
    )

    prompt = await crud.get_prompt_athlete_open_questions(db, user.id, now=now)
    assert [q.question for q in prompt] == ["Is FTP underestimated?"]


# ---------------------------------------------------------------------------
# AthleteExperiment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggest_athlete_experiment_creates_suggested(db: AsyncSession) -> None:
    user = await _make_user(db)
    experiment = await crud.suggest_athlete_experiment(
        db,
        user.id,
        question="Does upper-body strength suppress next-day HR?",
        protocol="Repeat the gym session and compare HR on the next easy ride.",
        rationale="A clear HR drop would confirm the hypothesis.",
        category="fatigue response",
        hypothesis_id="hyp-123",
    )

    assert experiment.id is not None
    assert experiment.status == "suggested"
    assert experiment.category == "fatigue_response"
    assert experiment.hypothesis_id == "hyp-123"
    assert experiment.protocol_key


@pytest.mark.asyncio
async def test_suggest_athlete_experiment_dedupes_on_protocol(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)
    first = await crud.suggest_athlete_experiment(
        db,
        user.id,
        question="Which bike is faster?",
        protocol="Compare both bikes over the same climb using identical pedals.",
    )
    second = await crud.suggest_athlete_experiment(
        db,
        user.id,
        question="Which bike is faster for the same power?",
        protocol="  compare BOTH bikes over the same climb using identical pedals. ",
        rationale="Whichever is quicker at equal power wins.",
    )

    assert second.id == first.id
    assert second.question == "Which bike is faster for the same power?"
    assert second.rationale == "Whichever is quicker at equal power wins."
    assert len(await crud.list_athlete_experiments(db, user.id)) == 1


@pytest.mark.asyncio
async def test_dismissed_experiment_hidden_then_revived(db: AsyncSession) -> None:
    user = await _make_user(db)
    experiment = await crud.suggest_athlete_experiment(
        db,
        user.id,
        question="Is threshold higher than assumed?",
        protocol="Perform a 30-minute threshold test.",
    )
    await crud.update_athlete_experiment(
        db, user.id, experiment.id, status="dismissed"
    )

    assert await crud.list_athlete_experiments(db, user.id) == []
    assert (
        len(await crud.list_athlete_experiments(db, user.id, include_resolved=True))
        == 1
    )

    revived = await crud.suggest_athlete_experiment(
        db,
        user.id,
        question="Is threshold higher than assumed?",
        protocol="Perform a 30-minute threshold test.",
    )
    assert revived.id == experiment.id
    assert revived.status == "suggested"


@pytest.mark.asyncio
async def test_completed_experiment_not_revived(db: AsyncSession) -> None:
    user = await _make_user(db)
    experiment = await crud.suggest_athlete_experiment(
        db,
        user.id,
        question="Does heat cut power?",
        protocol="Repeat the session under cooler conditions.",
    )
    await crud.update_athlete_experiment(
        db, user.id, experiment.id, status="completed"
    )

    again = await crud.suggest_athlete_experiment(
        db,
        user.id,
        question="Does heat cut power?",
        protocol="Repeat the session under cooler conditions.",
    )
    assert again.id == experiment.id
    assert again.status == "completed"


@pytest.mark.asyncio
async def test_delete_athlete_experiment_scoped_to_owner(db: AsyncSession) -> None:
    owner = await _make_user(db)
    other = await _make_user(db, email="other-exp@example.com")
    experiment = await crud.suggest_athlete_experiment(
        db,
        owner.id,
        question="Which bike is faster?",
        protocol="Compare both bikes using identical power pedals.",
    )

    assert await crud.delete_athlete_experiment(db, other.id, experiment.id) is False
    assert await crud.delete_athlete_experiment(db, owner.id, experiment.id) is True
    assert await crud.get_athlete_experiment(db, owner.id, experiment.id) is None


@pytest.mark.asyncio
async def test_clear_athlete_memory_removes_experiments(db: AsyncSession) -> None:
    user = await _make_user(db)
    await crud.suggest_athlete_experiment(
        db,
        user.id,
        question="Which bike is faster?",
        protocol="Compare both bikes using identical power pedals.",
    )

    await crud.clear_athlete_memory(db, user.id)
    assert (
        await crud.list_athlete_experiments(db, user.id, include_resolved=True) == []
    )


# ---------------------------------------------------------------------------
# AthletePrediction (#383)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_prediction_refreshes_pending_duplicate(db: AsyncSession) -> None:
    user = await _make_user(db, "pred-dupe@example.com")
    first = await crud.record_athlete_prediction(
        db,
        user.id,
        prediction="Recovered by tomorrow",
        expected_outcome="HR baseline",
        horizon="tomorrow",
    )
    second = await crud.record_athlete_prediction(
        db,
        user.id,
        prediction="  recovered BY tomorrow  ",  # same key, different casing/space
        expected_outcome="Resting HR near baseline",
        horizon="by tomorrow morning",
    )
    assert first.id == second.id
    assert second.expected_outcome == "Resting HR near baseline"
    assert second.horizon == "by tomorrow morning"
    pending = await crud.list_athlete_predictions(db, user.id)
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_record_prediction_requires_text_and_outcome(db: AsyncSession) -> None:
    user = await _make_user(db, "pred-empty@example.com")
    with pytest.raises(ValueError):
        await crud.record_athlete_prediction(
            db, user.id, prediction="   ", expected_outcome="something"
        )
    with pytest.raises(ValueError):
        await crud.record_athlete_prediction(
            db, user.id, prediction="a claim", expected_outcome="  "
        )


@pytest.mark.asyncio
async def test_evaluate_prediction_is_idempotent(db: AsyncSession) -> None:
    user = await _make_user(db, "pred-eval@example.com")
    prediction = await crud.record_athlete_prediction(
        db,
        user.id,
        prediction="Hits VO2 targets next ride",
        expected_outcome="All reps at target power",
        confidence=0.5,
    )
    resolved = await crud.evaluate_athlete_prediction(
        db,
        user.id,
        prediction.id,
        correct=True,
        actual_outcome="Nailed every rep",
    )
    assert resolved is not None
    assert resolved.status == "correct"
    assert resolved.confidence == pytest.approx(0.7)
    assert resolved.evaluated_at is not None

    # Re-evaluating an already-scored prediction must not double-count.
    again = await crud.evaluate_athlete_prediction(
        db,
        user.id,
        prediction.id,
        correct=False,
        actual_outcome="ignored",
    )
    assert again is not None
    assert again.status == "correct"
    assert again.confidence == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_prediction_accuracy_counts_only_resolved(db: AsyncSession) -> None:
    user = await _make_user(db, "pred-acc@example.com")
    correct = await crud.record_athlete_prediction(
        db, user.id, prediction="p1", expected_outcome="o1"
    )
    wrong = await crud.record_athlete_prediction(
        db, user.id, prediction="p2", expected_outcome="o2"
    )
    await crud.record_athlete_prediction(
        db, user.id, prediction="p3", expected_outcome="o3"
    )  # left pending
    await crud.evaluate_athlete_prediction(
        db, user.id, correct.id, correct=True, actual_outcome="hit"
    )
    await crud.evaluate_athlete_prediction(
        db, user.id, wrong.id, correct=False, actual_outcome="miss"
    )

    evaluated, hits = await crud.get_athlete_prediction_accuracy(db, user.id)
    assert (evaluated, hits) == (2, 1)


@pytest.mark.asyncio
async def test_clear_athlete_memory_removes_predictions(db: AsyncSession) -> None:
    user = await _make_user(db, "pred-clear@example.com")
    await crud.record_athlete_prediction(
        db, user.id, prediction="p", expected_outcome="o"
    )
    await crud.clear_athlete_memory(db, user.id)
    assert await crud.list_athlete_predictions(db, user.id, include_resolved=True) == []


# ---------------------------------------------------------------------------
# AthletePerformanceModel (#475)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_athlete_performance_model_round_trip(db: AsyncSession) -> None:
    user = await _make_user(db, "perf-model@example.com")
    assert await crud.get_athlete_performance_model(db, user.id) is None

    attributes = {
        "ftp": {
            "estimate": 302,
            "score": None,
            "confidence": 0.74,
            "unit": "W",
            "evidence": ["Best 20-min power 318 W across 3 rides"],
            "missing_information": [],
        },
        "aerobic_endurance": {
            "estimate": None,
            "score": "high",
            "confidence": 0.6,
            "unit": None,
            "evidence": ["4 h ride with 3.1% decoupling"],
            "missing_information": [],
        },
    }
    limiters = [
        {
            "limiter": "threshold",
            "confidence": 0.72,
            "evidence": ["FTP low vs MAP"],
            "counter_evidence": [],
        }
    ]
    created = await crud.upsert_athlete_performance_model(
        db,
        user.id,
        attributes=attributes,
        likely_limiter="threshold",
        limiters=limiters,
        source_window_days=120,
        derived_from_rides=5,
    )
    assert created.attributes["ftp"]["estimate"] == 302
    assert created.derived_from_rides == 5
    assert created.likely_limiter == "threshold"
    assert created.limiters[0]["confidence"] == 0.72

    # Upsert overwrites in place (one row per user).
    updated = await crud.upsert_athlete_performance_model(
        db,
        user.id,
        attributes={"ftp": {"estimate": 310, "confidence": 0.8}},
        derived_from_rides=6,
    )
    assert updated.user_id == created.user_id
    assert updated.attributes["ftp"]["estimate"] == 310
    assert "aerobic_endurance" not in updated.attributes

    fetched = await crud.get_athlete_performance_model(db, user.id)
    assert fetched is not None
    assert fetched.attributes["ftp"]["estimate"] == 310
    # Not re-passing limiters clears them (one derivation writes the full picture).
    assert fetched.likely_limiter is None
    assert fetched.limiters is None


@pytest.mark.asyncio
async def test_athlete_performance_snapshots_are_time_series(db: AsyncSession) -> None:
    user = await _make_user(db, "perf-snap@example.com")
    older = datetime(2026, 5, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 6, 1, tzinfo=timezone.utc)

    await crud.create_athlete_performance_snapshot(
        db, user.id, attributes={"ftp": {"estimate": 300, "confidence": 0.7}},
        recorded_at=older,
    )
    await crud.create_athlete_performance_snapshot(
        db, user.id, attributes={"ftp": {"estimate": 310, "confidence": 0.75}},
        recorded_at=newer,
    )

    history = await crud.get_athlete_performance_snapshots(db, user.id)
    assert [s.attributes["ftp"]["estimate"] for s in history] == [300, 310]
