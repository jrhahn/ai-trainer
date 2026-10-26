"""The route's own steps around the coach turn, without a request (ai-trainer-ops#247).

ask_trainer keeps the classification it starts beside the reads, the science
retrieval it asks for, and the memory task it queues. These tests call the
three helpers directly.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import BackgroundTasks

import routers.ai as ai_router
from services.knowledge_topics import topics_for_limiter


@pytest.mark.asyncio
async def test_a_classification_still_running_is_cancelled_when_the_turn_fails(
    db, user, monkeypatch
):
    started = asyncio.Event()

    async def corpus_is_there(_db):
        return True

    async def classify_forever(question, provider=None):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ai_router, "knowledge_corpus_is_populated", corpus_is_there)
    monkeypatch.setattr(ai_router.ai_service, "classify_question", classify_forever)

    # The pending classification must not outlive the turn (#450).
    with pytest.raises(RuntimeError):
        async with ai_router._classification(db, user, "Why threshold?") as task:
            await started.wait()
            raise RuntimeError("the model call failed")

    assert task.cancelled()


@pytest.mark.asyncio
async def test_retrieval_is_ranked_by_the_limiter_the_coach_believes(db, monkeypatch):
    seen: dict = {}

    async def retrieve(_db, question, *, focus_topics=None):
        seen["focus_topics"] = focus_topics
        return "Threshold work raises sustainable power.", [{"title": "A paper"}]

    monkeypatch.setattr(ai_router, "retrieve_cycling_context", retrieve)
    classified = asyncio.get_running_loop().create_future()
    classified.set_result({"needs_science_rag": True})

    found = await ai_router._science_for(
        db, classified, "How do I get faster?", {"likely_limiter": "threshold"}
    )

    # Ranked for this athlete, not for the question in the abstract (#627).
    assert seen["focus_topics"] == topics_for_limiter("threshold") != []
    assert found == (
        {"needs_science_rag": True},
        "Threshold work raises sustainable power.",
        [{"title": "A paper"}],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_memory_is_updated_in_the_background_only_when_switched_on(user, enabled):
    user.memory_updates_enabled = enabled
    tasks = BackgroundTasks()

    ai_router._remember_in_background(
        tasks, user, "How was my week?", {"response": "A solid week."}
    )

    queued = [(task.func, task.args[1:3]) for task in tasks.tasks]
    expected = (ai_router._update_memory_bg, ("How was my week?", "A solid week."))
    assert queued == ([expected] if enabled else [])
