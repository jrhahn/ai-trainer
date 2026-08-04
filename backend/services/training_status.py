"""Deterministic evidence behind the dashboard's training-status chip.

The chip under the dashboard greeting ("On track" / "Slightly behind" / …) used
to be computed in the browser from a bare `done / due` ratio over the trailing
week. That heuristic contradicted itself and the coach (#499):

* a day whose ride↔plan match came back ``ambiguous`` — which is exactly what a
  two-a-day produces, because the matcher cannot tell which activity was *the*
  planned session — was dropped from the window entirely, so the strip could
  render "Core and Upper Body Strength completed" and "Slightly behind" side by
  side about the same session;
* a session the plan itself marks optional counted as a missed obligation;
* unplanned work was invisible, so the ratio could only ever punish.

This module rebuilds that evidence server-side and honestly: sessions are keyed
by ``(date, slot)`` so two-a-days stay distinct (#496), a date's activities are
credited against that date's sessions even when the matcher could not split
them, optional sessions are reported separately from misses, and unplanned
activity is carried as its own signal.

It is deliberately pure and dependency-light (``schemas`` + stdlib only): it is
the *input* to :mod:`services.status_pipeline`, which hands these facts to the
coach to phrase, and the same facts are injected into the ask-trainer prompt so
the coach can explain the chip it wrote instead of confabulating a reason.
"""

from __future__ import annotations

import datetime
from typing import Any, Iterable

import schemas

# How far back the status window reaches, inclusive of today.
WINDOW_DAYS = 7

# Ride↔plan match states that pin an activity to one specific planned session.
# ``ambiguous`` is deliberately absent: it means "you trained on this date but we
# could not tell which session it was", which is evidence of work done, not of a
# miss. It is credited per-date below rather than per-session.
_DEFINITE_MATCH = frozenset({"auto_matched", "manual_matched"})

# Session outcomes, most-to-least deserving of coach attention.
STATUS_DONE = "done"
STATUS_MISSED = "missed"
STATUS_SKIPPED_OPTIONAL = "skipped_optional"
STATUS_PENDING = "pending"


def _is_training_session(day: Any) -> bool:
    """True when ``day`` is a session the athlete is meant to execute.

    Mirrors ``ride_matching._is_training_day``; duplicated rather than imported
    because ``ride_matching`` pulls in ``plan_pipeline`` and this module must
    stay importable from the pipeline layer without closing an import cycle.
    """
    if not isinstance(day, dict):
        return False
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "").lower()
    duration = day.get("durationMinutes") or day.get("duration_minutes") or 0
    try:
        minutes = int(duration or 0)
    except (TypeError, ValueError):
        minutes = 0
    return workout_type not in {"", "rest"} and minutes > 0


def _is_optional(day: dict) -> bool:
    """True when the plan itself presents this session as the athlete's choice.

    Skipping an "Optional Easy Recovery Spin" is compliance, not a miss, so it
    must never count against adherence. An explicit ``optional`` flag wins; the
    text check catches the far commoner case of the coach saying so in the title.
    """
    flag = day.get("optional")
    if isinstance(flag, bool):
        return flag
    haystack = " ".join(
        str(day.get(field) or "") for field in ("title", "description")
    ).lower()
    return "optional" in haystack


def _session_label(day: dict) -> str:
    title = str(day.get("title") or "").strip()
    if title:
        return title
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "").strip()
    return workout_type.capitalize() or "Session"


def _duration_minutes(seconds: Any) -> int | None:
    try:
        return round(int(seconds) / 60)
    except (TypeError, ValueError):
        return None


def window_start(today: datetime.date, *, days: int = WINDOW_DAYS) -> str:
    """First date of the trailing status window, inclusive of ``today``."""
    return (today - datetime.timedelta(days=days - 1)).isoformat()


def build_status_facts(
    plan: list[dict] | None,
    rides: Iterable[Any],
    today: datetime.date,
    *,
    days: int = WINDOW_DAYS,
) -> dict:
    """Assemble the deterministic training-status evidence for ``today``.

    ``rides`` are ``RideMetric`` rows (or duck-typed equivalents). The returned
    dict is both the LLM's input and the coach prompt's evidence block, so every
    number in it must be defensible on its own.
    """
    start = window_start(today, days=days)
    end = today.isoformat()

    sessions_by_date: dict[str, list[dict]] = {}
    for day in plan or []:
        if not isinstance(day, dict) or not day.get("date"):
            continue
        date = str(day["date"])
        if not (start <= date <= end):
            continue
        if not _is_training_session(day):
            continue
        sessions_by_date.setdefault(date, []).append(day)
    for day_sessions in sessions_by_date.values():
        day_sessions.sort(key=schemas.day_slot)

    rides_by_date: dict[str, list[Any]] = {}
    for ride in rides:
        date = str(getattr(ride, "activity_date", "") or "")
        if not (start <= date <= end):
            continue
        rides_by_date.setdefault(date, []).append(ride)

    sessions: list[dict] = []
    extra_activities: list[dict] = []

    for date in sorted(set(sessions_by_date) | set(rides_by_date)):
        day_sessions = sessions_by_date.get(date, [])
        day_rides = rides_by_date.get(date, [])

        # Pass 1 — activities the matcher pinned to one specific session. A slot
        # may hold several: a session recorded in two files is matched as two
        # rides on one slot (#543), and letting only the first claim it turned
        # the other half into an extra activity — or worse, into evidence for an
        # unrelated session of the same date (#545).
        claimed_by_slot: dict[int, list[Any]] = {}
        for ride in day_rides:
            if str(getattr(ride, "plan_match_status", "")) not in _DEFINITE_MATCH:
                continue
            if str(getattr(ride, "matched_plan_date", "") or "") != date:
                continue
            slot = schemas.normalize_slot(getattr(ride, "matched_plan_slot", None))
            claimed_by_slot.setdefault(slot, []).append(ride)

        # Pass 2 — everything else the athlete actually did on this date. An
        # ambiguous match lands here: the work is real, only its attribution is
        # uncertain, so it still earns credit against a session of that day.
        claimed_ids = {
            id(ride) for claimed in claimed_by_slot.values() for ride in claimed
        }
        loose_rides = [r for r in day_rides if id(r) not in claimed_ids]

        for day in day_sessions:
            slot = schemas.day_slot(day)
            optional = _is_optional(day)
            claimed = claimed_by_slot.get(slot) or []
            evidence_ride = claimed[0] if claimed else None
            if evidence_ride is None and bool(day.get("completed")):
                status = STATUS_DONE
                evidence = "marked complete by the athlete"
            elif evidence_ride is not None:
                status = STATUS_DONE
                names = [
                    str(getattr(r, "activity_name", None) or "recorded")
                    for r in claimed
                ]
                evidence = (
                    f"matched activity {names[0]}"
                    if len(names) == 1
                    # Naming both halves is what makes the badge legible: the
                    # athlete sees one session backed by the two files they rode.
                    else f"matched activities {', '.join(names)}"
                )
            elif loose_rides:
                evidence_ride = loose_rides.pop(0)
                status = STATUS_DONE
                evidence = (
                    f"activity {getattr(evidence_ride, 'activity_name', None) or 'recorded'} "
                    "on this date, not attributable to a single session"
                )
            elif date == end:
                # Still ahead of the athlete in the day — not a miss yet.
                status = STATUS_PENDING
                evidence = "not done yet today"
            elif optional:
                status = STATUS_SKIPPED_OPTIONAL
                evidence = "optional session, not done"
            else:
                status = STATUS_MISSED
                evidence = "no activity recorded"

            sessions.append(
                {
                    "date": date,
                    "slot": slot,
                    "title": _session_label(day),
                    "workoutType": str(
                        day.get("workoutType") or day.get("workout_type") or ""
                    ),
                    "optional": optional,
                    "status": status,
                    "evidence": evidence,
                }
            )

        # Anything left over is work the plan never asked for. It is not
        # adherence, but it is emphatically not a shortfall either.
        for ride in loose_rides:
            extra_activities.append(
                {
                    "date": date,
                    "name": getattr(ride, "activity_name", None) or "Activity",
                    "sportType": getattr(ride, "sport_type", None) or "",
                    "durationMinutes": _duration_minutes(
                        getattr(ride, "duration_seconds", None)
                    ),
                    "plannedSession": bool(day_sessions),
                }
            )

    # Only sessions that have actually fallen due can move adherence: today's
    # still-pending work is excluded, and so are optional sessions the athlete
    # declined. A done optional session still earns its credit.
    counted = [s for s in sessions if s["status"] in (STATUS_DONE, STATUS_MISSED)]
    planned_due = len(counted)
    planned_done = sum(1 for s in counted if s["status"] == STATUS_DONE)
    adherence = (planned_done / planned_due) if planned_due else None

    return {
        "windowStart": start,
        "windowEnd": end,
        "plannedDue": planned_due,
        "plannedDone": planned_done,
        "adherence": round(adherence, 3) if adherence is not None else None,
        "sessions": sessions,
        "missed": [s for s in sessions if s["status"] == STATUS_MISSED],
        "skippedOptional": [
            s for s in sessions if s["status"] == STATUS_SKIPPED_OPTIONAL
        ],
        "pending": [s for s in sessions if s["status"] == STATUS_PENDING],
        "extraActivities": extra_activities,
    }


def fallback_status(facts: dict) -> tuple[str, str, str]:
    """Deterministic ``(label, tone, rationale)`` for when the LLM is unusable.

    The chip is part of the dashboard's first paint, so it must never be blank
    just because a provider call failed or returned something unrenderable.
    """
    adherence = facts.get("adherence")
    done = facts.get("plannedDone") or 0
    due = facts.get("plannedDue") or 0
    extra = len(facts.get("extraActivities") or [])

    if adherence is None:
        return (
            "No sessions due",
            "steady",
            "Nothing in the plan has fallen due in the last week, so there is no "
            "adherence signal to report yet.",
        )

    detail = f"You have completed {done} of {due} sessions that fell due in the last 7 days"
    if extra:
        detail += f", plus {extra} unplanned session{'s' if extra != 1 else ''}"
    detail += "."

    if adherence >= 0.8:
        return "On track", "positive", detail
    if adherence >= 0.5:
        return "Slightly behind", "caution", detail
    return "Behind plan", "caution", detail
