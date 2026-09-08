"""Arrangements the coach and the athlete made, in the writers' vocabulary (#667).

``services/plan_constraints`` answers *what can this athlete do* — a fact about
their week. This answers *what did we decide* — a fact about the conversation.
The distinction is the whole point: "Friday is a rest day" is structurally
indistinguishable from any other rest day, and only becomes protected once
somebody records that it is rest *because the athlete races on Sunday*.

Pure functions over plain dicts, the same shape as the rest of the prompt layer:
row conversion happens once at the edge so nothing downstream has to know
whether it is holding an ORM object.
"""

from __future__ import annotations

from datetime import date, timedelta

# A commitment covering a long stretch would freeze the plan against every
# automated trigger for that stretch, which is a way to break the product with
# one bad LLM response. Windows are coaching arrangements about the near week;
# anything longer is a misunderstanding and is clamped rather than trusted.
MAX_COMMITMENT_DAYS = 21


def commitment_to_dict(row) -> dict:
    """One ORM row as the plain dict every consumer here expects."""
    return {
        "startDate": getattr(row, "start_date", None),
        "endDate": getattr(row, "end_date", None),
        "text": getattr(row, "text", "") or "",
        "source": getattr(row, "source", "") or "",
    }


def _parse(raw: object) -> date | None:
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def normalize_window(
    start: object, end: object, *, max_days: int = MAX_COMMITMENT_DAYS
) -> tuple[str, str] | None:
    """Coerce a proposed window to a usable inclusive ``(start, end)`` pair.

    ``None`` when either date is unparseable — a commitment nobody can place on
    the calendar protects nothing and would only sit in the prompt as noise. A
    reversed pair is swapped rather than rejected, since the intent is obvious;
    an over-long one is clamped to ``max_days``.
    """
    first, last = _parse(start), _parse(end)
    if first is None or last is None:
        return None
    if last < first:
        first, last = last, first
    if (last - first).days > max_days:
        last = first + timedelta(days=max_days)
    return first.isoformat(), last.isoformat()


def committed_dates(commitments: list[dict] | None) -> set[str]:
    """Every calendar date covered by an active commitment."""
    dates: set[str] = set()
    for commitment in commitments or []:
        window = normalize_window(
            commitment.get("startDate"), commitment.get("endDate")
        )
        if window is None:
            continue
        first, last = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        current = first
        while current <= last:
            dates.add(current.isoformat())
            current += timedelta(days=1)
    return dates
