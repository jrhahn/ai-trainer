"""A short push to the owner's phone when the server needs them (ai-trainer-ops#124).

Through ntfy, the same channel as the uptime check: ``settings.ntfy_url`` is a
private topic URL, and the topic name is its only protection, so it is a secret.
An alert carries no athlete data, only what the owner has to act on.
"""

from __future__ import annotations

import logging
import time

import httpx

from config import settings

logger = logging.getLogger(__name__)

# One push per alert and process in this long. The condition behind an alert
# lasts hours (a daily budget ages out over a day), and a push per refused
# request would bury the phone.
# ponytail: per process, so N workers can send N pushes per interval.
ALERT_INTERVAL_SECONDS = 6 * 3600
# After a push that did not get through: soon enough that one bad minute at
# ntfy does not hide the alert for hours, rare enough that a down ntfy is not
# asked on every refused request (review of #963).
RETRY_AFTER_SECONDS = 5 * 60
SEND_TIMEOUT_SECONDS = 5.0

# When each alert may go out again, on the ``time.monotonic`` clock.
_next_allowed: dict[str, float] = {}


async def notify_once(key: str, title: str, message: str) -> bool:
    """Push ``message`` unless ``key`` was pushed within the interval.

    Returns whether a push went out. Never raises: an alert that cannot be
    delivered must not fail the request or job that noticed the problem.
    """
    if not settings.ntfy_url:
        return False
    now = time.monotonic()
    if now < _next_allowed.get(key, now):
        return False
    # Claimed before the request, so two refusals at once send one push.
    _next_allowed[key] = now + ALERT_INTERVAL_SECONDS
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS) as client:
            response = await client.post(
                settings.ntfy_url,
                content=message.encode(),
                headers={"Title": title, "Tags": "warning"},
            )
            response.raise_for_status()
    # Every exception, not only httpx.HTTPError: a malformed secret raises
    # InvalidURL, which is not one, and would turn the caller's 402 into a 500.
    except Exception as exc:
        _next_allowed[key] = now + RETRY_AFTER_SECONDS
        # The topic URL is a secret: log the failure's type, never the URL.
        logger.warning("owner alert not delivered: key=%s error=%s", key, type(exc).__name__)
        return False
    return True
