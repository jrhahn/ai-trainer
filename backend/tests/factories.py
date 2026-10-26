"""Test data the backend tests share, so that a new users column or plan-day key
is fixed in one place, not in twenty files (ai-trainer-ops#289).

Each helper writes in a session of its own, commits, and returns the new row's
id, the way the copies it replaces did.
"""

from __future__ import annotations

import crud
import models
from domain.passwords import hash_password
from tests.conftest import TestSessionLocal


# The road rider most tests set up: FTP 250, Gemini.
ROAD_RIDER = {
    "bike_type": "road",
    "training_goal": "general_fitness",
    "fitness_level": "intermediate",
    "current_ftp": 250,
    "ai_provider": "gemini",
}


async def create_user_row(email: str, plan: list[dict] | None = None, **fields) -> str:
    """A users row with ``fields``, named "Rider" unless they say otherwise, and
    ``plan`` stored when given. A test module keeps its own defaults as a
    ``functools.partial`` of this, so the row is written in one place."""
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            hashed_password=hash_password("Str0ng!Pass"),
            **{"name": "Rider", **fields},
        )
        db.add(user)
        await db.flush()
        if plan is not None:
            await crud.upsert_training_plan(db, user.id, plan)
        await db.commit()
        return user.id


async def create_planned_user(email: str, plan: list[dict]) -> str:
    """An onboarded road rider (FTP 250, Gemini) with ``plan`` stored."""
    return await create_user_row(email, plan, is_onboarded=True, **ROAD_RIDER)


async def create_plain_user(email: str) -> str:
    """A user row and nothing else."""
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email=email, name="T", hashed_password=hash_password("pw")
        )
        await db.commit()
        return user.id
