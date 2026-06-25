"""Tests for AI provider selection logic (resolve_user_provider in services/llm.py)."""

from __future__ import annotations

import pytest

import models
from services.llm import resolve_user_provider


def _make_user(ai_provider: str):
    """Return a lightweight mock with only the ``ai_provider`` attribute."""
    from unittest.mock import MagicMock
    user = MagicMock(spec=models.User)
    user.ai_provider = ai_provider
    return user


# ---------------------------------------------------------------------------
# resolve_user_provider — user preference honoured when key is present
# ---------------------------------------------------------------------------


def test_provider_respects_gemini_preference(monkeypatch):
    import services.llm as llm
    monkeypatch.setattr(llm.settings, "gemini_api_key", "gemini-key")
    user = _make_user("gemini")
    assert resolve_user_provider(user) == "gemini"


def test_provider_respects_openai_preference(monkeypatch):
    import services.llm as llm
    monkeypatch.setattr(llm.settings, "gemini_api_key", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "openai-key")
    user = _make_user("openai")
    assert resolve_user_provider(user) == "openai"


def test_provider_falls_back_when_gemini_key_missing(monkeypatch):
    """User prefers gemini but gemini_api_key is not set → fall back to openai."""
    import services.llm as llm
    monkeypatch.setattr(llm.settings, "gemini_api_key", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "openai-key")
    user = _make_user("gemini")
    assert resolve_user_provider(user) == "openai"


def test_provider_falls_back_when_openai_key_missing(monkeypatch):
    """User prefers openai but openai_api_key is not set → fall back to gemini."""
    import services.llm as llm
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    monkeypatch.setattr(llm.settings, "gemini_api_key", "gemini-key")
    user = _make_user("openai")
    assert resolve_user_provider(user) == "gemini"


def test_provider_falls_back_to_gemini_when_neither_key_set(monkeypatch):
    """No keys configured → returns 'gemini' as last-resort default."""
    import services.llm as llm
    monkeypatch.setattr(llm.settings, "gemini_api_key", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    user = _make_user("openai")
    assert resolve_user_provider(user) == "gemini"


def test_provider_prefers_gemini_when_both_keys_set_and_no_preference(monkeypatch):
    """No stored preference + both keys → gemini wins (key order in function)."""
    import services.llm as llm
    monkeypatch.setattr(llm.settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(llm.settings, "openai_api_key", "openai-key")
    user = _make_user("")
    assert resolve_user_provider(user) == "gemini"


# ---------------------------------------------------------------------------
# Integration: generate-plan route passes provider through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_plan_uses_gemini_when_user_prefers_it(client, auth_headers, mock_ai_service, monkeypatch):
    """When GEMINI_API_KEY is set and user has no explicit preference, gemini is used."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

    response = await client.post(
        "/api/v1/ai/generate-plan",
        headers=auth_headers,
        json={
            "profile": {
                "name": "Test",
                "email": "test@example.com",
                "bikeType": "road",
                "trainingGoal": "general_fitness",
                "weeklyHours": 8,
                "followsTrainingPlan": True,
                "fitnessLevel": "intermediate",
            }
        },
    )
    assert response.status_code == 200
    assert mock_ai_service["generate_training_plan"].called
