"""Shared plan constraint enforcement — importable from both routers and services."""

from __future__ import annotations

import re

import schemas
from services.availability import SESSION_CAP, weekday_label_for

# A date may hold more than one session (#496); ``slot`` orders them. Reuse the
# canonical reader so this module and the pipeline can never disagree on which
# session a day dict is.
_slot = schemas.day_slot

# Read every other field through the same canonical reader. It resolves on key
# presence rather than truthiness, which is what the old
# ``day.get("durationMinutes") or day.get("duration_minutes")`` idiom got wrong
# for a legitimately-zero duration (ai-trainer-ops#29).
_field = schemas.day_field

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(name: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def _written(day: dict, values: dict) -> dict:
    """Apply ``values`` to a copy of ``day``, dropping the stale snake_case twins.

    Writing ``workoutType: "rest"`` onto a day that arrived as
    ``workout_type: "intervals"`` leaves the day asserting two different things
    about itself, and which one a consumer believes depends on the order it reads
    them in. Every value here is authoritative — the gate has just decided it —
    so the other spelling is not a fallback any more, it is stale data.
    """
    result = {**day, **values}
    for name in values:
        twin = _snake(name)
        if twin != name:
            result.pop(twin, None)
    return result


def _day_is_training(day: dict) -> bool:
    workout_type = str(_field(day, "workoutType", "workout_type", default="")).lower()
    duration = _field(day, "durationMinutes", "duration_minutes", default=0)
    return workout_type not in {"", "rest"} or int(duration or 0) > 0


def blocked_dates(constraints: list[dict]) -> set[str]:
    """The dates the athlete declared unavailable.

    Being blocked is a property of the *date*, not of whatever a day currently
    holds. ``sanitize_plan_for_constraints`` depends on that distinction — see
    the note there (ai-trainer-ops#30).
    """
    return {
        str(c.get("constraintDate"))
        for c in constraints
        if c.get("constraintType") == "no_training" and c.get("constraintDate")
    }


def day_violates_constraint(day: dict, constraints: list[dict]) -> bool:
    """Whether ``day`` schedules training on a date the athlete blocked.

    A question about a *day as written*, which is what the router needs when it
    filters requested updates: an update that rests a blocked day is fine and
    must pass. ``sanitize_plan_for_constraints`` asks the other question — is
    this date blocked — and the two must not be confused.
    """
    date_value = day.get("date")
    if not date_value:
        return False
    if not _day_is_training(day):
        return False
    return str(date_value) in blocked_dates(constraints)


def _required_workout_for_day(day: dict, constraints: list[dict]) -> dict | None:
    """Return the required-workout spec pinned to ``day``'s date, if any."""
    date_value = day.get("date")
    if not date_value:
        return None
    for constraint in constraints:
        if constraint.get("constraintType") != "required_workout":
            continue
        if constraint.get("constraintDate") == date_value:
            return constraint.get("requiredWorkout") or {}
    return None


def day_satisfies_required_workout(day: dict, spec: dict) -> bool:
    """Whether ``day`` already meets the required workout type and duration floor."""
    required_type = str(spec.get("workoutType") or "").lower()
    if required_type:
        day_type = str(_field(day, "workoutType", "workout_type", default="")).lower()
        if day_type != required_type:
            return False
    min_minutes = spec.get("minDurationMinutes")
    if min_minutes:
        duration = _field(day, "durationMinutes", "duration_minutes", default=0)
        if int(duration or 0) < int(min_minutes):
            return False
    return True


def _coerce_day_to_required(day: dict, spec: dict) -> dict:
    """Lift ``day`` up to the required workout, preserving the optimizer's extras.

    Enforcement is "ensure minimum": the day's type is set to the required type
    and its duration is raised to the required floor, but any richer detail the
    planner added (description, intervals, power) is left intact when compatible.
    """
    required_type = spec.get("workoutType")
    min_minutes = spec.get("minDurationMinutes")
    values: dict = {}
    # Read both spellings. This was the one reader in the module that looked at
    # camelCase alone, so a snake_case day holding a real session counted as
    # empty and had its title, description and intervals overwritten
    # (ai-trainer-ops#29).
    was_rest_or_empty = str(
        _field(day, "workoutType", "workout_type", default="")
    ).lower() in {"", "rest"}
    if required_type:
        values["workoutType"] = required_type
    if min_minutes:
        current = _field(day, "durationMinutes", "duration_minutes", default=0)
        values["durationMinutes"] = max(int(current or 0), int(min_minutes))
    if spec.get("targetPower") is not None:
        values["targetPower"] = spec.get("targetPower")
    if was_rest_or_empty:
        label = required_type or "endurance"
        values["title"] = f"Required {label} session"
        values["description"] = (
            "Protected required session from an athlete availability constraint."
        )
        values["intervals"] = None
        values["workoutPurpose"] = (
            "Protects a required-session constraint from being dropped by training optimization."
        )
    return _written(day, values)


def _session_cap(constraints: list[dict]) -> tuple[int, str] | None:
    """The per-session limit and the day it holds from, or ``None`` (#185).

    The chat keeps one active limit and a new one replaces it, so there is one;
    should there be more, the shortest holds.
    """
    caps = [
        (int(spec.get("maxDurationMinutes") or 0), str(spec.get("since") or ""))
        for c in constraints
        if c.get("constraintType") == SESSION_CAP
        for spec in [c.get("requiredWorkout") or {}]
    ]
    return min((cap for cap in caps if cap[0] > 0), default=None)


def session_cap_minutes(constraints: list[dict]) -> int | None:
    """The per-session limit the athlete stated, in minutes, or ``None``."""
    cap = _session_cap(constraints)
    return cap[0] if cap else None


def session_length(day: dict) -> int:
    """How long ``day`` may run: its duration, or its window's upper end."""
    duration = _field(day, "durationMinutes", "duration_minutes", default=0)
    upper = _field(day, "durationMaxMinutes", "duration_max_minutes", default=None)
    return max(int(duration or 0), int(upper or 0))


def _required_dates(constraints: list[dict]) -> set[str]:
    return {
        str(c.get("constraintDate"))
        for c in constraints
        if c.get("constraintType") == "required_workout" and c.get("constraintDate")
    }


def over_session_cap(day: dict, constraints: list[dict]) -> bool:
    """Whether ``day`` runs longer than the per-session limit allows.

    Only from the day the limit was said: an earlier session is history, and
    ``coach_chat`` writes skip the completed-day guard (review of PR #1000). A
    long ride the athlete pinned to a date (#110) is the more specific wish, so
    the limit leaves that date alone.
    """
    cap = _session_cap(constraints)
    date_value = str(day.get("date") or "")
    return bool(
        cap
        and date_value >= cap[1]
        and _day_is_training(day)
        and date_value not in _required_dates(constraints)
        and session_length(day) > cap[0]
    )


# A length the description states: "75 minutes", "75-minute", "1.5 hours",
# "90–120 min".
_STATED_LENGTH = re.compile(
    r"(?<![\w.,:])(\d+(?:[.,]\d+)?)(?:\s*[–-]\s*(\d+(?:[.,]\d+)?))?(?:\s*|-)"
    r"(min(?:uten|utes?)?\b|hours?\b|stunden?\b|std\b|h\b)",
    re.IGNORECASE,
)


def _restated(text: object, old: set[int], window: tuple[int, int] | None, cap: int) -> object:
    """``text`` with the session's old length, or its old window, said as ``cap`` min.

    Only those numbers: "4 x 8 min" or a "10-minute warm-up" are parts of the
    session and stay.
    """
    if not isinstance(text, str):
        return text

    def minutes(value: str, unit: str) -> int:
        number = float(value.replace(",", "."))
        return round(number * 60) if unit[0].lower() in "hs" else round(number)

    def replace(match: re.Match[str]) -> str:
        unit = match.group(3)
        first = minutes(match.group(1), unit)
        if match.group(2):
            stated = (first, minutes(match.group(2), unit))
            return f"{cap} min" if window and stated == window else match.group(0)
        return f"{cap} min" if first in old else match.group(0)

    return _STATED_LENGTH.sub(replace, text)


def _capped(day: dict, cap: int) -> dict:
    """``day`` shortened to the per-session limit, its description saying so."""
    duration = int(_field(day, "durationMinutes", "duration_minutes", default=0) or 0)
    lower = _field(day, "durationMinMinutes", "duration_min_minutes", default=None)
    upper = _field(day, "durationMaxMinutes", "duration_max_minutes", default=None)
    window = (int(lower), int(upper)) if lower and upper else None
    old = {duration} | ({int(upper)} if upper else set())
    return _written(
        day,
        {
            "durationMinutes": cap,
            "durationMinMinutes": None,
            "durationMaxMinutes": None,
            "description": _restated(day.get("description"), old, window, cap),
        },
    )


def _required_workout_satisfied_dates(
    plan: list[dict], constraints: list[dict]
) -> set[str]:
    """Dates where *some* session already satisfies the required-workout constraint.

    A required session is a constraint on the *day*, not on every session in it, so
    once a date holds a qualifying session the rest of that date's sessions must be
    left alone. Without this, a two-a-day would have both its morning and evening
    session rewritten into "Required endurance session" (#496).
    """
    satisfied: set[str] = set()
    for day in plan:
        date_value = day.get("date")
        if not date_value:
            continue
        spec = _required_workout_for_day(day, constraints)
        if spec and day_satisfies_required_workout(day, spec):
            satisfied.add(str(date_value))
    return satisfied


def sanitize_plan_for_constraints(plan: list[dict], constraints: list[dict]) -> list[dict]:
    """Apply the athlete's hard constraints, returning canonical days.

    Every day comes back through :func:`schemas.canonical_plan_day`, so the
    output holds **at most one spelling of each modelled field** — not two that
    happen to agree (ai-trainer-ops#38). The distinction is the point: agreement
    was maintained by this module remembering to drop each twin it wrote, which
    is a rule a future writer can forget and which #29 found three modules had
    already forgotten. Absence is maintained by construction.

    This is not a second opinion about what a day means. ``PlanDay`` is the
    persist gate the pipeline already routes every write through, before and
    after this function; canonicalising here only means the gate's own output
    satisfies the property for *any* caller, including the property tests and a
    future one that forgets to canonicalise first.

    The dual-spelling readers above stay, and are still reachable:
    ``day_violates_constraint`` is called on raw days by the pipeline
    (``_preserve_today``, the commitment filter) and
    ``filter_plan_updates_for_constraints`` on raw updates from the router.
    """
    if not constraints:
        return [schemas.canonical_plan_day(day) for day in plan]
    # Which dates already have a qualifying session, and which session on a date
    # gets coerced when none does — the first (lowest-slot) one, so the required
    # workout lands once per day rather than once per session.
    satisfied_dates = _required_workout_satisfied_dates(plan, constraints)
    # Resolved once, up front, and keyed by date rather than by what a day
    # currently holds. Asking ``day_violates_constraint`` here instead made the
    # precedence of no_training over required_workout a property of the *first*
    # application of this gate: once a day was blanked it no longer looked like
    # training, so a second pass skipped this branch and the required-workout
    # coercion below put a session back onto a day the athlete had blocked
    # (ai-trainer-ops#30). Blocking is a fact about the date.
    blocked = blocked_dates(constraints)
    coercion_slot: dict[str, int] = {}
    for day in plan:
        date_value = day.get("date")
        if not date_value:
            continue
        slot = _slot(day)
        date_value = str(date_value)
        if slot < coercion_slot.get(date_value, slot + 1):
            coercion_slot[date_value] = slot
    result: list[dict] = []
    for day in plan:
        if str(day.get("date") or "") in blocked:
            result.append(
                _written(
                    day,
                    {
                        "workoutType": "rest",
                        "title": "Unavailable",
                        "description": "No training scheduled because of an athlete availability constraint.",
                        "durationMinutes": 0,
                        "targetPower": None,
                        "targetHeartRate": None,
                        "intervals": None,
                        "workoutPurpose": "Protects a hard availability constraint from being overwritten by training optimization.",
                        "keyFocusPoints": [
                            "Keep the day free from training",
                            "Move any missed stimulus to an available day",
                            "Use the time for recovery or life commitments",
                        ],
                    },
                )
            )
            continue
        # Positive constraints: ensure a pinned required session is present and
        # meets its minimum, but only coerce when the *date* falls short — and
        # then only its first session, so the other half of a two-a-day survives.
        spec = _required_workout_for_day(day, constraints)
        date_value = str(day.get("date") or "")
        if (
            spec
            and date_value not in satisfied_dates
            and _slot(day) == coercion_slot.get(date_value, 0)
        ):
            result.append(_coerce_day_to_required(day, spec))
        elif over_session_cap(day, constraints):
            # The athlete's limit holds for every session, the stored ones too,
            # not only the day the chat happened to change (#185).
            result.append(_capped(day, session_cap_minutes(constraints)))
        else:
            result.append(day)
    return [schemas.canonical_plan_day(day) for day in result]


def filter_plan_updates_for_constraints(
    updates: list[dict], constraints: list[dict]
) -> list[dict]:
    if not constraints:
        return updates
    return [u for u in updates if not day_violates_constraint(u, constraints)]


def _constraint_for_date(
    constraints: list[dict], date_value: str, constraint_type: str
) -> dict | None:
    for constraint in constraints:
        if (
            constraint.get("constraintType") == constraint_type
            and constraint.get("constraintDate") == date_value
        ):
            return constraint
    return None


def describe_constraint_overrides(
    updates: list[dict], constraints: list[dict]
) -> list[dict]:
    """Describe requested day-updates that hard constraints would override.

    Constraint enforcement is deterministic and non-negotiable (see
    ``sanitize_plan_for_constraints``), so a coach-requested update to a
    constrained day never lands as asked. Callers use this to tell the athlete
    the truth instead of falsely confirming the change (#414).

    Returns one record per overridden update, in input order and deduplicated by
    date. Each record carries ``date``, ``weekday`` (may be ``None``),
    ``constraintType`` and — for required sessions — ``requiredType``:

    - ``no_training``: the update schedules training on an unavailable day, which
      the pipeline drops.
    - ``required_workout``: the update does not meet a pinned required session,
      which the pipeline coerces the day back to.
    - ``max_session_minutes``: the update runs longer than the athlete's limit
      per session, which the pipeline shortens it to (``capMinutes``, #185).

    Updates that comply with every active constraint are not reported.
    """
    if not constraints:
        return []
    seen: set[str] = set()
    overrides: list[dict] = []
    for day in updates:
        date_value = day.get("date")
        if not date_value or date_value in seen:
            continue
        if day_violates_constraint(day, constraints):
            constraint = _constraint_for_date(constraints, date_value, "no_training")
            seen.add(date_value)
            overrides.append(
                {
                    "date": date_value,
                    "weekday": (constraint or {}).get("weekday"),
                    "constraintType": "no_training",
                    "requiredType": None,
                }
            )
            continue
        spec = _required_workout_for_day(day, constraints)
        if spec and not day_satisfies_required_workout(day, spec):
            constraint = _constraint_for_date(
                constraints, date_value, "required_workout"
            )
            seen.add(date_value)
            overrides.append(
                {
                    "date": date_value,
                    "weekday": (constraint or {}).get("weekday"),
                    "constraintType": "required_workout",
                    "requiredType": spec.get("workoutType"),
                }
            )
            continue
        if over_session_cap(day, constraints):
            seen.add(date_value)
            overrides.append(
                {
                    "date": date_value,
                    "weekday": weekday_label_for(str(date_value)),
                    "constraintType": SESSION_CAP,
                    "requiredType": None,
                    "capMinutes": session_cap_minutes(constraints),
                }
            )
    return overrides
