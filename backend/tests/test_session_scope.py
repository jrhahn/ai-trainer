"""A route's writes are committed before its response is sent (ai-trainer-ops#161)."""

import re
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI

BACKEND = Path(__file__).resolve().parent.parent


# Any Depends(...) whose first argument is get_db, qualified or not, over as
# many lines as it takes (review of #974).
SESSION_DEPENDS = re.compile(r"Depends\(\s*(?:\w+\.)*get_db\b(?P<rest>[^)]*)\)")
FUNCTION_SCOPE = re.compile(r"scope\s*=\s*['\"]function['\"]")


def _unscoped(text: str) -> list[int]:
    """The line of each session dependency in ``text`` that lacks the scope."""
    return [
        text.count("\n", 0, match.start()) + 1
        for match in SESSION_DEPENDS.finditer(text)
        if not FUNCTION_SCOPE.search(match.group("rest"))
    ]


def test_the_guard_sees_every_form_of_the_dependency():
    for form in (
        "Depends(get_db)",
        "Depends(\n    get_db\n)",
        "Depends(database.get_db)",
        "Depends(get_db, use_cache=False)",
    ):
        assert _unscoped(form) == [1], form
    assert _unscoped('Depends(get_db, scope="function")') == []
    assert _unscoped("Depends(\n    get_db,\n    scope='function',\n)") == []


def test_every_route_takes_the_session_with_function_scope():
    bare = [
        f"{path.relative_to(BACKEND)}:{number}"
        for path in BACKEND.rglob("*.py")
        if not {"tests", ".venv", "alembic"} & set(path.relative_to(BACKEND).parts)
        for number in _unscoped(path.read_text(encoding="utf-8"))
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
