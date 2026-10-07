"""Password hashing, JWT creation/verification, and FastAPI auth dependency."""

import hashlib
import hmac
import logging
import os
import secrets
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from config import DEV_ENVS, settings
from database import get_db

_JWT_SECRET_DEFAULT = "change-me-in-production"
_MIN_HMAC_SECRET_BYTES = 32
logger = logging.getLogger(__name__)

JWT_SECRET = settings.jwt_secret
JWT_ALGORITHM = settings.jwt_algorithm
JWT_EXPIRE_MINUTES = settings.jwt_expire_minutes
APP_ENV = settings.app_env
AUTHELIA_AUTH_ENABLED = settings.authelia_auth_enabled
AUTHELIA_REMOTE_USER_HEADER = settings.authelia_remote_user_header
AUTHELIA_REMOTE_EMAIL_HEADER = settings.authelia_remote_email_header
AUTHELIA_REMOTE_NAME_HEADER = settings.authelia_remote_name_header
AUTHELIA_INTERNAL_URL = settings.authelia_internal_url.rstrip("/")
AUTHELIA_USERS_DB_PATH = settings.authelia_users_db_path
AUTHELIA_PROXY_SECRET_HEADER = settings.authelia_proxy_secret_header
AUTHELIA_PROXY_SHARED_SECRET = settings.authelia_proxy_shared_secret

_bearer_scheme = HTTPBearer(auto_error=False)
_argon2_hasher = PasswordHasher()


def validate_jwt_secret() -> None:
    """Validate JWT_SECRET strength for the configured runtime environment."""
    is_dev_env = APP_ENV.lower() in DEV_ENVS
    if JWT_SECRET == _JWT_SECRET_DEFAULT and not is_dev_env:
        raise RuntimeError(
            f"JWT_SECRET is set to the insecure default value '{_JWT_SECRET_DEFAULT}'. "
            "Set a strong, random JWT_SECRET environment variable before starting the app. "
            f"(APP_ENV={APP_ENV!r})"
        )
    if JWT_ALGORITHM.upper().startswith("HS"):
        secret_bytes = len(JWT_SECRET.encode("utf-8"))
        if secret_bytes < _MIN_HMAC_SECRET_BYTES:
            message = (
                f"JWT_SECRET is {secret_bytes} bytes long, below the "
                f"{_MIN_HMAC_SECRET_BYTES}-byte minimum recommended for {JWT_ALGORITHM}. "
                "Set JWT_SECRET to at least 32 random bytes."
            )
            if not is_dev_env:
                raise RuntimeError(message)
            logger.warning(
                "%s Suppressing PyJWT's repeated InsecureKeyLengthWarning in %s.",
                message,
                APP_ENV,
            )
            warnings.filterwarnings(
                "ignore",
                category=jwt.InsecureKeyLengthWarning,
            )


def warn_if_authelia_header_auth_is_off() -> None:
    """Report at boot that ``Remote-*`` identity headers are being ignored.

    Since ai-trainer-ops#34 an empty AUTHELIA_PROXY_SHARED_SECRET disables header
    authentication rather than loosening it, so this is a statement about what
    the deployment can do, not a warning about exposure. It is logged because the
    silent half matters: an operator who switches a forward-auth proxy on and
    forgets the secret gets 401 everywhere, and this line is what explains it.
    """
    if AUTHELIA_AUTH_ENABLED and not AUTHELIA_PROXY_SHARED_SECRET:
        logger.warning(
            "AUTHELIA_AUTH_ENABLED is set but AUTHELIA_PROXY_SHARED_SECRET is "
            "empty: Remote-* identity headers are ignored on every request, so "
            "only password login works. Set AUTHELIA_PROXY_SHARED_SECRET and "
            "have the proxy inject it to enable header authentication."
        )


def warn_if_authelia_user_store_unwritable() -> None:
    """Warn at boot when registration cannot write Authelia's user store.

    With AUTHELIA_AUTH_ENABLED, ``/auth/register`` appends to
    ``users_database.yml`` on a bind mount whose ownership comes from the host,
    not the image. Since the container stopped running as root (#682) a
    mismatched owner turns every registration into a 500 — and the only place
    that shows up is a user failing to sign up. Checked once at startup so the
    operator learns it from the logs instead.
    """
    if not AUTHELIA_AUTH_ENABLED or not AUTHELIA_USERS_DB_PATH:
        return
    path = Path(AUTHELIA_USERS_DB_PATH)
    if not path.exists():
        logger.warning(
            "AUTHELIA_AUTH_ENABLED is set but the user store %s does not exist: "
            "registration will fail with 503.",
            path,
        )
        return
    # The directory matters as much as the file: the write is a create-and-
    # rename, so registration needs to create a temp file next to the target.
    if not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
        logger.warning(
            "Authelia user store %s is not writable by the backend process "
            "(uid=%s). Registration will fail until the bind mount's ownership "
            "matches the container user.",
            path,
            os.getuid(),
        )


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Hash a password with Argon2id.

    Argon2 (unlike bcrypt) has no 72-byte input limit, so long passphrases are
    hashed in full. Legacy bcrypt hashes are still accepted by
    ``verify_password`` and upgraded on successful login (see
    ``password_needs_rehash``). See #329.
    """
    return _argon2_hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    if hashed.startswith("$argon2"):
        try:
            return _argon2_hasher.verify(hashed, plain)
        except Argon2Error:
            return False

    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


_absent_user_hash: str | None = None


def _hash_nobody_matches() -> str:
    """An Argon2 hash no submitted password can match, built like a real one.

    Produced by ``hash_password`` rather than written down as a constant, so its
    cost parameters cannot drift away from the hashes it stands in for. A
    stand-in cheaper than the real thing reopens the gap it exists to close, and
    a hard-coded hash would stop following ``PasswordHasher``'s defaults the next
    time argon2-cffi raises them.

    Computed on first use rather than at import: a process that never sees a
    login attempt for a missing account should not pay an Argon2 hash for it,
    and paying at import would slow every test collection for nothing.
    """
    global _absent_user_hash
    if _absent_user_hash is None:
        _absent_user_hash = hash_password(secrets.token_urlsafe(32))
    return _absent_user_hash


def password_matches(password: str, hashed: str | None) -> bool:
    """Whether *password* matches *hashed* — at one cost, hash or no hash.

    ``None`` means "no such account, or no usable password on it", and is a legal
    argument on purpose. The callers used to decide that themselves, each writing
    ``user is None or not verify_password(...)``, which skipped the Argon2
    verification entirely when the account did not exist. Argon2 is deliberately
    expensive, so that short-circuit was a plain enumeration oracle: measured on
    ``/auth/login``, a wrong password for an existing account answered in 165 ms
    and one for an address with no account in 8 ms — 20x, from a single request,
    no averaging needed (ai-trainer-ops#35).

    So the absent case spends a verification it knows will fail, and that
    decision lives in one function rather than at each call site, where the next
    caller would write the short-circuit again.

    Not constant-time in the strict sense, and not trying to be: a legacy bcrypt
    hash (#329) verifies on a different curve from Argon2, so a tell remains
    between two kinds of *existing* account — which answers nothing an attacker
    wants to know. What had to go is the tell between existing and not.
    """
    if hashed is None:
        hashed = _hash_nobody_matches()
    return verify_password(password, hashed)


def password_needs_rehash(hashed: str) -> bool:
    """Whether a stored hash should be replaced after a successful verify.

    True for any non-Argon2 (legacy bcrypt) hash, or an Argon2 hash made with
    out-of-date parameters, so callers can transparently upgrade it on login.
    """
    if not hashed.startswith("$argon2"):
        return True
    try:
        return _argon2_hasher.check_needs_rehash(hashed)
    except Argon2Error:
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


GENERATION_CLAIM = "gen"
"""Which generation of the account's sessions this token belongs to (#704)."""

ADMIN_CREDENTIAL_CLAIM = "cred"
"""Which admin credentials this token was issued against (#704)."""

SESSION_REVOKED_DETAIL = "Session has been signed out. Please sign in again."
ADMIN_SESSION_STALE_DETAIL = "Admin credentials changed. Sign in again."


@dataclass(frozen=True)
class AccessTokenClaims:
    """What an athlete's access token asserts, once the signature has held."""

    user_id: str
    token_generation: int


def _decode(token: str) -> dict:
    """Verify the signature and expiry, or raise 401. Says nothing about revocation."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )


def create_access_token(user_id: str, *, token_generation: int = 0) -> str:
    """Mint an access token for *user_id*, stamped with its session generation.

    Pass the user's current ``token_generation``. Omitting it stamps the token
    with 0, which is right for a user who has never revoked and wrong — in the
    safe direction — for one who has: ``get_current_user`` refuses it at once,
    rather than handing out a token the revocation cannot reach (#704).
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire, GENERATION_CLAIM: token_generation}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def admin_credential_fingerprint() -> str:
    """A short HMAC over the admin credentials, as they stand right now.

    The admin panel has no user row, so there is no counter to bump and no
    ``token_generation`` to compare against (#704). Binding the token to the
    credentials gives the same property by another route: rotating
    ``ADMIN_PASSWORD`` or ``ADMIN_TOTP_SECRET`` changes this value, and every
    admin token issued under the old one stops verifying.

    That is the sequence that matters — rotating the admin password is exactly
    what an operator does when they suspect a token leaked, and until now it
    left the leaked token working for its full two hours.

    Keyed with ``JWT_SECRET`` so the digest cannot be recomputed from a guessed
    password, and truncated because 128 bits of a SHA-256 HMAC is already far
    more than a two-hour token needs.
    """
    material = "\x00".join((settings.admin_password, settings.admin_totp_secret))
    digest = hmac.new(
        JWT_SECRET.encode("utf-8"), material.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest[:32]


def create_admin_token() -> str:
    """Return a short-lived JWT that grants admin panel access."""
    expire = datetime.now(timezone.utc) + timedelta(hours=2)
    payload = {
        "sub": "admin",
        "role": "admin",
        "exp": expire,
        ADMIN_CREDENTIAL_CLAIM: admin_credential_fingerprint(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def read_access_token(token: str) -> AccessTokenClaims:
    """Return the claims of a well-formed access token, or raise 401.

    The generation is read but not judged here — that needs the user row, and
    the only place that has one is ``get_current_user``. Nothing else should
    authenticate on the strength of this function alone.
    """
    payload = _decode(token)
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )

    generation = payload.get(GENERATION_CLAIM, 0)
    # ``bool`` is an ``int`` in Python, and a token claiming ``true`` would
    # otherwise compare equal to generation 1.
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )

    return AccessTokenClaims(user_id=user_id, token_generation=generation)


def decode_token(token: str) -> str:
    """Return the user id a token names, or raise HTTP 401.

    Identity only: a token revoked by ``/auth/sessions/revoke`` still decodes
    here. Authenticate with ``get_current_user``, which also checks the
    generation.
    """
    return read_access_token(token).user_id


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> models.User:
    """The only place a bearer token becomes an authenticated athlete.

    The generation check lives here rather than in ``read_access_token``
    because this is the function that holds the user row — and it already
    loaded it, which is why making tokens revocable costs no extra query
    (#704).

    The Authelia branch above it authenticates on headers and never looks at a
    token, so revocation does not reach it. That path needs a valid
    proof-of-transit secret since ai-trainer-ops#34, and no router forwards the
    headers that would use it (#696) — but "no generation to check" is a
    property of the branch, not of the routing, and it would need its own answer
    the day a forward-auth proxy is switched on.
    """
    if AUTHELIA_AUTH_ENABLED:
        authelia_user = await _get_or_create_authelia_user(request, db)
        if authelia_user is not None:
            return authelia_user

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )
    claims = read_access_token(credentials.credentials)
    user = await crud.get_user_by_id(db, claims.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    if claims.token_generation != user.token_generation:
        # Either the account signed out everywhere, or an operator revoked it.
        # 401 and not 403: the frontend tears the session down on a 401 with a
        # token, which is exactly the right response to this.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=SESSION_REVOKED_DETAIL
        )
    return user


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """FastAPI dependency — raises 401/403 unless the request carries a valid admin JWT."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )
    payload = _decode(credentials.credentials)
    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )

    # Checked after the role so a plain athlete token still gets the 403 that
    # names the actual problem.
    provided = payload.get(ADMIN_CREDENTIAL_CLAIM)
    if not isinstance(provided, str) or not secrets.compare_digest(
        provided, admin_credential_fingerprint()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ADMIN_SESSION_STALE_DETAIL
        )


async def get_authelia_user(
    request: Request,
    db: AsyncSession,
) -> models.User | None:
    if not AUTHELIA_AUTH_ENABLED:
        return None
    return await _get_or_create_authelia_user(request, db)


_EMAIL_ADAPTER = TypeAdapter(EmailStr)
"""The same validator ``schemas`` applies to a registration (ai-trainer-ops#34).

Deliberately the identical type rather than a second pattern. The question the
header path has to answer is not "is this plausible" but "will this resolve to
the same account the athlete registered", and two validators that normalise
differently answer it differently — a header spelling that normalises one way
here and another way at registration mints a second account instead of finding
the first.
"""


def _request_from_trusted_proxy(request: Request) -> bool:
    """Whether *request* proved it transited the proxy allowed to assert identity.

    The trusted reverse proxy injects AUTHELIA_PROXY_SHARED_SECRET as
    AUTHELIA_PROXY_SECRET_HEADER and *overwrites* any client-supplied value, so a
    request arriving by any other path — direct container access from inside the
    Docker network, the frontend's own nginx, SSRF — cannot carry it.

    An unset secret means no, not yes (ai-trainer-ops#34). It used to mean yes,
    which read as defence in depth and was the opposite: the secret is `optional`
    in ``deploy/forwarded-vars.yml`` and empty on this deployment, so the backend
    trusted ``Remote-Email`` on *any* request that reached it. Measured rather
    than reasoned about: a forged header with no bearer token at all returned 200
    from ``/api/v1/users/me`` for another athlete's account, and
    ``/api/v1/auth/session`` minted them a seven-day JWT.

    What stood between that and the internet was two hand-maintained header-strip
    lists — Traefik's ``backend-strip-remote`` middleware and the frontend
    nginx's ``proxy_set_header`` block — either of which is one label edit or one
    proxy upgrade away from not holding. Failing closed makes the backend's own
    check the boundary and leaves those two as the defence in depth they were
    always described as.
    """
    if not AUTHELIA_PROXY_SHARED_SECRET:
        return False
    provided = request.headers.get(AUTHELIA_PROXY_SECRET_HEADER)
    return provided is not None and secrets.compare_digest(
        provided, AUTHELIA_PROXY_SHARED_SECRET
    )


def _normalised_email(raw: str) -> str | None:
    """*raw* as a normalised address, or None if it is not one.

    This is the one path that creates an account without anyone's password, and
    until ai-trainer-ops#34 it was also the only one that skipped ``EmailStr``:
    whatever bytes arrived in the header became a row. Validating here closes the
    asymmetry and, because the adapter normalises, stops two spellings of one
    address from becoming two accounts.

    Takes the value rather than the request so there is one place that decides
    the header is present — its caller, which has to make that call before the
    proof-of-transit check anyway. A second emptiness guard here would be a
    branch no test can reach, and an unexercised branch is one a reader trusts
    without having seen it run.
    """
    try:
        return _EMAIL_ADAPTER.validate_python(raw)
    except ValidationError:
        # The value is somebody's identity, so it stays out of the log; its
        # length is what tells an operator whether this is a misconfigured proxy
        # or something probing (#499).
        logger.warning(
            "Ignoring Authelia %s header: the value is not an email address "
            "(%d characters).",
            AUTHELIA_REMOTE_EMAIL_HEADER,
            len(raw),
        )
        return None


async def _get_or_create_authelia_user(
    request: Request,
    db: AsyncSession,
) -> models.User | None:
    # Presence first, so the proof-of-transit warning below fires only for a
    # request that actually tried to assert an identity. Checking transit first
    # would log on every anonymous request instead, which is the same as not
    # logging: the one case worth seeing — Traefik's strip stopped working —
    # would arrive buried in its own noise.
    asserted = request.headers.get(AUTHELIA_REMOTE_EMAIL_HEADER)
    if not asserted:
        return None

    if not _request_from_trusted_proxy(request):
        logger.warning(
            "Ignoring Authelia %s header: request did not arrive via the trusted "
            "proxy (missing or invalid %s).",
            AUTHELIA_REMOTE_EMAIL_HEADER,
            AUTHELIA_PROXY_SECRET_HEADER,
        )
        return None

    email = _normalised_email(asserted)
    if email is None:
        return None

    user = await crud.get_user_by_email(db, email)
    if user is not None:
        return user

    remote_name = request.headers.get(AUTHELIA_REMOTE_NAME_HEADER)
    remote_user = request.headers.get(AUTHELIA_REMOTE_USER_HEADER)
    await crud.create_user(
        db,
        email=email,
        name=remote_name or remote_user,
        # Authelia-managed users do not authenticate via local password login.
        # A random one-way hash ensures no reusable local password exists.
        hashed_password=hash_password(secrets.token_urlsafe(32)),
    )
    # Re-read rather than return what ``create_user`` built: this is an auth
    # dependency, so the instance it returns gets serialised by whatever route
    # asked for it, and a freshly constructed one carries none of its
    # relationships. The first request of every new Authelia identity used to
    # end in MissingGreenlet — a 500 on the account-creating path.
    return await crud.get_user_by_email(db, email)
