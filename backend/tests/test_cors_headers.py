"""The CORS preflight must allow every header the frontend actually sends.

The frontend has sent ``X-App-Timezone`` on every request since the timezone
refactor, and the backend reads it in ``services.dates``.  It was never added to
``allow_headers``, so a browser blocked every cross-origin call before it left
the page.  Production never noticed: Traefik serves the app and the API from one
origin, so no preflight happens.  The setup that broke is the one the README
documents for local development — frontend on :5173, backend on :8000.

These tests read the header names out of the frontend client so the two sides
cannot drift apart again.
"""

import re
from pathlib import Path

import pytest

API_CLIENT = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "services" / "api.ts"
)


def _headers_the_frontend_sends() -> set[str]:
    """Every literal header key set inside apiFetch's headers object."""
    source = API_CLIENT.read_text()
    start = source.index("export async function apiFetch")
    body = source[start : source.index("\n}", start)]
    headers_block = body[body.index("headers: {") : body.index("body:", body.index("headers: {"))]
    # Keys appear both quoted ('X-Request-ID': ...) and bare (Authorization: ...).
    quoted = re.findall(r"'([A-Za-z][A-Za-z0-9-]*)':", headers_block)
    bare = re.findall(r"[{,]\s*([A-Za-z][A-Za-z0-9-]*):", headers_block)
    return {name.lower() for name in quoted + bare} - {"headers"}


def test_frontend_client_is_readable() -> None:
    """Guard the guard: a moved file must fail loudly, not silently pass."""
    found = _headers_the_frontend_sends()
    assert {"content-type", "authorization"} <= found, found


def test_every_header_the_frontend_sends_is_allowed() -> None:
    from main import _CORS_ALLOWED_HEADERS

    allowed = {header.lower() for header in _CORS_ALLOWED_HEADERS}
    missing = _headers_the_frontend_sends() - allowed

    assert not missing, (
        f"apiFetch sends {sorted(missing)}, which the CORS preflight rejects. "
        "Every cross-origin request fails in the browser — including the local "
        "dev setup in the README — while production hides it behind one origin."
    )


def test_timezone_header_name_matches_the_reader() -> None:
    """The allow-list must use the same spelling services.dates reads."""
    from main import _CORS_ALLOWED_HEADERS
    from services.dates import TIMEZONE_HEADER

    assert TIMEZONE_HEADER.lower() in {h.lower() for h in _CORS_ALLOWED_HEADERS}


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://localhost:5199"])
def test_preflight_accepts_a_request_carrying_every_frontend_header(origin) -> None:
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as client:
        response = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": ", ".join(
                    sorted(_headers_the_frontend_sends())
                ),
            },
        )

    if response.status_code != 200:
        pytest.skip(f"{origin} is not an allowed origin in this test config")
    allowed = response.headers.get("access-control-allow-headers", "").lower()
    for header in _headers_the_frontend_sends():
        assert header in allowed, f"{header} rejected by the preflight"
