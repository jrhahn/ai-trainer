"""The second factor: TOTP, login challenges, recovery codes, trusted devices (#688).

Everything with a secret in it lives here so there is one place to read when
asking "how is this actually protected", rather than four routes each doing a
little of it.

What this is for, and what it is not
------------------------------------
This protects an account whose password has leaked. It is **not** a bot defence
and must not be planned as one: TOTP proves possession of a secret the server
just issued, so a script enrolls its own device and computes valid codes in a
few lines. Automated signup is held off by the proof-of-work in
``services/captcha.py`` (#686) and, eventually, by email verification (#687).

Why the backend and not Authelia
--------------------------------
Authelia is the *user store* in this deployment, not a forward-auth proxy: its
middlewares are attached to no router, and ``auth_router`` reads
``users_database.yml`` directly (#324, #696). A second factor configured in
Authelia would therefore never run. Beyond that, Authelia's own TOTP enrollment
requires a notifier — it emails an identity-confirmation link before showing
the QR — and this deployment has no working SMTP (#687). Implemented here, the
feature needs neither.

The login challenge
-------------------
Adding a second step means the server must remember, between two requests, that
a password was already accepted — without trusting the client to say so. The
challenge is a signed statement to that effect: user id, issue time and a nonce,
HMAC'd with a key derived from ``JWT_SECRET``.

Deriving rather than adding a setting is deliberate. Every secret this app has
added lately had to be threaded through compose, the ansible template and two
places in the deploy workflow, and #617, #684 and #694 are all cases where one
of those was missed and the result was a silent fallback rather than an error.
A derived key cannot be half-configured.

A challenge is single-use. Without that, one accepted password would be worth
unlimited code attempts — the rate limit would still apply, but an attacker
could hold a valid challenge open indefinitely while brute-forcing six digits.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import time
from dataclasses import dataclass

import pyotp
import qrcode
import qrcode.image.svg

from config import settings

_CHALLENGE_LABEL = b"ai-trainer/totp-challenge/v1"
_DEVICE_LABEL = b"ai-trainer/trusted-device/v1"

# A TOTP step is 30 s. ±1 covers ordinary clock drift and a code typed as it
# rolls over; widening it multiplies the brute-force surface for no real gain.
_VALID_WINDOW = 1

RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no l/o/0/1
_RECOVERY_GROUP = 5


class TotpError(Exception):
    """A challenge or a code was missing, malformed, expired, spent or wrong."""


# ---------------------------------------------------------------------------
# Secrets and codes
# ---------------------------------------------------------------------------


def new_secret() -> str:
    """A fresh base32 TOTP secret."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, *, account: str) -> str:
    """The ``otpauth://`` URI an authenticator app expects."""
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=settings.totp_issuer)


def qr_svg(uri: str) -> str:
    """Render *uri* as an inline SVG.

    Server-side on purpose: the alternative is a QR library in the browser
    bundle, and the CSP that #677 put in place is worth more than the few
    kilobytes this saves. An SVG string also needs no ``img-src data:``
    exception, since it is markup rather than an image source.
    """
    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")


def verify_code(secret: str, code: str) -> bool:
    """Whether *code* is currently valid for *secret*."""
    if not secret or not code:
        return False
    cleaned = code.strip().replace(" ", "")
    if not cleaned.isdigit():
        return False
    return pyotp.TOTP(secret).verify(cleaned, valid_window=_VALID_WINDOW)


def new_recovery_codes() -> list[str]:
    """Human-transcribable one-time codes, shown once at enrollment.

    Without these a lost phone means losing the account — for the operator of a
    self-hosted instance that means an SSH session and a hand-edited database,
    which is a poor recovery story and the reason people leave 2FA switched off.
    """
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_GROUP * 2))
        codes.append(f"{raw[:_RECOVERY_GROUP]}-{raw[_RECOVERY_GROUP:]}")
    return codes


def normalise_recovery_code(code: str) -> str:
    """Compare codes by content, not by how they were typed."""
    return code.strip().lower().replace(" ", "").replace("-", "")


# ---------------------------------------------------------------------------
# Login challenge
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Challenge:
    user_id: str
    issued_at: float
    nonce: str
    signature: str

    def token(self) -> str:
        return f"{self.user_id}.{self.issued_at:.0f}.{self.nonce}.{self.signature}"


def _challenge_key() -> bytes:
    return hmac.new(
        settings.jwt_secret.encode("utf-8"), _CHALLENGE_LABEL, hashlib.sha256
    ).digest()


def _sign_challenge(user_id: str, issued_at: float, nonce: str) -> str:
    payload = f"{user_id}.{issued_at:.0f}.{nonce}"
    return hmac.new(_challenge_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_challenge(user_id: str, *, now: float | None = None) -> str:
    """Return an opaque token proving *user_id* just passed the first factor."""
    issued_at = time.time() if now is None else now
    nonce = secrets.token_urlsafe(12)
    return Challenge(
        user_id=user_id,
        issued_at=issued_at,
        nonce=nonce,
        signature=_sign_challenge(user_id, issued_at, nonce),
    ).token()


def read_challenge(token: str, *, now: float | None = None) -> str:
    """Return the user id *token* attests to, or raise :class:`TotpError`.

    The signature is checked before anything derived from the token is used,
    and expiry only after — an expired-but-valid token and a forged one are
    different problems and the forged one is the more interesting.
    """
    checked_at = time.time() if now is None else now
    if not isinstance(token, str) or token.count(".") != 3:
        raise TotpError("Invalid sign-in challenge.")

    user_id, issued_raw, nonce, signature = token.split(".")
    try:
        issued_at = float(issued_raw)
    except ValueError as exc:
        raise TotpError("Invalid sign-in challenge.") from exc

    expected = _sign_challenge(user_id, issued_at, nonce)
    if not hmac.compare_digest(expected, signature):
        raise TotpError("Invalid sign-in challenge.")

    if checked_at - issued_at > settings.totp_challenge_ttl_seconds:
        raise TotpError("Sign-in took too long. Please enter your password again.")

    if not _challenge_guard.claim(signature, now=checked_at):
        raise TotpError("This sign-in challenge was already used.")

    return user_id


class _SpentChallenges:
    """Single-use enforcement for challenges, same shape as the captcha's guard.

    In-process, therefore single-replica — the caveat recorded in
    ``docs/multi_replica.md``. With N replicas a challenge could be spent once
    per replica; the rate limit still bounds the attempts behind it.
    """

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def claim(self, signature: str, *, now: float) -> bool:
        ttl = settings.totp_challenge_ttl_seconds
        for key, seen in list(self._seen.items()):
            if now - seen > ttl:
                del self._seen[key]
        if signature in self._seen:
            return False
        self._seen[signature] = now
        return True

    def reset(self) -> None:
        self._seen.clear()


_challenge_guard = _SpentChallenges()


def reset_challenge_guard() -> None:
    """Forget spent challenges — for tests."""
    _challenge_guard.reset()


# ---------------------------------------------------------------------------
# Trusted devices
# ---------------------------------------------------------------------------


def new_device_token() -> str:
    """An opaque bearer value for the trusted-device cookie."""
    return secrets.token_urlsafe(32)


def hash_device_token(token: str) -> str:
    """What is stored, so a database read does not hand over live devices.

    Plain SHA-256 rather than Argon2: this is a 256-bit random value, not a
    password, so there is nothing to brute-force and the per-request cost of a
    password hash would buy nothing.
    """
    return hmac.new(
        hmac.new(settings.jwt_secret.encode("utf-8"), _DEVICE_LABEL, hashlib.sha256).digest(),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def encode_secret_for_display(secret: str) -> str:
    """Group the base32 secret so it can be typed without losing your place."""
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))


def admin_totp_configured() -> bool:
    """Whether the admin panel has a second factor configured."""
    return bool(settings.admin_totp_secret)


def verify_admin_code(code: str) -> bool:
    """Check *code* against ``ADMIN_TOTP_SECRET``.

    The admin panel has no user row to hang a secret on, so its secret is an
    environment variable and there is no enrollment endpoint — one less
    unauthenticated surface in front of the account that can read every
    athlete's email address and delete any of them.
    """
    secret = settings.admin_totp_secret
    if not secret:
        return False
    try:
        base64.b32decode(secret, casefold=True)
    except Exception:
        return False
    return verify_code(secret, code)
