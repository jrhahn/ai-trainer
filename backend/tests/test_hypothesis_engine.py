"""Tests for the deterministic per-session hypothesis engine (#479).

The pure :func:`derive_performance_hypotheses` maps the Athlete Performance Model +
detected limiter to testable hypotheses, each carrying evidence, confidence and
alternative explanations. :func:`refresh_performance_hypotheses` persists them
through the merge/decay lifecycle so recurring hypotheses accrue evidence while
ones the model no longer supports decay and retire.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
from services import limiter_detection
from services.hypothesis_engine import (
    CATEGORY,
    derive_performance_hypotheses,
    refresh_performance_hypotheses,
)
from tests.conftest import TestSessionLocal


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


def _attr(estimate=None, score=None, confidence=0.6):
    return {"estimate": estimate, "score": score, "confidence": confidence}


def _threshold_limited():
    """FTP well below MAP -> threshold is the limiter."""
    return {
        "ftp": _attr(estimate=250),
        "map": _attr(estimate=360),
        "fractional_utilization": _attr(estimate=0.69),
        "vo2max": _attr(estimate=62),
        "aerobic_endurance": _attr(score="high"),
        "fatigue_resistance": _attr(score="above_average"),
    }


def _vo2max_limited():
    return {
        "ftp": _attr(estimate=320),
        "map": _attr(estimate=375),
        "fractional_utilization": _attr(estimate=0.85),
        "vo2max": _attr(estimate=64),
        "aerobic_endurance": _attr(score="above_average"),
        "fatigue_resistance": _attr(score="high"),
    }


def _durability_limited():
    return {
        "ftp": _attr(estimate=280),
        "map": _attr(estimate=375),
        "fractional_utilization": _attr(estimate=0.75),
        "aerobic_endurance": _attr(score="developing"),
        "fatigue_resistance": _attr(score="fades"),
    }


def _derive(attrs):
    limiters = limiter_detection.detect_limiters(attrs)
    return derive_performance_hypotheses(attrs, limiters)


# --- pure engine -------------------------------------------------------------


def test_threshold_model_forms_a_threshold_limiter_hypothesis():
    hyps = _derive(_threshold_limited())
    limiter_hyp = next(
        h for h in hyps if "limiter is likely threshold" in h["statement"].lower()
    )
    assert limiter_hyp["category"] == CATEGORY
    # Evidence cites the athlete's own numbers; alternatives are non-empty.
    joined = " ".join(limiter_hyp["evidence"])
    assert "250" in joined and "360" in joined
    assert limiter_hyp["alternative_explanations"]
    assert 0.0 < limiter_hyp["confidence"] <= 1.0


def test_every_hypothesis_carries_evidence_alternatives_and_confidence():
    for build in (_threshold_limited, _vo2max_limited, _durability_limited):
        for hyp in _derive(build()):
            assert hyp["evidence"], hyp["statement"]
            assert hyp["alternative_explanations"], hyp["statement"]
            assert 0.0 < hyp["confidence"] <= 1.0


def test_vo2max_model_names_the_ceiling():
    hyps = _derive(_vo2max_limited())
    assert any("VO₂max" in h["statement"] for h in hyps)


def test_durability_model_names_durability():
    hyps = _derive(_durability_limited())
    assert any("durability" in h["statement"].lower() for h in hyps)


def test_strong_engine_and_low_ftp_are_surfaced_as_distinct_claims():
    hyps = _derive(_threshold_limited())
    statements = " || ".join(h["statement"] for h in hyps)
    assert "strong aerobic engine" in statements.lower()
    assert "ftp could be underestimated" in statements.lower()


def test_insufficient_model_forms_no_hypotheses():
    assert derive_performance_hypotheses({}, limiter_detection.detect_limiters({})) == []
    assert derive_performance_hypotheses(None, None) == []


def test_hypotheses_are_sorted_by_confidence():
    hyps = _derive(_threshold_limited())
    confs = [h["confidence"] for h in hyps]
    assert confs == sorted(confs, reverse=True)


# --- persistence lifecycle (DB) ----------------------------------------------


async def _persist_model(db, user_id, attrs):
    limiters = limiter_detection.detect_limiters(attrs)
    await crud.upsert_athlete_performance_model(
        db,
        user_id,
        attributes=attrs,
        likely_limiter=limiter_detection.top_limiter(limiters),
        limiters=limiters,
        source_window_days=90,
        derived_from_rides=6,
    )


@pytest.mark.asyncio
async def test_refresh_persists_hypotheses_with_structured_fields(db: AsyncSession):
    user = await crud.create_user(
        db, email="hyp-persist@example.com", name="H", hashed_password="x"
    )
    await _persist_model(db, user.id, _threshold_limited())

    count = await refresh_performance_hypotheses(db, user)
    assert count >= 1

    stored = await crud.list_athlete_hypotheses(db, user.id)
    assert stored
    threshold = next(h for h in stored if "threshold" in h.statement.lower())
    assert threshold.category == CATEGORY
    assert threshold.evidence and any("250" in e for e in threshold.evidence)
    assert threshold.alternative_explanations
    assert threshold.status == "proposed"


@pytest.mark.asyncio
async def test_repeated_refresh_reinforces_without_duplicating(db: AsyncSession):
    user = await crud.create_user(
        db, email="hyp-reinforce@example.com", name="H", hashed_password="x"
    )
    await _persist_model(db, user.id, _threshold_limited())

    await refresh_performance_hypotheses(db, user)
    # Snapshot the values as plain ints — the ORM identity map hands back the same
    # objects on the second read, so holding references would show post-refresh state.
    first = {
        h.statement_key: (h.evidence_count, h.confidence)
        for h in await crud.list_athlete_hypotheses(db, user.id)
    }
    await refresh_performance_hypotheses(db, user)
    second = {h.statement_key: h for h in await crud.list_athlete_hypotheses(db, user.id)}

    assert set(first) == set(second)  # no duplicates created
    for key, (before_count, before_conf) in first.items():
        after = second[key]
        assert after.evidence_count == before_count + 1
        assert after.confidence >= before_conf


@pytest.mark.asyncio
async def test_unsupported_hypothesis_decays_then_retires(db: AsyncSession):
    user = await crud.create_user(
        db, email="hyp-decay@example.com", name="H", hashed_password="x"
    )
    await _persist_model(db, user.id, _threshold_limited())
    await refresh_performance_hypotheses(db, user)

    threshold = next(
        h
        for h in await crud.list_athlete_hypotheses(db, user.id)
        if "threshold" in h.statement.lower()
    )
    before = threshold.confidence

    # The model changes so it no longer supports a threshold limiter: the stale
    # hypothesis should lose confidence on the next pass.
    await _persist_model(db, user.id, _vo2max_limited())
    await refresh_performance_hypotheses(db, user)
    survivor = next(
        (
            h
            for h in await crud.list_athlete_hypotheses(db, user.id)
            if h.statement_key == threshold.statement_key
        ),
        None,
    )
    assert survivor is not None
    assert survivor.confidence < before

    # Enough further unsupported passes retire it entirely (no stale duplicate left).
    for _ in range(5):
        await crud.decay_unsupported_model_hypotheses(
            db, user.id, category=CATEGORY, supported_keys=set()
        )
    gone = next(
        (
            h
            for h in await crud.list_athlete_hypotheses(db, user.id, include_resolved=True)
            if h.statement_key == threshold.statement_key
        ),
        None,
    )
    assert gone is None


@pytest.mark.asyncio
async def test_refresh_without_model_is_a_noop(db: AsyncSession):
    user = await crud.create_user(
        db, email="hyp-nomodel@example.com", name="H", hashed_password="x"
    )
    assert await refresh_performance_hypotheses(db, user) == 0
    assert await crud.list_athlete_hypotheses(db, user.id) == []
