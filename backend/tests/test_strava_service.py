"""Unit tests for services/strava_service.py HTTP helpers."""

from __future__ import annotations

import httpx
import pytest

from services import strava_service as ss


class _FakeResp:
    def __init__(self, payload=None, is_success=True, status_code=None):
        self._payload = payload
        self.is_success = is_success
        self.status_code = status_code if status_code is not None else (
            200 if is_success else 400
        )

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


def _patch_client_raising(monkeypatch, exc):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            raise exc

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


# ---------------------------------------------------------------------------
# fetch_activity_streams_strict: transient vs genuine-empty (#325)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_returns_streams_on_success(monkeypatch):
    _patch_client(monkeypatch, _FakeResp({"watts": {"data": [1]}}))
    assert await ss.fetch_activity_streams_strict("tok", 1) == {"watts": {"data": [1]}}


@pytest.mark.asyncio
async def test_strict_empty_on_permanent_non_success(monkeypatch):
    # A permanent 4xx (e.g. 404) means the activity genuinely has no streams.
    _patch_client(monkeypatch, _FakeResp(is_success=False, status_code=404))
    assert await ss.fetch_activity_streams_strict("tok", 1) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_strict_raises_on_transient_status(monkeypatch, status):
    _patch_client(monkeypatch, _FakeResp(is_success=False, status_code=status))
    with pytest.raises(ss.StravaStreamUnavailable):
        await ss.fetch_activity_streams_strict("tok", 1)


@pytest.mark.asyncio
async def test_strict_raises_on_network_error(monkeypatch):
    _patch_client_raising(monkeypatch, httpx.ConnectTimeout("timed out"))
    with pytest.raises(ss.StravaStreamUnavailable):
        await ss.fetch_activity_streams_strict("tok", 1)


@pytest.mark.asyncio
async def test_lenient_swallows_transient_and_returns_empty(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(is_success=False, status_code=429))
    assert await ss.fetch_activity_streams("tok", 1) == {}

    _patch_client_raising(monkeypatch, httpx.ReadTimeout("timed out"))
    assert await ss.fetch_activity_streams("tok", 1) == {}


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
