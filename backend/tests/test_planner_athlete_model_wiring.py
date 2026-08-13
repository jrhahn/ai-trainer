"""The planner actually receives the athlete model it is supposed to plan for (#602).

The scoring is unit-tested next door. This file tests the boring half that is
also the half that breaks silently: whether the block reaches the prompt at all,
from both triggers, and whether it correctly declines to.

Three failures it is here to catch:

* the section is built but never threaded into the prompt, so the whole feature
  is dead code that passes its own tests;
* the two plan triggers disagree — the button plans for the athlete and the
  nightly regen quietly keeps planning for a generic one, which is how the plan
  an athlete wakes up to stops matching the plan they were shown;
* memory is off and the objective reaches the prompt anyway.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services import freshness_allocation as fa
from services import motivation_model as mm
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
        db, email="planner-model@example.com", name="Rider", hashed_password="x"
    )


async def _give_them_an_objective(db: AsyncSession, user: models.User) -> None:
    await crud.upsert_athlete_motivation_model(
        db,
        user.id,
        updates={
            "primary_objective": "Maximise enjoyable technical trail riding",
            "utility_weights": {
                "enjoyment": 0.40,
                "adaptation": 0.20,
                "consistency": 0.20,
                "health": 0.20,
                "race_performance": 0.0,
            },
            "modality_affinity": {"mtb": 0.9, "road": 0.4},
        },
        source=mm.SOURCE_USER_SET,
    )
    await db.flush()


# ---------------------------------------------------------------------------
# The block itself
# ---------------------------------------------------------------------------


async def test_an_athlete_with_an_objective_gets_a_board(db, user):
    await _give_them_an_objective(db, user)

    section = await fa.athlete_model_section_for_user(db, user)

    assert "Maximise enjoyable technical trail riding" in section
    assert "freshness is worth spending on" in section


async def test_an_athlete_nobody_has_learned_anything_about_gets_nothing(db, user):
    """A new athlete's plan prompt is unchanged, which is the regression guard."""
    assert await fa.athlete_model_section_for_user(db, user) == ""


async def test_memory_off_means_the_objective_stays_out_of_the_plan(db, user):
    """The same switch the rest of the durable profile is gated on.

    An athlete who turned memory off did not ask for their objective to be
    recalled into a plan either.
    """
    await _give_them_an_objective(db, user)
    user.memory_updates_enabled = False
    await db.flush()

    assert await fa.athlete_model_section_for_user(db, user) == ""


async def test_a_failed_read_costs_the_context_and_never_the_plan(db, user, monkeypatch):
    """Best-effort by construction: a plan is the product."""
    await _give_them_an_objective(db, user)

    async def boom(*args, **kwargs):
        raise RuntimeError("motivation store is down")

    monkeypatch.setattr(crud, "get_athlete_motivation_model", boom)

    assert await fa.athlete_model_section_for_user(db, user) == ""


# ---------------------------------------------------------------------------
# Both triggers
# ---------------------------------------------------------------------------


async def test_plan_generation_passes_the_block_to_the_prompt(db, user, monkeypatch):
    from services import ai_service, prompts

    await _give_them_an_objective(db, user)
    section = await fa.athlete_model_section_for_user(db, user)
    assert section  # precondition: there is something to pass

    captured: dict[str, str] = {}

    async def fake_chat(provider, system_prompt, user_msg, **kwargs):
        captured["system"] = system_prompt
        captured["user"] = user_msg
        return '{"plan": []}'

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    await ai_service.generate_training_plan(
        {"currentFTP": 260}, athlete_model_section=section
    )

    assert "Maximise enjoyable technical trail riding" in captured["user"]
    assert "highest-value use" in captured["system"]
    assert prompts.plan_allocation_rule() in captured["system"]


async def test_plan_adaptation_passes_the_block_to_the_prompt(db, user, monkeypatch):
    """The nightly regen is the trigger nobody is watching, so it matters most."""
    from services import ai_service

    await _give_them_an_objective(db, user)
    section = await fa.athlete_model_section_for_user(db, user)

    captured: dict[str, str] = {}

    async def fake_chat(provider, system_prompt, user_msg, **kwargs):
        captured["system"] = system_prompt
        captured["user"] = user_msg
        return '{"updatedDays": []}'

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    await ai_service.adapt_training_plan(
        [], [], {"currentFTP": 260}, athlete_model_section=section
    )

    assert "Maximise enjoyable technical trail riding" in captured["user"]
    assert "highest-value use" in captured["system"]


async def test_both_plan_entry_points_build_the_block_the_same_way():
    """One helper, called by both — a second implementation is a second athlete."""
    import inspect

    from routers import ai as ai_router
    from services import plan_maintenance

    for module in (ai_router, plan_maintenance):
        source = inspect.getsource(module)
        assert "freshness_allocation.athlete_model_section_for_user" in source


@pytest.mark.parametrize("field", ["athlete_model_section"])
def test_the_plan_builders_accept_the_block(field):
    """Guards the thread from prompt builder to service, which has no runtime check."""
    import inspect

    from services import ai_service
    from services.prompts import adapt_plan_user, generate_plan_user

    for fn in (
        generate_plan_user,
        adapt_plan_user,
        ai_service.generate_training_plan,
        ai_service.adapt_training_plan,
    ):
        assert field in inspect.signature(fn).parameters
