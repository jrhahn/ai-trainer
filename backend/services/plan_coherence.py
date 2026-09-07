"""Read the plan as a week rather than as a bag of days (#659).

Every guard in ``services/plan_pipeline`` asks the same shape of question: may
*this* day be written? None of them ever asks what the write did to the days
either side of it. On 2026-09-07 that produced the same 45-minute strength
session on Monday and Tuesday — byte-identical ``workoutType``, ``title`` and
``durationMinutes`` — because the coach was told to move today's session and was
never told to look at tomorrow. When the athlete then asked it to review the
coming days, it read the duplicate, described it correctly and endorsed it.

Two consecutive days holding the same session is checkable without an LLM, so it
is checked here and the result is handed to the coach as a fact. Deliberately
detection only: what the second day should become instead is a coaching decision,
and a guard that invents one would be the #651 mistake again — a correct-looking
automated write that costs the athlete a session.
"""

from __future__ import annotations

from datetime import date, timedelta

from schemas import day_slot

# Days that carry no training load, where a repeat is the intended pattern
# rather than a collision. Two rest days in a row is a taper, not a bug.
_NON_LOADING_TYPES = frozenset({"rest", "off", ""})

# How far ahead to look. The coach only ever discusses and edits the near plan;
# a duplicate three weeks out will have been rewritten several times over before
# the athlete reaches it, and reporting it is noise.
DEFAULT_HORIZON_DAYS = 14


def session_signature(day: dict) -> tuple[str, str, object] | None:
    """What makes two sessions "the same session" for coherence purposes.

    ``None`` when the day carries no load, or when it is too sparsely specified
    to compare — an absent title or type is not evidence of a duplicate, and
    guessing one would manufacture collisions out of incomplete data.
    """
    if not isinstance(day, dict):
        return None
    workout_type = str(day.get("workoutType") or "").strip().lower()
    title = str(day.get("title") or "").strip().lower()
    if workout_type in _NON_LOADING_TYPES or not title:
        return None
    duration = day.get("durationMinutes")
    if duration in (0, "0"):
        return None
    return (workout_type, title, duration)


def _parse(raw: object) -> date | None:
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def find_repeated_sessions(
    plan: list[dict] | None,
    today: str | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[dict]:
    """Consecutive calendar days scheduled to hold the same session.

    Returns ``{"dates": [earlier, later], "workoutType", "title",
    "durationMinutes"}`` per collision, in date order. Only today and forward:
    a duplicate the athlete has already ridden past is history, not something
    the coach can act on.

    Two-a-days are handled by comparing the *set* of sessions on each date, so a
    session repeating across days is found regardless of which slot it sits in —
    but a date's own two sessions are never compared with each other, because
    doubling up within one day is a deliberate pattern, not a scheduling slip.
    """
    start = _parse(today)
    by_date: dict[date, dict[tuple, dict]] = {}
    for day in plan or []:
        parsed = _parse(day.get("date") if isinstance(day, dict) else None)
        if parsed is None or (start is not None and parsed < start):
            continue
        if start is not None and (parsed - start).days > horizon_days:
            continue
        signature = session_signature(day)
        if signature is None:
            continue
        # Slot only decides which entry wins if one date somehow lists the same
        # signature twice; the comparison itself is across dates.
        by_date.setdefault(parsed, {}).setdefault(signature, day)

    repeats: list[dict] = []
    for current in sorted(by_date):
        following = by_date.get(current + timedelta(days=1))
        if not following:
            continue
        for signature in sorted(set(by_date[current]) & set(following)):
            day = by_date[current][signature]
            repeats.append(
                {
                    "dates": [current.isoformat(), (current + timedelta(days=1)).isoformat()],
                    "workoutType": day.get("workoutType"),
                    "title": day.get("title"),
                    "durationMinutes": day.get("durationMinutes"),
                    "slot": day_slot(day),
                }
            )
    return repeats
