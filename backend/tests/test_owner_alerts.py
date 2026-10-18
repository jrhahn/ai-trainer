"""The push to the owner's phone when the server needs them (ai-trainer-ops#124)."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from config import settings
from services import owner_alerts

URL = "https://ntfy.example/topic-only-the-owner-knows"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(owner_alerts, "_next_allowed", {})
    monkeypatch.setattr(settings, "ntfy_url", URL)


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(owner_alerts.time, "monotonic", lambda: now[0])
    return now


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
async def test_one_push_per_alert_and_interval(monkeypatch, clock):
    requests = _capture(monkeypatch, lambda _r: httpx.Response(200))

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
    monkeypatch, caplog, clock, handler
):
    requests = _capture(monkeypatch, handler)
    with caplog.at_level(logging.WARNING, logger="services.owner_alerts"):
        assert not await owner_alerts.notify_once("budget", "t", "m")
    assert "owner alert not delivered" in caplog.text
    assert "topic-only-the-owner-knows" not in caplog.text

    # A failure holds the alert back minutes, not hours (review of #963).
    clock[0] += owner_alerts.RETRY_AFTER_SECONDS - 1
    await owner_alerts.notify_once("budget", "t", "m")
    assert len(requests) == 1
    clock[0] += 1
    await owner_alerts.notify_once("budget", "t", "m")
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_a_cancelled_push_holds_the_alert_back_minutes_only(monkeypatch, clock):
    """A request cancelled mid-send (the client went away) sent nothing."""

    def first_cancelled(request: httpx.Request) -> httpx.Response:
        if len(requests) == 1:
            raise asyncio.CancelledError
        return httpx.Response(200)

    requests = _capture(monkeypatch, first_cancelled)
    with pytest.raises(asyncio.CancelledError):
        await owner_alerts.notify_once("budget", "t", "m")
    clock[0] += owner_alerts.RETRY_AFTER_SECONDS
    assert await owner_alerts.notify_once("budget", "t", "m")
    assert len(requests) == 2


def test_httpx_never_logs_the_topic(caplog):
    """httpx logs every request URL at INFO, and production logs INFO."""
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info('HTTP Request: POST %s "HTTP/1.1 200 OK"', URL)
        logging.getLogger("httpx").info('HTTP Request: GET %s "HTTP/1.1 200 OK"', "https://x.example/")
    assert "topic-only-the-owner-knows" not in caplog.text
    assert "https://x.example/" in caplog.text


def test_httpx_never_logs_the_topic_in_its_normalized_form(monkeypatch, caplog):
    """httpx lowercases the host and percent-encodes before it logs."""
    monkeypatch.setattr(settings, "ntfy_url", "https://NTFY.Example/Secret Topic")
    logged = str(httpx.URL(settings.ntfy_url))
    assert logged != settings.ntfy_url
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info('HTTP Request: POST %s "HTTP/1.1 200 OK"', logged)
    assert "Secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["not a url\n", "ntfy.example/topic"])
async def test_a_malformed_topic_url_never_raises(monkeypatch, caplog, url):
    """httpx.InvalidURL is no httpx.HTTPError; a 402 must not become a 500."""
    monkeypatch.setattr(settings, "ntfy_url", url)
    with caplog.at_level(logging.WARNING, logger="services.owner_alerts"):
        assert not await owner_alerts.notify_once("budget", "t", "m")
    assert "owner alert not delivered" in caplog.text
