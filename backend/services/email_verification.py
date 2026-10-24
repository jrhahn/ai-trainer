"""Confirming the address an account registers with (app #687).

Only where mail goes out. Without SMTP there is nothing to confirm with, so an
account works from the start, as before: a self-hosted instance, development,
the keyless e2e stack.

An unconfirmed account cannot sign in (``auth_router._complete_login``), so it
cannot spend tokens either. It is deleted after ``unverified_account_days``, so
a mistyped address, or someone else's, does not hold the name for good. A
password reset confirms the address as well, since its link reaches the same
mailbox (ai-trainer-ops#103 Q29).

The link carries a signed token rather than a stored one: the user id, an
expiry and an HMAC keyed from ``JWT_SECRET``, the pattern of ``captcha`` and
``totp``. No new secret has to reach the four places a deploy setting lives,
which is how #617, #684 and #694 went wrong. Following a link twice is harmless:
the second time there is nothing left to confirm.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import crud
import models
from config import settings
from services import authelia_store, mailer
from services.plan_maintenance import seconds_until_next_daily_run
from services.scheduler import ScheduledJob

logger = logging.getLogger(__name__)

_KEY_LABEL = b"email-verification"
# After the nightly plan run, before anyone is up.
PRUNE_HOUR = 4

LINK_INVALID = "This link is no longer valid. Ask for a new one."


class VerificationError(ValueError):
    """A link that is forged, malformed or expired."""


def required() -> bool:
    """Whether a new account has to confirm its address before it signs in."""
    return settings.email_verification_required and mailer.mail_configured()


def _key() -> bytes:
    return hmac.new(
        settings.jwt_secret.encode("utf-8"), _KEY_LABEL, hashlib.sha256
    ).digest()


def _sign(user_id: str, expires_at: int) -> str:
    payload = f"{user_id}.{expires_at}".encode("utf-8")
    return hmac.new(_key(), payload, hashlib.sha256).hexdigest()


def issue_token(user_id: str, *, now: float | None = None) -> str:
    """A token that confirms *user_id*'s address until it expires."""
    issued = time.time() if now is None else now
    expires_at = int(issued + settings.email_verification_link_hours * 3600)
    return f"{user_id}.{expires_at}.{_sign(user_id, expires_at)}"


def read_token(token: str, *, now: float | None = None) -> str:
    """The user id *token* confirms, or :class:`VerificationError`."""
    checked = time.time() if now is None else now
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        raise VerificationError(LINK_INVALID)
    user_id, expires_raw, signature = parts
    try:
        expires_at = int(expires_raw)
    except ValueError as exc:
        raise VerificationError(LINK_INVALID) from exc
    if not hmac.compare_digest(_sign(user_id, expires_at), signature):
        raise VerificationError(LINK_INVALID)
    if checked > expires_at:
        raise VerificationError(LINK_INVALID)
    return user_id


async def send_link(
    session_factory: async_sessionmaker[AsyncSession],
    email: str,
    language: str | None = None,
) -> None:
    """Mail a confirmation link if *email* has an unconfirmed account.

    Runs after the response has gone, so the reply takes the same time whether
    or not the address has an account. Failures are logged, never raised.
    ``language`` is the interface language of the request that asked for it:
    a German sign-up gets a German mail (ai-trainer-ops#220).
    """
    try:
        async with session_factory() as db:
            user = await crud.get_user_by_email_simple(db, email)
            if user is None or user.email_verified_at is not None:
                return
            link = (
                f"{settings.primary_frontend_url}/verify-email"
                f"#token={issue_token(user.id)}"
            )
            hours = settings.email_verification_link_hours
            days = settings.unverified_account_days
            # The sign-in page names the subject, so keep LoginPage.tsx in step.
            if language == "German":
                subject = "Bestätige dein Konto bei Train Like a Pro"
                body = (
                    (f"Hallo {user.name},\n\n" if user.name else "Hallo,\n\n")
                    + "bitte bestätige, dass diese Adresse zu deinem neuen Konto bei "
                    f"Train Like a Pro gehört. Öffne diesen Link innerhalb von {hours} "
                    f"Stunden:\n\n{link}\n\n"
                    "Falls du dich nicht angemeldet hast, ignoriere diese Mail. Das "
                    f"Konto wird nach {days} Tagen gelöscht, wenn es niemand bestätigt.\n"
                )
            else:
                subject = "Confirm your Train Like a Pro account"
                body = (
                    f"Hello {user.name or 'there'},\n\n"
                    "please confirm that this address belongs to your new Train Like "
                    f"a Pro account. Open this link within {hours} hours:\n\n"
                    f"{link}\n\n"
                    "If you did not sign up, ignore this mail. The account is deleted "
                    f"after {days} days unless someone confirms it.\n"
                )
            await mailer.send_mail(user.email, subject, body)
            logger.info("Sent an email confirmation link to user %s", user.id)
    except Exception:
        logger.exception("Could not send an email confirmation link")


async def confirm(db: AsyncSession, token: str) -> models.User:
    """Mark the account *token* names as confirmed; raise if the link is bad."""
    user = await crud.get_user_by_id(db, read_token(token))
    if user is None:
        raise VerificationError(LINK_INVALID)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)
    return user


async def prune_unverified(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete accounts that never confirmed their address in time.

    Only while confirmation is required: once it is switched off, or mail no
    longer goes out, an unconfirmed account signs in like any other, and deleting
    it would delete an account in use.
    """
    if not required():
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        days=settings.unverified_account_days
    )
    stale = (
        await db.scalars(
            select(models.User).where(
                models.User.email_verified_at.is_(None),
                models.User.created_at < cutoff,
            )
        )
    ).all()
    deleted = 0
    for user in stale:
        try:
            # The credential first, as the account routes do: a row deleted
            # while its password still works recreates the account at login.
            await asyncio.to_thread(authelia_store.delete_user, user.email)
        except RuntimeError:
            logger.exception("Keeping unconfirmed user %s: store not writable", user.id)
            continue
        await db.delete(user)
        deleted += 1
    if deleted:
        logger.info("Deleted %s accounts that never confirmed their address", deleted)
    return deleted


def pruning_job(session_factory: async_sessionmaker[AsyncSession]) -> ScheduledJob:
    async def _run() -> object:
        async with session_factory() as db:
            deleted = await prune_unverified(db)
            await db.commit()
        return deleted

    return ScheduledJob(
        name="unverified-account-pruning",
        run=_run,
        next_delay=lambda: seconds_until_next_daily_run(
            timezone_name=settings.app_timezone, hour=PRUNE_HOUR
        ),
    )
