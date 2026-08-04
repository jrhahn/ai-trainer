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
    # Input tokens Gemini served from its implicit cache, billed at 10 % of the
    # normal input rate.  Tracked separately because it is the only way to tell
    # whether the cache-friendly prompt order actually works (#514) — the totals
    # look identical whether the cache hits or not.
    cached: int = 0


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


def _gemini_cached_tokens(response: object) -> int | None:
    """Input tokens Gemini served from its implicit cache, if it reports any."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    return _attribute_int(usage, "cached_content_token_count")


def _record_gemini_usage(response: object) -> None:
    """Record a Gemini call's tokens, including what the implicit cache covered.

    Caching is invisible in the total — a cached prompt reports the same token
    count, only cheaper — so without this the reorder in #514 could not be told
    apart from a no-op.
    """
    _record_token_usage(_gemini_total_tokens(response))
    cached = _gemini_cached_tokens(response)
    if not cached or cached <= 0:
        return
    usage = _token_usage.get()
    if usage is not None:
        usage.cached += int(cached)
    total = _gemini_total_tokens(response) or 0
    logger.info(
        "Gemini implicit cache hit: %d of %d tokens served from cache", cached, total
    )


# ---------------------------------------------------------------------------
# Task type constants
# ---------------------------------------------------------------------------

TASK_CLASSIFY = "classify"
TASK_PLAN = "plan"
TASK_COACH = "coach"
TASK_FEEDBACK = "feedback"

# Fallback model names used when settings resolution is unavailable
OPENAI_MODEL = "gpt-4o-mini"
GEMINI_MODEL = "gemini-3.5-flash-lite"

# Gemini models that reject an explicit ``thinking_budget=0`` with a 400
# INVALID_ARGUMENT. Verified against the live API on 2026-08-02; note this does
# not follow model family or naming — ``gemini-3.1-flash-lite`` accepts a zero
# while the non-lite ``gemini-3.6-flash`` does not — so it cannot be inferred
# from the model string and is discovered at runtime instead (see ``_generate``).
# Seeded with what is known so the common case never pays a failed request.
_ZERO_THINKING_BUDGET_UNSUPPORTED: set[str] = {
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
}


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

    def _build_config(self, system: str, json_mode: bool, types: object) -> object:
        # Thinking tokens bill at the output rate, so they stay off. Most models
        # accept an explicit zero budget; the ones that don't are left at their
        # default, which measured at zero thinking tokens across repeat calls.
        # A budget of 1 is deliberately NOT used as the workaround — those models
        # treat it as a hint rather than a cap and spent 0–1,348 thinking tokens
        # from call to call on an identical prompt.
        kwargs: dict = {
            "system_instruction": system,
            "response_mime_type": "application/json" if json_mode else None,
        }
        if self._model not in _ZERO_THINKING_BUDGET_UNSUPPORTED:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        return types.GenerateContentConfig(**kwargs)

    async def _generate(
        self, contents: object, system: str, json_mode: bool, genai, genai_errors, types
    ) -> object:
        """Call generate_content, learning which models refuse a zero budget.

        A model that rejects ``thinking_budget=0`` fails *every* request rather
        than degrading, which would take the whole app down on a model bump. The
        API reports only ``INVALID_ARGUMENT`` with no field detail, so the 400 is
        retried once without the thinking config and the model is remembered for
        the rest of the process. A 400 raised for any other reason simply fails
        again on the retry and propagates.
        """
        while True:
            config = self._build_config(system, json_mode, types)
            sent_thinking_config = self._model not in _ZERO_THINKING_BUDGET_UNSUPPORTED
            try:
                client = genai.Client(api_key=self._api_key)
                async with client.aio as aio_client:
                    return await aio_client.models.generate_content(
                        model=self._model, contents=contents, config=config
                    )
            except genai_errors.ClientError as exc:
                if exc.code == 429:
                    raise AIRateLimitError(str(exc)) from exc
                if exc.code == 400 and sent_thinking_config:
                    logger.warning(
                        "Model %s rejected thinking_budget=0; retrying without it "
                        "and disabling it for this model",
                        self._model,
                    )
                    _ZERO_THINKING_BUDGET_UNSUPPORTED.add(self._model)
                    continue
                raise

    async def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        from google import genai
        from google.genai import errors as genai_errors, types

        response = await self._generate(
            user, system, json_mode, genai, genai_errors, types
        )
        _record_gemini_usage(response)
        return response.text or ""

    async def chat_history(
        self, system: str, messages: list[dict[str, str]], json_mode: bool = False
    ) -> str:
        from google import genai
        from google.genai import errors as genai_errors, types

        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in messages
        ]
        response = await self._generate(
            contents, system, json_mode, genai, genai_errors, types
        )
        _record_gemini_usage(response)
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

    A provider is usable when the user supplied their own key for it (BYOK), or
    when admin-key fallback is enabled and the backend owner configured a global
    key. Respects the user's stored preference when its provider is usable;
    otherwise falls back to whichever provider is usable. Previously this looked
    only at the global keys, so in BYOK-only mode a user with their own key was
    routed to the wrong provider and got an "unknown key" error (#448).
    """

    def _usable(user_key: str | None, global_key: str) -> bool:
        if user_key:
            return True
        return bool(settings.allow_admin_ai_key_fallback and global_key)

    gemini_usable = _usable(user.user_gemini_api_key, settings.gemini_api_key)
    openai_usable = _usable(user.user_openai_api_key, settings.openai_api_key)

    stored = user.ai_provider
    if stored == "gemini" and gemini_usable:
        return "gemini"
    if stored == "openai" and openai_usable:
        return "openai"
    if gemini_usable:
        return "gemini"
    if openai_usable:
        return "openai"
    # Nothing usable: return the user's stated preference so get_provider raises
    # the correct provider-specific "key not configured" error.
    return stored if stored in ("openai", "gemini") else "gemini"
