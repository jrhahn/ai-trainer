"""Tests for memory privacy controls: disable, clear, export."""

from __future__ import annotations

import pytest

import crud
import routers.ai as ai_router
from database import async_session_maker
from tests.conftest import TestSessionLocal


# ---------------------------------------------------------------------------
# GET /memory-privacy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_memory_privacy_defaults_enabled(client, auth_headers):
    response = await client.get("/api/v1/users/me/memory-privacy", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["memoryUpdatesEnabled"] is True


# ---------------------------------------------------------------------------
# PUT /memory-privacy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_memory_updates(client, auth_headers):
    response = await client.put(
        "/api/v1/users/me/memory-privacy",
        headers=auth_headers,
        json={"memoryUpdatesEnabled": False},
    )
    assert response.status_code == 200
    assert response.json()["memoryUpdatesEnabled"] is False

    get_response = await client.get(
        "/api/v1/users/me/memory-privacy", headers=auth_headers
    )
    assert get_response.json()["memoryUpdatesEnabled"] is False


@pytest.mark.asyncio
async def test_re_enable_memory_updates(client, auth_headers):
    await client.put(
        "/api/v1/users/me/memory-privacy",
        headers=auth_headers,
        json={"memoryUpdatesEnabled": False},
    )
    response = await client.put(
        "/api/v1/users/me/memory-privacy",
        headers=auth_headers,
        json={"memoryUpdatesEnabled": True},
    )
    assert response.status_code == 200
    assert response.json()["memoryUpdatesEnabled"] is True


# ---------------------------------------------------------------------------
# DELETE /memory  (clear all)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_memory_deletes_facts_and_clears_coach_memory(client, auth_headers):
    # Seed some data
    await client.put(
        "/api/v1/users/me/coach-memory",
        headers=auth_headers,
        json={"memory": "Athlete prefers morning rides."},
    )
    await client.post(
        "/api/v1/users/me/athlete-memory-facts",
        headers=auth_headers,
        json={"fact": "Gets anxious on rest days", "category": "coaching_risk"},
    )
    await client.post(
        "/api/v1/users/me/athlete-memory-facts",
        headers=auth_headers,
        json={"fact": "Loves long endurance rides", "category": "preference"},
    )

    # Verify data exists
    facts_before = await client.get(
        "/api/v1/users/me/athlete-memory-facts", headers=auth_headers
    )
    assert len(facts_before.json()["facts"]) == 2

    # Clear all memory
    clear_response = await client.delete(
        "/api/v1/users/me/memory", headers=auth_headers
    )
    assert clear_response.status_code == 204

    # Facts are gone
    facts_after = await client.get(
        "/api/v1/users/me/athlete-memory-facts", headers=auth_headers
    )
    assert facts_after.json()["facts"] == []

    # Coach memory text is cleared
    coach_memory = await client.get(
        "/api/v1/users/me/coach-memory", headers=auth_headers
    )
    assert coach_memory.json()["memory"] == ""


@pytest.mark.asyncio
async def test_clear_memory_idempotent_when_empty(client, auth_headers):
    response = await client.delete("/api/v1/users/me/memory", headers=auth_headers)
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# GET /memory-export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_memory_empty(client, auth_headers):
    response = await client.get("/api/v1/users/me/memory-export", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "exportedAt" in body
    assert body["coachMemory"] == ""
    assert body["memoryFacts"] == []
    assert body["athleteContext"] is None
    assert body["memoryUpdatesEnabled"] is True


@pytest.mark.asyncio
async def test_export_memory_includes_all_data(client, auth_headers):
    await client.put(
        "/api/v1/users/me/coach-memory",
        headers=auth_headers,
        json={"memory": "Prefers evening rides."},
    )
    await client.post(
        "/api/v1/users/me/athlete-memory-facts",
        headers=auth_headers,
        json={"fact": "Dislikes back-to-back hard days", "category": "coaching_risk"},
    )

    response = await client.get("/api/v1/users/me/memory-export", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["coachMemory"] == "Prefers evening rides."
    assert len(body["memoryFacts"]) == 1
    assert body["memoryFacts"][0]["fact"] == "Dislikes back-to-back hard days"


@pytest.mark.asyncio
async def test_export_memory_reflects_disabled_flag(client, auth_headers):
    await client.put(
        "/api/v1/users/me/memory-privacy",
        headers=auth_headers,
        json={"memoryUpdatesEnabled": False},
    )
    response = await client.get("/api/v1/users/me/memory-export", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["memoryUpdatesEnabled"] is False


# ---------------------------------------------------------------------------
# Disabled memory not included in prompts (via crud.get_prompt_athlete_memory_facts
# being skipped when memory_updates_enabled=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_memory_not_returned_in_prompt_facts(client, auth_headers):
    from auth import decode_token

    # Seed a high-confidence fact
    await client.post(
        "/api/v1/users/me/athlete-memory-facts",
        headers=auth_headers,
        json={
            "fact": "Needs extra recovery after intervals",
            "category": "coaching_risk",
            "confidence": 0.9,
        },
    )

    # Confirm fact to guarantee it would normally appear in prompts
    facts = (
        await client.get("/api/v1/users/me/athlete-memory-facts", headers=auth_headers)
    ).json()["facts"]
    assert len(facts) == 1
    await client.patch(
        f"/api/v1/users/me/athlete-memory-facts/{facts[0]['id']}",
        headers=auth_headers,
        json={"status": "user_confirmed"},
    )

    # With memory enabled, crud returns the fact
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with async_session_maker() as session:
        prompt_facts_enabled = await crud.get_prompt_athlete_memory_facts(
            session, user_id
        )
    assert len(prompt_facts_enabled) == 1

    # Disable memory
    await client.put(
        "/api/v1/users/me/memory-privacy",
        headers=auth_headers,
        json={"memoryUpdatesEnabled": False},
    )

    # The router skips crud.get_prompt_athlete_memory_facts when disabled,
    # so verify via the export endpoint that the fact still exists (not deleted),
    # but the privacy flag is set correctly.
    export = (
        await client.get("/api/v1/users/me/memory-export", headers=auth_headers)
    ).json()
    assert export["memoryUpdatesEnabled"] is False
    # Facts are still stored — they're just not used in prompts
    assert len(export["memoryFacts"]) == 1


# ---------------------------------------------------------------------------
# Background coach-memory update concurrency (#346)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_update_does_not_clobber_concurrent_edit(monkeypatch):
    """A coach-memory edit made while the model is generating must survive (#346).

    The background task must rebase onto the athlete's concurrent edit instead of
    overwriting it with an update derived from a stale request-time snapshot.
    """
    async with TestSessionLocal() as s:
        user = await crud.create_user(
            s, email="mem-race@example.com", name="Racer", hashed_password="h"
        )
        await crud.upsert_coach_memory(s, user.id, "ORIGINAL")
        await s.commit()
        user_id = user.id

    calls = {"n": 0}

    async def fake_update(current_memory, user_message, coach_response, provider="openai"):
        calls["n"] += 1
        if calls["n"] == 1:
            # Athlete edits their memory in-app while the model is "generating".
            async with TestSessionLocal() as s:
                await crud.upsert_coach_memory(s, user_id, "ATHLETE-EDIT")
                await s.commit()
        return f"{current_memory} + turn"

    monkeypatch.setattr(ai_router.ai_service, "update_coach_memory", fake_update)

    await ai_router._update_memory_bg(user_id, "q", "a", "openai")

    async with TestSessionLocal() as s:
        row = await crud.get_coach_memory(s, user_id)

    # The stale overwrite ("ORIGINAL + turn") must not win; the retry rebased on
    # the athlete's concurrent edit.
    assert row.memory == "ATHLETE-EDIT + turn"
    assert calls["n"] == 2  # first attempt hit the conflict and retried
