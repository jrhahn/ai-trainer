"""Password reset by email (ai-trainer-ops#35, #14)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

import auth
import crud
import models
from config import settings
from routers import dependencies as deps
from services import authelia_store, mailer
from tests.conftest import TestSessionLocal

EMAIL = "rider@example.com"
OLD_PASSWORD = "Str0ng!Pass"
NEW_PASSWORD = "gravel climbs at dawn"


@pytest.fixture(autouse=True)
def fresh_reset_limiter():
    """The limiter is module state; a test that turns limits on must not see
    the events of the one before it."""
    deps.password_reset_limiter.reset()


@pytest.fixture
def outbox(monkeypatch) -> list[EmailMessage]:
    """Mail configured, and every message kept instead of sent."""
    sent: list[EmailMessage] = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "mail_from", "Train Like a Pro <noreply@example.test>")
    monkeypatch.setattr(mailer, "_send", sent.append)
    return sent


def _token_from(message: EmailMessage) -> str:
    match = re.search(r"/reset-password#token=([A-Za-z0-9_-]+)", message.get_content())
    assert match, message.get_content()
    return match.group(1)


async def _request(client, email: str = EMAIL):
    return await client.post("/api/v1/auth/password-reset", json={"email": email})


async def _confirm(client, token: str, password: str = NEW_PASSWORD):
    return await client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "password": password}
    )


async def _login(client, password: str):
    return await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": password})


@pytest.mark.asyncio
async def test_without_a_mail_provider_the_reset_is_not_offered(client, auth_headers):
    resp = await _request(client)

    assert resp.status_code == 503
    assert "Ask the person who invited you" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_link_resets_the_password_once_and_ends_every_session(
    client, auth_headers, outbox
):
    resp = await _request(client)

    assert resp.status_code == 202
    assert len(outbox) == 1
    assert outbox[0]["To"] == EMAIL
    token = _token_from(outbox[0])
    # Only the hash is stored.
    async with TestSessionLocal() as db:
        stored = (await db.scalars(select(models.PasswordResetToken))).one()
    assert stored.token_hash != token and len(stored.token_hash) == 64

    assert (await _confirm(client, token)).status_code == 204

    assert (await _login(client, OLD_PASSWORD)).status_code == 401
    assert (await _login(client, NEW_PASSWORD)).status_code == 200
    # The session from before the reset is over.
    assert (await client.get("/api/v1/users/me", headers=auth_headers)).status_code == 401
    # And the link does not work twice.
    again = await _confirm(client, token, "another long phrase here")
    assert again.status_code == 400
    assert "no longer valid" in again.json()["detail"]


@pytest.mark.asyncio
async def test_an_unknown_address_gets_the_same_answer_and_no_mail(client, auth_headers, outbox):
    known = await _request(client)
    unknown = await _request(client, "nobody@example.com")

    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert [m["To"] for m in outbox] == [EMAIL]


@pytest.mark.asyncio
async def test_a_newer_link_retires_the_older_one(client, auth_headers, outbox):
    await _request(client)
    await _request(client)
    first, second = (_token_from(m) for m in outbox)

    assert (await _confirm(client, first)).status_code == 400
    assert (await _confirm(client, second)).status_code == 204


@pytest.mark.asyncio
async def test_an_expired_link_is_refused(client, auth_headers, outbox):
    await _request(client)
    async with TestSessionLocal() as db:
        stored = (await db.scalars(select(models.PasswordResetToken))).one()
        stored.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()

    assert (await _confirm(client, _token_from(outbox[0]))).status_code == 400
    assert (await _login(client, OLD_PASSWORD)).status_code == 200


@pytest.mark.asyncio
async def test_a_weak_password_is_refused_and_the_link_still_works(client, auth_headers, outbox):
    await _request(client)
    token = _token_from(outbox[0])

    weak = await _confirm(client, token, "rider12345")

    assert weak.status_code == 400
    assert weak.json()["detail"].startswith("Password must")
    assert (await _confirm(client, token)).status_code == 204


@pytest.mark.asyncio
async def test_one_address_gets_a_few_mails_an_hour_at_most(
    client, auth_headers, outbox, monkeypatch
):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "password_reset_rate_limit_attempts", 2)

    answers = [(await _request(client)).status_code for _ in range(3)]

    assert answers == [202, 202, 429]
    assert len(outbox) == 2


@pytest.mark.asyncio
async def test_all_addresses_together_get_a_capped_number_of_mails(
    client, auth_headers, outbox, monkeypatch
):
    """The global bucket bounds what anonymous callers can make the provider
    send, whatever addresses they type (accepted trade-off, docs/security.md)."""
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "password_reset_rate_limit_global_attempts", 2)

    answers = [
        (await _request(client, f"someone{i}@example.com")).status_code for i in range(3)
    ]

    assert answers == [202, 202, 429]


@pytest.mark.asyncio
async def test_garbage_confirmations_do_not_block_anyone_s_reset(
    client, auth_headers, outbox, monkeypatch
):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "password_reset_rate_limit_global_attempts", 2)

    bogus = [(await _confirm(client, "x" * 43)).status_code for _ in range(5)]
    resp = await _request(client)

    assert bogus == [400] * 5
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_two_confirmations_with_one_link_cannot_both_win(client, auth_headers, outbox):
    await _request(client)
    now = datetime.now(timezone.utc)
    async with TestSessionLocal() as first, TestSessionLocal() as second:
        token_a = (await first.scalars(select(models.PasswordResetToken))).one()
        token_b = (await second.scalars(select(models.PasswordResetToken))).one()
        assert await crud.claim_password_reset_token(first, token_a, now)
        await first.commit()
        assert not await crud.claim_password_reset_token(second, token_b, now)


@pytest.mark.asyncio
async def test_header_auth_without_a_store_answers_503_and_keeps_the_link(
    client, auth_headers, outbox, monkeypatch
):
    await _request(client)
    token = _token_from(outbox[0])
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", "")

    resp = await _confirm(client, token)

    assert resp.status_code == 503
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", False)
    # Nothing was spent: the claim rolled back with the request.
    assert (await _confirm(client, token)).status_code == 204


@pytest.mark.asyncio
async def test_a_provider_failure_is_logged_not_answered(client, auth_headers, monkeypatch, caplog):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "mail_from", "noreply@example.test")

    def refuse(message):
        raise OSError("connection refused")

    monkeypatch.setattr(mailer, "_send", refuse)

    resp = await _request(client)

    assert resp.status_code == 202
    assert "Could not send a password reset link" in caplog.text


def test_the_store_takes_the_new_password_and_keeps_a_disabled_entry_shut(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "users_database.yml"
    db_path.write_text(
        yaml.dump(
            {
                "users": {
                    EMAIL: {"email": EMAIL, "password": auth.hash_password(OLD_PASSWORD)},
                    "off@example.com": {
                        "email": "off@example.com",
                        "disabled": True,
                        "password": auth.hash_password(OLD_PASSWORD),
                    },
                }
            }
        )
    )
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(db_path))

    assert authelia_store.set_password(EMAIL, NEW_PASSWORD)
    assert authelia_store.verify_credentials(EMAIL, NEW_PASSWORD)
    assert not authelia_store.verify_credentials(EMAIL, OLD_PASSWORD)
    assert not authelia_store.set_password("off@example.com", NEW_PASSWORD)
    assert not authelia_store.set_password("nobody@example.com", NEW_PASSWORD)


def test_the_mail_goes_out_encrypted_and_signed_in(monkeypatch):
    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(f"connect {host}:{port}")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls.append("quit")

        def starttls(self, context):
            calls.append("starttls")

        def login(self, user, password):
            calls.append(f"login {user}")

        def send_message(self, message):
            calls.append(f"send to {message['To']}")

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "smtp_host", "smtp.tem.scaleway.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "project-id")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    message = EmailMessage()
    message["To"] = EMAIL

    mailer._send(message)

    # Never a login before the connection is encrypted.
    assert calls == [
        "connect smtp.tem.scaleway.com:587",
        "starttls",
        "login project-id",
        f"send to {EMAIL}",
        "quit",
    ]
