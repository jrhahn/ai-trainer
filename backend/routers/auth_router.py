"""Authentication routes."""

import contextlib
import fcntl
import logging
import os
import secrets
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import get_db
from routers.dependencies import (
    enforce_captcha_rate_limit,
    enforce_login_rate_limit,
    enforce_registration_rate_limit,
    enforce_totp_code_rate_limit,
)
from services import captcha as captcha_service
from services import totp as totp_service

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


def _carry_over_file_identity(original: Path, replacement: str) -> None:
    """Give *replacement* the mode and ownership that *original* has.

    ``os.replace`` swaps in a new inode, so without this an atomic write
    silently re-owns the file to whoever ran it and resets the mode to
    ``mkstemp``'s 0600. That is how #684 happened: a registration handled while
    the backend still ran as root rewrote the store as ``root:root 0600``, and
    the next deploy — which moved the backend to uid 1001 — could no longer
    read it. Login and registration both read this file, so the whole auth
    surface went down until the ownership was restored by hand.

    Ownership is best-effort: ``chown`` needs privilege the container
    deliberately no longer has, and failing the registration over it would be
    worse than writing a file the process already owns. The mode is not
    best-effort — a widened mode on a file of password hashes is a real
    regression, and the process always owns the temp file, so the call cannot
    fail for lack of privilege.
    """
    try:
        stat_result = original.stat()
    except FileNotFoundError:
        return
    os.chmod(replacement, stat.S_IMODE(stat_result.st_mode))
    if (os.geteuid(), os.getegid()) != (stat_result.st_uid, stat_result.st_gid):
        with contextlib.suppress(PermissionError, OSError):
            os.chown(replacement, stat_result.st_uid, stat_result.st_gid)


def _create_authelia_user(email: str, display_name: str, password: str) -> None:
    """Append a new user entry to Authelia's file-based users_database.yml."""
    db_path = Path(auth.AUTHELIA_USERS_DB_PATH)
    if not db_path.exists():
        raise RuntimeError(f"Authelia users database not found at {db_path}")

    # Use a separate lock file so the main file is never partially written.
    lock_path = db_path.with_suffix(".lock")
    with open(lock_path, "w", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            with open(db_path, "r", encoding="utf-8") as fh:
                data: dict[str, Any] = yaml.safe_load(fh) or {}
            users: dict[str, Any] = data.get("users") or {}

            # Reject if the email is already taken
            for entry in users.values():
                if entry.get("email") == email:
                    raise ValueError("Email already registered")

            # Use email as the username key; also check the key itself
            if email in users:
                raise ValueError("Email already registered")

            users[email] = {
                "disabled": False,
                "displayname": display_name,
                "email": email,
                "password": auth.hash_password(password),
                "groups": [],
            }
            data["users"] = users

            # Write atomically: write to a temp file in the same directory, then
            # rename over the target.  This ensures Authelia's file-watcher always
            # sees a complete, valid YAML file and receives a single clean inotify
            # CREATE event rather than a truncate-then-write sequence that can
            # cause Authelia to cache an empty user list.
            tmp_fd, tmp_name = tempfile.mkstemp(
                dir=db_path.parent, suffix=".tmp", prefix="users_database_"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_fh:
                    yaml.dump(data, tmp_fh, default_flow_style=False, allow_unicode=True)
                _carry_over_file_identity(db_path, tmp_name)
                os.replace(tmp_name, db_path)
            except Exception:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_name)
                raise
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


@router.get("/captcha/challenge", response_model=schemas.CaptchaChallengeResponse)
async def captcha_challenge() -> schemas.CaptchaChallengeResponse:
    """Issue a proof-of-work challenge for the registration form (#686).

    Rate-limited in its own right: issuing is cheap but not free, and an
    unbounded challenge endpoint is a way to make this server hash on demand.
    The limit is looser than the registration one because a human who reloads
    the form legitimately asks for several.

    404 when the captcha is switched off, rather than a challenge nobody will
    check. That is what tells the client to skip solving — otherwise every
    registration on a deployment that disabled this would still burn a few
    hundred milliseconds of the user's CPU for nothing.
    """
    if not settings.captcha_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Captcha is not enabled on this server.",
        )
    enforce_captcha_rate_limit()
    return schemas.CaptchaChallengeResponse(**captcha_service.issue_challenge().as_dict())


@router.post("/register", response_model=schemas.TokenResponse)
async def register(
    body: schemas.RegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenResponse | Response:
    # Before either branch: registration is public on this deployment (it has
    # its own Traefik router) and the Authelia branch below writes to the user
    # store on disk, so the limit has to sit in front of both (#682).
    enforce_registration_rate_limit()

    # And before any of the work: a request that cannot show a solved challenge
    # should cost this server a signature check, not a database round trip and
    # an Argon2 hash (#686).
    try:
        captcha_service.verify_solution(
            body.captcha.model_dump() if body.captcha is not None else None
        )
    except captcha_service.CaptchaError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if auth.AUTHELIA_AUTH_ENABLED:
        if not auth.AUTHELIA_USERS_DB_PATH:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="User registration is not configured on this server.",
            )

        existing = await crud.get_user_by_email_simple(db, body.email)
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

        try:
            _create_authelia_user(body.email, body.name, body.password)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

        await crud.create_user(
            db,
            email=body.email,
            name=body.name,
            hashed_password=auth.hash_password(secrets.token_urlsafe(32)),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    existing = await crud.get_user_by_email_simple(db, body.email)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = await crud.create_user(
        db,
        email=body.email,
        name=body.name,
        hashed_password=auth.hash_password(body.password),
    )

    # Commit before issuing the token so the user row is visible to subsequent
    # requests that arrive immediately after registration (e.g. in integration
    # tests).  FastAPI's generator dependency (`get_db`) commits after the
    # response is sent, which creates a race window; an explicit commit here
    # closes it.
    await db.commit()
    token = auth.create_access_token(user.id)
    return schemas.TokenResponse(access_token=token)


def _verify_authelia_credentials(email: str, password: str) -> bool:
    """Verify credentials directly against Authelia's file-based users_database.yml.

    This avoids calling Authelia's /api/firstfactor REST endpoint, which is a
    browser-session API (requires an existing session cookie) and will reject
    server-side requests without that context.
    """
    db_path = Path(auth.AUTHELIA_USERS_DB_PATH)
    if not db_path.exists():
        return False

    try:
        with open(db_path, "r", encoding="utf-8") as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}
    except OSError as exc:
        # Not "wrong password" — the store is there and we cannot read it, which
        # is an operator problem and must not be reported as a credential one.
        # It happened twice (#684, #696): the file is shared with the Authelia
        # container, whose entrypoint chowns it to root, while this process runs
        # as uid 1001. Before this, it surfaced as a raw traceback and a 500 on
        # every login — true but useless. The boot-time check in ``auth`` cannot
        # help either, because the condition appears long after boot, whenever
        # the other container happens to restart.
        logger.error(
            "Cannot read the Authelia user store at %s (uid=%s): %s. "
            "Login is down for every user until the file is readable by this "
            "process — check ownership of the bind mount.",
            db_path,
            os.getuid(),
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
        ) from exc

    users: dict[str, Any] = data.get("users") or {}

    # Users are keyed by username (set to email at registration).
    # Also support lookup by the email field for flexibility.
    user_entry: dict[str, Any] | None = None
    for key, entry in users.items():
        if key == email or entry.get("email") == email:
            user_entry = entry
            break

    if user_entry is None or user_entry.get("disabled", False):
        return False

    hashed = user_entry.get("password", "")
    return auth.verify_password(password, hashed)


TRUSTED_DEVICE_COOKIE = "tlap_device"


async def _has_trusted_device(request: Request, db: AsyncSession, user_id: str) -> bool:
    """Whether this browser already proved a second factor recently (#688)."""
    token = request.cookies.get(TRUSTED_DEVICE_COOKIE)
    if not token:
        return False
    device = await crud.get_trusted_device(
        db,
        user_id,
        totp_service.hash_device_token(token),
        now=datetime.now(timezone.utc),
    )
    return device is not None


def _set_trusted_device_cookie(response: Response, token: str) -> None:
    """Store the device token where JavaScript cannot read it.

    ``httponly`` matters more here than for the JWT, which already lives in
    sessionStorage by design: this value survives the tab, so an XSS that could
    read it would keep bypassing the second factor for a month.

    ``secure`` follows the environment rather than being hard-coded true.
    Production is HTTPS-only (HSTS since #682) and a second-factor bypass has
    no business travelling in clear, so it is set there. But a browser silently
    *discards* a Secure cookie sent over http, and development runs on
    ``http://localhost`` — hard-coding it would have made the feature
    impossible to use or test outside production, which is how a flag ends up
    switched off to "make it work locally". ``is_dev_environment`` is the same
    switch the encryption-key requirement already uses.
    """
    response.set_cookie(
        TRUSTED_DEVICE_COOKIE,
        token,
        max_age=settings.trusted_device_days * 24 * 3600,
        httponly=True,
        secure=not settings.is_dev_environment,
        samesite="lax",
        path="/",
    )


async def _complete_login(
    user: models.User,
    *,
    request: Request,
    response: Response,
    db: AsyncSession,
) -> schemas.TokenResponse | schemas.LoginChallengeResponse:
    """The one place a login turns into a token (#688).

    Both branches of ``login`` used to end by issuing one, which is exactly the
    shape that lets a second factor be enforced on one path and forgotten on
    the other — the same reasoning that put the rate limit in front of the
    branch rather than inside it (#682).

    A user with TOTP on gets a signed challenge instead, unless this browser
    carries a live trusted-device cookie.
    """
    if user.totp_enabled and not await _has_trusted_device(request, db, user.id):
        return schemas.LoginChallengeResponse(
            challenge=totp_service.issue_challenge(user.id)
        )

    user.last_login = datetime.now(timezone.utc)
    return schemas.TokenResponse(access_token=auth.create_access_token(user.id))


@router.post("/login")
async def login(
    body: schemas.LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenResponse | schemas.LoginChallengeResponse:
    # Both branches below verify a password, so the limit goes in front of the
    # branch rather than inside it — otherwise flipping AUTHELIA_AUTH_ENABLED
    # would silently change whether brute force is bounded (#682).
    enforce_login_rate_limit(body.email)

    if auth.AUTHELIA_AUTH_ENABLED:
        if not auth.AUTHELIA_USERS_DB_PATH:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service is not configured.",
            )

        if not _verify_authelia_credentials(body.email, body.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password.",
            )

        user = await crud.get_user_by_email_simple(db, body.email)
        if user is None:
            user = await crud.create_user(
                db,
                email=body.email,
                name=body.email.split("@")[0],
                hashed_password=auth.hash_password(secrets.token_urlsafe(32)),
            )

        return await _complete_login(user, request=request, response=response, db=db)

    user = await crud.get_user_by_email_simple(db, body.email)
    if user is None or not auth.verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    # Transparently upgrade a legacy bcrypt (or outdated) hash to the current
    # Argon2 scheme now that we have the plaintext and it verified (#329).
    if auth.password_needs_rehash(user.hashed_password):
        user.hashed_password = auth.hash_password(body.password)

    return await _complete_login(user, request=request, response=response, db=db)


@router.get("/session")
async def session_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenResponse | schemas.LoginChallengeResponse:
    """Exchange an Authelia portal session for an app token.

    Routed through ``_complete_login`` like the password paths, so it honours
    the second factor too (#688). This endpoint issues a token without ever
    seeing a password, which makes it the obvious way to walk around TOTP.

    It is currently unreachable — the forward-auth middlewares are attached to
    no router, so ``Remote-*`` headers never arrive and ``get_authelia_user``
    returns None (#696). That is a routing accident, not a guarantee, and it is
    one label edit away from not being true.
    """
    user = await auth.get_authelia_user(request, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authelia session not found")
    return await _complete_login(user, request=request, response=response, db=db)


# ---------------------------------------------------------------------------
# Second factor (#688)
# ---------------------------------------------------------------------------


async def _consume_recovery_code(
    db: AsyncSession, user: models.User, code: str
) -> bool:
    """Spend a recovery code if *code* matches one, else report no match.

    Compared against every stored hash rather than looked up: they are hashed
    with a salted password hasher, so there is nothing to index on. Ten codes
    means ten verifies in the worst case, only on a path that is already
    rate-limited.
    """
    normalised = totp_service.normalise_recovery_code(code)
    if not normalised:
        return False
    for stored in await crud.list_recovery_codes(db, user.id):
        if auth.verify_password(normalised, stored.code_hash):
            await crud.consume_recovery_code(db, stored)
            return True
    return False


@router.post("/login/totp")
async def login_totp(
    body: schemas.TotpLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenResponse:
    """Second step: exchange a challenge plus a code for a token.

    The challenge is what carries "a password was already accepted" between the
    two requests, signed so the client cannot mint one. Accepting a recovery
    code here as well as a TOTP code keeps the lost-phone path on the same
    rate limit and the same single-use challenge, rather than giving it a
    weaker door of its own.
    """
    try:
        user_id = totp_service.read_challenge(body.challenge)
    except totp_service.TotpError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    # Keyed on the user the challenge names, so the limit cannot be sidestepped
    # by fetching a fresh challenge for each guess.
    enforce_totp_code_rate_limit(user_id)

    user = await crud.get_user_by_id(db, user_id)
    if user is None or not user.totp_enabled or not user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid sign-in challenge."
        )

    accepted = totp_service.verify_code(user.totp_secret, body.code)
    if not accepted:
        accepted = await _consume_recovery_code(db, user, body.code)
    if not accepted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That code is not valid. Check your authenticator app and try again.",
        )

    if body.remember_device:
        device_token = totp_service.new_device_token()
        await crud.add_trusted_device(
            db,
            user.id,
            token_hash=totp_service.hash_device_token(device_token),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.trusted_device_days),
            user_agent=request.headers.get("user-agent"),
        )
        _set_trusted_device_cookie(response, device_token)

    user.last_login = datetime.now(timezone.utc)
    return schemas.TokenResponse(access_token=auth.create_access_token(user.id))


@router.get("/totp/status", response_model=schemas.TotpStatusResponse)
async def totp_status(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.TotpStatusResponse:
    now = datetime.now(timezone.utc)
    return schemas.TotpStatusResponse(
        enabled=current_user.totp_enabled,
        confirmed_at=current_user.totp_confirmed_at,
        recovery_codes_remaining=len(await crud.list_recovery_codes(db, current_user.id)),
        trusted_device_count=await crud.count_trusted_devices(db, current_user.id, now=now),
    )


@router.post("/totp/enroll", response_model=schemas.TotpEnrollResponse)
async def totp_enroll(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.TotpEnrollResponse:
    """Start enrollment: generate a secret and hand back a QR.

    The secret is stored but ``totp_enabled`` stays false until a code proves
    the athlete can actually produce one. Enabling here would lock out anyone
    whose camera failed halfway through — the exact person least able to
    recover from it.

    Re-enrolling while already enabled is refused rather than silently
    replacing the secret: that would invalidate the authenticator entry in use
    without warning, and it is what an attacker with a borrowed session would
    try. Disable first, which needs the password.
    """
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Two-factor authentication is already on. Turn it off first to re-enroll.",
        )

    secret = totp_service.new_secret()
    current_user.totp_secret = secret
    await db.flush()

    uri = totp_service.provisioning_uri(secret, account=current_user.email)
    return schemas.TotpEnrollResponse(
        secret=totp_service.encode_secret_for_display(secret),
        provisioning_uri=uri,
        qr_svg=totp_service.qr_svg(uri),
    )


@router.post("/totp/confirm", response_model=schemas.TotpRecoveryCodesResponse)
async def totp_confirm(
    body: schemas.TotpConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.TotpRecoveryCodesResponse:
    """Verify the first code, switch the factor on, and issue recovery codes.

    The codes are returned exactly once. Storing them retrievably would make
    the account's backup door readable by anything that can reach this
    endpoint with a live session.
    """
    enforce_totp_code_rate_limit(current_user.id)

    if not current_user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Start enrollment before confirming a code.",
        )
    if not totp_service.verify_code(current_user.totp_secret, body.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That code is not valid. Check the time on your phone and try again.",
        )

    codes = totp_service.new_recovery_codes()
    await crud.replace_recovery_codes(
        db,
        current_user.id,
        [auth.hash_password(totp_service.normalise_recovery_code(c)) for c in codes],
    )
    # Any device trusted before now was trusted under no second factor at all.
    await crud.revoke_trusted_devices(db, current_user.id)

    current_user.totp_enabled = True
    current_user.totp_confirmed_at = datetime.now(timezone.utc)
    await db.flush()

    return schemas.TotpRecoveryCodesResponse(recovery_codes=codes)


@router.post("/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(
    body: schemas.TotpDisableRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    """Turn the second factor off. Requires the password, not just a session.

    A borrowed unlocked browser should not be enough to strip the protection
    the second factor exists to provide.
    """
    enforce_login_rate_limit(current_user.email)

    if auth.AUTHELIA_AUTH_ENABLED:
        valid = _verify_authelia_credentials(current_user.email, body.password)
    else:
        valid = auth.verify_password(body.password, current_user.hashed_password)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password."
        )

    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_confirmed_at = None
    await crud.replace_recovery_codes(db, current_user.id, [])
    await crud.revoke_trusted_devices(db, current_user.id)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/totp/trusted-devices/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_trusted_devices(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    """Forget every trusted device — the answer to a lost laptop."""
    await crud.revoke_trusted_devices(db, current_user.id)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
