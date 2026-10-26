"""A coach turn loads only the newest chat messages, and the intervals.icu list
call has room to answer and fails as a provider error (ai-trainer-ops#284).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

import crud
import models
from services import intervals_service
from tests.conftest import TestSessionLocal
from tests.test_activity_sync import _create_user


async def _chat_of(user_id: str, count: int) -> None:
    start = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
    async with TestSessionLocal() as db:
        for i in range(count):
            db.add(
                models.ChatMessage(
                    user_id=user_id,
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"message {i}",
                    timestamp=start + timedelta(minutes=i),
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_the_newest_messages_come_back_oldest_first():
    user_id = await _create_user(email="chat-limit-284@example.com")
    await _chat_of(user_id, 12)

    async with TestSessionLocal() as db:
        newest = await crud.get_chat_messages(db, user_id, limit=3)
        everything = await crud.get_chat_messages(db, user_id)

    assert [m.content for m in newest] == ["message 9", "message 10", "message 11"]
    assert len(everything) == 12


@pytest.mark.asyncio
async def test_a_coach_turn_loads_a_bounded_history(client, auth_headers, monkeypatch):
    import routers.ai as ai_router

    limits: list[int | None] = []
    real = crud.get_chat_messages

    async def spy(db, user_id, *, limit=None):
        limits.append(limit)
        return await real(db, user_id, limit=limit)

    monkeypatch.setattr(crud, "get_chat_messages", spy)
    await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Wie war die Woche?"},
    )

    assert limits == [ai_router.CHAT_CONTEXT_MESSAGES]


@pytest.mark.asyncio
async def test_the_chat_timeline_still_shows_the_whole_history(
    client, auth_headers, monkeypatch
):
    limits: list[int | None] = []
    real = crud.get_chat_messages

    async def spy(db, user_id, *, limit=None):
        limits.append(limit)
        return await real(db, user_id, limit=limit)

    monkeypatch.setattr(crud, "get_chat_messages", spy)
    response = await client.get("/api/v1/users/me/chat", headers=auth_headers)
    assert response.status_code == 200, response.text

    assert limits == [None]


class _SlowIntervals:
    """httpx.AsyncClient stand-in whose list request never answers."""

    timeouts: list[httpx.Timeout | None] = []

    def __init__(self, *, timeout=None, **_kwargs):
        _SlowIntervals.timeouts.append(timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, *_args, **_kwargs):
        raise httpx.ReadTimeout("intervals.icu took too long")


@pytest.mark.asyncio
async def test_a_list_that_does_not_answer_is_a_provider_error(monkeypatch):
    _SlowIntervals.timeouts = []
    monkeypatch.setattr(intervals_service.httpx, "AsyncClient", _SlowIntervals)

    with pytest.raises(intervals_service.IntervalsAPIError, match="did not answer"):
        await intervals_service.fetch_recent_activities(
            "key", "i1", oldest=date(2024, 10, 1), newest=date(2026, 10, 1)
        )

    assert _SlowIntervals.timeouts == [intervals_service.LISTING_TIMEOUT]
    assert intervals_service.LISTING_TIMEOUT.read == 30.0
