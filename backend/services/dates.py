"""Date helpers for athlete-facing calendar logic."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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


def _date_label(value: date) -> str:
    return f"{value.strftime('%A, %B')} {value.day}, {value.year}"


def app_date_context(
    now: datetime | None = None, timezone_name: str | None = None
) -> str:
    """Return an authoritative local date block for LLM prompts."""
    tz = app_timezone(timezone_name)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(tz)
    today = local_now.date()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    timezone_label = getattr(tz, "key", str(tz))

    return (
        f"Current local date context ({timezone_label}):\n"
        f"- Today is {_date_label(today)} ({today.isoformat()}).\n"
        f"- Yesterday was {_date_label(yesterday)} ({yesterday.isoformat()}).\n"
        f"- Tomorrow is {_date_label(tomorrow)} ({tomorrow.isoformat()})."
    )


def request_timezone(request: Any) -> str | None:
    """Return the browser-supplied timezone header if present."""
    value = request.headers.get(TIMEZONE_HEADER)
    if not value:
        return None
    return str(value).strip() or None
