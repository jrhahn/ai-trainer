"""Password hashing, the user store and FIT parsing run off the event loop
(ai-trainer-ops#280).

The app runs one uvicorn worker. An Argon2 hash takes 100–300 ms, and parsing a
FIT file is CPU work; done inline in an async route, either one stalled every
other athlete's request while it ran. Each test records whether the slow call
ran on the event loop's thread.
"""

from __future__ import annotations

import io
import threading
import zipfile
from unittest.mock import patch

import pytest

import auth
from services import llm as llm_service
from tests.test_byok import _fake_parsed_fit

PASSWORD = "Str0ng!Pass"


def _recording(fn, on_loop: list[bool]):
    def wrapper(*args, **kwargs):
        on_loop.append(threading.current_thread() is threading.main_thread())
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
    monkeypatch.setattr(auth, "password_matches", _recording(auth.password_matches, on_loop))

    response = await client.post(
        "/api/v1/auth/login", json={"email": "loop-login@example.com", "password": PASSWORD}
    )

    assert response.status_code == 200
    assert on_loop == [False]


@pytest.mark.asyncio
async def test_registration_hashes_off_the_event_loop(client, monkeypatch):
    on_loop: list[bool] = []
    monkeypatch.setattr(auth, "hash_password", _recording(auth.hash_password, on_loop))

    await _register(client, "loop-register@example.com")

    assert on_loop and not any(on_loop)


@pytest.mark.asyncio
async def test_the_password_check_before_a_change_runs_off_the_event_loop(client, monkeypatch):
    headers = await _register(client, "loop-change@example.com")
    on_loop: list[bool] = []
    monkeypatch.setattr(auth, "verify_password", _recording(auth.verify_password, on_loop))

    response = await client.post(
        "/api/v1/auth/password",
        json={"currentPassword": PASSWORD, "newPassword": "An0ther!Strong"},
        headers=headers,
    )

    assert response.status_code < 300, response.text
    assert on_loop and not any(on_loop)


@pytest.mark.asyncio
async def test_a_fit_upload_is_parsed_off_the_event_loop(client, auth_headers):
    on_loop: list[bool] = []
    parsed = _fake_parsed_fit()
    with (
        patch(
            "routers.users._parse_fit_activity",
            side_effect=_recording(lambda *_a, **_k: parsed, on_loop),
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

    unzip_on_loop: list[bool] = []
    parse_on_loop: list[bool] = []
    parsed = _fake_parsed_fit()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ride.fit", b"not a real fit file")

    with (
        patch(
            "routers.users._fit_payloads_from_zip",
            side_effect=_recording(users_router._fit_payloads_from_zip, unzip_on_loop),
        ),
        patch(
            "routers.users._parse_fit_activity",
            side_effect=_recording(lambda *_a, **_k: parsed, parse_on_loop),
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
