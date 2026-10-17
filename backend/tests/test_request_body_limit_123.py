"""Streaming request-body cap (ops#123).

The Content-Length middleware cannot catch a chunked / HTTP/2 body (no declared
length), so a 150 MB chunked POST was buffered in full before any handler ran.
_BodyStreamLimitMiddleware counts bytes off the ASGI receive channel and answers
413 before the body reaches the app.

These go through the real app with a chunked (async-generator) body, so they
exercise the path FastAPI takes — which wraps a body-read *exception* as a 400,
the reason the middleware answers 413 itself rather than raising.
"""

import main
from config import settings
from main import _BodyStreamLimitMiddleware

# A public JSON route that reads a request body: the cap must fire before it.
_ROUTE = "/api/v1/auth/login"


async def _chunked(total_bytes: int, chunk: int = 32):
    """An async body → httpx sends it chunked, with no Content-Length."""
    sent = 0
    while sent < total_bytes:
        n = min(chunk, total_bytes - sent)
        yield b"x" * n
        sent += n


async def test_chunked_body_over_the_limit_is_refused_413(client, monkeypatch):
    monkeypatch.setattr(settings, "max_request_body_bytes", 50)
    resp = await client.post(_ROUTE, content=_chunked(500))
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"] == "Request body too large"


async def test_a_body_within_the_limit_reaches_the_route(client, monkeypatch):
    """Under the cap the request is handled normally (bad creds, not 413)."""
    monkeypatch.setattr(settings, "max_request_body_bytes", 5_000)
    resp = await client.post(
        _ROUTE, json={"email": "nobody@example.com", "password": "whatever"}
    )
    assert resp.status_code != 413, resp.text


async def test_the_cap_is_wired_into_the_app_outermost():
    classes = [m.cls for m in main.app.user_middleware]
    assert _BodyStreamLimitMiddleware in classes
    # Added last → outermost, so it wraps receive before the body is read.
    assert classes[0] is _BodyStreamLimitMiddleware
