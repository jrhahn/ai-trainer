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
    def __init__(
        self,
        total: int,
        cached: int | None,
        prompt: int | None = None,
        candidates: int | None = None,
        thoughts: int | None = None,
    ) -> None:
        self.total_token_count = total
        if cached is not None:
            self.cached_content_token_count = cached
        if prompt is not None:
            self.prompt_token_count = prompt
        if candidates is not None:
            self.candidates_token_count = candidates
        if thoughts is not None:
            self.thoughts_token_count = thoughts


class _FakeResponse:
    def __init__(
        self,
        total: int,
        cached: int | None = None,
        prompt: int | None = None,
        candidates: int | None = None,
        thoughts: int | None = None,
    ) -> None:
        self.usage_metadata = _FakeUsage(total, cached, prompt, candidates, thoughts)


def test_cached_tokens_are_recorded_separately_from_the_total():
    """A cache hit is invisible in the total, so it has to be counted on its own.

    Without this the prompt reorder in #514 cannot be distinguished from a no-op:
    the same prompt reports the same total whether it was billed at the full rate
    or at a tenth of it.
    """
    from services import llm

    tokens = llm._gemini_call_tokens(_FakeResponse(total=20_000, cached=6_900))
    assert tokens.total == 20_000
    assert tokens.cached == 6_900


def test_a_cache_miss_leaves_the_cached_counter_at_zero():
    from services import llm

    assert llm._gemini_call_tokens(_FakeResponse(total=20_000, cached=0)).cached == 0
    # The field can be absent entirely rather than zero.
    assert llm._gemini_call_tokens(_FakeResponse(total=1_000)).cached == 0


def test_gemini_input_and_output_are_split_for_pricing():
    """Input and output bill at very different rates, so the split is the point (#516)."""
    from services import llm

    tokens = llm._gemini_call_tokens(
        _FakeResponse(total=12_500, cached=9_000, prompt=12_000, candidates=500)
    )
    assert tokens.input == 12_000
    assert tokens.output == 500
    # cached is a subset of input, not a fourth bucket
    assert tokens.cached == 9_000


def test_thinking_tokens_count_as_output():
    """They bill at the output rate, so a model ignoring thinking_budget=0 must show up."""
    from services import llm

    tokens = llm._gemini_call_tokens(
        _FakeResponse(total=3_000, prompt=2_000, candidates=400, thoughts=600)
    )
    assert tokens.output == 1_000


def test_openai_usage_is_split_including_its_cache_field():
    from services import llm

    class _Details:
        cached_tokens = 800

    class _Usage:
        prompt_tokens = 1_000
        completion_tokens = 250
        total_tokens = 1_250
        prompt_tokens_details = _Details()

    class _Resp:
        usage = _Usage()

    tokens = llm._openai_call_tokens(_Resp())
    assert (tokens.input, tokens.output, tokens.cached, tokens.total) == (
        1_000,
        250,
        800,
        1_250,
    )


def test_usage_without_metadata_is_not_an_error():
    """Providers and stubs that report nothing must not break a chat turn."""
    from services import llm

    tokens = llm._gemini_call_tokens(object())
    assert (tokens.input, tokens.output, tokens.cached, tokens.total) == (0, 0, 0, 0)
    assert llm._openai_call_tokens(object()).total == 0


# ---------------------------------------------------------------------------
# Per-call attribution (#516)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_chat_records_the_task_and_the_model_it_resolved_to(monkeypatch):
    """The provider is the only layer that knows which model the task resolved to."""
    import services.llm as llm
    from services import token_accounting

    monkeypatch.setattr(llm.settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(llm.settings, "gemini_classify_model", "gemini-classify-x")

    class _Resp:
        text = "ok"
        usage_metadata = _FakeUsage(900, 100, prompt=800, candidates=100)

    async def fake_generate(self, contents, system, json_mode, *args):
        return _Resp()

    monkeypatch.setattr(llm.GeminiProvider, "_generate", fake_generate)

    recorded: list[dict] = []
    monkeypatch.setattr(
        token_accounting, "record_call", lambda **kw: recorded.append(kw)
    )

    provider = llm.get_provider("gemini", task=llm.TASK_CLASSIFY)
    assert await provider.chat("system prompt", "is this a training question?") == "ok"

    assert len(recorded) == 1
    assert recorded[0]["task"] == llm.TASK_CLASSIFY
    assert recorded[0]["model"] == "gemini-classify-x"
    assert recorded[0]["input_tokens"] == 800
    assert recorded[0]["output_tokens"] == 100
    assert recorded[0]["ok"] is True
    assert recorded[0]["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_a_failing_call_is_still_recorded(monkeypatch):
    """A model that rejects every request must not be free in the cost logs (#401)."""
    import services.llm as llm
    from services import token_accounting

    monkeypatch.setattr(llm.settings, "gemini_api_key", "gemini-key")

    async def boom(self, contents, system, json_mode, *args):
        raise llm.AIRateLimitError("429")

    monkeypatch.setattr(llm.GeminiProvider, "_generate", boom)

    recorded: list[dict] = []
    monkeypatch.setattr(
        token_accounting, "record_call", lambda **kw: recorded.append(kw)
    )

    provider = llm.get_provider("gemini", task=llm.TASK_COACH)
    with pytest.raises(llm.AIRateLimitError):
        await provider.chat("system", "user")

    assert len(recorded) == 1
    assert recorded[0]["ok"] is False
    assert recorded[0]["error"] == "AIRateLimitError"
