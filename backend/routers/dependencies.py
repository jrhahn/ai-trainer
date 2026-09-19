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
"""

from __future__ import annotations

import math

from fastapi import Depends, HTTPException, status

import auth
import models
from config import settings
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


async def enforce_ai_rate_limit(
    current_user: models.User = Depends(auth.get_current_user),
) -> None:
    """Per-user rate limit on the endpoints that spend provider tokens.

    Keyed on the authenticated user id rather than the client IP. The IP
    arrives through Traefik *and* nginx, so trusting it needs a trusted-proxy
    hop count that nothing in this app currently establishes — and
    ``get_current_user`` has already resolved the identity that owns the spend.

    Applied to the whole ``/ai`` router rather than route by route. The cheap
    GETs on there pay a limit they do not need, which the generous defaults
    make harmless; in exchange a route added later is covered without anyone
    remembering to cover it. That is the failure mode worth designing against —
    and ``upload-fit``, which spends tokens from a different router, is the
    proof that it happens.
    """
    if not settings.ai_rate_limit_enabled:
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
# Auth brute-force limits (#682)
#
# Separate limiter instances rather than one keyspace: they hold unrelated
# windows, and a shared instance would let registrations evict login history
# under the bucket cap.
# ---------------------------------------------------------------------------

login_limiter = SlidingWindowLimiter()
registration_limiter = SlidingWindowLimiter()
admin_login_limiter = SlidingWindowLimiter()

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
