"""A reworded memory fact strengthens the one it restates (ai-trainer-ops#73)."""

import pytest
from sqlalchemy import func, select

import crud
import models
from domain.passwords import hash_password
from tests.conftest import TestSessionLocal

# Rewordings the extractor actually produced for one athlete, paraphrased.
FTP_RESTATEMENTS = [
    ("fact", "The athlete has a typical functional threshold power of 285W."),
    ("general", "FTP is approximately 285W."),
    (
        "general",
        "The athlete maintains a highly stable functional threshold power of 285W.",
    ),
    ("fact", "The athlete's functional threshold power is 285W."),
    ("general", "The athlete maintains a stable FTP of 285 watts."),
]


async def _user(db) -> str:
    user = models.User(
        email="memory@example.com",
        name="Rider",
        hashed_password=hash_password("Str0ng!Pass"),
        bike_type="road",
    )
    db.add(user)
    await db.flush()
    return user.id


async def _count(db, user_id: str) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(models.AthleteMemoryFact)
        .where(models.AthleteMemoryFact.user_id == user_id)
    )


@pytest.mark.asyncio
async def test_rewordings_of_one_fact_make_one_row_with_the_evidence_of_all():
    async with TestSessionLocal() as db:
        user_id = await _user(db)
        for category, fact in FTP_RESTATEMENTS:
            await crud.observe_athlete_memory_fact(
                db, user_id, fact=fact, category=category
            )

        assert await _count(db, user_id) == 1
        row = await db.scalar(select(models.AthleteMemoryFact))
        assert row.observation_count == len(FTP_RESTATEMENTS)
        assert row.fact == FTP_RESTATEMENTS[0][1]
        assert row.category == "fact"  # later "general" writes joined it


@pytest.mark.asyncio
async def test_a_rewording_revives_a_stale_fact():
    async with TestSessionLocal() as db:
        user_id = await _user(db)
        row = await crud.observe_athlete_memory_fact(
            db, user_id, fact=FTP_RESTATEMENTS[0][1], category="fact"
        )
        row.status = "stale"
        await db.flush()

        await crud.observe_athlete_memory_fact(db, user_id, fact=FTP_RESTATEMENTS[1][1])

        assert await _count(db, user_id) == 1
        assert row.status == "active"


@pytest.mark.asyncio
async def test_a_different_figure_or_a_different_subject_is_a_different_fact():
    async with TestSessionLocal() as db:
        user_id = await _user(db)
        for fact in [
            "The athlete has a typical functional threshold power of 285W.",
            "The athlete has a typical functional threshold power of 300W.",
            "The athlete's typical maximum heart rate during 4-minute VO2 max intervals is 174 bpm.",
            "The athlete generally enjoys rest days.",
            "The athlete prefers mountain bike trails over road riding.",
        ]:
            await crud.observe_athlete_memory_fact(db, user_id, fact=fact)

        assert await _count(db, user_id) == 5


@pytest.mark.asyncio
async def test_without_figures_only_a_near_identical_wording_merges():
    async with TestSessionLocal() as db:
        user_id = await _user(db)
        await crud.observe_athlete_memory_fact(
            db,
            user_id,
            fact="Maximum heart rate under heavy training fatigue typically remains suppressed during intervals",
        )
        await crud.observe_athlete_memory_fact(
            db,
            user_id,
            fact="Maximum heart rate under heavy training fatigue typically remains suppressed during intervals.",
        )
        await crud.observe_athlete_memory_fact(
            db, user_id, fact="Heart rate under heat stress rises during long rides."
        )

        assert await _count(db, user_id) == 2
