"""Unit tests for the bounded in-process progress store (#326)."""

from __future__ import annotations

from services import progress_store as ps


def _terminal(status: str, finished_at: float) -> dict:
    return {"status": status, "finished_at": finished_at}


def test_mark_finished_stamps_timestamp():
    out = ps.mark_finished({"status": "done"})
    assert out["status"] == "done"
    assert isinstance(out["finished_at"], float)


def test_prune_evicts_terminal_past_ttl_keeps_recent():
    store = {
        "old": _terminal("done", finished_at=0.0),
        "recent": _terminal("error", finished_at=1000.0),
    }
    ps.prune_progress(store, ttl_seconds=100, now=1050.0)
    assert "old" not in store  # 1050 - 0 > 100
    assert "recent" in store  # 1050 - 1000 <= 100


def test_prune_never_evicts_running_entries():
    store = {
        "running": {"status": "running"},  # no finished_at
        "done": _terminal("done", finished_at=0.0),
    }
    ps.prune_progress(store, ttl_seconds=1, now=10_000.0)
    assert "running" in store
    assert "done" not in store


def test_prune_caps_size_evicting_oldest_terminal_first():
    store = {
        "a": _terminal("done", finished_at=1.0),
        "b": _terminal("done", finished_at=2.0),
        "c": _terminal("done", finished_at=3.0),
        "run": {"status": "running"},
    }
    # TTL huge so only the size cap applies; keep at most 2 entries.
    ps.prune_progress(store, ttl_seconds=10**9, max_entries=2, now=5.0)
    assert len(store) == 2
    assert "run" in store  # running never evicted
    assert "a" not in store  # oldest terminal evicted first
    assert "c" in store  # newest terminal kept


def test_try_mark_running_starts_when_absent():
    store: dict = {}
    assert ps.try_mark_running(store, "u1", {"status": "running", "total": 0}) is True
    assert store["u1"]["status"] == "running"


def test_try_mark_running_rejects_when_already_running():
    store = {"u1": {"status": "running"}}
    assert ps.try_mark_running(store, "u1", {"status": "running", "total": 99}) is False
    assert store["u1"] == {"status": "running"}  # untouched


def test_try_mark_running_restarts_after_terminal():
    store = {"u1": _terminal("done", finished_at=1.0)}
    assert ps.try_mark_running(store, "u1", {"status": "running"}) is True
    assert store["u1"]["status"] == "running"
