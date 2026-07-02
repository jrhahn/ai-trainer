"""Unit tests for services/intervals_service.py."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from services import intervals_service as isvc


# ---------------------------------------------------------------------------
# httpx fakes
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.is_success = 200 <= status_code < 300

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

        async def get(self, url, params=None, auth=None):
            return resp

    monkeypatch.setattr(isvc.httpx, "AsyncClient", _FakeClient)


def _patch_client_raising(monkeypatch, exc):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, auth=None):
            raise exc

    monkeypatch.setattr(isvc.httpx, "AsyncClient", _FakeClient)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_intervals_auth_is_basic_auth():
    assert isinstance(isvc.intervals_auth("key"), httpx.BasicAuth)


def test_intervals_activity_id_is_stable_and_signed_safe():
    a = isvc.intervals_activity_id("abc")
    b = isvc.intervals_activity_id("abc")
    assert a == b
    assert 0 <= a < (1 << 63)
    assert isvc.intervals_activity_id("abc") != isvc.intervals_activity_id("xyz")


def test_first_str_int_float():
    src = {"a": "", "b": "value", "n": 5, "f": 2.6, "flag": True}
    assert isvc._first_str(src, "a", "b") == "value"
    assert isvc._first_str(src, "missing", default="d") == "d"
    assert isvc._first_int(src, "flag", "n") == 5  # bool skipped
    assert isvc._first_int(src, "f") == 3  # float rounded
    assert isvc._first_int(src, "missing", default=0) == 0
    assert isvc._first_float(src, "n") == 5.0
    assert isvc._first_float(src, "flag") is None
    assert isvc._first_float(src, "missing") is None


def test_numeric_list_and_latlng_points():
    assert isvc._numeric_list([1, 2.5, "x", None, 3]) == [1.0, 2.5, 3.0]
    assert isvc._numeric_list("nope") == []
    assert isvc._latlng_points({"data": [[1, 2], [3, 4], ["a", "b"], [5]]}) == [[1.0, 2.0], [3.0, 4.0]]
    assert isvc._latlng_points("bad") == []


def test_start_latlng_variants():
    assert isvc._start_latlng({"start_latlng": [52.5, 13.4]}) == (52.5, 13.4)
    assert isvc._start_latlng({"start_lat": 1, "start_lng": 2}) == (1.0, 2.0)
    assert isvc._start_latlng({"lat": 3, "lng": 4}) == (3.0, 4.0)
    assert isvc._start_latlng({}) == (None, None)


def test_sanitize_intervals_streams_dict_shape():
    result = isvc.sanitize_intervals_streams(
        {
            "power": {"data": [100, 200]},
            "hr": [120, 130],
            "latlng": {"data": [[1.0, 2.0], [3.0, 4.0]]},
            "unknown_key": [1, 2],
        }
    )
    assert result["watts"] == {"data": [100.0, 200.0]}
    assert result["heartrate"] == {"data": [120.0, 130.0]}
    assert result["latlng"] == {"data": [[1.0, 2.0], [3.0, 4.0]]}
    assert "unknown_key" not in result


def test_sanitize_intervals_streams_list_shape():
    result = isvc.sanitize_intervals_streams(
        [
            {"watts": 100, "heartrate": 120, "lat": 1.0, "lng": 2.0},
            {"watts": 110, "heartrate": 125, "lat": 1.1, "lng": 2.1},
            "garbage",
        ]
    )
    assert result["watts"] == {"data": [100.0, 110.0]}
    assert result["heartrate"] == {"data": [120.0, 125.0]}
    assert result["latlng"]["data"] == [[1.0, 2.0], [1.1, 2.1]]


def test_sanitize_intervals_streams_other_type():
    assert isvc.sanitize_intervals_streams(None) == {}


def test_map_activity_to_imported_activity():
    imported = isvc.map_activity_to_imported_activity(
        {
            "id": 555,
            "name": "Morning Ride",
            "type": "Ride",
            "start_date_local": "2026-05-05T08:00:00",
            "moving_time": 3600,
            "average_watts": 210,
            "icu_training_load": 75,
            "start_latlng": [52.5, 13.4],
        },
        None,
        {"watts": {"data": [1.0]}},
    )
    assert imported is not None
    assert imported.external_activity_id == "555"
    assert imported.activity_date == "2026-05-05"
    assert imported.sport_type == "Ride"
    assert imported.start_lat == 52.5
    assert imported.summary_avg_power_w == 210


def test_map_activity_returns_none_without_id_or_date():
    assert isvc.map_activity_to_imported_activity({"name": "x"}, None, {}) is None
    assert isvc.map_activity_to_imported_activity({"id": 1}, None, {}) is None  # no date


def test_map_activity_to_ride_input_passthrough():
    assert isvc.map_activity_to_ride_input({"name": "x"}, None, {}) is None
    ride = isvc.map_activity_to_ride_input(
        {"id": 1, "start_date_local": "2026-05-05T08:00:00", "type": "Ride"}, None, {}
    )
    assert isinstance(ride, dict)


def test_apply_summary_fallback():
    metric = {"avg_power_w": None, "normalized_power_w": None, "tss": None}
    ride = {"_summary_avg_power_w": 200, "_summary_np_w": 210, "_summary_tss": 80}
    isvc.apply_summary_fallback(metric, ride)
    assert metric == {"avg_power_w": 200, "normalized_power_w": 210, "tss": 80}
    # existing values are not overwritten
    metric2 = {"avg_power_w": 1, "normalized_power_w": 2, "tss": 3}
    isvc.apply_summary_fallback(metric2, ride)
    assert metric2 == {"avg_power_w": 1, "normalized_power_w": 2, "tss": 3}


# ---------------------------------------------------------------------------
# httpx-backed fetches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_recent_activities_success(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(200, [{"id": 1}]))
    result = await isvc.fetch_recent_activities(
        "key", "athlete1", oldest=date(2026, 5, 1), newest=date(2026, 5, 7)
    )
    assert result == [{"id": 1}]


@pytest.mark.asyncio
async def test_fetch_recent_activities_non_list_payload(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(200, {"not": "a list"}))
    result = await isvc.fetch_recent_activities(
        "key", "a", oldest=date(2026, 5, 1), newest=date(2026, 5, 7)
    )
    assert result == []


@pytest.mark.asyncio
async def test_fetch_recent_activities_auth_error(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(401))
    with pytest.raises(isvc.IntervalsAuthError):
        await isvc.fetch_recent_activities(
            "key", "a", oldest=date(2026, 5, 1), newest=date(2026, 5, 7)
        )


@pytest.mark.asyncio
async def test_fetch_recent_activities_api_error(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(500))
    with pytest.raises(isvc.IntervalsAPIError):
        await isvc.fetch_recent_activities(
            "key", "a", oldest=date(2026, 5, 1), newest=date(2026, 5, 7)
        )


@pytest.mark.asyncio
async def test_fetch_activity_detail_paths(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(200, {"id": 9}))
    assert await isvc.fetch_activity_detail("key", 9) == {"id": 9}

    _patch_client(monkeypatch, _FakeResp(200, ["not a dict"]))
    assert await isvc.fetch_activity_detail("key", 9) == {}

    # Permanent non-success (404) means the activity has no detail — empty dict.
    _patch_client(monkeypatch, _FakeResp(404))
    assert await isvc.fetch_activity_detail("key", 9) == {}

    _patch_client(monkeypatch, _FakeResp(403))
    with pytest.raises(isvc.IntervalsAuthError):
        await isvc.fetch_activity_detail("key", 9)

    # Transient errors must raise so callers can retry (#352).
    for status in (429, 500, 503):
        _patch_client(monkeypatch, _FakeResp(status))
        with pytest.raises(isvc.IntervalsDataUnavailable):
            await isvc.fetch_activity_detail("key", 9)

    _patch_client_raising(monkeypatch, httpx.ConnectTimeout("boom"))
    with pytest.raises(isvc.IntervalsDataUnavailable):
        await isvc.fetch_activity_detail("key", 9)


@pytest.mark.asyncio
async def test_fetch_activity_streams_paths(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(200, {"watts": {"data": [1]}}))
    assert await isvc.fetch_activity_streams("key", 9) == {"watts": {"data": [1]}}

    _patch_client(monkeypatch, _FakeResp(200, "scalar"))
    assert await isvc.fetch_activity_streams("key", 9) == {}

    # A transient 5xx now raises instead of silently returning {} (#352).
    _patch_client(monkeypatch, _FakeResp(500))
    with pytest.raises(isvc.IntervalsDataUnavailable):
        await isvc.fetch_activity_streams("key", 9)

    _patch_client(monkeypatch, _FakeResp(429))
    with pytest.raises(isvc.IntervalsDataUnavailable):
        await isvc.fetch_activity_streams("key", 9)

    # A permanent non-success (404) still returns {} (genuinely no streams).
    _patch_client(monkeypatch, _FakeResp(404))
    assert await isvc.fetch_activity_streams("key", 9) == {}

    _patch_client_raising(monkeypatch, httpx.ReadTimeout("boom"))
    with pytest.raises(isvc.IntervalsDataUnavailable):
        await isvc.fetch_activity_streams("key", 9)

    _patch_client(monkeypatch, _FakeResp(401))
    with pytest.raises(isvc.IntervalsAuthError):
        await isvc.fetch_activity_streams("key", 9)
