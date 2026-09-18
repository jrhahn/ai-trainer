"""FastAPI dependencies shared by more than one router.

It exists because the AI rate limit (#676) has to cover two routers: the whole
``/ai`` router, and the one LLM-spending route that lives on ``/users/me``
(``upload-fit``). Putting it in ``services/`` would make a service import
``auth``, and importing one router from another would make them circular — this
module is the seam that keeps both conventions intact.
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
    retry_after = ai_limiter.check(current_user.id, ai_rate_limit_windows())
    if retry_after is None:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many AI requests. Please wait a moment before trying again.",
        headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
    )
