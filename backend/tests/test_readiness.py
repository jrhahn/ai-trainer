"""Readiness reads what login reads, for the external uptime check (ai-trainer-ops#124)."""

import pytest

import main
from services import authelia_store
import config


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Login through the user store, at a path the test controls."""
    path = tmp_path / "users_database.yml"
    monkeypatch.setattr(config, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "AUTHELIA_USERS_DB_PATH", str(path))
    return path


@pytest.mark.asyncio
async def test_ready_when_the_database_and_the_user_store_can_be_read(client, store):
    store.write_text("users: {}\n", encoding="utf-8")

    response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    # Missing, as when a deploy lost it; unparseable, as after a hand edit.
    [None, "users: [unclosed\n"],
)
async def test_not_ready_when_login_cannot_read_the_user_store(client, store, content):
    """Twice every login returned 500 while /healthz stayed green (#684, #696)."""
    if content is not None:
        store.write_text(content, encoding="utf-8")

    response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "failing": ["user_store"]}
    # World-readable: it names the check, not the path.
    assert "users_database" not in response.text
    assert (await client.get("/healthz")).status_code == 200


@pytest.mark.asyncio
async def test_not_ready_when_the_database_cannot_be_read(client, store, monkeypatch):
    store.write_text("users: {}\n", encoding="utf-8")

    class _Broken:
        async def __aenter__(self):
            raise ConnectionError("database down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(main, "async_session_maker", lambda: _Broken())

    response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json()["failing"] == ["database"]


def test_without_the_user_store_login_there_is_nothing_to_read(monkeypatch):
    monkeypatch.setattr(config, "AUTHELIA_AUTH_ENABLED", False)
    monkeypatch.setattr(
        config, "AUTHELIA_USERS_DB_PATH", "/nonexistent/users_database.yml"
    )

    assert authelia_store.store_is_readable() is True
