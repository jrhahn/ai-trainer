"""Confirming the address an account registers with (app #687)."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
from sqlalchemy import select

import crud
import models
from auth import hash_password
from config import settings
from routers import dependencies as deps
from services import email_verification, mailer
from tests.conftest import TestSessionLocal

EMAIL = "new.rider@example.com"
PASSWORD = "Str0ng!Pass"


@pytest.fixture(autouse=True)
def fresh_mail_limiter():
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
    match = re.search(r"/verify-email#token=([A-Za-z0-9_.-]+)", message.get_content())
    assert match, message.get_content()
    return match.group(1)


async def _register(client, email: str = EMAIL):
    return await client.post(
        "/api/v1/auth/register", json={"name": "New Rider", "email": email, "password": PASSWORD}
    )


async def _login(client, email: str = EMAIL):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})


def test_a_link_names_its_account_until_it_expires():
    token = email_verification.issue_token("user-1", now=1000.0)

    assert email_verification.read_token(token, now=1000.0) == "user-1"
    with pytest.raises(email_verification.VerificationError):
        email_verification.read_token(token, now=1000.0 + settings.email_verification_link_hours * 3600 + 1)
    with pytest.raises(email_verification.VerificationError):
        email_verification.read_token(token.replace("user-1", "user-2"), now=1000.0)
    with pytest.raises(email_verification.VerificationError):
        email_verification.read_token("not-a-token", now=1000.0)


@pytest.mark.asyncio
async def test_a_new_account_signs_in_only_after_following_its_link(client, outbox):
    registered = await _register(client)

    assert registered.status_code == 202
    assert registered.json() == {"verificationRequired": True}
    assert [m["To"] for m in outbox] == [EMAIL]
    refused = await _login(client)
    assert refused.status_code == 403
    assert "Confirm your email address" in refused.json()["detail"]

    confirmed = await client.post(
        "/api/v1/auth/verify-email", json={"token": _token_from(outbox[0])}
    )

    assert confirmed.status_code == 204
    assert (await _login(client)).status_code == 200


@pytest.mark.asyncio
async def test_a_bad_link_says_so(client, outbox):
    response = await client.post(
        "/api/v1/auth/verify-email", json={"token": "x" * 40}
    )

    assert response.status_code == 400
    assert "no longer valid" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_new_link_goes_only_to_an_unconfirmed_account(client, outbox):
    await _register(client)
    outbox.clear()

    unknown = await client.post(
        "/api/v1/auth/verify-email/resend", json={"email": "nobody@example.com"}
    )
    known = await client.post("/api/v1/auth/verify-email/resend", json={"email": EMAIL})

    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert [m["To"] for m in outbox] == [EMAIL]


@pytest.mark.asyncio
async def test_without_mail_an_account_works_from_the_start(client):
    registered = await _register(client)

    assert registered.status_code == 200
    assert "access_token" in registered.json()
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email_simple(db, EMAIL)
    assert user.email_verified_at is not None


@pytest.mark.asyncio
async def test_an_account_never_confirmed_is_deleted_after_its_window(outbox):
    now = datetime.now(timezone.utc)
    async with TestSessionLocal() as db:
        for email, age_days, verified in (
            ("stale@example.com", settings.unverified_account_days + 1, False),
            ("fresh@example.com", 1, False),
            ("old.confirmed@example.com", 400, True),
        ):
            user = await crud.create_user(
                db, email=email, name=None, hashed_password=hash_password(PASSWORD), verified=verified
            )
            user.created_at = now - timedelta(days=age_days)
        await db.commit()

    async with TestSessionLocal() as db:
        deleted = await email_verification.prune_unverified(db, now=now)
        await db.commit()
        left = set(await db.scalars(select(models.User.email)))

    assert deleted == 1
    assert "stale@example.com" not in left
    assert {"fresh@example.com", "old.confirmed@example.com"} <= left


def test_the_link_expires_after_the_configured_hours():
    issued = time.time()
    token = email_verification.issue_token("user-9", now=issued)
    expires_at = int(token.split(".")[1])

    assert expires_at == int(issued + settings.email_verification_link_hours * 3600)


@pytest.mark.asyncio
async def test_a_test_stack_can_switch_confirmation_off_while_it_sends_mail(
    client, outbox, monkeypatch
):
    monkeypatch.setattr(settings, "email_verification_required", False)

    registered = await _register(client)

    assert registered.status_code == 200
    assert outbox == []



async def _stale_unconfirmed(email: str) -> None:
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email=email, name=None, hashed_password=hash_password(PASSWORD), verified=False
        )
        user.created_at = datetime.now(timezone.utc) - timedelta(
            days=settings.unverified_account_days + 1
        )
        await db.commit()


async def _emails() -> set[str]:
    async with TestSessionLocal() as db:
        return set(await db.scalars(select(models.User.email)))


@pytest.mark.asyncio
async def test_nothing_is_deleted_once_confirmation_is_no_longer_required(outbox, monkeypatch):
    """Switched off, an unconfirmed account signs in like any other: it is in use."""
    await _stale_unconfirmed("in.use@example.com")
    monkeypatch.setattr(settings, "email_verification_required", False)

    async with TestSessionLocal() as db:
        assert await email_verification.prune_unverified(db) == 0

    assert "in.use@example.com" in await _emails()


@pytest.mark.asyncio
async def test_an_account_whose_credential_cannot_be_removed_is_kept(outbox, monkeypatch):
    """Credential first: a row deleted while its password still works comes back at login."""
    await _stale_unconfirmed("locked.store@example.com")

    def broken(email):
        raise RuntimeError("store not writable")

    monkeypatch.setattr(email_verification.authelia_store, "delete_user", broken)
    async with TestSessionLocal() as db:
        assert await email_verification.prune_unverified(db) == 0
        await db.commit()

    assert "locked.store@example.com" in await _emails()


@pytest.mark.asyncio
async def test_the_nightly_job_deletes_what_has_expired(outbox):
    await _stale_unconfirmed("nightly@example.com")
    job = email_verification.pruning_job(TestSessionLocal)

    assert job.name == "unverified-account-pruning"
    assert await job.run() == 1
    assert job.next_delay() > 0
    assert "nightly@example.com" not in await _emails()


@pytest.mark.asyncio
async def test_a_mail_that_fails_does_not_fail_the_request(client, outbox, monkeypatch):
    async def down(*args, **kwargs):
        raise OSError("provider down")

    monkeypatch.setattr(email_verification.mailer, "send_mail", down)

    assert (await _register(client)).status_code == 202


@pytest.mark.asyncio
async def test_without_mail_there_is_no_new_link_to_ask_for(client):
    response = await client.post("/api/v1/auth/verify-email/resend", json={"email": EMAIL})

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_new_links_count_against_the_reset_mail_limit(client, outbox, monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "password_reset_rate_limit_attempts", 1)
    deps.password_reset_limiter.reset()

    first = await client.post("/api/v1/auth/password-reset", json={"email": EMAIL})
    second = await client.post("/api/v1/auth/verify-email/resend", json={"email": EMAIL})

    assert first.status_code == 202
    assert second.status_code == 429


@pytest.mark.asyncio
async def test_with_authelia_a_new_account_is_written_unconfirmed_and_asked_to_confirm(
    client, outbox, monkeypatch, tmp_path
):
    import auth
    import yaml

    store = tmp_path / "users_database.yml"
    store.write_text(yaml.safe_dump({"users": {}}))
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", str(store))

    response = await _register(client)

    assert response.status_code == 202
    assert response.json() == {"verificationRequired": True}
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_email_simple(db, EMAIL)
    assert user.email_verified_at is None
    assert [m["To"] for m in outbox] == [EMAIL]
