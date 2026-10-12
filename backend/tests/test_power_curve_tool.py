"""The public FIT tool (ai-trainer-ops#25): no account, no storage, no model call."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from config import settings
from routers import tools
from services.power_curve import fit_critical_power, power_duration

URL = "/api/v1/tools/power-curve"
CP, W_PRIME = 250, 20_000


def test_exact_model_points_give_back_the_model_with_no_band():
    points = [
        {"seconds": t, "watts": CP + W_PRIME / t} for t in (120, 180, 300, 600, 1200)
    ]
    fit = fit_critical_power(points)
    assert fit["cp"] == pytest.approx(CP)
    assert fit["w_prime"] == pytest.approx(W_PRIME)
    assert fit["var_cp"] == pytest.approx(0, abs=1e-9)


def test_a_flat_ride_gets_no_model():
    """Steady riding describes pacing, not capacity."""
    watts = [200.0] * 3600
    result = power_duration(watts, [float(i) for i in range(3600)])
    assert result["model"] is None
    assert result["curve"] == []
    assert {p["seconds"] for p in result["points"]} >= {60, 1200, 3600}


def _ride() -> list[float]:
    """Hard efforts at the model's power for 3, 5, 12 and 20 minutes."""
    watts: list[float] = []
    for seconds, power in (
        (600, 150), (180, CP + W_PRIME / 180), (600, 120), (300, CP + W_PRIME / 300),
        (600, 120), (720, CP + W_PRIME / 720), (600, 120), (1200, CP + W_PRIME / 1200),
        (600, 150),
    ):
        watts += [power] * seconds
    return watts


def test_a_ride_with_hard_efforts_gets_a_banded_model():
    watts = _ride()
    result = power_duration(watts, [float(i) for i in range(len(watts))])
    model = result["model"]
    assert model is not None
    assert model["cp_low"] <= model["cp"] <= model["cp_high"]
    assert 230 <= model["cp"] <= 270
    assert all(c["low"] <= c["watts"] <= c["high"] for c in result["curve"])


class _Field:
    def __init__(self, name, value):
        self.name, self.value = name, value


class _Record:
    def __init__(self, fields):
        self._fields = fields

    def __iter__(self):
        return iter(self._fields)


class _FitFile:
    """Stands in for fitparse with the hard-effort ride above."""

    def __init__(self, raw_stream, *args, **kwargs):
        self.raw = raw_stream.read()

    def parse(self):
        if self.raw == b"invalid":
            raise RuntimeError("invalid FIT")

    def get_messages(self, msg_type):
        start = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
        if msg_type == "session":
            return [_Record([_Field("sport", "cycling"), _Field("start_time", start)])]
        if msg_type == "record" and self.raw == b"ride":
            return [
                _Record([_Field("timestamp", start + timedelta(seconds=i)),
                         _Field("power", round(w))])
                for i, w in enumerate(_ride())
            ]
        return []


@pytest.fixture(autouse=True)
def fake_fitparse(monkeypatch):
    import fitparse

    monkeypatch.setattr(fitparse, "FitFile", _FitFile)
    tools.power_curve_limiter.reset()
    yield
    tools.power_curve_limiter.reset()


@pytest.fixture
def no_model_calls(monkeypatch):
    """The tool must cost nothing: any provider lookup fails the test."""
    from services import llm

    def refuse(*args, **kwargs):
        raise AssertionError("the public tool reached a model provider")

    monkeypatch.setattr(llm, "get_provider", refuse)


async def _post(client: AsyncClient, name: str, data: bytes):
    return await client.post(URL, files={"file": (name, data, "application/octet-stream")})


@pytest.mark.asyncio
async def test_anyone_gets_the_curve_without_an_account(client, no_model_calls):
    response = await _post(client, "ride.fit", b"ride")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"]["cp"] > 0
    assert body["curve"] and body["points"]


@pytest.mark.asyncio
async def test_a_file_without_power_is_refused_with_the_reason(client):
    response = await _post(client, "run.fit", b"no-power")

    assert response.status_code == 422
    assert "power" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_broken_file_and_a_wrong_type_are_refused(client):
    assert (await _post(client, "bad.fit", b"invalid")).status_code == 422
    assert (await _post(client, "ride.gpx", b"ride")).status_code == 422


@pytest.mark.asyncio
async def test_the_tool_is_rate_limited_deployment_wide(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "power_curve_rate_limit_burst", 2)

    assert (await _post(client, "a.fit", b"no-power")).status_code == 422
    assert (await _post(client, "b.fit", b"no-power")).status_code == 422
    third = await _post(client, "c.fit", b"ride")

    assert third.status_code == 429
    assert "Retry-After" in third.headers
