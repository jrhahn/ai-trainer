"""Per-call LLM cost accounting.

Answers "which feature is costing me money" from the logs and from
``users.consumed_*_tokens``, instead of by rebuilding prompts by hand against
the production database (#516).

Three things live here:

*Per-call records.* Every provider call emits one structured line naming the
task, the resolved model, the input/output/cached split, the latency and a
``prompt_sha``. The line is written whether or not a collection scope is
active, so a call can never be invisible — an unscoped call is labelled
``source=unscoped`` and is a bug worth grepping for.

*The input/output/cached split.* Input and output bill at very different rates
(currently $1.50/M vs $9.00/M for the coach model) and cached input at a tenth
of the input rate, so a single total cannot be converted to a cost at all.
Note that ``cached`` is a *subset* of ``input``, not a fourth bucket: both
providers report the cache hit as part of the prompt count.

*Attribution.* :func:`track_llm_usage` is the one way to collect and persist
usage. It replaced a begin/finish/persist boilerplate that each of fifteen call
sites repeated, one of which (the per-ride review chain in ``activity_sync``)
never had it at all, so scheduler-driven coaching was billed to nobody.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from config import settings

logger = logging.getLogger(__name__)

# Source label for a provider call made outside any collection scope. Its
# tokens are reported in the log but billed to no user.
UNSCOPED = "unscoped"


@dataclass
class TokenUsage:
    """Provider-reported tokens for everything inside one collection scope."""

    input: int = 0
    output: int = 0
    # Input tokens the provider served from its cache, billed at a tenth of the
    # input rate. A *subset* of ``input``, so billable input is
    # ``input - cached``. Tracked because the totals look identical whether the
    # cache hits or not, which is the only way to tell whether the
    # cache-friendly prompt order actually works (#514).
    cached: int = 0
    total: int = 0
    calls: int = 0


@dataclass
class _Scope:
    source: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    # The most recent call, so a parse failure discovered after the provider
    # returned can still be attributed to the call that produced it.
    last_task: str = ""
    last_model: str = ""
    last_prompt_sha: str = ""


_scope: ContextVar[_Scope | None] = ContextVar("llm_usage_scope", default=None)


def prompt_fingerprint(system_prompt: str) -> str:
    """Short stable hash of a system prompt, so a prompt edit is greppable."""
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:12]


def current_source() -> str:
    scope = _scope.get()
    return scope.source if scope is not None else UNSCOPED


def begin_collection(source: str) -> Token[_Scope | None]:
    """Start collecting provider usage for the current context."""
    return _scope.set(_Scope(source=source))


def finish_collection(token: Token[_Scope | None]) -> TokenUsage:
    """Return the collected usage and restore the previous context."""
    scope = _scope.get()
    usage = scope.usage if scope is not None else TokenUsage()
    _scope.reset(token)
    return usage


def record_call(
    *,
    task: str,
    provider: str,
    model: str,
    system_prompt: str,
    json_mode: bool,
    latency_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    total_tokens: int = 0,
    ok: bool = True,
    error: str = "",
) -> None:
    """Log one provider call and add it to the active scope, if any.

    Called from the provider layer rather than from ``ai_service._chat`` so it
    also covers anything holding an ``LLMProvider`` directly, and because only
    the provider knows which model the task actually resolved to.
    """
    source = current_source()
    prompt_sha = prompt_fingerprint(system_prompt)
    logger.info(
        "LLM call task=%s provider=%s model=%s source=%s input=%d output=%d "
        "cached=%d total=%d latency_ms=%d json_mode=%s ok=%s prompt_sha=%s%s",
        task,
        provider,
        model,
        source,
        input_tokens,
        output_tokens,
        cached_tokens,
        total_tokens,
        latency_ms,
        str(json_mode).lower(),
        str(ok).lower(),
        prompt_sha,
        f" error={error}" if error else "",
    )
    scope = _scope.get()
    if scope is None:
        return
    scope.usage.input += max(0, input_tokens)
    scope.usage.output += max(0, output_tokens)
    scope.usage.cached += max(0, cached_tokens)
    scope.usage.total += max(0, total_tokens)
    scope.usage.calls += 1
    scope.last_task = task
    scope.last_model = model
    scope.last_prompt_sha = prompt_sha


def note_json_repair(original_len: int, repaired_len: int) -> None:
    """Report that ``json_repair`` had to alter the last call's response.

    A separate line rather than a field on the call record: the response is
    parsed well after the provider returned, by which time that line is already
    written. Carries the task/model/prompt_sha so it joins back to the call
    that produced the malformed JSON — a prompt that keeps needing repair is a
    prompt to fix.
    """
    scope = _scope.get()
    logger.warning(
        "LLM json_repair task=%s model=%s source=%s prompt_sha=%s "
        "original_chars=%d repaired_chars=%d",
        scope.last_task if scope else "",
        scope.last_model if scope else "",
        current_source(),
        scope.last_prompt_sha if scope else "",
        original_len,
        repaired_len,
    )


def log_payload(kind: str, text: str) -> None:
    """Log prompt/response content, but only when explicitly switched on.

    Prompts carry the athlete's health data, so this stays off by default and
    is a deliberate debugging step, never something a normal deployment writes
    to its logs (#499).
    """
    if not settings.log_llm_payloads:
        return
    logger.debug("LLM payload kind=%s source=%s\n%s", kind, current_source(), text)


@asynccontextmanager
async def track_llm_usage(
    db: AsyncSession, user: models.User, *, source: str
) -> AsyncIterator[None]:
    """Collect provider usage for the enclosed block and always persist it.

    Persisting in a ``finally`` guarantees the ContextVar is reset exactly once
    on every exit path, so a raising block (an AI rate limit becoming an
    ``HTTPException``, say) cannot leak the scope — call sites used to have to
    remember that in every ``except`` branch and un-handled exception types
    leaked it entirely (#449). The persist itself is best-effort: accounting
    must never mask the real error from the enclosed block.

    *source* names what spent the money: ``api:<endpoint>`` for a request,
    ``job:<scheduler-job>`` for background work.
    """
    token = begin_collection(source)
    try:
        yield
    finally:
        usage = finish_collection(token)
        try:
            if usage.total or usage.input or usage.output:
                logger.info(
                    "LLM usage source=%s user=%s calls=%d input=%d output=%d "
                    "cached=%d total=%d",
                    source,
                    user.id,
                    usage.calls,
                    usage.input,
                    usage.output,
                    usage.cached,
                    usage.total,
                )
                await crud.increment_user_consumed_tokens(
                    db,
                    user,
                    usage.total,
                    input_tokens=usage.input,
                    output_tokens=usage.output,
                    cached_tokens=usage.cached,
                )
        except Exception:  # noqa: BLE001 — never mask the enclosed block's error
            logger.warning("Failed to persist collected token usage", exc_info=True)
