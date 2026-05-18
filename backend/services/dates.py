"""Date helpers for athlete-facing calendar logic."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import settings

TIMEZONE_HEADER = "x-app-timezone"


def app_timezone(timezone_name: str | None = None) -> ZoneInfo:
    """Return the configured application timezone, falling back to UTC."""
    name = (timezone_name or settings.app_timezone).strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        try:
            return ZoneInfo(settings.app_timezone)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")


def app_today(now: datetime | None = None, timezone_name: str | None = None) -> date:
    """Return today's date in the athlete-facing application timezone."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(app_timezone(timezone_name)).date()


def app_today_iso(now: datetime | None = None, timezone_name: str | None = None) -> str:
    """Return today's ISO date in the athlete-facing application timezone."""
    return app_today(now, timezone_name).isoformat()


def request_timezone(request: Any) -> str | None:
    """Return the browser-supplied timezone header if present."""
    value = request.headers.get(TIMEZONE_HEADER)
    if not value:
        return None
    return str(value).strip() or None
