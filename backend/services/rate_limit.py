"""Bounded in-process per-user rate limiting for the expensive AI endpoints.

This is **single-replica only** (see #326 and ``docs/multi_replica.md``): the
windows live in module memory, so with N replicas a user gets N times the
allowance. That is a weaker guarantee, not a broken one — the limit exists to
stop a script, and N is small and fixed — and it matches how every other piece
of request-scoped state in this app already works.

The check is deliberately a plain synchronous function with no ``await``
between reading a window and appending to it, so two concurrent requests on one
event loop cannot both slip past a full window. Same reasoning as
``progress_store.try_mark_running``.

Two windows run against one bucket rather than one window per route:

- a **burst** window, which is what a runaway client or a retry loop trips
- a **sustained** window, which is what a patient script drip-feeding requests
  under the burst limit trips

Either alone leaves an obvious hole, so both apply and the tighter one wins.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

# A bucket is one user. The cap only matters if a deployment ever sees more
# distinct users than this between prunes; eviction is oldest-idle-first, which
# can only ever hand back allowance, never take it away.
DEFAULT_MAX_BUCKETS = 10_000


@dataclass(frozen=True)
class Window:
    """An allowance of *limit* events per *seconds*."""

    limit: int
    seconds: float

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("A rate-limit window needs a limit of at least 1")
        if self.seconds <= 0:
            raise ValueError("A rate-limit window needs a positive duration")


class SlidingWindowLimiter:
    """Per-key sliding-window counter over one or more :class:`Window` limits."""

    def __init__(self, *, max_buckets: int = DEFAULT_MAX_BUCKETS) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._max_buckets = max_buckets

    def check(
        self,
        key: str,
        windows: tuple[Window, ...],
        *,
        now: float | None = None,
    ) -> float | None:
        """Consume one event for *key*, or report how long until it would fit.

        Returns ``None`` when the event was allowed and recorded. Otherwise
        returns the number of seconds until the tightest exceeded window has
        room again, suitable for a ``Retry-After`` header — and records
        **nothing**, so a client that keeps hammering while limited does not
        push its own recovery further out.
        """
        if not windows:
            return None

        current = time.monotonic() if now is None else now
        longest = max(window.seconds for window in windows)
        hits = self._hits.get(key)
        if hits is None:
            # Prune *before* inserting: the new bucket is empty, and an empty
            # bucket is exactly what _prune evicts — so pruning after would
            # drop the key again and every caller would then append to an
            # orphaned deque, recording nothing and limiting no one.
            self._prune(current, longest)
            hits = deque()
            self._hits[key] = hits

        # One trim against the longest window keeps the deque bounded; the
        # shorter windows then count a suffix of what survives.
        while hits and current - hits[0] > longest:
            hits.popleft()

        retry_after: float | None = None
        for window in windows:
            cutoff = current - window.seconds
            in_window = sum(1 for hit in hits if hit > cutoff)
            if in_window >= window.limit:
                # The oldest hit inside this window is the one whose expiry
                # frees a slot.
                oldest = next(hit for hit in hits if hit > cutoff)
                wait = oldest + window.seconds - current
                retry_after = wait if retry_after is None else max(retry_after, wait)

        if retry_after is not None:
            return max(retry_after, 0.0)

        hits.append(current)
        return None

    def reset(self, key: str | None = None) -> None:
        """Forget *key*'s history, or everything when *key* is ``None``."""
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)

    def _prune(self, now: float, longest: float) -> None:
        """Drop buckets whose newest hit has aged out, then cap the store."""
        for key, hits in list(self._hits.items()):
            if not hits or now - hits[-1] > longest:
                self._hits.pop(key, None)

        if len(self._hits) > self._max_buckets:
            by_age = sorted(
                self._hits.items(),
                key=lambda item: item[1][-1] if item[1] else 0.0,
            )
            for key, _ in by_age[: len(self._hits) - self._max_buckets]:
                self._hits.pop(key, None)
