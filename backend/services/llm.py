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
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import models
from config import settings
from services import token_accounting

logger = logging.getLogger(__name__)


@dataclass
class _CallTokens:
    """One call's token counts, normalised across providers.

    ``cached`` is a subset of ``input`` in both providers' reporting, not a
    fourth bucket — billable input is ``input - cached``.
    """

    input: int = 0
    output: int = 0
    cached: int = 0
    total: int = 0


def _attribute_int(obj: object, *names: str) -> int | None:
    for name in names:
        value = getattr(obj, name, None)
        if isinstance(value, int):
            return value
    return None


def _openai_call_tokens(response: object) -> _CallTokens:
    usage = getattr(response, "usage", None)
    if usage is None:
        return _CallTokens()
    details = getattr(usage, "prompt_tokens_details", None)
    return _CallTokens(
        input=_attribute_int(usage, "prompt_tokens") or 0,
        output=_attribute_int(usage, "completion_tokens") or 0,
        cached=(_attribute_int(details, "cached_tokens") or 0) if details else 0,
        total=_attribute_int(usage, "total_tokens") or 0,
    )


def _gemini_call_tokens(response: object) -> _CallTokens:
    """Normalise Gemini's usage metadata.

    Thinking tokens (``thoughts_token_count``) bill at the *output* rate, so
    they are counted as output even though the API reports them separately —
    otherwise a model that ignores ``thinking_budget=0`` would look free. They
    should be zero given ``_build_config``; counting them is how a regression
    there becomes visible instead of silently expensive.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return _CallTokens()
    output = _attribute_int(usage, "candidates_token_count") or 0
    output += _attribute_int(usage, "thoughts_token_count") or 0
    return _CallTokens(
        input=_attribute_int(usage, "prompt_token_count") or 0,
        output=output,
        cached=_attribute_int(usage, "cached_content_token_count") or 0,
        total=_attribute_int(usage, "total_token_count") or 0,
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


async def _observed(
    *,
    task: str,
    provider_name: str,
    model: str,
    system: str,
    json_mode: bool,
    invoke,
    extract,
):
    """Run one provider call and record it, whether it succeeds or fails.

    A failed call still costs latency and still says which task and model was
    involved, so it is logged too — a model that starts rejecting every request
    is otherwise invisible in the cost logs (see #401).
    """
    started = time.perf_counter()
    try:
        response = await invoke()
    except Exception as exc:
        token_accounting.record_call(
            task=task,
            provider=provider_name,
            model=model,
            system_prompt=system,
            json_mode=json_mode,
            latency_ms=round((time.perf_counter() - started) * 1000),
            ok=False,
            error=type(exc).__name__,
        )
        raise
    tokens = extract(response)
    token_accounting.record_call(
        task=task,
        provider=provider_name,
        model=model,
        system_prompt=system,
        json_mode=json_mode,
        latency_ms=round((time.perf_counter() - started) * 1000),
        input_tokens=tokens.input,
        output_tokens=tokens.output,
        cached_tokens=tokens.cached,
        total_tokens=tokens.total,
        ok=True,
    )
    return response


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
    _task: str = TASK_COACH

    def __init__(
        self,
        model: str = OPENAI_MODEL,
        api_key: str | None = None,
        task: str = TASK_COACH,
    ) -> None:
        from openai import AsyncOpenAI

        self._model = model
        self._task = task
        self._client = AsyncOpenAI(api_key=api_key or settings.openai_api_key)

    async def _complete(self, system: str, messages: list, json_mode: bool) -> str:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await _observed(
            task=self._task,
            provider_name="openai",
            model=self._model,
            system=system,
            json_mode=json_mode,
            invoke=lambda: self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system}, *messages],
                **kwargs,
            ),
            extract=_openai_call_tokens,
        )
        text = response.choices[0].message.content or ""
        token_accounting.log_payload("response", text)
        return text

    async def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        token_accounting.log_payload("system", system)
        token_accounting.log_payload("user", user)
        return await self._complete(
            system, [{"role": "user", "content": user}], json_mode
        )

    async def chat_history(
        self, system: str, messages: list[dict[str, str]], json_mode: bool = False
    ) -> str:
        token_accounting.log_payload("system", system)
        return await self._complete(system, list(messages), json_mode)


class GeminiProvider:
    _model: str = GEMINI_MODEL  # class-level default; overridden by __init__
    _task: str = TASK_COACH

    def __init__(
        self,
        model: str = GEMINI_MODEL,
        api_key: str | None = None,
        task: str = TASK_COACH,
    ) -> None:
        self._model = model
        self._task = task
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

    async def _run(self, contents: object, system: str, json_mode: bool) -> str:
        from google import genai
        from google.genai import errors as genai_errors, types

        response = await _observed(
            task=self._task,
            provider_name="gemini",
            model=self._model,
            system=system,
            json_mode=json_mode,
            invoke=lambda: self._generate(
                contents, system, json_mode, genai, genai_errors, types
            ),
            extract=_gemini_call_tokens,
        )
        text = response.text or ""
        token_accounting.log_payload("response", text)
        return text

    async def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        token_accounting.log_payload("system", system)
        token_accounting.log_payload("user", user)
        return await self._run(user, system, json_mode)

    async def chat_history(
        self, system: str, messages: list[dict[str, str]], json_mode: bool = False
    ) -> str:
        from google.genai import types

        token_accounting.log_payload("system", system)
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in messages
        ]
        return await self._run(contents, system, json_mode)


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
            return OpenAIProvider(
                model=_resolve_model("openai", task), api_key=user_key, task=task
            )
        if name == "gemini":
            return GeminiProvider(
                model=_resolve_model("gemini", task), api_key=user_key, task=task
            )
        raise AIKeyNotConfiguredError(f"Unknown AI provider: {name}")
    return _get_provider_global(name, task)


def _get_provider_global(name: str, task: str) -> LLMProvider:
    """Return a provider using the global (backend-owner) settings keys."""
    if name == "gemini" and settings.gemini_api_key:
        return GeminiProvider(model=_resolve_model("gemini", task), task=task)
    if name == "openai" and settings.openai_api_key:
        return OpenAIProvider(model=_resolve_model("openai", task), task=task)
    if settings.gemini_api_key:
        return GeminiProvider(model=_resolve_model("gemini", task), task=task)
    if settings.openai_api_key:
        return OpenAIProvider(model=_resolve_model("openai", task), task=task)
    logger.warning("No AI provider API key configured; defaulting to GeminiProvider")
    return GeminiProvider(model=_resolve_model("gemini", task), task=task)


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
