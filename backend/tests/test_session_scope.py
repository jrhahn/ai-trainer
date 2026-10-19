"""A route's writes are committed before its response is sent (ai-trainer-ops#161)."""

import re
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI

BACKEND = Path(__file__).resolve().parent.parent


def test_every_route_takes_the_session_with_function_scope():
    bare = [
        f"{path.relative_to(BACKEND)}:{number}"
        for path in BACKEND.rglob("*.py")
        if not {"tests", ".venv", "alembic"} & set(path.relative_to(BACKEND).parts)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"Depends\(\s*get_db\s*\)", line)
    ]

    assert bare == [], f"take the session with scope='function': {bare}"


async def _order(scope: str | None) -> list[str]:
    """What happens first for a route whose session dependency has ``scope``:
    its commit, or the start of its response."""
    events: list[str] = []

    async def session():
        yield "session"
        events.append("commit")

    app = FastAPI()
    marker = Depends(session, scope=scope) if scope else Depends(session)

    @app.post("/write")
    async def write(db=marker):
        return {"ok": True}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            events.append("response")

    scope_dict = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/write",
        "raw_path": b"/write",
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("test", 1),
        "server": ("test", 80),
    }
    await app(scope_dict, receive, send)
    return events


@pytest.mark.asyncio
async def test_the_session_commits_before_the_response_leaves():
    # The behaviour the routes rely on, and the default it replaces.
    assert await _order("function") == ["commit", "response"]
    assert await _order(None) == ["response", "commit"]
