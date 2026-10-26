"""Changing the password while signed in (ai-trainer-ops#172).

Both modes: the hash in ``users.hashed_password``, and the Authelia store,
which is what login checks in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from config import settings
from routers import dependencies as deps
from services import authelia_store
from domain import passwords
import config

EMAIL = "rider@example.com"
OLD_PASSWORD = "Str0ng!Pass"
NEW_PASSWORD = "gravel climbs at dawn"


@pytest.fixture(autouse=True)
def fresh_login_limiter():
    deps.login_limiter.reset()
    yield
    deps.login_limiter.reset()


@pytest.fixture(params=["hash", "store"])
def mode(request, monkeypatch, tmp_path: Path) -> str:
    """Run each test against the column and against the Authelia store."""
    if request.param == "store":
        db_path = tmp_path / "users_database.yml"
        db_path.write_text(
            yaml.dump(
                {
                    "users": {
                        EMAIL: {
                            "email": EMAIL,
                            "password": passwords.hash_password(OLD_PASSWORD),
                        }
                    }
                }
            )
        )
        monkeypatch.setattr(config, "AUTHELIA_AUTH_ENABLED", True)
        monkeypatch.setattr(config, "AUTHELIA_USERS_DB_PATH", str(db_path))
    return request.param


async def _change(
    client, headers, current: str = OLD_PASSWORD, new: str = NEW_PASSWORD
):
    return await client.post(
        "/api/v1/auth/password",
        json={"currentPassword": current, "newPassword": new},
        headers=headers,
    )


async def _login(client, password: str):
    return await client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": password}
    )


async def _signed_in(client, headers) -> bool:
    return (await client.get("/api/v1/users/me", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_the_new_password_signs_in_the_old_does_not_and_every_session_ends(
    client, auth_headers, mode
):
    # A second session, as on another device.
    other = {
        "Authorization": f"Bearer {(await _login(client, OLD_PASSWORD)).json()['access_token']}"
    }

    assert (await _change(client, auth_headers)).status_code == 204

    assert not await _signed_in(client, auth_headers)
    assert not await _signed_in(client, other)
    assert (await _login(client, OLD_PASSWORD)).status_code == 401
    assert (await _login(client, NEW_PASSWORD)).status_code == 200
    if mode == "store":
        assert authelia_store.verify_credentials(EMAIL, NEW_PASSWORD)


@pytest.mark.asyncio
async def test_a_wrong_current_password_is_403_and_keeps_everything(
    client, auth_headers, mode
):
    resp = await _change(client, auth_headers, current="not my password")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Incorrect password."
    # 403 and not 401: the session is fine, so the app does not sign out (#131).
    assert await _signed_in(client, auth_headers)
    assert (await _login(client, OLD_PASSWORD)).status_code == 200
    assert (await _login(client, NEW_PASSWORD)).status_code == 401


@pytest.mark.asyncio
async def test_a_weak_new_password_is_400_with_the_rule_and_keeps_everything(
    client, auth_headers, mode
):
    resp = await _change(client, auth_headers, new="short")

    assert resp.status_code == 400
    assert "characters" in resp.json()["detail"]
    assert await _signed_in(client, auth_headers)
    assert (await _login(client, OLD_PASSWORD)).status_code == 200


@pytest.mark.asyncio
async def test_a_new_password_built_from_the_address_is_refused(
    client, auth_headers, mode
):
    resp = await _change(client, auth_headers, new="rider-example-rider-example")

    assert resp.status_code == 400
    assert (await _login(client, OLD_PASSWORD)).status_code == 200


@pytest.mark.asyncio
async def test_guessing_the_current_password_is_rate_limited(
    client, auth_headers, mode, monkeypatch
):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "login_rate_limit_attempts", 3)
    monkeypatch.setattr(settings, "login_rate_limit_seconds", 300)
    monkeypatch.setattr(settings, "login_rate_limit_global_attempts", 1000)

    statuses = [
        (await _change(client, auth_headers, current=f"guess {i}")).status_code
        for i in range(4)
    ]

    assert statuses == [403, 403, 403, 429], statuses
    # Limited by account, as a login is: the right password is refused too
    # until the window passes, so guessing gains nothing.
    assert (await _change(client, auth_headers)).status_code == 429


@pytest.mark.asyncio
async def test_it_needs_a_session(client):
    resp = await client.post(
        "/api/v1/auth/password",
        json={"currentPassword": OLD_PASSWORD, "newPassword": NEW_PASSWORD},
    )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_megabyte_does_not_reach_the_hash(client, auth_headers):
    assert (await _change(client, auth_headers, current="x" * 201)).status_code == 422
    assert (await _change(client, auth_headers, new="x" * 201)).status_code == 422


@pytest.mark.asyncio
async def test_a_store_entry_lost_after_the_check_is_a_server_fault(
    client, auth_headers, monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "users_database.yml"
    db_path.write_text(
        yaml.dump(
            {
                "users": {
                    EMAIL: {
                        "email": EMAIL,
                        "password": passwords.hash_password(OLD_PASSWORD),
                    }
                }
            }
        )
    )
    monkeypatch.setattr(config, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "AUTHELIA_USERS_DB_PATH", str(db_path))
    monkeypatch.setattr(authelia_store, "set_password", lambda email, password: False)

    resp = await _change(client, auth_headers)

    assert resp.status_code == 503
    # Nothing ended: the sessions are revoked only after the password is set.
    assert await _signed_in(client, auth_headers)


@pytest.mark.asyncio
async def test_a_store_that_cannot_be_written_is_503(
    client, auth_headers, monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "users_database.yml"
    db_path.write_text(
        yaml.dump(
            {
                "users": {
                    EMAIL: {
                        "email": EMAIL,
                        "password": passwords.hash_password(OLD_PASSWORD),
                    }
                }
            }
        )
    )
    monkeypatch.setattr(config, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "AUTHELIA_USERS_DB_PATH", str(db_path))

    def broken(email, password):
        raise RuntimeError("The user store could not be written.")

    monkeypatch.setattr(authelia_store, "set_password", broken)

    resp = await _change(client, auth_headers)

    assert resp.status_code == 503
    assert resp.json()["detail"] == "The user store could not be written."
    assert await _signed_in(client, auth_headers)
