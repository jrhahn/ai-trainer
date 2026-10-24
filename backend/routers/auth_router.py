"""Authentication routes."""

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import models
import schemas
from config import settings
from database import async_session_maker, get_db
from routers.dependencies import (
    enforce_captcha_rate_limit,
    enforce_login_rate_limit,
    enforce_password_reset_rate_limit,
    enforce_registration_rate_limit,
    enforce_totp_code_rate_limit,
    require_password,
    verify_store_credentials,
)
from services import authelia_store
from services import captcha as captcha_service
from services import invite_codes
from services import email_verification
from services import mailer
from services import totp as totp_service
from services.athlete_language import interface_language

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


CONSENT_REQUIRED_DETAIL = (
    "Please agree to the privacy policy and the processing of your health data to "
    "create an account."
)

INVITE_REQUIRED_DETAIL = (
    "Sign-up is by invitation only for now. Use the link from your invitation, "
    "or check the code."
)


@router.get("/legal", response_model=schemas.LegalContactResponse)
async def legal_contact() -> schemas.LegalContactResponse:
    """The controller and contact the privacy page and the legal notice name
    (ai-trainer-ops#2, #15, #1).

    From configuration, so the owner sets them as variables rather than in code.
    """
    return schemas.LegalContactResponse(
        operator_name=settings.operator_name.strip() or None,
        operator_address=settings.operator_address.strip() or None,
        contact_email=settings.contact_email.strip() or None,
        coach_review_opt_in=settings.coach_review_opt_in,
    )


@router.get("/signup", response_model=schemas.SignupConfigResponse)
async def signup_config() -> schemas.SignupConfigResponse:
    """Whether a registration needs an invitation code (ops#108, #96)."""
    return schemas.SignupConfigResponse(invite_only=settings.signup_invite_only)


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


@router.post(
    "/register", response_model=schemas.TokenResponse | schemas.SessionResponse
)
async def register(
    body: schemas.RegisterRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> schemas.TokenResponse | schemas.SessionResponse | Response:
    # Before either branch: registration is public on this deployment (it has
    # its own Traefik router) and the Authelia branch below writes to the user
    # store on disk, so the limit has to sit in front of both (#682).
    # Captcha first: a request that cannot show a solved challenge costs this
    # server a signature check, not a signup slot, a database round trip or an
    # Argon2 hash (#686, ops#108). Spending the global allowance before the
    # challenge let anyone block every signup with five anonymous POSTs an hour.
    try:
        captcha_service.verify_solution(
            body.captcha.model_dump() if body.captcha is not None else None
        )
    except captcha_service.CaptchaError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # Health data need explicit consent (Art. 9(2)(a) GDPR, ai-trainer-ops#2).
    if settings.require_privacy_consent and not body.privacy_consent:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CONSENT_REQUIRED_DETAIL)

    # A closed round (ops#108, #96): no unused invitation, no account. Checked
    # before the allowance, so a stranger without a code cannot spend the
    # hour's sign-ups; used further down, once the address is known to be free.
    if settings.signup_invite_only and not await invite_codes.is_open(db, body.invite_code):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=INVITE_REQUIRED_DETAIL)

    # Spend the allowance after the captcha, before the work: a request with no
    # solved challenge is already a 400 above and never reaches here, so five
    # anonymous POSTs can no longer exhaust the hour's signups without any
    # proof of work (ops#108). A duplicate still costs a slot on purpose — that
    # is what keeps the address-taken oracle bounded by this limit rather than
    # only by the captcha (test_asking_whether_an_address_is_taken_spends_the_
    # registration_allowance). Whether to drop that too — count only accounts
    # actually created — is the owner's call, tied to the invite-code question.
    enforce_registration_rate_limit()

    # Existence check, hoisted so it runs once for both branches below.
    existing = await crud.get_user_by_email_simple(db, body.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    # Used in this request's transaction: a registration that fails below
    # leaves the code unused. The conditional update is what stops two sign-ups
    # racing with one code.
    if settings.signup_invite_only and not await invite_codes.redeem(db, body.invite_code):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=INVITE_REQUIRED_DETAIL)

    if auth.AUTHELIA_AUTH_ENABLED:
        if not auth.AUTHELIA_USERS_DB_PATH:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="User registration is not configured on this server.",
            )

        try:
            await asyncio.to_thread(authelia_store.create_user, body.email, body.name, body.password)
        except authelia_store.EmailTaken as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

        await crud.create_user(
            db,
            email=body.email,
            name=body.name,
            hashed_password=await asyncio.to_thread(auth.hash_password, secrets.token_urlsafe(32)),
            verified=not email_verification.required(),
            privacy_consent=body.privacy_consent,
        )
        if email_verification.required():
            return await _ask_to_confirm(db, background, body.email, interface_language(request))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    try:
        user = await crud.create_user(
            db,
            email=body.email,
            name=body.name,
            hashed_password=await asyncio.to_thread(auth.hash_password, body.password),
            verified=not email_verification.required(),
            privacy_consent=body.privacy_consent,
        )
    except IntegrityError as exc:
        # The lower(email) unique index caught a case variant that the existence
        # check above raced (ops#109): answer 409, not a 500.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    if email_verification.required():
        return await _ask_to_confirm(db, background, body.email, interface_language(request))

    # Commit before issuing the token so the user row is visible to subsequent
    # requests that arrive immediately after registration (e.g. in integration
    # tests). The session's own commit used to run after the response was sent,
    # which left a race window; since ai-trainer-ops#161 every route takes
    # `get_db` with scope="function" and commits before the response anyway, so
    # this is now explicit rather than necessary.
    await db.commit()
    return _sign_in(user, request=request, response=response)


async def _ask_to_confirm(
    db: AsyncSession, background: BackgroundTasks, email: str, language: str | None
) -> JSONResponse:
    """Answer a registration that has to confirm its address first (app #687).

    Committed before the answer: the link goes out after the response, from its
    own session, and the request's transaction would otherwise close only after
    it (#522), leaving the mail a user it cannot find.
    """
    await db.commit()
    background.add_task(email_verification.send_link, async_session_maker, email, language)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED, content={"verificationRequired": True}
    )


AUTH_MODE_HEADER = "X-Auth-Mode"


def _sign_in(
    user: models.User, *, request: Request, response: Response
) -> schemas.TokenResponse | schemas.SessionResponse:
    """Issue the session every sign-in path ends in.

    The web app sends ``X-Auth-Mode: cookie`` and gets the session as an
    HttpOnly cookie plus the CSRF token that goes with it, so the body carries
    nothing a script could steal (ai-trainer-ops#45). Any other client still
    gets a bearer token, as before.
    """
    if request.headers.get(AUTH_MODE_HEADER) == "cookie":
        return _start_cookie_session(user, response)
    return schemas.TokenResponse(
        access_token=auth.create_access_token(
            user.id, token_generation=user.token_generation
        )
    )


def _start_cookie_session(
    user: models.User, response: Response, *, session_id: str | None = None
) -> schemas.SessionResponse:
    """Set the session cookie; a renewal passes the session id it keeps."""
    session_id = session_id or secrets.token_urlsafe(16)
    token = auth.create_access_token(
        user.id,
        token_generation=user.token_generation,
        lifetime=timedelta(days=auth.SESSION_DAYS),
        session_id=session_id,
    )
    # Same flags as the trusted-device cookie below, for the same reasons:
    # HttpOnly always, Secure wherever the site is HTTPS.
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 24 * 3600,
        httponly=True,
        secure=not settings.is_dev_environment,
        samesite="lax",
        path="/",
    )
    return schemas.SessionResponse(csrf_token=auth.csrf_token_for(session_id))


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
) -> schemas.TokenResponse | schemas.SessionResponse | schemas.LoginChallengeResponse:
    """The one place a login turns into a token (#688).

    Both branches of ``login`` used to end by issuing one, which is exactly the
    shape that lets a second factor be enforced on one path and forgotten on
    the other — the same reasoning that put the rate limit in front of the
    branch rather than inside it (#682).

    A user with TOTP on gets a signed challenge instead, unless this browser
    carries a live trusted-device cookie.

    An account that has not confirmed its address gets no token at all (app
    #687), so it can neither use the coach nor spend a provider's tokens.
    """
    if user.email_verified_at is None and email_verification.required():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=EMAIL_NOT_VERIFIED
        )
    if user.totp_enabled and not await _has_trusted_device(request, db, user.id):
        return schemas.LoginChallengeResponse(
            challenge=totp_service.issue_challenge(user.id)
        )

    user.last_login = datetime.now(timezone.utc)
    return _sign_in(user, request=request, response=response)


@router.post("/login")
async def login(
    body: schemas.LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> schemas.TokenResponse | schemas.SessionResponse | schemas.LoginChallengeResponse:
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

        if not await asyncio.to_thread(verify_store_credentials, body.email, body.password):
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
                hashed_password=await asyncio.to_thread(auth.hash_password, secrets.token_urlsafe(32)),
            )

        return await _complete_login(user, request=request, response=response, db=db)

    user = await crud.get_user_by_email_simple(db, body.email)
    # ``password_matches`` and not ``verify_password``: it takes None for "no
    # such account" and still spends the Argon2 verification, so the reply does
    # not arrive 20x faster for an address nobody has registered
    # (ai-trainer-ops#35).
    if not await asyncio.to_thread(
        auth.password_matches, body.password, user.hashed_password if user is not None else None
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    # Transparently upgrade a legacy bcrypt (or outdated) hash to the current
    # Argon2 scheme now that we have the plaintext and it verified (#329).
    if auth.password_needs_rehash(user.hashed_password):
        user.hashed_password = await asyncio.to_thread(auth.hash_password, body.password)

    return await _complete_login(user, request=request, response=response, db=db)


# ---------------------------------------------------------------------------
# Password reset by email (ai-trainer-ops#35, #14)
# ---------------------------------------------------------------------------

RESET_NOT_AVAILABLE = (
    "Resetting a password by email is not available on this server. "
    "Ask the person who invited you; they can reset it for you."
)
RESET_LINK_INVALID = "This link is no longer valid. Ask for a new one."
EMAIL_NOT_VERIFIED = (
    "Confirm your email address first: open the link in the mail we sent when "
    "you signed up, or ask for a new one."
)


def _hash_reset_token(token: str) -> str:
    """SHA-256, as for trusted devices: the token is 256 random bits, so
    there is nothing a slow hash would protect (``totp.hash_device_token``)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _send_reset_link(email: str, language: str | None = None) -> None:
    """Look the address up and mail a link, after the response has gone.

    Everything that differs between an address with an account and one
    without happens here, so the reply to the request takes the same time
    either way: the one thing the request must not tell is whether the
    address has an account (ai-trainer-ops#35). Failures are logged, never
    raised: there is nobody left to answer.
    """
    try:
        async with async_session_maker() as db:
            user = await crud.get_user_by_email_simple(db, email)
            if user is None:
                return
            token = secrets.token_urlsafe(32)
            await crud.create_password_reset_token(
                db,
                user.id,
                _hash_reset_token(token),
                datetime.now(timezone.utc)
                + timedelta(minutes=settings.password_reset_token_minutes),
            )
            await db.commit()
            # In the fragment, not the query: a fragment never reaches a
            # server, so the token stays out of access logs and Referer headers.
            link = f"{settings.primary_frontend_url}/reset-password#token={token}"
            minutes = settings.password_reset_token_minutes
            # In the language of the page that asked (ai-trainer-ops#220).
            if language == "German":
                subject = "Setze dein Passwort bei Train Like a Pro zurück"
                body = (
                    (f"Hallo {user.name},\n\n" if user.name else "Hallo,\n\n")
                    + "jemand möchte das Passwort deines Kontos bei Train Like a Pro "
                    "zurücksetzen. Wenn du das warst, öffne diesen Link innerhalb von "
                    f"{minutes} Minuten:\n\n{link}\n\n"
                    "Der Link funktioniert einmal. Falls du das nicht angefordert hast, "
                    "ignoriere diese Mail. Dein Passwort bleibt, wie es ist.\n"
                )
            else:
                subject = "Reset your Train Like a Pro password"
                body = (
                    f"Hello {user.name or 'there'},\n\n"
                    "someone asked to reset the password of your Train Like a Pro "
                    f"account. If that was you, open this link within {minutes} "
                    f"minutes:\n\n{link}\n\n"
                    "The link works once. If you did not ask for this, ignore this "
                    "mail; your password stays as it is.\n"
                )
            await mailer.send_mail(user.email, subject, body)
            # Logs the user id only; "password" is the topic, not the value.
            logger.info(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                "Sent a password reset link to user %s", user.id
            )
    except Exception:
        logger.exception("Could not send a password reset link")


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    body: schemas.PasswordResetRequest, background: BackgroundTasks, request: Request
) -> dict:
    """Mail a reset link if the address has an account; answer the same either way."""
    if not mailer.mail_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=RESET_NOT_AVAILABLE
        )
    enforce_password_reset_rate_limit(str(body.email))
    background.add_task(_send_reset_link, str(body.email), interface_language(request))
    return {
        "detail": "If this address has an account, a link to reset the password "
        "is on its way. It works for "
        f"{settings.password_reset_token_minutes} minutes."
    }


async def _store_password(user: models.User, password: str) -> bool:
    """Set the password where login checks it; ``False`` when the store has no
    active entry for the account.

    With Authelia as the user store that is the store, not the column (which
    holds a random throwaway, see ``register``); without a store login answers
    503, and so does this.
    """
    if not auth.AUTHELIA_AUTH_ENABLED:
        user.hashed_password = await asyncio.to_thread(auth.hash_password, password)
        return True
    if not auth.AUTHELIA_USERS_DB_PATH:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is not configured.",
        )
    try:
        return await asyncio.to_thread(authelia_store.set_password, user.email, password)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(
    body: schemas.PasswordResetConfirm, db: AsyncSession = Depends(get_db, scope="function")
) -> Response:
    """Set a new password from a reset link, end every session, and confirm the address.

    Signing out everywhere is the point of a reset as much as the new
    password: whoever had the old one may still hold a session.
    """
    now = datetime.now(timezone.utc)
    token = await crud.get_password_reset_token(db, _hash_reset_token(body.token))
    if token is None or token.used_at is not None or token.expires_at.replace(
        tzinfo=token.expires_at.tzinfo or timezone.utc
    ) <= now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=RESET_LINK_INVALID)
    user = await crud.get_user_by_id(db, token.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=RESET_LINK_INVALID)
    if problem := schemas.password_problem(body.password, user.email, user.name or ""):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)
    # Claimed before anything is written, so a second confirmation with the
    # same link loses here. A failure below rolls the claim back with the rest.
    if not await crud.claim_password_reset_token(db, token, now):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=RESET_LINK_INVALID)

    if not await _store_password(user, body.password):
        logger.warning(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            "Password reset for user %s found no active store entry", user.id
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=RESET_LINK_INVALID)

    # The reset link reached the account's mailbox as surely as a confirmation
    # link does, so it confirms the address too (ai-trainer-ops#103 Q29). Before
    # this, someone who lost the first mail had a new password and no way in.
    if user.email_verified_at is None:
        user.email_verified_at = now
    await crud.revoke_user_tokens(db, user.id)
    await db.commit()
    logger.info(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        "Reset the password of user %s and ended every session", user.id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: schemas.PasswordChangeRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    """Set a new password while signed in, and end every session (ai-trainer-ops#172).

    Until this existed, the only way was the reset by mail: sign out, wait for
    the link, and with the mailbox gone, no way at all.

    The current password is asked like ``/sessions/revoke`` asks it, and
    limited per account like a login: a stolen session must not be able to
    guess the password through here, nor lock the owner out by setting one.
    Every session ends, this one included, as after a reset: whoever had the
    old password may still hold one.
    """
    enforce_login_rate_limit(current_user.email)
    await require_password(current_user, body.current_password)
    if problem := schemas.password_problem(
        body.new_password, current_user.email, current_user.name or ""
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)
    # The check above passed against the store, so its entry is there and
    # active; losing it in between is a server fault, not the athlete's.
    if not await _store_password(current_user, body.new_password):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The password could not be changed. Try again later.",
        )
    await crud.revoke_user_tokens(db, current_user.id)
    await db.commit()
    logger.info(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        "User %s changed the password and ended every session", current_user.id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/session")
async def session_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> schemas.TokenResponse | schemas.SessionResponse | schemas.LoginChallengeResponse:
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


# ---------------------------------------------------------------------------
# Confirming the address an account registered with (app #687)
# ---------------------------------------------------------------------------


@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(
    body: schemas.EmailVerificationConfirm, db: AsyncSession = Depends(get_db, scope="function")
) -> Response:
    """Confirm an account's address from the link it was mailed."""
    try:
        await email_verification.confirm(db, body.token)
    except email_verification.VerificationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/verify-email/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_email_verification(
    body: schemas.EmailVerificationResend, background: BackgroundTasks, request: Request
) -> dict:
    """Mail the link again, answering the same whether the address has an account."""
    if not email_verification.required():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This server sends no mail, so an account needs no confirming.",
        )
    # The reset buckets: both bound the mail one address can be sent, and one
    # count across the two keeps either from being the way around the other.
    enforce_password_reset_rate_limit(str(body.email))
    background.add_task(
        email_verification.send_link, async_session_maker, str(body.email), interface_language(request)
    )
    return {
        "detail": "If this address has an account that is not confirmed yet, a new "
        "link is on its way."
    }


@router.post("/login/totp")
async def login_totp(
    body: schemas.TotpLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> schemas.TokenResponse | schemas.SessionResponse:
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
    return _sign_in(user, request=request, response=response)


@router.get("/resume", response_model=None)
async def resume_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> schemas.SessionResponse | Response:
    """Pick up the cookie session when the app starts (ai-trainer-ops#45).

    The client cannot see an HttpOnly cookie, so this is how it learns it is
    signed in, and how it gets the CSRF token back after a reload. It also
    re-issues the cookie, so the 30 days count from the last time the app was
    opened. A bearer token does not qualify: there is no cookie to renew.

    "No session" is a 204, not a 401: every signed-out visitor asks this once,
    and an error for each of them would be noise in the browser console and in
    the logs. A cookie that no longer authenticates (expired, revoked) is
    cleared on the way.

    GET, because after a reload the client has no CSRF token yet. That is safe:
    a cross-site request can at most renew a session it cannot read, and the
    CSRF token in the body is out of its reach (CORS).
    """
    if auth.SESSION_COOKIE not in request.cookies:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        user = await auth.get_current_user(request, None, db)
        # Inside the try: the Authelia branch of get_current_user never looks
        # at the cookie, so this may be the first time it is decoded.
        claims = auth.read_access_token(request.cookies[auth.SESSION_COOKIE])
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise
        return _end_cookie_session(Response(status_code=status.HTTP_204_NO_CONTENT))
    if claims.user_id != user.id:
        # The Authelia branch identified someone other than the cookie's owner
        # (a stale cookie from another account). Renewing would hand that
        # user a cookie carrying the other account's session id.
        return _end_cookie_session(Response(status_code=status.HTTP_204_NO_CONTENT))
    return _start_cookie_session(user, response, session_id=claims.session_id)


def _end_cookie_session(response: Response) -> Response:
    response.delete_cookie(
        auth.SESSION_COOKIE,
        httponly=True,
        secure=not settings.is_dev_environment,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    """End this browser's cookie session (ai-trainer-ops#45).

    No authentication and no CSRF check: it only ever removes the caller's own
    cookie, which is what a stale or revoked session needs to be able to do
    too. "Sign out everywhere" is /sessions/revoke.
    """
    return _end_cookie_session(Response(status_code=status.HTTP_204_NO_CONTENT))


@router.get("/totp/status", response_model=schemas.TotpStatusResponse)
async def totp_status(
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
        # Ten Argon2 hashes in a row: off the event loop (ai-trainer-ops#280).
        await asyncio.to_thread(
            lambda: [auth.hash_password(totp_service.normalise_recovery_code(c)) for c in codes]
        ),
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
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: models.User = Depends(auth.get_current_user),
) -> Response:
    """Turn the second factor off. Requires the password, not just a session.

    A borrowed unlocked browser should not be enough to strip the protection
    the second factor exists to provide.
    """
    enforce_login_rate_limit(current_user.email)
    await require_password(current_user, body.password)

    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_confirmed_at = None
    await crud.replace_recovery_codes(db, current_user.id, [])
    await crud.revoke_trusted_devices(db, current_user.id)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/totp/trusted-devices/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_trusted_devices(
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    await require_password(current_user, body.password)

    generation = await crud.revoke_user_tokens(db, current_user.id)
    await db.commit()

    logger.info(
        "Revoked all sessions for user %s at the athlete's request; "
        "token generation is now %s.",
        current_user.id,
        generation,
    )
    return schemas.SessionRevokeResponse(token_generation=generation)
