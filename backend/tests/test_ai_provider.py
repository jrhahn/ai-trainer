"""Tests for AI provider selection logic (resolve_user_provider in services/llm.py)."""

from __future__ import annotations

import pytest

import models
from services.llm import resolve_user_provider


def _make_user(
    ai_provider: str,
    *,
    user_openai_api_key: str | None = None,
    user_gemini_api_key: str | None = None,
):
    """Return a lightweight mock user for provider resolution.

    ``spec=models.User`` would leave the BYOK key attributes as truthy mocks, so
    they are set explicitly (defaulting to ``None``) to mirror a real user row.
    """
    from unittest.mock import MagicMock
    user = MagicMock(spec=models.User)
    user.ai_provider = ai_provider
    user.user_openai_api_key = user_openai_api_key
    user.user_gemini_api_key = user_gemini_api_key
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


def test_provider_returns_stated_preference_when_nothing_usable(monkeypatch):
    """No keys usable → return the user's stated preference so get_provider can
    raise the correct provider-specific 'key not configured' error (#448)."""
    import services.llm as llm
    monkeypatch.setattr(llm.settings, "gemini_api_key", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    user = _make_user("openai")
    assert resolve_user_provider(user) == "openai"


def test_provider_defaults_to_gemini_with_no_preference_and_no_keys(monkeypatch):
    """No stored preference and nothing usable → gemini as last-resort default."""
    import services.llm as llm
    monkeypatch.setattr(llm.settings, "gemini_api_key", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    user = _make_user("")
    assert resolve_user_provider(user) == "gemini"


def test_provider_honours_byok_key_when_no_global_key(monkeypatch):
    """BYOK-only mode: a user's own key makes that provider usable even when no
    global key is configured and admin fallback is disabled (#448)."""
    import services.llm as llm
    monkeypatch.setattr(llm.settings, "gemini_api_key", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    monkeypatch.setattr(llm.settings, "allow_admin_ai_key_fallback", False)
    user = _make_user("openai", user_openai_api_key="sk-user-owned")
    assert resolve_user_provider(user) == "openai"


def test_provider_skips_global_fallback_when_admin_fallback_disabled(monkeypatch):
    """With admin fallback off and no user key, a global key does not make the
    provider usable; resolution falls through to the stated preference (#448)."""
    import services.llm as llm
    monkeypatch.setattr(llm.settings, "gemini_api_key", "global-gemini")
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    monkeypatch.setattr(llm.settings, "allow_admin_ai_key_fallback", False)
    user = _make_user("openai")
    assert resolve_user_provider(user) == "openai"


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
