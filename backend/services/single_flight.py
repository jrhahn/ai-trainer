"""Coalesce concurrent identical requests into one execution (#664).

``progress_store.try_mark_running`` solves the neighbouring problem — it *rejects*
a second start, which is right for a fire-and-forget background import whose
caller only wants an acknowledgement. It is wrong for a request whose caller
needs the result: rejecting the second ``POST /ai/generate-plan`` would leave a
freshly onboarded athlete staring at an empty plan.

So this coalesces instead. The first caller runs the work; anyone arriving while
it is in flight waits for the same result. That is what they asked for, and it
means the expensive, non-deterministic part (the LLM call and the plan write)
happens exactly once.

Like ``progress_store`` this is **single-replica only**: the in-flight map lives
in module memory. See ``docs/multi_replica.md``.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Hashable, TypeVar

T = TypeVar("T")

InFlight = dict[Hashable, "asyncio.Future"]


async def run_single_flight(
    inflight: InFlight,
    key: Hashable,
    factory: Callable[[], Awaitable[T]],
) -> T:
    """Run ``factory()`` for ``key``, or join the run already in flight.

    The leader's result — or its exception — is delivered to every follower, so
    a coalesced caller cannot silently receive a stale value in place of an
    error. Followers are shielded: a follower giving up (client disconnect,
    timeout) must not cancel the leader's work, which the other callers and the
    database write still depend on.

    The check and the claim happen with no ``await`` between them, so on a single
    event loop two concurrent callers cannot both become leader.
    """
    existing = inflight.get(key)
    if existing is not None:
        return await asyncio.shield(existing)

    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    inflight[key] = future
    try:
        result = await factory()
    except BaseException as exc:
        if not future.done():
            future.set_exception(exc)
        raise
    else:
        if not future.done():
            future.set_result(result)
        return result
    finally:
        inflight.pop(key, None)
        # With no follower nobody ever awaits this future, and an unretrieved
        # exception makes asyncio log a spurious "exception was never retrieved"
        # when it is collected. Reading it here marks it retrieved; the real
        # exception is still propagating out of the `raise` above.
        if future.done() and not future.cancelled():
            future.exception()
