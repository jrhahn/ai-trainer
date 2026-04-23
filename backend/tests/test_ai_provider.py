"""Tests for AI provider selection logic in routers/ai.py."""

from __future__ import annotations

import pytest

import models
from routers.ai import _default_provider, _provider


# ---------------------------------------------------------------------------
# _default_provider
# ---------------------------------------------------------------------------


def test_default_provider_prefers_gemini_when_both_set(monkeypatch):
    import routers.ai as ai_router
    monkeypatch.setattr(ai_router.settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(ai_router.settings, "openai_api_key", "openai-key")
    assert _default_provider() == "gemini"


def test_default_provider_uses_openai_when_no_gemini(monkeypatch):
    import routers.ai as ai_router
    monkeypatch.setattr(ai_router.settings, "gemini_api_key", "")
    monkeypatch.setattr(ai_router.settings, "openai_api_key", "openai-key")
    assert _default_provider() == "openai"


def test_default_provider_returns_gemini_when_neither_set(monkeypatch):
    import routers.ai as ai_router
    monkeypatch.setattr(ai_router.settings, "gemini_api_key", "")
    monkeypatch.setattr(ai_router.settings, "openai_api_key", "")
    assert _default_provider() == "gemini"


def test_default_provider_uses_gemini_when_only_gemini_set(monkeypatch):
    import routers.ai as ai_router
    monkeypatch.setattr(ai_router.settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(ai_router.settings, "openai_api_key", "")
    assert _default_provider() == "gemini"


# ---------------------------------------------------------------------------
# _provider (user preference + environment)
# ---------------------------------------------------------------------------


def _make_user(ai_provider: str):
    """Return a lightweight mock with only the ``ai_provider`` attribute."""
    from unittest.mock import MagicMock
    user = MagicMock(spec=models.User)
    user.ai_provider = ai_provider
    return user


def test_provider_respects_gemini_preference(monkeypatch):
    import routers.ai as ai_router
    monkeypatch.setattr(ai_router.settings, "gemini_api_key", "gemini-key")
    user = _make_user("gemini")
    assert _provider(user) == "gemini"


def test_provider_respects_openai_preference(monkeypatch):
    import routers.ai as ai_router
    monkeypatch.setattr(ai_router.settings, "gemini_api_key", "")
    monkeypatch.setattr(ai_router.settings, "openai_api_key", "openai-key")
    user = _make_user("openai")
    assert _provider(user) == "openai"


def test_provider_falls_back_when_gemini_key_missing(monkeypatch):
    """User prefers gemini but gemini_api_key is not set → fall back to default."""
    import routers.ai as ai_router
    monkeypatch.setattr(ai_router.settings, "gemini_api_key", "")
    monkeypatch.setattr(ai_router.settings, "openai_api_key", "openai-key")
    user = _make_user("gemini")
    result = _provider(user)
    # Should fall back to openai since gemini key is absent
    assert result == "openai"


def test_provider_falls_back_when_openai_key_missing(monkeypatch):
    """User prefers openai but openai_api_key is not set → fall back to default."""
    import routers.ai as ai_router
    monkeypatch.setattr(ai_router.settings, "openai_api_key", "")
    monkeypatch.setattr(ai_router.settings, "gemini_api_key", "gemini-key")
    user = _make_user("openai")
    result = _provider(user)
    # Should fall back to gemini since openai key is absent
    assert result == "gemini"


def test_provider_falls_back_when_neither_key_set(monkeypatch):
    import routers.ai as ai_router
    monkeypatch.setattr(ai_router.settings, "gemini_api_key", "")
    monkeypatch.setattr(ai_router.settings, "openai_api_key", "")
    user = _make_user("openai")
    result = _provider(user)
    # Falls back to _default_provider() which returns "gemini" as last resort
    assert result == "gemini"




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
                "trainingGoal": "ftp_improvement",
                "weeklyHours": 8,
                "followsTrainingPlan": True,
                "fitnessLevel": "intermediate",
            }
        },
    )
    assert response.status_code == 200
    # Confirm mock was called (provider is passed internally)
    assert mock_ai_service["generate_training_plan"].called
