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


def activity_date_anchor(raw_date: object, today: date | None = None) -> str:
    """Return "Thursday, 2 days ago" for a completed activity's ISO date.

    The past-facing counterpart of :func:`plan_day_date_labels`. Plan days have
    carried a weekday and a relative-day anchor since #462, but the ride history
    never did: every line opened with a bare ISO date, so the coach had to work
    out for itself how long ago the newest ride was. It defaulted to the nearest
    round answer and called a Thursday ride "yesterday's 118-minute effort" on a
    Saturday — then, once corrected, described the *planned* Friday session as if
    it had happened (#650).

    Both the weekday and the offset are always stated, because the whole point is
    that the model never derives either. Returns "" for a missing or unparseable
    date, so callers can append it unconditionally.
    """
    if not raw_date:
        return ""
    try:
        parsed = date.fromisoformat(str(raw_date))
    except ValueError:
        return ""
    weekday = parsed.strftime("%A")
    if today is None:
        return weekday
    delta_days = (today - parsed).days
    if delta_days == 0:
        return f"{weekday}, today"
    if delta_days == 1:
        return f"{weekday}, yesterday"
    if delta_days > 1:
        return f"{weekday}, {delta_days} days ago"
    return weekday


def plan_window_calendar(start: date, days: int) -> str:
    """List the dates a planner may fill, each already carrying its weekday.

    :func:`plan_day_date_labels` anchors a plan the model is *reading*. A model
    being asked to *write* one has no plan to annotate yet — it invents the
    dates — so it was left deriving weekdays from a bare "Today's date" line and
    counting forward. That is the #462 arithmetic, and it produced a "long
    weekend ride" on a Monday (#625).

    Weekends are called out rather than left implicit: "Saturday" only rules out
    a weekday session if the model reliably knows Saturday is the weekend, and
    the whole point here is not to rely on it knowing.
    """
    lines = []
    for offset in range(max(days, 0)):
        current = start + timedelta(days=offset)
        weekday = current.strftime("%A")
        suffix = ", weekend" if current.weekday() >= 5 else ""
        lines.append(f"- {current.isoformat()} ({weekday}{suffix})")
    return "The dates you are planning, with the weekday of each — use these and " \
        "never derive a weekday yourself:\n" + "\n".join(lines)


def plan_session_labels(session_index: int, session_count: int, time_of_day) -> dict:
    """Return sessionOrder/sessionCount/sessionLabel anchors for one session (#496).

    A date can hold more than one session, so a prompt that only showed the date
    would flatten "AM yoga + PM endurance" into two indistinguishable entries and
    the coach could not reason about load ordering within the day. Single-session
    days get no annotations at all, keeping every existing prompt byte-identical.

    ``sessionLabel`` prefers the athlete's own ``timeOfDay`` wording when present
    and otherwise states the position explicitly ("session 1 of 2"), so the model
    is never left to infer ordering from list position.
    """
    if session_count <= 1:
        return {}
    labels: dict = {
        "sessionOrder": session_index + 1,
        "sessionCount": session_count,
    }
    hint = str(time_of_day or "").strip()
    labels["sessionLabel"] = (
        f"{hint} (session {session_index + 1} of {session_count})"
        if hint
        else f"session {session_index + 1} of {session_count}"
    )
    return labels


def annotate_plan_days(
    plan: list[dict] | None, today: date | None = None
) -> list[dict] | None:
    """Return each plan session merged with its date and within-day anchors.

    Pass-through for a falsy plan so callers can annotate unconditionally. Use
    this (paired with :func:`app_date_context`) in every prompt that shows the
    athlete's plan, so the model can state a session's timing relative to today
    instead of guessing it is "today".

    On a date holding two-a-days, each session additionally carries its order
    within the day, so the coach can move the hard PM session out of the heat and
    leave the easy AM one alone (#495 × #496) rather than treating the date as one
    indivisible block. Sessions are emitted in slot order within a date.
    """
    if not plan:
        return plan
    counts: dict[str, int] = {}
    for day in plan:
        counts[str(day.get("date") or "")] = counts.get(str(day.get("date") or ""), 0) + 1
    seen: dict[str, int] = {}
    annotated: list[dict] = []
    for day in _slot_ordered(plan):
        date_key = str(day.get("date") or "")
        index = seen.get(date_key, 0)
        seen[date_key] = index + 1
        annotated.append(
            {
                **day,
                **plan_day_date_labels(day.get("date"), today),
                **plan_session_labels(
                    index, counts[date_key], day.get("timeOfDay") or day.get("time_of_day")
                ),
            }
        )
    return annotated


def _slot_ordered(plan: list[dict]) -> list[dict]:
    """Plan sessions in ``(date, slot)`` order, preserving the caller's date order.

    Only reorders *within* a date, so a prompt that deliberately passes an unsorted
    or windowed plan keeps its overall shape while AM still precedes PM.
    """
    import schemas

    date_order: dict[str, int] = {}
    for day in plan:
        date_order.setdefault(str(day.get("date") or ""), len(date_order))
    return sorted(
        plan,
        key=lambda d: (date_order[str(d.get("date") or "")], schemas.day_slot(d)),
    )


def request_timezone(request: Any) -> str | None:
    """Return the browser-supplied timezone header if present."""
    value = request.headers.get(TIMEZONE_HEADER)
    if not value:
        return None
    return str(value).strip() or None
