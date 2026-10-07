"""Concurrent writers to the Authelia user store lose nothing (ai-trainer-ops#14).

Self-registration writes ``users_database.yml`` on every signup. A read-modify-
write of one YAML file is the textbook lost update: two signups read the same
document, each adds itself, and the second write erases the first — a person
who was told "registered" and cannot log in. The window is not small either:
the Argon2 hash sits between the read and the write, so it is as long as a
password hash takes.

``services/authelia_store`` closes it with an exclusive ``flock`` on a lock
file beside the store, which is held across processes — and across containers
on one host, since they share the file through the bind mount. That is the
claim this file tests, with real processes rather than threads, because a
``flock`` taken twice inside one process does not exclude anything and a
threaded test would pass with or without it.

What it does not cover: two *hosts* sharing the file over a network
filesystem, where ``flock`` is not reliable. ``docs/multi_replica.md`` says so.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest
import yaml

import auth
from services import authelia_store

_PASSWORD = "Str0ng!Pass"
_WRITERS = 8

# Spawn, not fork: pytest's process is multi-threaded by the time a test runs,
# and a forked child can inherit a lock some other thread was holding and hang
# on it — a flaky CI job in place of a test. Spawned children re-import this
# module and are told the store path explicitly. (The environment the conftest
# set up is inherited, so ``auth`` imports the same way it does here.)
_spawn = multiprocessing.get_context("spawn")


def _write_store(path: Path, users: dict) -> None:
    path.write_text(yaml.safe_dump({"users": users}))


def _users(path: Path) -> dict:
    return yaml.safe_load(path.read_text())["users"]


def _point_at(path: str) -> None:
    auth.AUTHELIA_USERS_DB_PATH = path
    # delete_user is a deliberate no-op unless header auth is on.
    auth.AUTHELIA_AUTH_ENABLED = True


def _register(barrier, path: str, email: str) -> None:
    _point_at(path)
    barrier.wait()
    authelia_store.create_user(email, email.split("@")[0], _PASSWORD)


def _delete(barrier, path: str, email: str) -> None:
    _point_at(path)
    barrier.wait()
    authelia_store.delete_user(email)


def _run_together(store: Path, jobs: list[tuple]) -> None:
    """Start every job behind one barrier, so they hit the store at once."""
    barrier = _spawn.Barrier(len(jobs))
    processes = [
        _spawn.Process(target=fn, args=(barrier, str(store), *args))
        for fn, *args in jobs
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
    assert [p.exitcode for p in processes] == [0] * len(processes)


@pytest.fixture
def store(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "users_database.yml"
    # For the assertions made from this process; the children set their own.
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(path))
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    return path


def test_simultaneous_signups_are_all_written(store):
    _write_store(store, {})

    emails = [f"rider{i}@example.com" for i in range(_WRITERS)]
    _run_together(store, [(_register, email) for email in emails])

    assert sorted(_users(store)) == sorted(emails)


def test_signups_and_deletions_at_once_each_land(store):
    """Deletion is a read-modify-write of the same file, so it races the same way."""
    leaving = [f"leaver{i}@example.com" for i in range(_WRITERS // 2)]
    staying = {"stayer@example.com"}
    _write_store(
        store,
        {
            email: {"email": email, "password": "x", "displayname": "x"}
            for email in [*leaving, *staying]
        },
    )

    joining = [f"joiner{i}@example.com" for i in range(_WRITERS // 2)]
    _run_together(
        store,
        [(_delete, email) for email in leaving]
        + [(_register, email) for email in joining]
    )

    assert set(_users(store)) == staying | set(joining)


def test_every_concurrent_signup_can_log_in(store):
    """Written is not enough: the entry must carry a hash that verifies."""
    _write_store(store, {})

    emails = [f"rider{i}@example.com" for i in range(4)]
    _run_together(store, [(_register, email) for email in emails])

    assert all(authelia_store.verify_credentials(e, _PASSWORD) for e in emails)
