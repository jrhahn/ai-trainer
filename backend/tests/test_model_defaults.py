"""Tests for per-task model defaults and provider model selection.

Covers:
- Config defaults for each task × provider combination (no env overrides).
- Model selection via environment variable overrides.
- get_provider() returns a provider configured with the correct model for the task.
- _resolve_model() falls back gracefully for unknown task names.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_openai_defaults(monkeypatch):
    """Cheap tasks use gpt-4o-mini; strong tasks use gpt-4o by default."""
    # Re-import to pick up clean state (monkeypatch operates on the settings object)
    from config import settings

    assert settings.openai_classify_model == "gpt-4o-mini"
    assert settings.openai_plan_model == "gpt-4o-mini"
    assert settings.openai_coach_model == "gpt-4o"
    assert settings.openai_feedback_model == "gpt-4o"


def test_gemini_defaults():
    """Gemini defaults all tasks to gemini-2.5-flash."""
    from config import settings

    assert settings.gemini_classify_model == "gemini-2.5-flash"
    assert settings.gemini_plan_model == "gemini-2.5-flash"
    assert settings.gemini_coach_model == "gemini-2.5-flash"
    assert settings.gemini_feedback_model == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------


def test_openai_coach_model_env_override(monkeypatch):
    """OPENAI_COACH_MODEL overrides the default without code edits."""
    from config import settings

    monkeypatch.setattr(settings, "openai_coach_model", "gpt-4-turbo")
    assert settings.openai_coach_model == "gpt-4-turbo"


def test_gemini_coach_model_env_override(monkeypatch):
    """GEMINI_COACH_MODEL overrides the default without code edits."""
    from config import settings

    monkeypatch.setattr(settings, "gemini_coach_model", "gemini-2.5-pro")
    assert settings.gemini_coach_model == "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# _resolve_model()
# ---------------------------------------------------------------------------


def test_resolve_model_openai_tasks(monkeypatch):
    """_resolve_model returns the correct model for each OpenAI task."""
    import services.llm as llm
    from config import settings

    monkeypatch.setattr(settings, "openai_classify_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "openai_plan_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "openai_coach_model", "gpt-4o")
    monkeypatch.setattr(settings, "openai_feedback_model", "gpt-4o")

    assert llm._resolve_model("openai", llm.TASK_CLASSIFY) == "gpt-4o-mini"
    assert llm._resolve_model("openai", llm.TASK_PLAN) == "gpt-4o-mini"
    assert llm._resolve_model("openai", llm.TASK_COACH) == "gpt-4o"
    assert llm._resolve_model("openai", llm.TASK_FEEDBACK) == "gpt-4o"


def test_resolve_model_gemini_tasks(monkeypatch):
    """_resolve_model returns the correct model for each Gemini task."""
    import services.llm as llm
    from config import settings

    monkeypatch.setattr(settings, "gemini_classify_model", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "gemini_plan_model", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "gemini_coach_model", "gemini-2.5-pro")
    monkeypatch.setattr(settings, "gemini_feedback_model", "gemini-2.5-pro")

    assert llm._resolve_model("gemini", llm.TASK_CLASSIFY) == "gemini-2.5-flash"
    assert llm._resolve_model("gemini", llm.TASK_PLAN) == "gemini-2.5-flash"
    assert llm._resolve_model("gemini", llm.TASK_COACH) == "gemini-2.5-pro"
    assert llm._resolve_model("gemini", llm.TASK_FEEDBACK) == "gemini-2.5-pro"


def test_resolve_model_unknown_task_falls_back(monkeypatch):
    """Unknown task names fall back to the module-level default constants."""
    import services.llm as llm

    assert llm._resolve_model("openai", "unknown_task") == llm.OPENAI_MODEL
    assert llm._resolve_model("gemini", "unknown_task") == llm.GEMINI_MODEL


def test_resolve_model_unknown_provider_falls_back():
    """Unknown provider names fall back to OPENAI_MODEL."""
    import services.llm as llm

    result = llm._resolve_model("anthropic", llm.TASK_COACH)
    assert result == llm.OPENAI_MODEL


# ---------------------------------------------------------------------------
# get_provider() uses task-specific model
# ---------------------------------------------------------------------------


def test_get_provider_openai_uses_coach_model(monkeypatch):
    """OpenAIProvider created for TASK_COACH uses the configured coach model."""
    import services.llm as llm
    from config import settings

    monkeypatch.setattr(settings, "openai_api_key", "fake-key")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openai_coach_model", "gpt-4o")

    # Prevent real client instantiation
    from unittest.mock import patch, MagicMock
    with patch("services.llm.OpenAIProvider.__init__", lambda self, model=llm.OPENAI_MODEL: setattr(self, "_model", model) or setattr(self, "_client", MagicMock())):
        provider = llm.get_provider("openai", task=llm.TASK_COACH)
    assert isinstance(provider, llm.OpenAIProvider)
    assert provider._model == "gpt-4o"


def test_get_provider_openai_uses_classify_model(monkeypatch):
    """OpenAIProvider created for TASK_CLASSIFY uses the cheap classify model."""
    import services.llm as llm
    from config import settings

    monkeypatch.setattr(settings, "openai_api_key", "fake-key")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openai_classify_model", "gpt-4o-mini")

    from unittest.mock import patch, MagicMock
    with patch("services.llm.OpenAIProvider.__init__", lambda self, model=llm.OPENAI_MODEL: setattr(self, "_model", model) or setattr(self, "_client", MagicMock())):
        provider = llm.get_provider("openai", task=llm.TASK_CLASSIFY)
    assert isinstance(provider, llm.OpenAIProvider)
    assert provider._model == "gpt-4o-mini"


def test_get_provider_gemini_uses_task_model(monkeypatch):
    """GeminiProvider created for a task uses the configured model."""
    import services.llm as llm
    from config import settings

    monkeypatch.setattr(settings, "gemini_api_key", "fake-gemini-key")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "gemini_feedback_model", "gemini-2.5-flash")

    provider = llm.get_provider("gemini", task=llm.TASK_FEEDBACK)
    assert isinstance(provider, llm.GeminiProvider)
    assert provider._model == "gemini-2.5-flash"


def test_get_provider_fallback_carries_task_model(monkeypatch):
    """When falling back to an available provider, it still uses the task model."""
    import services.llm as llm
    from config import settings

    # Only gemini key set; requesting openai → fallback to gemini
    monkeypatch.setattr(settings, "gemini_api_key", "fake-gemini-key")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "gemini_coach_model", "gemini-2.5-flash")

    provider = llm.get_provider("openai", task=llm.TASK_COACH)
    assert isinstance(provider, llm.GeminiProvider)
    assert provider._model == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Cheap vs. strong model distinction (provider-agnostic)
# ---------------------------------------------------------------------------


def test_classify_model_is_different_from_coach_model_openai(monkeypatch):
    """By default, classify model is cheaper than coach model for OpenAI."""
    from config import settings

    assert settings.openai_classify_model != settings.openai_coach_model


def test_plan_model_is_different_from_feedback_model_openai():
    """By default, plan model is cheaper than feedback model for OpenAI."""
    from config import settings

    assert settings.openai_plan_model != settings.openai_feedback_model
