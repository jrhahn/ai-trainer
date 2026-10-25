"""FastAPI dependencies shared by more than one router.

It exists because the AI rate limit (#676) has to cover two routers: the whole
``/ai`` router, and the LLM-spending routes that live on ``/users/me``
(``upload-fit`` and ``upload-fit/bulk``). Putting it in ``services/`` would
make a service import ``auth``, and importing one router from another would
make them circular — this module is the seam that keeps both conventions
intact.

The auth limits (#682) live here for the same reason: they span
``auth_router`` and ``admin``. They are plain functions rather than FastAPI
dependencies because they key on the request *body* (the email being tried),
which a dependency would have to parse a second time.

``require_password`` (ai-trainer-ops#35) is here because step-up now spans three
routers: ``/auth/totp/disable``, ``/auth/sessions/revoke`` and ``DELETE
/users/me``. While it was private to ``auth_router``, the route that destroys the
account was the one route that could not reach it.
"""

from __future__ import annotations

import asyncio
import math

from fastapi import Depends, HTTPException, Request, status

import auth
import models
from config import settings
from services import authelia_store
from services import llm as llm_service
from services.rate_limit import SlidingWindowLimiter, Window

# One limiter for every AI-spending endpoint, so a user cannot get a fresh
# allowance by alternating between routers. Module-level state, therefore
# per-process and single-replica only — see ``docs/multi_replica.md``.
ai_limiter = SlidingWindowLimiter()


def ai_rate_limit_windows() -> tuple[Window, ...]:
    """Read the configured windows at call time so tests can vary them."""
    return (
        Window(settings.ai_rate_limit_burst, settings.ai_rate_limit_burst_seconds),
        Window(
            settings.ai_rate_limit_sustained,
            settings.ai_rate_limit_sustained_seconds,
        ),
    )


async def set_user_ai_keys(
    current_user: models.User = Depends(auth.get_current_user),
):
    """Put the athlete's own API keys in scope for the request (#693).

    Without this, ``llm.get_provider`` finds no BYOK context and takes the
    branch meant for scheduler jobs: the global key, used unconditionally and
    *without* consulting ``allow_admin_ai_key_fallback``. That is correct for a
    nightly job, which has no user to bill. It is wrong for a request made by
    a specific athlete — and it silently defeated BYOK-only mode on the one
    LLM-spending pair of routes outside the ``/ai`` router, the .fit uploads,
    which kept spending the owner's key after the fallback was turned off.

    Lives here rather than in ``routers/ai.py`` for the reason this module
    exists: two routers need it, and neither may import the other.
    """
    keys = {
        "openai": current_user.user_openai_api_key,
        "gemini": current_user.user_gemini_api_key,
    }
    token = llm_service.set_user_ai_keys(keys)
    try:
        yield
    finally:
        llm_service.reset_user_ai_keys(token)


# The calls the app makes by itself, not the athlete (ai-trainer-ops#183). A
# German athlete's first minute spent 4 plan translations, 2 login summaries
# (404 for a new account) and the week goals out of the same 10 requests the
# chat draws from, and the third chat message was refused. They keep a limit of
# their own, the same size, so a loop of them is still stopped.
APP_INITIATED_AI_PATHS = (
    "/ai/plan-translations",
    "/ai/refresh-login-summary",
    "/ai/season-plan/goals",
    # The rebuild the app makes after a change the athlete saved (ai-trainer-ops#206).
    "/ai/plan-rebuild",
)
APP_ALLOWANCE_SUFFIX = ":app"


async def enforce_ai_rate_limit(
    request: Request,
    current_user: models.User = Depends(auth.get_current_user),
) -> None:
    """Per-user rate limit on the endpoints that spend provider tokens.

    Keyed on the authenticated user id rather than the client IP. The IP
    arrives through Traefik *and* nginx, so trusting it needs a trusted-proxy
    hop count that nothing in this app currently establishes — and
    ``get_current_user`` has already resolved the identity that owns the spend.

    Applied to the whole ``/ai`` router, but only to the token-spending verbs:
    the GETs on there (``season-plan``, ``readiness-score``,
    ``athlete-performance-model``) read already-derived data and spend nothing,
    yet a dashboard that fans out to several of them per load was burning the
    allowance and then 429ing the chat (ops#132). Everything that is not a GET
    is still covered automatically, so a token-spending route added later needs
    no one to remember it — ``upload-fit``, which spends from a different
    router, is the proof that that matters.
    """
    if not settings.ai_rate_limit_enabled:
        return
    if request.method == "GET":
        return
    # What the app asks for by itself has its own allowance (ai-trainer-ops#183).
    if request.url.path.endswith(APP_INITIATED_AI_PATHS):
        consume_ai_allowance(f"{current_user.id}{APP_ALLOWANCE_SUFFIX}")
        return
    consume_ai_allowance(current_user.id)


def consume_ai_allowance(user_id: str) -> None:
    """Spend one unit of *user_id*'s AI allowance, or raise 429.

    Split out of the dependency so a route that spends more than one LLM call
    per request can charge itself per call. ``upload-fit/bulk`` is that route:
    the dependency gates the request, this gates each file after the first.
    """
    if not settings.ai_rate_limit_enabled:
        return
    retry_after = ai_limiter.check(user_id, ai_rate_limit_windows())
    if retry_after is None:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many AI requests. Please wait a moment before trying again.",
        headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
    )


# ---------------------------------------------------------------------------
# Credentials: reading the user store, and step-up on a live session
# ---------------------------------------------------------------------------


def verify_store_credentials(email: str, password: str) -> bool:
    """``authelia_store.verify_credentials``, with the operator case as a 503.

    The mapping lives here and not in the service because the service has no
    business knowing about HTTP, and it lives in *one* function rather than at
    each call site because an unreadable store is not a wrong password and must
    never be answered as one. Before it had a 503 it surfaced as a raw traceback
    and a 500 on every login (#684, #696): true, and useless.
    """
    try:
        return authelia_store.verify_credentials(email, password)
    except authelia_store.StoreUnreadable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
        ) from exc


async def require_password(user: models.User, password: str) -> None:
    """Re-check *user*'s password, or raise 403.

    Shared by every route that demands the password on top of a live session,
    because the two branches are the trap: with Authelia as the user store the
    password is not in ``users.hashed_password`` at all (that column holds a
    random throwaway, see ``register``), so a route that checked only the column
    would accept nothing in production, and one that checked only the store would
    accept nothing in development. Getting that wrong in one route out of several
    is how step-up auth quietly stops being a check.

    Which is what had happened: this was private to ``auth_router``, so
    ``DELETE /users/me`` — the one route that cannot be undone — did not call it
    and took a bare session (ai-trainer-ops#35).
    """
    if auth.AUTHELIA_AUTH_ENABLED:
        valid = await asyncio.to_thread(verify_store_credentials, user.email, password)
    else:
        valid = await asyncio.to_thread(auth.verify_password, password, user.hashed_password)
    # 403, not 401: the session is valid, only the re-typed password is wrong.
    # A 401 tells the client the session is gone, and the app signed the athlete
    # out over a typo (ai-trainer-ops#131).
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Incorrect password."
        )


# ---------------------------------------------------------------------------
# Auth brute-force limits (#682)
#
# Separate limiter instances rather than one keyspace: they hold unrelated
# windows, and a shared instance would let registrations evict login history
# under the bucket cap.
# ---------------------------------------------------------------------------

login_limiter = SlidingWindowLimiter()
registration_limiter = SlidingWindowLimiter()
admin_login_limiter = SlidingWindowLimiter()
captcha_limiter = SlidingWindowLimiter()
totp_code_limiter = SlidingWindowLimiter()
password_reset_limiter = SlidingWindowLimiter()

# The key for a limit that has nothing request-specific to key on.
_GLOBAL_KEY = "*"


def _enforce(
    limiter: SlidingWindowLimiter,
    key: str,
    windows: tuple[Window, ...],
    detail: str,
) -> None:
    """Consume one event, or raise 429 with a ``Retry-After``."""
    if not settings.auth_rate_limit_enabled:
        return
    retry_after = limiter.check(key, windows)
    if retry_after is None:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
    )


def enforce_login_rate_limit(email: str) -> None:
    """Rate-limit one login attempt, per email and globally.

    Both buckets are consumed on every attempt, successful or not — see the
    note on ``login_rate_limit_attempts`` for why failures alone are the wrong
    thing to count.

    The email is lower-cased so ``A@b.c`` and ``a@b.c`` cannot be alternated
    for a fresh allowance; ``EmailStr`` does not normalise case for us.
    """
    normalised = email.strip().lower()
    _enforce(
        login_limiter,
        normalised,
        (
            Window(
                settings.login_rate_limit_attempts,
                settings.login_rate_limit_seconds,
            ),
        ),
        "Too many login attempts for this account. Please wait and try again.",
    )
    _enforce(
        login_limiter,
        _GLOBAL_KEY,
        (
            Window(
                settings.login_rate_limit_global_attempts,
                settings.login_rate_limit_global_seconds,
            ),
        ),
        "Too many login attempts. Please wait and try again.",
    )


def enforce_password_reset_rate_limit(email: str) -> None:
    """Rate-limit reset mails, per email and globally (ai-trainer-ops#35).

    The email bucket bounds the mail one address can be sent, whoever asks; it
    answers the same for every address, so a 429 says nothing about whether an
    account exists. The global bucket caps what the endpoint can make the
    provider send.

    The confirmation is deliberately not limited here. Its token is 256 random
    bits, so there is nothing to guess, and a wrong one costs one indexed
    lookup. Counting it into this bucket let anyone block every legitimate
    reset for an hour with thirty garbage tokens.
    """
    windows = {
        _GLOBAL_KEY: (
            Window(
                settings.password_reset_rate_limit_global_attempts,
                settings.password_reset_rate_limit_global_seconds,
            ),
        ),
        f"email:{email.strip().lower()}": (
            Window(
                settings.password_reset_rate_limit_attempts,
                settings.password_reset_rate_limit_seconds,
            ),
        ),
    }
    for key, key_windows in windows.items():
        _enforce(
            password_reset_limiter,
            key,
            key_windows,
            "Too many password reset requests. Please wait and try again.",
        )


def enforce_registration_rate_limit() -> None:
    """Rate-limit account creation across the whole deployment."""
    _enforce(
        registration_limiter,
        _GLOBAL_KEY,
        (
            Window(
                settings.registration_rate_limit_attempts,
                settings.registration_rate_limit_seconds,
            ),
        ),
        "Too many accounts created recently. Please wait and try again.",
    )


def enforce_captcha_rate_limit() -> None:
    """Bound how often challenges may be issued, deployment-wide (#686).

    Deliberately far looser than the registration limit: one human filling in
    the form may legitimately fetch several challenges (reload, a slow solve
    that expires, a typo'd password rejected by the strength check), and each
    costs one signature. This exists so the endpoint cannot be used to make the
    server hash on demand, not to gate signup — that is registration's job.
    """
    _enforce(
        captcha_limiter,
        _GLOBAL_KEY,
        (
            Window(
                settings.captcha_challenge_rate_limit_attempts,
                settings.captcha_challenge_rate_limit_seconds,
            ),
        ),
        "Too many captcha requests. Please wait and try again.",
    )


def enforce_totp_code_rate_limit(user_id: str) -> None:
    """Bound guesses at a six-digit code, per account (#688).

    10^6 combinations sounds like a lot until you notice a step lasts 30 s with
    ±1 drift tolerance, which widens the accepted set, and that an unlimited
    endpoint can be hit as fast as the network allows. This is the number that
    makes brute force impractical; the single-use challenge is what stops one
    accepted password funding an unbounded number of attempts.

    Keyed on the user the challenge names rather than on the challenge, so
    fetching a fresh one per guess buys nothing.
    """
    _enforce(
        totp_code_limiter,
        user_id,
        (
            Window(
                settings.totp_code_rate_limit_attempts,
                settings.totp_code_rate_limit_seconds,
            ),
        ),
        "Too many codes tried. Please wait and try again.",
    )


def enforce_admin_totp_rate_limit() -> None:
    """Same bound for the admin panel, which has no user to key on."""
    _enforce(
        totp_code_limiter,
        _GLOBAL_KEY,
        (
            Window(
                settings.totp_code_rate_limit_attempts,
                settings.totp_code_rate_limit_seconds,
            ),
        ),
        "Too many codes tried. Please wait and try again.",
    )


def enforce_admin_login_rate_limit() -> None:
    """Rate-limit admin password attempts across the whole deployment."""
    _enforce(
        admin_login_limiter,
        _GLOBAL_KEY,
        (
            Window(
                settings.admin_login_rate_limit_attempts,
                settings.admin_login_rate_limit_seconds,
            ),
        ),
        "Too many admin login attempts. Please wait and try again.",
    )
