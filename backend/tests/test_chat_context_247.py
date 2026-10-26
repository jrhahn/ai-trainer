"""What the coach knows for one chat turn, without the app (ai-trainer-ops#247).

``chat_context.build_coach_context`` is what ask_trainer loaded inline before
it asks the model. These tests call it with a session and a user, no FastAPI.
"""

from __future__ import annotations

import pytest

import crud
from services import chat_context


async def _remembered(db, user) -> None:
    await crud.upsert_coach_memory(db, user.id, "Prefers early rides.")
    await crud.observe_athlete_memory_fact(
        db, user.id, fact="Rides to work on Mondays", trusted=True, confidence=0.9
    )
    await crud.propose_athlete_hypothesis(
        db, user.id, statement="Recovers quickly between climbs", confidence=0.6
    )


async def _context(db, user) -> chat_context.CoachContext:
    # The route's user comes with its assessment loaded (auth's eager options);
    # a fixture's row loads it here, so no lazy load runs outside the session.
    await db.refresh(user, ["rider_assessment"])
    return await chat_context.build_coach_context(
        db, user, "How was my week?", context_workout=None, timezone_name="UTC"
    )


@pytest.mark.asyncio
async def test_what_the_coach_remembers_reaches_the_turn(db, user):
    await _remembered(db, user)

    context = await _context(db, user)

    assert context.coach_memory == "Prefers early rides."
    assert [fact["fact"] for fact in context.athlete_memory_facts] == [
        "Rides to work on Mondays"
    ]
    assert [h["statement"] for h in context.hypotheses] == [
        "Recovers quickly between climbs"
    ]


@pytest.mark.asyncio
async def test_with_memory_off_the_coach_recalls_nothing(db, user):
    await _remembered(db, user)
    user.memory_updates_enabled = False

    context = await _context(db, user)

    assert context.coach_memory == ""
    assert context.athlete_context is None
    assert context.motivation_model is None
    assert context.athlete_model is None
    assert context.athlete_memory_facts == []
    assert context.open_questions == []
    assert context.pending_inquiries == []
    assert context.hypotheses == []
    assert context.performance_model is None
    assert context.rider_identity_context is None
