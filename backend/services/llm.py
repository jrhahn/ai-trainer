"""LLM provider abstraction.

Adds a thin Protocol layer so the rest of the codebase never branches on
provider strings beyond this module.  New providers (e.g. Anthropic) only
require implementing the ``LLMProvider`` protocol here.

``AIRateLimitError`` is defined here and re-exported from
``services.ai_service`` for backward compatibility.

Model selection
---------------
Each call can specify a *task* so that cheap classification requests use a
fast model while conversational coach chat uses a stronger one.  The four
task types are exposed as module-level constants:

    TASK_CLASSIFY  – question classification / routing
    TASK_PLAN      – structured JSON plan generation / adaptation
    TASK_COACH     – conversational ask-trainer chat
    TASK_FEEDBACK  – post-ride / workout feedback

Defaults for each task × provider combination are stored in
``config.Settings`` and can be overridden via environment variables without
code edits (e.g. ``OPENAI_COACH_MODEL=gpt-4o``).
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import models
from config import settings

logger = logging.getLogger(__name__)


@dataclass
class TokenUsage:
    total: int = 0


_token_usage: ContextVar[TokenUsage | None] = ContextVar("token_usage", default=None)


def begin_token_usage_collection() -> Token[TokenUsage | None]:
    """Start collecting provider-reported token usage for the current request."""
    return _token_usage.set(TokenUsage())


def finish_token_usage_collection(token: Token[TokenUsage | None]) -> int:
    """Return collected token usage and restore the previous collection context."""
    usage = _token_usage.get()
    total = usage.total if usage is not None else 0
    _token_usage.reset(token)
    return total


def _record_token_usage(tokens: int | None) -> None:
    if not tokens or tokens <= 0:
        return
    usage = _token_usage.get()
    if usage is not None:
        usage.total += int(tokens)


def _attribute_int(obj: object, *names: str) -> int | None:
    for name in names:
        value = getattr(obj, name, None)
        if isinstance(value, int):
            return value
    return None


def _openai_total_tokens(response: object) -> int | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return _attribute_int(usage, "total_tokens")


def _gemini_total_tokens(response: object) -> int | None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    return _attribute_int(usage, "total_token_count")


# ---------------------------------------------------------------------------
# Task type constants
# ---------------------------------------------------------------------------

TASK_CLASSIFY = "classify"
TASK_PLAN = "plan"
TASK_COACH = "coach"
TASK_FEEDBACK = "feedback"

# Fallback model names used when settings resolution is unavailable
OPENAI_MODEL = "gpt-4o-mini"
GEMINI_MODEL = "gemini-2.5-flash"


def _resolve_model(provider_name: str, task: str) -> str:
    """Return the configured model name for *provider_name* and *task*."""
    _openai_map = {
        TASK_CLASSIFY: settings.openai_classify_model,
        TASK_PLAN: settings.openai_plan_model,
        TASK_COACH: settings.openai_coach_model,
        TASK_FEEDBACK: settings.openai_feedback_model,
    }
    _gemini_map = {
        TASK_CLASSIFY: settings.gemini_classify_model,
        TASK_PLAN: settings.gemini_plan_model,
        TASK_COACH: settings.gemini_coach_model,
        TASK_FEEDBACK: settings.gemini_feedback_model,
    }
    if provider_name == "openai":
        return _openai_map.get(task, OPENAI_MODEL)
    if provider_name == "gemini":
        return _gemini_map.get(task, GEMINI_MODEL)
    return OPENAI_MODEL


class AIRateLimitError(Exception):
    """Raised when the AI provider returns a rate-limit (429) response."""


class AIKeyNotConfiguredError(Exception):
    """Raised when no AI provider key is available for the current user."""


# ---------------------------------------------------------------------------
# Per-request user API key context (BYOK)
# ---------------------------------------------------------------------------

_BYOK_INACTIVE: Any = object()

_user_ai_keys: ContextVar[dict[str, str | None] | Any] = ContextVar(
    "user_ai_keys", default=_BYOK_INACTIVE
)


def set_user_ai_keys(keys: dict[str, str | None]) -> Token[dict[str, str | None] | Any]:
    """Activate BYOK context with *keys* mapping provider name → API key."""
    return _user_ai_keys.set(keys)


def reset_user_ai_keys(token: Token[dict[str, str | None] | Any]) -> None:
    _user_ai_keys.reset(token)


@runtime_checkable
class LLMProvider(Protocol):
    async def chat(self, system: str, user: str, json_mode: bool = False) -> str: ...
    async def chat_history(
        self, system: str, messages: list[dict[str, str]], json_mode: bool = False
    ) -> str: ...


class OpenAIProvider:
    _model: str = OPENAI_MODEL  # class-level default; overridden by __init__

    def __init__(self, model: str = OPENAI_MODEL, api_key: str | None = None) -> None:
        from openai import AsyncOpenAI

        self._model = model
        self._client = AsyncOpenAI(api_key=api_key or settings.openai_api_key)

    async def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **kwargs,
        )
        _record_token_usage(_openai_total_tokens(response))
        return response.choices[0].message.content or ""

    async def chat_history(
        self, system: str, messages: list[dict[str, str]], json_mode: bool = False
    ) -> str:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, *messages],
            **kwargs,
        )
        _record_token_usage(_openai_total_tokens(response))
        return response.choices[0].message.content or ""


class GeminiProvider:
    _model: str = GEMINI_MODEL  # class-level default; overridden by __init__

    def __init__(self, model: str = GEMINI_MODEL, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key or settings.gemini_api_key

    @staticmethod
    def _build_config(
        system: str, json_mode: bool, types: object
    ) -> object:
        # Disable thinking/reasoning (thinking_budget=0) to prevent the hidden
        # chain-of-thought tokens that cause cost explosions with Gemini 2.5 Flash.
        return types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if json_mode else None,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

    async def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        from google import genai
        from google.genai import errors as genai_errors, types

        config = self._build_config(system, json_mode, types)
        try:
            client = genai.Client(api_key=self._api_key)
            async with client.aio as aio_client:
                response = await aio_client.models.generate_content(
                    model=self._model, contents=user, config=config
                )
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise AIRateLimitError(str(exc)) from exc
            raise
        _record_token_usage(_gemini_total_tokens(response))
        return response.text or ""

    async def chat_history(
        self, system: str, messages: list[dict[str, str]], json_mode: bool = False
    ) -> str:
        from google import genai
        from google.genai import errors as genai_errors, types

        config = self._build_config(system, json_mode, types)
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in messages
        ]
        try:
            client = genai.Client(api_key=self._api_key)
            async with client.aio as aio_client:
                response = await aio_client.models.generate_content(
                    model=self._model, contents=contents, config=config
                )
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise AIRateLimitError(str(exc)) from exc
            raise
        _record_token_usage(_gemini_total_tokens(response))
        return response.text or ""


def get_provider(name: str, task: str = TASK_COACH) -> LLMProvider:
    """Return an ``LLMProvider`` for *name* configured for *task*.

    When a per-request BYOK context is active (set via :func:`set_user_ai_keys`)
    the user's own key is used.  If the user has no key and
    ``settings.allow_admin_ai_key_fallback`` is False an
    :class:`AIKeyNotConfiguredError` is raised.  Outside a BYOK context the
    global settings keys are used unchanged (existing behaviour).
    """
    ctx = _user_ai_keys.get()
    if ctx is not _BYOK_INACTIVE:
        user_key = ctx.get(name) if isinstance(ctx, dict) else None
        if not user_key:
            if not settings.allow_admin_ai_key_fallback:
                raise AIKeyNotConfiguredError(
                    f"No {name} API key configured. "
                    "Please add your key in Settings → AI Provider."
                )
            return _get_provider_global(name, task)
        if name == "openai":
            return OpenAIProvider(model=_resolve_model("openai", task), api_key=user_key)
        if name == "gemini":
            return GeminiProvider(model=_resolve_model("gemini", task), api_key=user_key)
        raise AIKeyNotConfiguredError(f"Unknown AI provider: {name}")
    return _get_provider_global(name, task)


def _get_provider_global(name: str, task: str) -> LLMProvider:
    """Return a provider using the global (backend-owner) settings keys."""
    if name == "gemini" and settings.gemini_api_key:
        return GeminiProvider(model=_resolve_model("gemini", task))
    if name == "openai" and settings.openai_api_key:
        return OpenAIProvider(model=_resolve_model("openai", task))
    if settings.gemini_api_key:
        return GeminiProvider(model=_resolve_model("gemini", task))
    if settings.openai_api_key:
        return OpenAIProvider(model=_resolve_model("openai", task))
    logger.warning("No AI provider API key configured; defaulting to GeminiProvider")
    return GeminiProvider(model=_resolve_model("gemini", task))


def resolve_user_provider(user: models.User) -> str:
    """Return the AI provider name to use for *user*.

    Respects the user's stored preference when the matching API key is
    configured; falls back to the best globally-available provider.
    """
    stored = user.ai_provider
    if stored == "gemini" and settings.gemini_api_key:
        return "gemini"
    if stored == "openai" and settings.openai_api_key:
        return "openai"
    if settings.gemini_api_key:
        return "gemini"
    if settings.openai_api_key:
        return "openai"
    return "gemini"
