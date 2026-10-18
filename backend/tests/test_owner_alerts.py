"""The push to the owner's phone when the server needs them (ai-trainer-ops#124)."""

from __future__ import annotations

import logging

import httpx
import pytest

from config import settings
from services import owner_alerts

URL = "https://ntfy.example/topic-only-the-owner-knows"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(owner_alerts, "_last_sent", {})
    monkeypatch.setattr(settings, "ntfy_url", URL)


def _capture(monkeypatch, handler) -> list[httpx.Request]:
    """Route the module's HTTP calls to ``handler``, keeping every request."""
    requests: list[httpx.Request] = []
    real = httpx.AsyncClient

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        owner_alerts.httpx,
        "AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(answer), **kwargs),
    )
    return requests


@pytest.mark.asyncio
async def test_one_push_per_alert_and_interval(monkeypatch):
    requests = _capture(monkeypatch, lambda _r: httpx.Response(200))
    clock = [1000.0]
    monkeypatch.setattr(owner_alerts.time, "monotonic", lambda: clock[0])

    assert await owner_alerts.notify_once("budget", "AI budget used up", "spent")
    assert not await owner_alerts.notify_once("budget", "AI budget used up", "spent")
    # Another alert is not held back by this one.
    assert await owner_alerts.notify_once("other", "Other", "x")
    # Once the interval has passed, the same alert goes out again.
    clock[0] += owner_alerts.ALERT_INTERVAL_SECONDS
    assert await owner_alerts.notify_once("budget", "AI budget used up", "spent")

    assert len(requests) == 3
    assert str(requests[0].url) == URL
    assert requests[0].headers["Title"] == "AI budget used up"
    assert requests[0].content == b"spent"


@pytest.mark.asyncio
async def test_without_a_topic_nothing_is_sent(monkeypatch):
    requests = _capture(monkeypatch, lambda _r: httpx.Response(200))
    monkeypatch.setattr(settings, "ntfy_url", "")
    assert not await owner_alerts.notify_once("budget", "t", "m")
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        lambda _r: httpx.Response(500),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("down", request=request)),
    ],
)
async def test_a_failed_push_never_raises_and_keeps_the_topic_out_of_the_log(
    monkeypatch, caplog, handler
):
    _capture(monkeypatch, handler)
    with caplog.at_level(logging.WARNING, logger="services.owner_alerts"):
        assert not await owner_alerts.notify_once("budget", "t", "m")
    assert "owner alert not delivered" in caplog.text
    assert "topic-only-the-owner-knows" not in caplog.text
