"""Streaming request-body cap (ops#123).

The Content-Length middleware cannot catch a chunked / HTTP/2 body (no declared
length), so a 150 MB chunked POST was buffered in full before any handler ran.
_BodyStreamLimitMiddleware counts bytes off the ASGI receive channel and answers
413 before the body reaches the app. Driven at the ASGI layer directly so the
cap can be tested at a small limit without pushing 64 MB through a client.
"""

import main
from config import settings
from main import _BodyStreamLimitMiddleware


async def _collect(scope, chunks, *, max_bytes):
    """Run the middleware over *chunks* and return the sent ASGI messages."""
    sent: list[dict] = []
    it = iter(chunks)

    async def receive():
        return next(it)

    async def send(message):
        sent.append(message)

    async def inner(scope, receive, send):
        # A route that reads the whole body, then answers 200 — the shape the
        # middleware must cut short before the body is fully read.
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = _BodyStreamLimitMiddleware(inner, max_bytes=max_bytes)
    await mw(scope, receive, send)
    return sent


def _status(sent):
    starts = [m for m in sent if m["type"] == "http.response.start"]
    return starts[0]["status"] if starts else None


async def test_chunked_body_over_the_limit_is_refused_413():
    scope = {"type": "http", "method": "POST", "path": "/api/v1/users/me/chat"}
    chunks = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"x" * 60, "more_body": False},
    ]
    sent = await _collect(scope, chunks, max_bytes=100)
    assert _status(sent) == 413


async def test_body_within_the_limit_passes_through():
    scope = {"type": "http", "method": "POST", "path": "/api/v1/users/me/chat"}
    chunks = [
        {"type": "http.request", "body": b"x" * 40, "more_body": True},
        {"type": "http.request", "body": b"x" * 40, "more_body": False},
    ]
    sent = await _collect(scope, chunks, max_bytes=100)
    assert _status(sent) == 200


async def test_the_cap_is_wired_into_the_app_at_the_configured_limit():
    assert any(
        m.cls is _BodyStreamLimitMiddleware for m in main.app.user_middleware
    )
    # The backstop tracks the same setting as the Content-Length check.
    assert settings.max_request_body_bytes == 64 * 1024 * 1024
