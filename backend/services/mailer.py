"""Outgoing mail, for the password reset (ai-trainer-ops#35, #14).

Sent through the transactional provider configured in ``settings.smtp_*``
(Scaleway TEM on this deployment), never from the host itself: port 25 is
blocked there and its reverse DNS is a generic cloud name (app #687).

``smtplib`` in a thread rather than an async client: a handful of mails a day
does not justify a dependency, and the caller already runs this after the
response has gone (see ``auth_router.request_password_reset``).
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from config import settings

# Long enough for a slow provider, short enough that a dead one does not hold a
# worker thread for minutes.
SMTP_TIMEOUT_SECONDS = 20


def mail_configured() -> bool:
    return bool(settings.smtp_host and settings.mail_from)


def _send(message: EmailMessage) -> None:
    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS, context=context
        )
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS)
    with smtp:
        if settings.smtp_port != 465:
            # Never in clear: the provider requires it, and the mail carries a
            # link that sets a password.
            smtp.starttls(context=context)
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def send_mail(to: str, subject: str, body: str) -> None:
    """Send a plain-text mail. Raises when the provider refuses it."""
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    await asyncio.to_thread(_send, message)
