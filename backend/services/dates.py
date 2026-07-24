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


def app_today_stamp(
    now: datetime | None = None, timezone_name: str | None = None
) -> str:
    """Return a compact one-line date stamp for prepending to user messages.

    Injecting this into the user turn (not just the system prompt) keeps the
    authoritative date adjacent to the question even when conversation history
    contains earlier messages that stated a wrong weekday.

    Example: ``[Tuesday, June 23, 2026 · 2026-06-23 · Europe/Berlin]``
    """
    tz = app_timezone(timezone_name)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    today = current.astimezone(tz).date()
    tz_label = getattr(tz, "key", str(tz))
    return f"[{_date_label(today)} · {today.isoformat()} · {tz_label}]"


def plan_day_date_labels(raw_date: object, today: date | None = None) -> dict:
    """Return weekday/dateLabel/relativeDay annotations for a plan day's ISO date.

    Gives the LLM an explicit weekday and relative-day anchor so it never has to
    compute a weekday from a bare date (a known drift-bug source) — e.g. calling
    the next planned session "today" when it is actually days away. Returns an
    empty dict when the date is missing or unparseable.

    This is the single source of truth for plan-day date anchoring; every
    plan-consuming LLM prompt should route through here (directly, or via
    :func:`annotate_plan_days`) rather than re-deriving weekdays ad hoc.
    """
    if not raw_date:
        return {}
    try:
        parsed = date.fromisoformat(str(raw_date))
    except ValueError:
        return {}
    labels: dict = {
        "weekday": parsed.strftime("%A"),
        "dateLabel": _date_label(parsed),
    }
    if today is not None:
        delta_days = (parsed - today).days
        if delta_days == 0:
            labels["relativeDay"] = "today"
        elif delta_days == 1:
            labels["relativeDay"] = "tomorrow"
        elif delta_days == -1:
            labels["relativeDay"] = "yesterday"
    return labels


def annotate_plan_days(
    plan: list[dict] | None, today: date | None = None
) -> list[dict] | None:
    """Return each plan day merged with its weekday/dateLabel/relativeDay anchors.

    Pass-through for a falsy plan so callers can annotate unconditionally. Use
    this (paired with :func:`app_date_context`) in every prompt that shows the
    athlete's plan, so the model can state a session's timing relative to today
    instead of guessing it is "today".
    """
    if not plan:
        return plan
    return [{**day, **plan_day_date_labels(day.get("date"), today)} for day in plan]


def request_timezone(request: Any) -> str | None:
    """Return the browser-supplied timezone header if present."""
    value = request.headers.get(TIMEZONE_HEADER)
    if not value:
        return None
    return str(value).strip() or None
