"""Tests for Authelia-mode registration and login in auth_router.py."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

import auth
from routers.auth_router import _create_authelia_user, _verify_authelia_credentials


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_users_db(path: Path, users: dict | None = None) -> None:
    """Write a minimal Authelia users_database.yml to *path*."""
    data = {"users": users or {}}
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)


# ---------------------------------------------------------------------------
# _create_authelia_user
# ---------------------------------------------------------------------------


def test_create_authelia_user_adds_entry(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path)
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        _create_authelia_user("alice@example.com", "Alice", "Str0ng!Pass")

        with open(db_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        users = data["users"]
        assert "alice@example.com" in users
        entry = users["alice@example.com"]
        assert entry["email"] == "alice@example.com"
        assert entry["displayname"] == "Alice"
        assert entry["disabled"] is False
        assert auth.verify_password("Str0ng!Pass", entry["password"])


def test_create_authelia_user_raises_on_missing_db(monkeypatch):
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", "/nonexistent/path/users_database.yml")
    with pytest.raises(RuntimeError, match="not found"):
        _create_authelia_user("x@example.com", "X", "Str0ng!Pass")


def test_create_authelia_user_raises_on_duplicate_email(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path, users={
            "existing@example.com": {
                "disabled": False,
                "displayname": "Existing",
                "email": "existing@example.com",
                "password": auth.hash_password("pass"),
                "groups": [],
            }
        })
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        with pytest.raises(ValueError, match="already registered"):
            _create_authelia_user("existing@example.com", "New", "password2")


def test_create_authelia_user_overwrites_atomically(monkeypatch):
    """Multiple registrations should all persist (atomic replace)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path)
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        _create_authelia_user("a@example.com", "A", "Str0ng!Pass")
        _create_authelia_user("b@example.com", "B", "password2")

        with open(db_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        assert "a@example.com" in data["users"]
        assert "b@example.com" in data["users"]


# ---------------------------------------------------------------------------
# _verify_authelia_credentials
# ---------------------------------------------------------------------------


def test_verify_authelia_credentials_valid(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        hashed = auth.hash_password("secret123")
        _make_users_db(db_path, users={
            "bob@example.com": {
                "disabled": False,
                "displayname": "Bob",
                "email": "bob@example.com",
                "password": hashed,
                "groups": [],
            }
        })
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        assert _verify_authelia_credentials("bob@example.com", "secret123") is True


def test_verify_authelia_credentials_wrong_password(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path, users={
            "bob@example.com": {
                "disabled": False,
                "displayname": "Bob",
                "email": "bob@example.com",
                "password": auth.hash_password("rightpass"),
                "groups": [],
            }
        })
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        assert _verify_authelia_credentials("bob@example.com", "wrongpass") is False


def test_verify_authelia_credentials_disabled_user(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path, users={
            "disabled@example.com": {
                "disabled": True,
                "displayname": "Disabled",
                "email": "disabled@example.com",
                "password": auth.hash_password("pass"),
                "groups": [],
            }
        })
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        assert _verify_authelia_credentials("disabled@example.com", "pass") is False


def test_verify_authelia_credentials_user_not_found(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path)
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        assert _verify_authelia_credentials("nobody@example.com", "pass") is False


def test_verify_authelia_credentials_missing_db(monkeypatch):
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", "/nonexistent/users_database.yml")
    assert _verify_authelia_credentials("x@example.com", "pass") is False


# ---------------------------------------------------------------------------
# HTTP endpoints — Authelia mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authelia_register_creates_user_and_returns_204(client, monkeypatch):
    """In Authelia mode, register should write the YAML and return 204."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path)
        monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        response = await client.post(
            "/api/v1/auth/register",
            json={
                "name": "Carol",
                "email": "carol@example.com",
                "password": "Str0ng!Pass",
            },
        )
        assert response.status_code == 204


@pytest.mark.asyncio
async def test_authelia_register_duplicate_returns_409(client, monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path)
        monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        payload = {
            "name": "Dave",
            "email": "dave@example.com",
            "password": "Str0ng!Pass",
        }
        # Register once
        await client.post("/api/v1/auth/register", json=payload)
        # Register again — should fail with 409
        duplicate = await client.post("/api/v1/auth/register", json=payload)
        assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_authelia_register_missing_db_returns_503(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", "/nonexistent/users_database.yml")

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Eve",
            "email": "eve@example.com",
            "password": "Str0ng!Pass",
        },
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_authelia_login_valid_credentials(client, monkeypatch):
    """Login should succeed when credentials match the Authelia users database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path)
        monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        # Register
        await client.post(
            "/api/v1/auth/register",
            json={
                "name": "Frank",
                "email": "frank@example.com",
                "password": "Str0ng!Pass",
            },
        )

        # Login via authelia mode (reads YAML directly)
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "frank@example.com", "password": "Str0ng!Pass"},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()


@pytest.mark.asyncio
async def test_authelia_login_wrong_password_returns_401(client, monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path)
        monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        await client.post(
            "/api/v1/auth/register",
            json={"name": "Grace", "email": "grace@example.com", "password": "Str0ng!Pass"},
        )

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "grace@example.com", "password": "wrongpass"},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_authelia_login_auto_creates_db_user(client, monkeypatch):
    """Authelia login for a user not in the app DB should auto-create the user."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        hashed = auth.hash_password("Str0ng!Pass")
        _make_users_db(db_path, users={
            "henry@example.com": {
                "disabled": False,
                "displayname": "Henry",
                "email": "henry@example.com",
                "password": hashed,
                "groups": [],
            }
        })
        monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "henry@example.com", "password": "Str0ng!Pass"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

        me = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200


@pytest.mark.asyncio
async def test_session_token_returns_401_when_authelia_disabled(client, monkeypatch):
    """GET /auth/session should return 401 when Authelia is not enabled."""
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", False)

    response = await client.get("/api/v1/auth/session")
    assert response.status_code == 401
