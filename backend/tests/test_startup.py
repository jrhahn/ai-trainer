"""Tests for app startup schema handling (#327).

Alembic is authoritative in real deployments (entrypoint.sh runs
`alembic upgrade head`); create_all is a dev/test-only bootstrap and must not
run in other environments, where it would mask a forgotten migration.
"""

from __future__ import annotations

import pytest

import main


class _FakeConn:
    def __init__(self, calls: dict) -> None:
        self._calls = calls

    async def run_sync(self, _fn) -> None:
        self._calls["create_all"] += 1


class _FakeBegin:
    def __init__(self, calls: dict) -> None:
        self._calls = calls

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._calls)

    async def __aexit__(self, *_args) -> bool:
        return False


class _FakeEngine:
    def __init__(self, calls: dict) -> None:
        self._calls = calls

    def begin(self) -> _FakeBegin:
        self._calls["begin"] += 1
        return _FakeBegin(self._calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env, should_run",
    [
        ("development", True),
        ("test", True),
        ("production", False),
        ("staging", False),
    ],
)
async def test_create_dev_schema_gating(monkeypatch, env, should_run):
    calls = {"create_all": 0, "begin": 0}
    monkeypatch.setattr(main.settings, "app_env", env)
    monkeypatch.setattr(main, "engine", _FakeEngine(calls))

    await main._create_dev_schema()

    assert (calls["create_all"] > 0) is should_run
    assert (calls["begin"] > 0) is should_run
