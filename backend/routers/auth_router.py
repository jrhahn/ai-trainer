"""Authentication routes."""

import logging
import secrets
from datetime import datetime, timedelta, timezone

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
    require_password,
    verify_store_credentials,
)
from services import authelia_store
from services import captcha as captcha_service
from services import totp as totp_service

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


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
            authelia_store.create_user(body.email, body.name, body.password)
        except authelia_store.EmailTaken as exc:
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
    token = auth.create_access_token(
        user.id, token_generation=user.token_generation
    )
    return schemas.TokenResponse(access_token=token)


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
    return schemas.TokenResponse(
        access_token=auth.create_access_token(
            user.id, token_generation=user.token_generation
        )
    )


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

        if not verify_store_credentials(body.email, body.password):
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
    # ``password_matches`` and not ``verify_password``: it takes None for "no
    # such account" and still spends the Argon2 verification, so the reply does
    # not arrive 20x faster for an address nobody has registered
    # (ai-trainer-ops#35).
    if not auth.password_matches(
        body.password, user.hashed_password if user is not None else None
    ):
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
    return schemas.TokenResponse(
        access_token=auth.create_access_token(
            user.id, token_generation=user.token_generation
        )
    )


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
    require_password(current_user, body.password)

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


# ---------------------------------------------------------------------------
# Session revocation (#704)
# ---------------------------------------------------------------------------


@router.post("/sessions/revoke", response_model=schemas.SessionRevokeResponse)
async def revoke_sessions(
    body: schemas.SessionRevokeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> schemas.SessionRevokeResponse:
    """Sign out everywhere, including here.

    Until this existed, an access token could not be taken back at all: seven
    days of validity, and the only lever was rotating ``JWT_SECRET``, which
    signs out every athlete at once and re-keys the captcha and TOTP-challenge
    HMACs along the way, because both derive from it. A control that drastic
    does not get pulled, which is the same as not having one.

    Requires the password, like ``/totp/disable``: a borrowed unlocked browser
    must not be able to lock the owner out of their own account.

    The commit is explicit. A revocation that is reported as done and then
    rolled back by a later failure in the same request would be the worst
    possible outcome here — the athlete has been told their sessions are gone
    and would stop looking for the problem.
    """
    enforce_login_rate_limit(current_user.email)
    require_password(current_user, body.password)

    generation = await crud.revoke_user_tokens(db, current_user.id)
    await db.commit()

    logger.info(
        "Revoked all sessions for user %s at the athlete's request; "
        "token generation is now %s.",
        current_user.id,
        generation,
    )
    return schemas.SessionRevokeResponse(token_generation=generation)
