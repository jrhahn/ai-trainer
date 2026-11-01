"""The memory update after a coach question stops at the global daily cap like
every other owner-key path (review of 2026-11-01, ai-trainer-ops#338, security
finding 11)."""

from __future__ import annotations

import logging

import pytest

import routers.ai as ai_router
import services.llm as llm
from config import settings
from services import token_accounting
from tests.test_job_byok_budget_122 import _record_owner_key_spend
from tests.test_memory_privacy import _current_memory, _seed_memory_user
from tests.conftest import TestSessionLocal


@pytest.fixture
def counted_update(monkeypatch):
    calls = {"n": 0}

    async def fake_update(current_memory, *args, **kwargs):
        calls["n"] += 1
        return f"{current_memory} + turn"

    monkeypatch.setattr(ai_router.ai_service, "update_coach_memory", fake_update)
    monkeypatch.setattr(llm, "stub_is_active", lambda: False)
    monkeypatch.setattr(settings, "ai_token_budget", 0)
    monkeypatch.setattr(settings, "allow_admin_ai_key_fallback", True)
    monkeypatch.setattr(settings, "gemini_api_key", "owner-global-key")
    return calls


@pytest.mark.asyncio
async def test_the_memory_update_spends_nothing_once_the_shared_cap_is_used_up(
    monkeypatch, counted_update, caplog
):
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 1)
    user_id = await _seed_memory_user("bg-cap@example.com", "OLD")
    async with TestSessionLocal() as db:
        await _record_owner_key_spend(db, user_id, tokens=5)
        await db.commit()

    with caplog.at_level(logging.INFO, logger=ai_router.logger.name):
        await ai_router._update_memory_bg(user_id, "q", "a", "gemini")

    assert counted_update["n"] == 0
    assert await _current_memory(user_id) == "OLD"
    assert "shared AI budget is used up" in caplog.text


@pytest.mark.asyncio
async def test_under_the_cap_the_memory_update_runs(monkeypatch, counted_update):
    monkeypatch.setattr(settings, "ai_global_daily_token_budget", 1_000_000)
    user_id = await _seed_memory_user("bg-cap-ok@example.com", "OLD")

    await ai_router._update_memory_bg(user_id, "q", "a", "gemini")

    assert counted_update["n"] == 1
    assert await _current_memory(user_id) == "OLD + turn"


@pytest.mark.asyncio
async def test_a_failed_budget_check_costs_the_update_not_the_worker(
    monkeypatch, counted_update, caplog
):
    async def broken(*args, **kwargs):
        raise RuntimeError("database gone")

    monkeypatch.setattr(token_accounting, "enforce_token_budget", broken)
    user_id = await _seed_memory_user("bg-cap-broken@example.com", "OLD")

    with caplog.at_level(logging.WARNING, logger=ai_router.logger.name):
        await ai_router._update_memory_bg(user_id, "q", "a", "gemini")

    assert counted_update["n"] == 0
    assert "Coach memory background update failed" in caplog.text
