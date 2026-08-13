"""Reading back what was learned about this athlete and heat (#602).

:mod:`services.weather_preference` has always been able to *derive* a heat
belief. Nothing could ask it for one. The freshness allocator needs to, because
its heat deduction is only defensible if it can be switched off for an athlete
who demonstrably rides fine in the heat — and a lookup that silently returns
``None`` would make that guard pass its own unit tests while never firing in
production.

The match is on the canonical statement text on purpose: that text *is* the merge
key here, so ride evidence and what the athlete said themselves reinforce one
row. A test that asserted on a parsed field instead would be testing a different
mechanism from the one that runs.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import weather_preference as wp
from tests.conftest import TestSessionLocal


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> models.User:
    return await crud.create_user(
        db, email="heat-tolerance@example.com", name="Rider", hashed_password="x"
    )


async def _believe(
    db: AsyncSession, user: models.User, direction: str, *, confidence: float
) -> None:
    await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement=wp.preference_statement(wp.DIMENSION_HEAT, direction),
        category=wp.CATEGORY,
        confidence=confidence,
    )
    await db.flush()


async def test_nothing_learned_means_no_claim(db, user):
    """The honest answer for an athlete who has not ridden in the heat yet."""
    assert await wp.heat_tolerance_for_user(db, user.id) is None


async def test_a_tolerant_belief_is_read_back(db, user):
    await _believe(db, user, wp.DIRECTION_TOLERANT, confidence=0.7)

    assert await wp.heat_tolerance_for_user(db, user.id) == wp.DIRECTION_TOLERANT


async def test_a_sensitive_belief_is_read_back(db, user):
    await _believe(db, user, wp.DIRECTION_SENSITIVE, confidence=0.7)

    assert await wp.heat_tolerance_for_user(db, user.id) == wp.DIRECTION_SENSITIVE


async def test_the_better_supported_belief_wins(db, user):
    """Both directions can be on file; acting on the weaker one is the failure."""
    await _believe(db, user, wp.DIRECTION_SENSITIVE, confidence=0.3)
    await _believe(db, user, wp.DIRECTION_TOLERANT, confidence=0.8)

    assert await wp.heat_tolerance_for_user(db, user.id) == wp.DIRECTION_TOLERANT


async def test_beliefs_about_other_weather_are_not_heat(db, user):
    """Rain and wind live in the same category and must not answer this question."""
    await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement=wp.preference_statement(wp.DIMENSION_RAIN, wp.DIRECTION_TOLERANT),
        category=wp.CATEGORY,
        confidence=0.9,
    )
    await db.flush()

    assert await wp.heat_tolerance_for_user(db, user.id) is None


async def test_a_hypothesis_from_another_category_is_not_a_weather_belief(db, user):
    """Performance-model and LLM-formed hypotheses share the table, not the meaning."""
    await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement=wp.preference_statement(wp.DIMENSION_HEAT, wp.DIRECTION_TOLERANT),
        category="performance_model",
        confidence=0.9,
    )
    await db.flush()

    assert await wp.heat_tolerance_for_user(db, user.id) is None


async def test_one_athletes_belief_is_not_anothers(db, user):
    other = await crud.create_user(
        db, email="other-rider@example.com", name="Other", hashed_password="x"
    )
    await _believe(db, user, wp.DIRECTION_TOLERANT, confidence=0.8)

    assert await wp.heat_tolerance_for_user(db, other.id) is None
