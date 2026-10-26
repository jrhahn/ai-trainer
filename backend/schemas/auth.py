"""Sign-up, sign-in, passwords, two-factor codes and sessions."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)
from schemas.base import CamelModel


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class CaptchaChallengeResponse(BaseModel):
    """A proof-of-work challenge for the registration form (#686)."""

    algorithm: str
    challenge: str
    salt: str
    signature: str
    maxnumber: int


class CaptchaSolution(BaseModel):
    """The solved challenge, echoed back with the number the client found."""

    challenge: str
    salt: str
    signature: str
    number: int


def _normalize_email(value: str) -> str:
    """Strip and lowercase the whole address (ops#109).

    ``EmailStr`` lowercases only the domain, so ``Foo@Example.de`` was stored and
    looked up with its local part's case intact: sign-in with another spelling
    failed, and a second spelling made a second account. Normalising at every
    request boundary that carries an email makes the spellings one address.
    """
    return value.strip().lower()


#: An email that is validated, then stripped and lowercased in full.
NormalizedEmail = Annotated[EmailStr, AfterValidator(_normalize_email)]


class LegalContactResponse(CamelModel):
    """Who runs this deployment and how to reach them (ai-trainer-ops#2, #15)."""

    operator_name: str | None = None
    # The postal address on /impressum (ai-trainer-ops#1).
    operator_address: str | None = None
    contact_email: str | None = None
    # Whether /privacy describes the coach review (ai-trainer-ops#136).
    coach_review_opt_in: bool = False


class SignupConfigResponse(CamelModel):
    """What the registration form needs to know before it is filled in."""

    invite_only: bool


class RegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # max_length matches models.User.name (String(255)); without it a long name
    # was a 500 on Postgres, and with Authelia on the YAML entry is written
    # before the DB row, so the retry 409s for an account that never existed
    # (ops#132). min_length + the strip validator reject an empty or blank name.
    name: str = Field(min_length=1, max_length=255)
    email: NormalizedEmail
    password: str
    privacy_consent: bool = Field(default=False, alias="privacyConsent")
    """The athlete agreed to the privacy policy and to processing their health
    data (ai-trainer-ops#2). Recorded with its time on the account."""
    invite_code: str | None = Field(default=None, max_length=64, alias="inviteCode")
    """Required by the route when ``SIGNUP_INVITE_ONLY`` is on (ops#108, #96)."""
    captcha: CaptchaSolution | None = None
    """Optional in the schema, required by the route when captcha is enabled.

    Kept optional here so a deployment with ``CAPTCHA_ENABLED=false`` accepts
    the plain body, and so a missing solution fails with the route's own 400 —
    which says what to do about it — rather than a 422 listing a field the
    caller has never heard of.
    """

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        """A name is required; whitespace-only is not one (ops#132)."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @model_validator(mode="after")
    def password_strength(self) -> "RegisterRequest":
        if problem := password_problem(self.password, str(self.email), self.name):
            raise ValueError(problem)
        return self


WEAK_PASSWORD_TERMS = (
    "password",
    "passwort",
    "qwert",
    "12345",
    "abcde",
    "admin",
    "welcome",
    "trainlikea",
)


def password_problem(password: str, email: str, name: str) -> str | None:
    """What is wrong with *password*, as one sentence, or None.

    Shared by registration and the password reset (ai-trainer-ops#35), so a
    reset cannot set a password registration would refuse. The first problem
    only, and one fixed sentence each, so the client can translate it; a part
    of the name or email is named (ai-trainer-ops#114). The client checks the
    same rule (``frontend/src/utils/passwordChecks.ts``), against the same
    cases (``passwordCases.json``).
    """
    password_lower = unicodedata.normalize("NFC", password.lower())

    # NIST SP 800-63B: length plus a check against common and personal
    # words, and no composition rules (ai-trainer-ops#44, #39 Q11).
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if len(set(password_lower)) < 5:
        return "Password must not repeat a few characters."
    if any(term in password_lower for term in WEAK_PASSWORD_TERMS):
        return "Password must avoid common or app-related words."
    part = next(
        (p for p in _password_personal_fragments(email, name) if p in password_lower),
        None,
    )
    if part is not None:
        return f"Password must not include “{part}” from your name or email."
    return None


class PasswordResetRequest(BaseModel):
    email: NormalizedEmail


class EmailVerificationConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=300)


class EmailVerificationResend(BaseModel):
    email: NormalizedEmail


class PasswordResetConfirm(BaseModel):
    # 43 characters is ``secrets.token_urlsafe(32)``; the bounds only keep
    # garbage out of a hash lookup.
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(max_length=200)


# Letters and digits in any script: "Müller" stays one word.
_PERSONAL_WORD = re.compile(r"[^\W_]+")


PERSONAL_PART_MIN_CHARS = 4


def _password_personal_fragments(email: str, name: str) -> list[str]:
    """The parts of the athlete's email and name a password must not contain.

    The local part, its pieces and the name's words, from four characters up
    (ai-trainer-ops#114). Not the domain: "web" in anna@web.de and "online" in
    jens@t-online.de refused "mein-web-kennwort-99", and web.de, GMX and
    T-Online are the most common German mail domains. Not an ASCII split
    either: it cut "Müller" into "m" and "ller". Longest first, so the part
    named is the most telling; the client orders them the same way.
    """
    local_part = unicodedata.normalize("NFC", email.lower().partition("@")[0])
    words = unicodedata.normalize("NFC", name.lower())
    candidates = {
        local_part,
        *_PERSONAL_WORD.findall(local_part),
        *_PERSONAL_WORD.findall(words),
    }
    return sorted(
        (c for c in candidates if len(c) >= PERSONAL_PART_MIN_CHARS),
        key=lambda c: (-len(c), c),
    )


class LoginRequest(BaseModel):
    email: NormalizedEmail
    password: str


class TotpLoginRequest(CamelModel):
    """Second step of a login that needs a code (#688).

    CamelModel rather than BaseModel, and that is load-bearing: the client
    sends ``rememberDevice``. On a plain BaseModel that key does not map to
    ``remember_device``, so it is dropped and the field silently keeps its
    default — the device is never trusted and nothing reports why.
    """

    challenge: str
    code: str
    remember_device: bool = False


class LoginChallengeResponse(CamelModel):
    """What ``/auth/login`` returns instead of a token when TOTP is on.

    A 200 with a discriminated body rather than a 401: the password *was*
    accepted, and reporting that as an auth failure would leave the client
    unable to tell "wrong password" from "now show the code field".
    """

    mfa_required: bool = True
    challenge: str


class TotpEnrollResponse(CamelModel):
    """The QR and the secret, returned once when enrollment starts."""

    secret: str
    """Grouped for transcription, for anyone entering it by hand."""
    provisioning_uri: str
    qr_svg: str


class TotpConfirmRequest(BaseModel):
    code: str


class TotpStatusResponse(CamelModel):
    enabled: bool
    confirmed_at: datetime | None = None
    recovery_codes_remaining: int = 0
    trusted_device_count: int = 0


class TotpRecoveryCodesResponse(CamelModel):
    """Shown exactly once, at confirmation. Never retrievable afterwards."""

    recovery_codes: list[str]


class PasswordChangeRequest(CamelModel):
    """A new password while signed in: the current one first (ai-trainer-ops#172).

    Bounded like the reset's, so a megabyte does not reach the hash.
    """

    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class TotpDisableRequest(BaseModel):
    """Disabling needs the password again, not just a live session.

    A borrowed unlocked browser should not be enough to remove the factor that
    protects the account.
    """

    password: str


class SessionRevokeRequest(BaseModel):
    """Signing out everywhere needs the password, for the same reason as above.

    It is also the one request that deliberately invalidates the token making
    it: whoever presses this wants every session gone, including this one
    (#704).
    """

    password: str


class AccountDeleteRequest(BaseModel):
    """Deleting the account needs the password, like disabling the second factor.

    It did not, which made a borrowed unlocked browser enough to destroy somebody's
    training history (ai-trainer-ops#35). The two neighbouring step-up routes —
    ``/auth/totp/disable`` and ``/auth/sessions/revoke`` — both ask, on the
    argument that locking the owner out of their own account must cost more than
    a live session. Deletion is that argument's stronger case, and it is the one
    route that had no way to make it: ``require_password`` was private to
    ``auth_router``.
    """

    password: str


class SessionRevokeResponse(CamelModel):
    """What the client needs to know: the session it holds is finished.

    ``token_generation`` is returned because it is the only visible evidence
    the revocation happened, and an operator reading the admin panel or a log
    can line it up with the column.
    """

    token_generation: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SessionResponse(CamelModel):
    """A cookie session started (ai-trainer-ops#45): the token is in the
    HttpOnly cookie, and this is the CSRF token that goes with it."""

    csrf_token: str
