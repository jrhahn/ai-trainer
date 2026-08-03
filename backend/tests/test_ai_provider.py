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


# ---------------------------------------------------------------------------
# Thinking config — off, but not every model accepts an explicit zero (#511)
# ---------------------------------------------------------------------------


def test_zero_thinking_budget_sent_when_the_model_accepts_it():
    from google.genai import types

    from services.llm import GeminiProvider

    provider = GeminiProvider(model="gemini-3.5-flash", api_key="fake")
    config = provider._build_config("sys", False, types)

    assert config.thinking_config.thinking_budget == 0


def test_thinking_config_omitted_for_models_that_reject_a_zero_budget():
    """These 400 on an explicit zero, so they are left at their default.

    A budget of 1 is not a substitute — they treat it as a hint rather than a
    cap and spend thinking tokens anyway.
    """
    from google.genai import types

    from services.llm import GeminiProvider

    for model in ("gemini-3.5-flash-lite", "gemini-3.6-flash"):
        config = GeminiProvider(model=model, api_key="fake")._build_config(
            "sys", False, types
        )
        assert config.thinking_config is None


def test_build_config_still_sets_json_mode():
    from google.genai import types

    from services.llm import GeminiProvider

    provider = GeminiProvider(model="gemini-3.5-flash-lite", api_key="fake")

    assert provider._build_config("s", True, types).response_mime_type == "application/json"
    assert provider._build_config("s", False, types).response_mime_type is None


@pytest.mark.asyncio
async def test_generate_retries_without_thinking_config_on_400(monkeypatch):
    """An unknown model that rejects a zero budget self-heals instead of failing.

    Without this, bumping to such a model would 400 every single call.
    """
    from google.genai import errors as genai_errors, types

    import services.llm as llm

    model = "gemini-9.9-flash-unknown"
    monkeypatch.setattr(
        llm, "_ZERO_THINKING_BUDGET_UNSUPPORTED", set(), raising=True
    )

    seen: list[object] = []

    class _FakeModels:
        async def generate_content(self, *, model, contents, config):
            seen.append(config.thinking_config)
            if config.thinking_config is not None:
                raise genai_errors.ClientError(400, {"error": {"message": "bad"}})
            return object()

    class _FakeAio:
        models = _FakeModels()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakeClient:
        aio = _FakeAio()

        def __init__(self, api_key=None):
            pass

    fake_genai = type("genai", (), {"Client": _FakeClient})

    provider = llm.GeminiProvider(model=model, api_key="fake")
    await provider._generate("hi", "sys", False, fake_genai, genai_errors, types)

    assert len(seen) == 2, "expected one rejected attempt then one retry"
    assert seen[0] is not None and seen[1] is None
    assert model in llm._ZERO_THINKING_BUDGET_UNSUPPORTED


@pytest.mark.asyncio
async def test_generate_does_not_swallow_a_429(monkeypatch):
    """Rate limits still surface as AIRateLimitError, not as a thinking retry."""
    from google.genai import errors as genai_errors, types

    import services.llm as llm

    class _FakeModels:
        async def generate_content(self, *, model, contents, config):
            raise genai_errors.ClientError(429, {"error": {"message": "slow down"}})

    class _FakeAio:
        models = _FakeModels()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakeClient:
        aio = _FakeAio()

        def __init__(self, api_key=None):
            pass

    fake_genai = type("genai", (), {"Client": _FakeClient})
    provider = llm.GeminiProvider(model="gemini-3.5-flash", api_key="fake")

    with pytest.raises(llm.AIRateLimitError):
        await provider._generate("hi", "sys", False, fake_genai, genai_errors, types)


# ---------------------------------------------------------------------------
# Implicit-cache accounting (#514)
# ---------------------------------------------------------------------------


class _FakeUsage:
    def __init__(self, total: int, cached: int | None) -> None:
        self.total_token_count = total
        if cached is not None:
            self.cached_content_token_count = cached


class _FakeResponse:
    def __init__(self, total: int, cached: int | None = None) -> None:
        self.usage_metadata = _FakeUsage(total, cached)


def test_cached_tokens_are_recorded_separately_from_the_total():
    """A cache hit is invisible in the total, so it has to be counted on its own.

    Without this the prompt reorder in #514 cannot be distinguished from a no-op:
    the same prompt reports the same total whether it was billed at the full rate
    or at a tenth of it.
    """
    from services import llm

    token = llm.begin_token_usage_collection()
    try:
        llm._record_gemini_usage(_FakeResponse(total=20_000, cached=6_900))
        usage = llm._token_usage.get()
        assert usage.total == 20_000
        assert usage.cached == 6_900
    finally:
        llm.finish_token_usage_collection(token)


def test_a_cache_miss_leaves_the_cached_counter_at_zero():
    from services import llm

    token = llm.begin_token_usage_collection()
    try:
        llm._record_gemini_usage(_FakeResponse(total=20_000, cached=0))
        llm._record_gemini_usage(_FakeResponse(total=1_000))  # field absent entirely
        usage = llm._token_usage.get()
        assert usage.total == 21_000
        assert usage.cached == 0
    finally:
        llm.finish_token_usage_collection(token)


def test_usage_without_metadata_is_not_an_error():
    """Providers and stubs that report nothing must not break a chat turn."""
    from services import llm

    token = llm.begin_token_usage_collection()
    try:
        llm._record_gemini_usage(object())
        assert llm.finish_token_usage_collection(token) == 0
    except BaseException:
        llm.finish_token_usage_collection(token)
        raise
