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
— $0.30/M vs $2.50/M for the configured coach model, ``gemini-3.5-flash-lite``
— so a single total cannot be converted to a cost at all. Note that ``cached``
is a *subset* of ``input``, not a fourth bucket: both providers report the
cache hit as part of the prompt count. It is always zero on the coach model:
flash-lite offers no context caching, implicit or explicit, which #538
established by probe and the pricing page states outright. The field is still
collected because it is the first thing to check after a model change, and
because a cached token bills at a tenth of an input token where it is offered.

*Attribution.* :func:`track_llm_usage` is the way to collect and persist usage
for work that has a request or job session. It replaced a begin/finish/persist
boilerplate that each of fifteen call sites repeated, one of which (the per-ride
review chain in ``activity_sync``) never had it at all, so scheduler-driven
coaching was billed to nobody. Work that outlives its session — a FastAPI
``BackgroundTasks`` callback — uses :func:`track_llm_usage_detached` instead;
using neither is the bug that both of those were.
"""

from __future__ import annotations

import hashlib
import logging
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import crud
import database
import models
from config import settings
from services import owner_alerts

logger = logging.getLogger(__name__)

# Source label for a provider call made outside any collection scope. Its
# tokens are reported in the log but billed to no user.
UNSCOPED = "unscoped"

# Every source is ``<kind>:<kebab-case-name>``. The kind says what *opened the
# scope*, which is knowable where the code is written — unlike "what triggered
# this", which is not: ``coach-narration`` runs under two endpoints, a
# scheduler job and the ride-review chain, so any fixed answer would be wrong
# most of the time.
#
#   api    an HTTP endpoint's own work
#   job    a registered scheduler job
#   bg     a FastAPI background task, running after the response
#   step   a reusable unit of work that can run under any of the above
#
# Labels used to be a mix of ``api:ask_trainer``, ``job:strava-sync`` and bare
# ``coach-narration``, which made the dashboards read as if a third of the
# spend came from nowhere in particular (#549).
SOURCE_KINDS = ("api", "job", "bg", "step")
_SOURCE_PATTERN = re.compile(rf"^({'|'.join(SOURCE_KINDS)}):[a-z0-9]+(-[a-z0-9]+)*$")


def source_is_well_formed(source: str) -> bool:
    return bool(_SOURCE_PATTERN.match(source))


class TokenBudgetExceededError(Exception):
    """Raised before a provider call when the user is over their token budget.

    Deliberately its own type rather than a generic 500 or a reused
    ``AIRateLimitError``: "you have spent your allowance" and "the provider is
    throttling us" need different words in the UI and different reactions from
    the client — one is worth retrying in a minute, the other is not (#676).
    """

    def __init__(self, *, spent: int, budget: int, window_days: int) -> None:
        self.spent = spent
        self.budget = budget
        self.window_days = window_days
        super().__init__(
            f"You have used the free coaching allowance: {spent:,} of {budget:,} "
            f"tokens in the last {window_days} days. Add your own AI key (Gemini "
            f"or OpenAI) in Settings to keep going without a limit, or get in "
            f"touch with whoever runs this coach. The allowance frees up again as "
            f"older usage ages out of the {window_days}-day window."
        )


class GlobalTokenBudgetExceededError(TokenBudgetExceededError):
    """The owner's key has spent its daily cap across all free accounts.

    The per-user budget does not bound the total: registration allows a few
    accounts an hour, and each may spend its full allowance. This caps what a
    wave of new accounts can cost in a day (ai-trainer-ops#39). Answered like
    the per-user budget, 402 with a pointer to an own key, because that is the
    one thing the athlete can do about it.
    """

    def __init__(self, *, spent: int, budget: int) -> None:
        self.spent = spent
        self.budget = budget
        self.window_days = 1
        Exception.__init__(
            self,
            "The free coaching on this server has reached its limit for today. "
            "Add your own AI key (Gemini or OpenAI) in Settings to keep going "
            "now; the free allowance comes back as the last day's usage ages out.",
        )


async def enforce_token_budget(
    db: AsyncSession, user: models.User, *, source: str, now: datetime | None = None
) -> None:
    """Raise :class:`TokenBudgetExceededError` when *user* is over budget.

    The **per-user rolling budget** applies only to ``api:`` sources: scheduler
    jobs are bounded by their schedule, not by request rate, and killing a
    nightly plan-maintenance run because the athlete chatted a lot is the #472
    failure mode (silent loss of work the athlete never asked to lose).

    The **global daily cap** applies to every owner-key source, background work
    included (ops#122): a few heavy accounts' jobs used to drain the shared
    ceiling without it ever being checked, because the cap was only consulted on
    the next ``api:`` call. A job that would push the owner's key over the shared
    cap now raises here and spends nothing.

    Checked *before* the block runs, so an over-budget user costs nothing at
    all rather than one more prompt.
    """
    budget = settings.ai_token_budget
    daily = settings.ai_global_daily_token_budget
    if budget <= 0 and daily <= 0:
        return
    # Only the owner's key is rationed (ai-trainer-ops#79); imported here
    # because llm imports this module.
    from services.llm import pays_with_admin_key

    if not pays_with_admin_key(user):
        return

    reference = now or datetime.now(timezone.utc)
    if source.startswith("api:") and budget > 0:
        window_days = settings.ai_token_budget_window_days
        since = reference - timedelta(days=window_days)
        spent = await crud.sum_user_tokens_since(db, user.id, since)
        if spent >= budget:
            raise TokenBudgetExceededError(
                spent=spent, budget=budget, window_days=window_days
            )
    if daily > 0:
        spent = await crud.sum_admin_key_tokens_since(db, reference - timedelta(days=1))
        if spent >= daily:
            # Every tester without an own key now gets no coach replies, so the
            # owner hears of it (ai-trainer-ops#124). Here, because API requests
            # and background jobs both stop at this line.
            await owner_alerts.notify_once(
                "ai-global-daily-budget",
                "AI budget used up",
                f"The shared key has spent {spent:,} of {daily:,} tokens in the "
                "last 24 hours. Accounts without their own key get no coach "
                "replies until the usage ages out. Raise "
                "AI_GLOBAL_DAILY_TOKEN_BUDGET or wait.",
            )
            raise GlobalTokenBudgetExceededError(spent=spent, budget=daily)


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
class CallRecord:
    """One provider call, as it will be stored (#549).

    Collected in the scope rather than written when it happens: ``record_call``
    runs in the provider layer, which is synchronous and holds no session. The
    scope already has to be open for the tokens to be billed at all, so the
    records ride along with them and are inserted at the same moment.
    """

    task: str
    provider: str
    model: str
    prompt_sha: str
    json_mode: bool
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    total_tokens: int
    ok: bool
    error: str


@dataclass
class _Scope:
    source: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    records: list[CallRecord] = field(default_factory=list)
    # The most recent call, so a parse failure discovered after the provider
    # returned can still be attributed to the call that produced it.
    last_task: str = ""
    last_model: str = ""
    last_prompt_sha: str = ""


@dataclass
class Collection:
    """What one closed scope collected: the totals, and the calls behind them."""

    usage: TokenUsage = field(default_factory=TokenUsage)
    records: list[CallRecord] = field(default_factory=list)


_scope: ContextVar[_Scope | None] = ContextVar("llm_usage_scope", default=None)


def prompt_fingerprint(system_prompt: str) -> str:
    """Short stable hash of a system prompt, so a prompt edit is greppable."""
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:12]


def current_source() -> str:
    scope = _scope.get()
    return scope.source if scope is not None else UNSCOPED


def begin_collection(source: str) -> Token[_Scope | None]:
    """Start collecting provider usage for the current context."""
    if not source_is_well_formed(source):
        # Warned, not raised: a mislabelled scope still bills correctly, and
        # accounting must never be the thing that fails a request. The warning
        # is what stops the labels drifting apart again (#549).
        logger.warning(
            "LLM usage source %r is not <kind>:<kebab-name> with kind in %s",
            source,
            ", ".join(SOURCE_KINDS),
        )
    return _scope.set(_Scope(source=source))


def finish_collection(token: Token[_Scope | None]) -> Collection:
    """Return what the scope collected and restore the previous context."""
    scope = _scope.get()
    collected = (
        Collection(usage=scope.usage, records=scope.records)
        if scope is not None
        else Collection()
    )
    _scope.reset(token)
    return collected


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
        # Deliberately louder than the line above: a call nobody is billed for
        # is a bug, and #537 took a month to notice because the only evidence
        # was the word "unscoped" inside a routine INFO line. Silence at
        # WARNING is now the signal that attribution is complete.
        logger.warning(
            "LLM call billed to nobody task=%s model=%s prompt_sha=%s total=%d "
            "— no collection scope was open; see track_llm_usage",
            task,
            model,
            prompt_sha,
            total_tokens,
        )
        return
    scope.usage.input += max(0, input_tokens)
    scope.usage.output += max(0, output_tokens)
    scope.usage.cached += max(0, cached_tokens)
    scope.usage.total += max(0, total_tokens)
    scope.usage.calls += 1
    scope.records.append(
        CallRecord(
            task=task,
            provider=provider,
            model=model,
            prompt_sha=prompt_sha,
            json_mode=json_mode,
            latency_ms=max(0, latency_ms),
            input_tokens=max(0, input_tokens),
            output_tokens=max(0, output_tokens),
            cached_tokens=max(0, cached_tokens),
            total_tokens=max(0, total_tokens),
            ok=ok,
            error=error,
        )
    )
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


def log_prompt_sections(prompt: str, sections: dict[str, str]) -> None:
    """Report how big each part of a prompt is. Sizes only, never content.

    Where the coach prompt's bulk sits has been guessed at twice and gone stale
    both times: #510 measured it by hand against a ~21,300-token prompt, then
    #512 and #513 changed it. Emitting it from the code that builds the prompt
    keeps the answer current and about the real athlete's real data, where a
    script would have to duplicate the twenty-odd fetches the endpoint does and
    would drift from them (#556).

    Characters, not tokens — counting tokens needs the provider's tokeniser.
    The live API counted this prompt's static block at 4.8 chars/token (#549),
    close enough to convert by eye.

    Safe at INFO because it carries no prompt text: the content of these
    sections is the athlete's health data, and that stays behind
    ``LOG_LLM_PAYLOADS`` (#499). Empty sections are left out — a section that is
    not there is not the question.
    """
    present = {name: len(text) for name, text in sections.items() if text}
    ranked = sorted(present.items(), key=lambda item: item[1], reverse=True)
    logger.info(
        "LLM prompt sections total_chars=%d prompt_sha=%s %s",
        len(prompt),
        prompt_fingerprint(prompt),
        " ".join(f"{name}={size}" for name, size in ranked),
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


async def _persist_usage(
    db: AsyncSession,
    user: models.User,
    source: str,
    collected: Collection,
) -> None:
    """Log the scope total, store its calls, bill the user. Never commits."""
    usage = collected.usage
    # The call rows go in first and unconditionally: a failed call reports zero
    # tokens, and a model that has started rejecting every request is exactly
    # what a cost table has to be able to show (#401).
    await crud.record_llm_calls(db, user.id, source, collected.records)
    if not (usage.total or usage.input or usage.output):
        return
    logger.info(
        "LLM usage source=%s user=%s calls=%d input=%d output=%d cached=%d total=%d",
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


# Key under which usage from a raising block waits on the session until its
# transaction is over. ``Session.info`` is per-session scratch space, which is
# exactly the lifetime this needs: the deferral belongs to the unit of work that
# failed, not to the process.
_DEFERRED_USAGE_KEY = "deferred_llm_usage"


def _defer_usage(
    db: AsyncSession, user_id: str, source: str, collected: Collection
) -> None:
    """Hold usage until the failed transaction it belongs to has been resolved.

    Writing it now would put the rows inside a transaction that is on its way to
    a rollback. Writing it from a *second* connection would be worse: the
    ``users`` row update would wait for this transaction to end, and this
    transaction ends only after the ``finally`` that is doing the waiting — a
    request that hangs instead of a row that is missing.
    """
    if not (collected.records or collected.usage.total):
        return
    db.info.setdefault(_DEFERRED_USAGE_KEY, []).append((user_id, source, collected))
    # A request's session settles once its transaction is resolved (database.py);
    # a job's own session flushes in its own ``finally``.
    database.settle_after_transaction(db, flush_deferred_usage)


async def flush_deferred_usage(db: AsyncSession) -> None:
    """Write usage collected by a block that raised, and commit it (#560).

    Called once the session's transaction has been committed or rolled back, so
    the write starts a fresh one that nothing is about to discard. Best-effort in
    both directions: it must not mask the error that caused the deferral, and it
    must not turn a successful request into a failed one.

    A model that has started rejecting every request is exactly what a cost table
    has to be able to show, and it is the one case that used to be invisible.
    """
    pending = db.info.pop(_DEFERRED_USAGE_KEY, None)
    if not pending:
        return
    try:
        for user_id, source, collected in pending:
            user = await db.get(models.User, user_id)
            if user is None:
                # "tokens" here are LLM usage counts, not credentials (ai-trainer-ops#32).
                logger.warning(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                    "Cannot bill %d tokens from source=%s: user %s is gone",
                    collected.usage.total,
                    source,
                    user_id,
                )
                continue
            await _persist_usage(db, user, source, collected)
        await db.commit()
    except Exception:  # noqa: BLE001 — never mask the error that caused this
        logger.warning("Failed to persist deferred token usage", exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            logger.warning("Could not roll back after a failed usage flush")


def _byok_keys_for(user: models.User) -> dict[str, str | None]:
    """The athlete's own provider keys, shaped for ``set_user_ai_keys``.

    Mirrors the request dependency (``routers/dependencies.set_user_ai_keys``)
    so background work routes to the same key a request would (ops#122).
    """
    return {
        "openai": user.user_openai_api_key,
        "gemini": user.user_gemini_api_key,
    }


@asynccontextmanager
async def flushing_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A per-athlete job session that always flushes deferred token usage.

    The nightly and weekly runners open one session per athlete and commit, but
    never flushed: a step that raised after a paid call deferred its usage into
    ``db.info`` (so it would not write into a transaction about to roll back),
    and with no flush that spend vanished from ``llm_calls`` and the cost tables
    (ops#122). This rolls a failed transaction back before flushing, so the
    usage write starts on a clean one — the same shape ``run_activity_sync``
    already uses inline.
    """
    async with session_factory() as db:
        try:
            yield db
        except Exception:
            await db.rollback()
            raise
        finally:
            await flush_deferred_usage(db)


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

    When the block raises, the usage is **deferred** rather than written here:
    the rows would go into a transaction that is about to be rolled back, which
    is why three failed coach requests billed ~52k tokens and left no trace in
    ``llm_calls`` at all (#560). :func:`flush_deferred_usage` writes them once
    the session's transaction has ended, one way or the other.

    Raises :class:`TokenBudgetExceededError` before the block runs when the user
    is over their configured budget (#676). Raising here rather than at each
    call site is the same "one gate" argument that put attribution here:
    fifteen call sites cannot each be relied on to remember the check.
    """
    await enforce_token_budget(db, user, source=source)
    # Background work (job:/step:/bg:) carries no request BYOK context, so
    # get_provider would fall through to the owner's global key for every
    # athlete and never consult allow_admin_ai_key_fallback (ops#122). Put the
    # athlete's own keys in scope for the block so their work bills their key;
    # a keyless athlete under fallback-off then raises AIKeyNotConfiguredError
    # from get_provider and spends nothing. The request path (api:) already set
    # these in a dependency, so leave it untouched.
    key_token = None
    if not source.startswith("api:"):
        # Imported here because llm imports this module.
        from services.llm import set_user_ai_keys

        key_token = set_user_ai_keys(_byok_keys_for(user))
    token = begin_collection(source)
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        if key_token is not None:
            # Imported here because llm imports this module.
            from services.llm import reset_user_ai_keys

            reset_user_ai_keys(key_token)
        collected = finish_collection(token)
        if failed:
            _defer_usage(db, user.id, source, collected)
        else:
            try:
                await _persist_usage(db, user, source, collected)
            except Exception:  # noqa: BLE001 — never mask the enclosed block's error
                logger.warning("Failed to persist collected token usage", exc_info=True)


@asynccontextmanager
async def track_llm_usage_detached(
    session_maker: async_sessionmaker[AsyncSession], user_id: str, *, source: str
) -> AsyncIterator[None]:
    """Collect usage for work that holds no session across its provider calls.

    :func:`track_llm_usage` needs a live session and the ``User`` row for the
    whole block, which a FastAPI ``BackgroundTasks`` callback has neither of: it
    runs after the response, and therefore after the request's session *and*
    after the ContextVar scope have been torn down. That is why every coach
    question's memory update was billed to nobody (#537).

    The session is opened once, at the end, purely to persist — so a provider
    call that takes seconds is still never made with a transaction held open.
    That property is load-bearing for the memory update, which deliberately
    reads and writes in separate short sessions so an athlete's concurrent edit
    is not blocked or clobbered (#346, #522).

    *session_maker* is passed in rather than imported so the caller's factory —
    including the one the test suite substitutes — is the one used.
    """
    # NB: bg: work (e.g. bg:update-coach-memory) still runs on the owner's key,
    # because this path holds no session during the block by design (#346/#522)
    # and so cannot read the athlete's keys without breaking that. Fixing bg:
    # BYOK means threading the keys in from the request — a follow-up (ops#122).
    token = begin_collection(source)
    try:
        yield
    finally:
        collected = finish_collection(token)
        usage = collected.usage
        try:
            # A call that spent nothing still happened, so the session opens for
            # records too — a failed call reports zero tokens and is the most
            # interesting row in the table.
            if collected.records or usage.total or usage.input or usage.output:
                async with session_maker() as session:
                    user = await session.get(models.User, user_id)
                    if user is None:
                        # "tokens" here are LLM usage counts, not credentials (ai-trainer-ops#32).
                        logger.warning(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                            "Cannot bill %d tokens from source=%s: user %s is gone",
                            usage.total,
                            source,
                            user_id,
                        )
                    else:
                        await _persist_usage(session, user, source, collected)
                        await session.commit()
        except Exception:  # noqa: BLE001 — never mask the enclosed block's error
            logger.warning("Failed to persist collected token usage", exc_info=True)
