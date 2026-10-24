"""Rest days and training days in a plan the coach wrote by itself (ai-trainer-ops#178, #189).

A new account with all seven weekdays as training days got a first plan with a
session on every one of 14 days (#178): "training days" read as days the
athlete must train, not days they can. Another got a gym session on a weekday
that is not one of their training days at all (#189).

Two rules, applied to what an automatic writer (generation, the nightly run, an
adaptation) proposes, never to a day the athlete asked for in chat:

- A session of any sport, strength included, lands only on a training day.
- Until there is data to go by, no more than ``MAX_DAYS_IN_A_ROW`` training
  days in a row: the owner's numbers of 2026-10-21, at least one rest day a
  week and at most three days in a row. Three in a row already leaves a rest
  day in every seven.

Only days after today that nothing protects are changed: not today, not a day
done, ridden or pinned. Pure: the caller says which days those are.

Today too on the athlete's first plan (ai-trainer-ops#234). At sign-up nothing
has started, and the exemption let the model's first day through: Tom chose
Monday, Wednesday and Friday, signed up on a Saturday and opened on a 90-minute
Saturday ride.
"""

from __future__ import annotations

from datetime import date

MAX_DAYS_IN_A_ROW = 3

# The automatic writers. A chat change, an edit, a logged session or a synced
# ride is the athlete's own and keeps its day.
GUARDED_TRIGGERS = frozenset({"generate", "adapt", "auto_adapt", "nightly_maintenance", "ride_review"})

# As the profile stores training days.
_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

OFF_DAY_REASON = "Not one of your training days, so it stays free."
IN_A_ROW_REASON = (
    f"A rest day after {MAX_DAYS_IN_A_ROW} training days in a row, while the plan "
    "has no rides of yours to go by."
)


def is_rest(day: dict) -> bool:
    kind = str(day.get("workoutType") or "").lower()
    try:
        minutes = float(day.get("durationMinutes") or 0)
    except (TypeError, ValueError):
        minutes = 0
    return kind in ("rest", "off") or minutes <= 0


def _weekday(iso: str) -> str | None:
    try:
        return _WEEKDAY_NAMES[date.fromisoformat(iso).weekday()]
    except (ValueError, IndexError):
        return None


def _as_rest(day: dict, reason: str) -> dict:
    return {
        **day,
        "workoutType": "rest",
        "title": "Rest day",
        "description": reason,
        "durationMinutes": 0,
        "durationMinMinutes": None,
        "durationMaxMinutes": None,
        "targetPower": None,
        "targetHeartRate": None,
        "intervals": None,
        "strengthExercises": None,
        "workoutPurpose": "Recovery: the days around it are where the training lands.",
        "keyFocusPoints": ["Rest or an easy walk", "Sleep and eat well"],
    }


def guard(
    plan: list[dict],
    *,
    training_days: list[str] | None,
    no_data_yet: bool,
    today: str,
    protected: set[str],
    first_plan: bool = False,
) -> tuple[list[dict], dict[tuple[str, int], str]]:
    """``plan`` with sessions off the training days, and runs too long for an
    athlete without data, turned into rest; and why, per ``(date, slot)``.

    ``protected`` are dates nothing may change here (done, ridden, pinned).
    ``first_plan`` says nothing was planned before this write, so today is the
    plan's first day rather than one the athlete may be in the middle of.
    """
    allowed = {d.lower() for d in training_days or [] if isinstance(d, str)}
    reasons: dict[tuple[str, int], str] = {}

    def changeable(day: dict) -> bool:
        iso = str(day.get("date") or "")
        coming = iso > today or (first_plan and iso == today)
        return coming and iso not in protected and not day.get("completed")

    result = list(plan)
    if allowed:
        for i, day in enumerate(result):
            if is_rest(day) or not changeable(day):
                continue
            if _weekday(str(day.get("date") or "")) not in allowed:
                result[i] = _as_rest(day, OFF_DAY_REASON)
                reasons[(str(day["date"]), int(day.get("slot") or 0))] = OFF_DAY_REASON

    if no_data_yet:
        by_date: dict[str, list[int]] = {}
        for i, day in enumerate(result):
            if day.get("date"):
                by_date.setdefault(str(day["date"]), []).append(i)
        run, previous = 0, None
        for iso in sorted(by_date):
            current = date.fromisoformat(iso)
            # A date the plan does not hold is no training day.
            if previous is not None and (current - previous).days > 1:
                run = 0
            previous = current
            indices = by_date[iso]
            if all(is_rest(result[i]) for i in indices):
                run = 0
                continue
            if run < MAX_DAYS_IN_A_ROW or not all(changeable(result[i]) for i in indices):
                run += 1
                continue
            for i in indices:
                if not is_rest(result[i]):
                    reasons[(iso, int(result[i].get("slot") or 0))] = IN_A_ROW_REASON
                result[i] = _as_rest(result[i], IN_A_ROW_REASON)
            run = 0
    return result, reasons
