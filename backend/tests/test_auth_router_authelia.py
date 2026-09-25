"""Tests for Authelia-mode registration and login in auth_router.py."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest
import yaml
from argon2 import PasswordHasher

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


def test_verify_authelia_credentials_argon2_valid(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path, users={
            "argon@example.com": {
                "disabled": False,
                "displayname": "Argon",
                "email": "argon@example.com",
                "password": PasswordHasher().hash("Str0ng!Pass"),
                "groups": [],
            }
        })
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        assert _verify_authelia_credentials("argon@example.com", "Str0ng!Pass") is True


def test_verify_authelia_credentials_malformed_argon2_hash_returns_false(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "users_database.yml"
        _make_users_db(db_path, users={
            "argon@example.com": {
                "disabled": False,
                "displayname": "Argon",
                "email": "argon@example.com",
                "password": "$argon2id$v=19$m=65536,t=3,p=4$abc$def",
                "groups": [],
            }
        })
        monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

        assert _verify_authelia_credentials("argon@example.com", "Str0ng!Pass") is False


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


# ---------------------------------------------------------------------------
# warn_if_authelia_user_store_unwritable (#682)
#
# The safety net for the non-root container: registration writes
# users_database.yml on a bind mount whose ownership comes from the host, so a
# uid mismatch turns every sign-up into a 500. Checked once at boot so the
# operator learns it from the logs rather than from a user who cannot register.
#
# os.access is monkeypatched rather than driven through real file modes,
# because a suite running as root would find every path writable and the
# branch under test would never be reached.
# ---------------------------------------------------------------------------


def test_no_warning_when_authelia_is_disabled(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(tmp_path / "users.yml"))

    with caplog.at_level("WARNING"):
        auth.warn_if_authelia_user_store_unwritable()

    assert caplog.records == []


def test_no_warning_when_no_user_store_is_configured(monkeypatch, caplog):
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", "")

    with caplog.at_level("WARNING"):
        auth.warn_if_authelia_user_store_unwritable()

    assert caplog.records == []


def test_warns_when_the_user_store_is_missing(monkeypatch, caplog, tmp_path):
    missing = tmp_path / "nope" / "users_database.yml"
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(missing))

    with caplog.at_level("WARNING"):
        auth.warn_if_authelia_user_store_unwritable()

    assert len(caplog.records) == 1
    assert "does not exist" in caplog.records[0].getMessage()


def test_warns_when_the_user_store_is_not_writable(monkeypatch, caplog, tmp_path):
    store = tmp_path / "users_database.yml"
    _make_users_db(store)
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(store))
    monkeypatch.setattr(auth.os, "access", lambda *_args, **_kwargs: False)

    with caplog.at_level("WARNING"):
        auth.warn_if_authelia_user_store_unwritable()

    assert len(caplog.records) == 1
    assert "not writable" in caplog.records[0].getMessage()


def test_warns_when_only_the_directory_is_unwritable(monkeypatch, caplog, tmp_path):
    """The write is create-and-rename, so the *directory* has to be writable too.

    A file the process can write, in a directory it cannot, still fails: the
    temp file next to the target cannot be created. Checking only the file
    would report healthy and let registration break anyway.
    """
    store = tmp_path / "users_database.yml"
    _make_users_db(store)
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(store))
    monkeypatch.setattr(
        auth.os, "access", lambda path, _mode: Path(path) != store.parent
    )

    with caplog.at_level("WARNING"):
        auth.warn_if_authelia_user_store_unwritable()

    assert len(caplog.records) == 1
    assert "not writable" in caplog.records[0].getMessage()


def test_silent_when_the_user_store_is_writable(monkeypatch, caplog, tmp_path):
    store = tmp_path / "users_database.yml"
    _make_users_db(store)
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(store))
    monkeypatch.setattr(auth.os, "access", lambda *_args, **_kwargs: True)

    with caplog.at_level("WARNING"):
        auth.warn_if_authelia_user_store_unwritable()

    assert caplog.records == []


# ---------------------------------------------------------------------------
# Atomic write keeps the file's identity (#684)
#
# os.replace swaps in a new inode, so an atomic write re-owns the file to
# whoever ran it and resets the mode to mkstemp's 0600 unless something carries
# the original across. That is not cosmetic: the backend reads this file on
# every login, so a store it can no longer read is a total auth outage.
# ---------------------------------------------------------------------------


def test_registration_preserves_the_store_mode(monkeypatch, tmp_path):
    store = tmp_path / "users_database.yml"
    _make_users_db(store)
    os.chmod(store, 0o640)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(store))

    _create_authelia_user("rider@example.com", "Rider", "Str0ng!Pass")

    assert stat.S_IMODE(store.stat().st_mode) == 0o640


def test_registration_does_not_widen_a_locked_down_store(monkeypatch, tmp_path):
    """The file holds password hashes; an atomic write must not loosen it."""
    store = tmp_path / "users_database.yml"
    _make_users_db(store)
    os.chmod(store, 0o600)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(store))

    _create_authelia_user("rider@example.com", "Rider", "Str0ng!Pass")

    assert stat.S_IMODE(store.stat().st_mode) == 0o600


def test_the_new_user_is_actually_written(monkeypatch, tmp_path):
    """Guards the guard: preserving the mode must not cost the write itself."""
    store = tmp_path / "users_database.yml"
    _make_users_db(store)
    os.chmod(store, 0o640)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(store))

    _create_authelia_user("rider@example.com", "Rider", "Str0ng!Pass")

    written = yaml.safe_load(store.read_text())
    assert "rider@example.com" in written["users"]
    assert stat.S_IMODE(store.stat().st_mode) == 0o640


def test_a_failed_chown_does_not_fail_the_registration(monkeypatch, tmp_path):
    """chown needs privilege the container deliberately no longer has.

    Refusing to register over it would trade a cosmetic problem for an outage,
    so ownership is best-effort while the mode is not.
    """
    store = tmp_path / "users_database.yml"
    _make_users_db(store)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(store))

    def _denied(*_args, **_kwargs):
        raise PermissionError("not permitted")

    monkeypatch.setattr(os, "chown", _denied)
    # Force the chown branch: pretend the file belongs to somebody else.
    real_stat = Path.stat

    def _foreign_owner(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == store:
            return os.stat_result(
                tuple(result)[:4] + (result.st_uid + 1, result.st_gid + 1) + tuple(result)[6:]
            )
        return result

    monkeypatch.setattr(Path, "stat", _foreign_owner)

    _create_authelia_user("rider@example.com", "Rider", "Str0ng!Pass")

    written = yaml.safe_load(store.read_text())
    assert "rider@example.com" in written["users"]


# ---------------------------------------------------------------------------
# An unreadable user store is an operator problem, not a wrong password (#696)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_reports_503_when_the_user_store_cannot_be_read(
    client, monkeypatch, tmp_path
):
    """Twice now the store has become root-owned while the backend runs as 1001.

    #684 was a deploy doing it, #696 was the Authelia container's entrypoint
    chowning the shared mount on every restart. Both surfaced as a raw
    ``PermissionError`` traceback and a 500 on every login — accurate and
    useless. A 503 with the cause in the log says what to go and fix.

    Explicitly *not* a 401: reporting "invalid credentials" for a file the
    process cannot open would send the operator looking at passwords.
    """
    store = tmp_path / "users_database.yml"
    _make_users_db(store, {"rider@example.com": {"email": "rider@example.com"}})
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(store))

    real_open = open

    def _denied(path, *args, **kwargs):
        if str(path) == str(store):
            raise PermissionError(13, "Permission denied", str(store))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("routers.auth_router.open", _denied, raising=False)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "rider@example.com", "password": "whatever"},
    )

    assert resp.status_code == 503, resp.text
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_a_missing_store_is_still_a_plain_auth_failure(
    client, monkeypatch, tmp_path
):
    """Absent is different from unreadable, and must stay a 401.

    A deployment that has not been set up yet should not report itself as
    broken to every visitor.
    """
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(tmp_path / "absent.yml"))

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "rider@example.com", "password": "whatever"},
    )

    assert resp.status_code == 401
