"""Unit tests for services/strava_service.py HTTP helpers."""

from __future__ import annotations

import pytest

from services import strava_service as ss


class _FakeResp:
    def __init__(self, payload=None, is_success=True):
        self._payload = payload
        self.is_success = is_success

    def json(self):
        return self._payload


def _patch_client(monkeypatch, resp):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            return resp

    monkeypatch.setattr(ss.httpx, "AsyncClient", _FakeClient)


@pytest.mark.asyncio
async def test_fetch_activity_streams_success(monkeypatch):
    _patch_client(monkeypatch, _FakeResp({"watts": {"data": [1, 2, 3]}}))
    result = await ss.fetch_activity_streams("tok", 123)
    assert result == {"watts": {"data": [1, 2, 3]}}


@pytest.mark.asyncio
async def test_fetch_activity_streams_error_returns_empty(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(is_success=False))
    assert await ss.fetch_activity_streams("tok", 123) == {}


@pytest.mark.asyncio
async def test_fetch_activity_detail_success_and_non_dict(monkeypatch):
    _patch_client(monkeypatch, _FakeResp({"id": 9, "name": "Ride"}))
    assert await ss.fetch_activity_detail("tok", 9) == {"id": 9, "name": "Ride"}

    _patch_client(monkeypatch, _FakeResp(["not", "a", "dict"]))
    assert await ss.fetch_activity_detail("tok", 9) == {}


@pytest.mark.asyncio
async def test_fetch_activity_detail_error_returns_empty(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(is_success=False))
    assert await ss.fetch_activity_detail("tok", 9) == {}
