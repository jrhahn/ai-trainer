"""Password hashing, the user store and FIT parsing run off the event loop
(ai-trainer-ops#280).

The app runs one uvicorn worker. An Argon2 hash takes 100–300 ms, and parsing a
FIT file is CPU work; done inline in an async route, either one stalled every
other athlete's request while it ran. Each test records, for every slow call,
whether it ran on the thread of the event loop serving the request.
"""

from __future__ import annotations

import io
import threading
import zipfile
from unittest.mock import patch

import bcrypt
import pyotp
import pytest
from sqlalchemy import update

import auth
import models
from routers import dependencies as limits
from services import authelia_store
from services import llm as llm_service
from services import totp as totp_service
from tests.conftest import TestSessionLocal
from tests.test_byok import _fake_parsed_fit

PASSWORD = "Str0ng!Pass"


@pytest.fixture(autouse=True)
def _limits():
    totp_service.reset_challenge_guard()
    limits.totp_code_limiter.reset()
    limits.login_limiter.reset()
    yield
    totp_service.reset_challenge_guard()
    limits.totp_code_limiter.reset()
    limits.login_limiter.reset()


def _recording(fn, on_loop: list[bool], loop_thread: int):
    def wrapper(*args, **kwargs):
        on_loop.append(threading.get_ident() == loop_thread)
        return fn(*args, **kwargs)

    return wrapper


def _no_model(*_args, **_kwargs):
    raise llm_service.AIKeyNotConfiguredError("the parse is the point, not the coach")


async def _register(client, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"name": "Loop Rider", "email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_login_checks_the_password_off_the_event_loop(client, monkeypatch):
    await _register(client, "loop-login@example.com")
    on_loop: list[bool] = []
    monkeypatch.setattr(
        auth, "password_matches", _recording(auth.password_matches, on_loop, threading.get_ident())
    )

    response = await client.post(
        "/api/v1/auth/login", json={"email": "loop-login@example.com", "password": PASSWORD}
    )

    assert response.status_code == 200
    assert on_loop == [False]


@pytest.mark.asyncio
async def test_a_login_that_upgrades_an_old_hash_rehashes_off_the_event_loop(client, monkeypatch):
    await _register(client, "loop-rehash@example.com")
    legacy = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
    async with TestSessionLocal() as db:
        await db.execute(
            update(models.User)
            .where(models.User.email == "loop-rehash@example.com")
            .values(hashed_password=legacy)
        )
        await db.commit()
    on_loop: list[bool] = []
    monkeypatch.setattr(
        auth, "hash_password", _recording(auth.hash_password, on_loop, threading.get_ident())
    )

    response = await client.post(
        "/api/v1/auth/login", json={"email": "loop-rehash@example.com", "password": PASSWORD}
    )

    assert response.status_code == 200
    assert on_loop == [False]


@pytest.mark.asyncio
async def test_registration_hashes_off_the_event_loop(client, monkeypatch):
    on_loop: list[bool] = []
    monkeypatch.setattr(
        auth, "hash_password", _recording(auth.hash_password, on_loop, threading.get_ident())
    )

    await _register(client, "loop-register@example.com")

    assert on_loop and not any(on_loop)


@pytest.mark.asyncio
async def test_a_password_change_checks_and_hashes_off_the_event_loop(client, monkeypatch):
    headers = await _register(client, "loop-change@example.com")
    loop_thread = threading.get_ident()
    checked: list[bool] = []
    hashed: list[bool] = []
    monkeypatch.setattr(auth, "verify_password", _recording(auth.verify_password, checked, loop_thread))
    monkeypatch.setattr(auth, "hash_password", _recording(auth.hash_password, hashed, loop_thread))

    response = await client.post(
        "/api/v1/auth/password",
        json={"currentPassword": PASSWORD, "newPassword": "An0ther!Strong"},
        headers=headers,
    )

    assert response.status_code < 300, response.text
    assert checked and not any(checked)
    assert hashed and not any(hashed)


@pytest.mark.asyncio
async def test_two_factor_codes_are_hashed_and_checked_off_the_event_loop(
    client, auth_headers, monkeypatch
):
    loop_thread = threading.get_ident()
    enroll = await client.post("/api/v1/auth/totp/enroll", headers=auth_headers)
    secret = enroll.json()["secret"].replace(" ", "")
    hashed: list[bool] = []
    monkeypatch.setattr(auth, "hash_password", _recording(auth.hash_password, hashed, loop_thread))
    confirm = await client.post(
        "/api/v1/auth/totp/confirm", headers=auth_headers, json={"code": pyotp.TOTP(secret).now()}
    )
    assert confirm.status_code == 200, confirm.text
    assert len(hashed) == totp_service.RECOVERY_CODE_COUNT and not any(hashed)

    checked: list[bool] = []
    monkeypatch.setattr(auth, "verify_password", _recording(auth.verify_password, checked, loop_thread))
    challenge = (
        await client.post("/api/v1/auth/login", json={"email": "rider@example.com", "password": PASSWORD})
    ).json()["challenge"]
    signed_in = await client.post(
        "/api/v1/auth/login/totp",
        json={"challenge": challenge, "code": confirm.json()["recoveryCodes"][-1]},
    )

    assert signed_in.status_code == 200, signed_in.text
    assert checked and not any(checked)


@pytest.mark.asyncio
async def test_deleting_the_account_removes_the_store_entry_off_the_event_loop(client, monkeypatch):
    headers = await _register(client, "loop-delete@example.com")
    on_loop: list[bool] = []
    monkeypatch.setattr(
        authelia_store, "delete_user", _recording(lambda _email: True, on_loop, threading.get_ident())
    )

    response = await client.request(
        "DELETE", "/api/v1/users/me", headers=headers, json={"password": PASSWORD}
    )

    assert response.status_code == 200, response.text
    assert on_loop == [False]


@pytest.mark.asyncio
async def test_a_fit_upload_is_parsed_off_the_event_loop(client, auth_headers):
    on_loop: list[bool] = []
    parsed = _fake_parsed_fit()
    with (
        patch(
            "routers.users._parse_fit_activity",
            side_effect=_recording(lambda *_a, **_k: parsed, on_loop, threading.get_ident()),
        ),
        patch("services.ai_service.get_provider", side_effect=_no_model),
    ):
        await client.post(
            "/api/v1/users/me/upload-fit",
            headers=auth_headers,
            files={"file": ("ride.fit", b"not a real fit file", "application/octet-stream")},
        )

    assert on_loop == [False]


@pytest.mark.asyncio
async def test_a_zip_is_unpacked_and_parsed_off_the_event_loop(client, auth_headers):
    import routers.users as users_router

    loop_thread = threading.get_ident()
    unzip_on_loop: list[bool] = []
    parse_on_loop: list[bool] = []
    parsed = _fake_parsed_fit()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ride.fit", b"not a real fit file")

    with (
        patch(
            "routers.users._fit_payloads_from_zip",
            side_effect=_recording(users_router._fit_payloads_from_zip, unzip_on_loop, loop_thread),
        ),
        patch(
            "routers.users._parse_fit_activity",
            side_effect=_recording(lambda *_a, **_k: parsed, parse_on_loop, loop_thread),
        ),
        patch("services.ai_service.get_provider", side_effect=_no_model),
    ):
        response = await client.post(
            "/api/v1/users/me/upload-fit/bulk",
            headers=auth_headers,
            files=[("files", ("rides.zip", buffer.getvalue(), "application/zip"))],
        )

    assert response.status_code == 200, response.text
    assert unzip_on_loop == [False]
    assert parse_on_loop == [False]
