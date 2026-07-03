"""Bounded in-process progress tracking for background imports.

This is **single-replica only** (see #326 and ``docs/multi_replica.md``): the
Strava and intervals.icu import routers each keep a per-user progress dict in
module memory. Two helpers here address the parts of #326 that are real bugs on
a single replica:

- ``prune_progress`` evicts *terminal* (``done`` / ``error``) entries once they
  are older than a TTL, and caps the total number of entries — fixing the
  unbounded growth of a never-evicted dict. Active (``running``) entries are
  never evicted.
- ``try_mark_running`` performs the "is an import already running?" check and
  the "mark it running" write as one synchronous step, so the guard cannot be
  raced by two near-simultaneous requests on the same event loop.

Multi-replica correctness (state visible across replicas) is intentionally out
of scope; it requires a shared store *and* fixing the in-process scheduler.
"""

from __future__ import annotations

import time
from typing import Any, Hashable

DEFAULT_TTL_SECONDS = 3600
DEFAULT_MAX_ENTRIES = 1000

_TERMINAL_STATUSES = ("done", "error")


def mark_finished(value: dict) -> dict:
    """Stamp a terminal progress payload with the time it finished (for TTL)."""
    return {**value, "finished_at": time.time()}


def prune_progress(
    store: dict[Any, dict],
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    now: float | None = None,
) -> None:
    """Evict terminal entries past their TTL, then cap the store size.

    Only ``done`` / ``error`` entries are eligible for eviction (they carry a
    ``finished_at`` set via :func:`mark_finished`); ``running`` entries are kept
    so an in-flight import is never dropped. When the store still exceeds
    ``max_entries``, the oldest terminal entries are evicted first.
    """
    current = time.time() if now is None else now

    for key, payload in list(store.items()):
        if payload.get("status") in _TERMINAL_STATUSES:
            if current - payload.get("finished_at", current) > ttl_seconds:
                store.pop(key, None)

    if len(store) > max_entries:
        evictable = sorted(
            (
                (key, payload.get("finished_at", 0.0))
                for key, payload in store.items()
                if payload.get("status") in _TERMINAL_STATUSES
            ),
            key=lambda item: item[1],
        )
        for key, _ in evictable[: len(store) - max_entries]:
            store.pop(key, None)


def try_mark_running(
    store: dict[Any, dict], key: Hashable, running_value: dict
) -> bool:
    """Atomically start a job for ``key``.

    Returns ``False`` (and leaves the store untouched) when an import is already
    ``running`` for ``key``; otherwise writes ``running_value`` and returns
    ``True``. The check and the write happen with no ``await`` between them, so
    on a single event loop two concurrent callers cannot both start.
    """
    if store.get(key, {}).get("status") == "running":
        return False
    store[key] = running_value
    return True
