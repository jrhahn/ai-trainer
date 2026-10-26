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
GUARDED_TRIGGERS = frozenset(
    {"generate", "adapt", "auto_adapt", "nightly_maintenance", "ride_review"}
)

# As the profile stores training days.
_WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

OFF_DAY_REASON = "Not one of your training days, so it stays free."
IN_A_ROW_REASON = (
    f"A rest day after {MAX_DAYS_IN_A_ROW} training days in a row, while the plan "
    "has no rides of yours to go by."
)
# What a day this guard turns into rest says. The guard writes exactly the
# first days of a new tester's plan, so these have German of their own rather
# than waiting for a translation round trip (ai-trainer-ops#265).
REST_TITLE = "Rest day"
REST_PURPOSE = "Recovery: the days around it are where the training lands."
REST_FOCUS = ("Rest or an easy walk", "Sleep and eat well")

# Sam (Mon, Wed, Sat) signed up on a Saturday and opened on "Rest Day — Take
# today completely off the bike to let your body absorb the upcoming training
# block". On day one there is nothing to absorb. The owner's (b), 2026-10-24:
# the rest day stays, says the true reason and offers today (ai-trainer-ops#234).
# Fixed words, so the German is fixed too (``plan_translations.FIXED``).
WEEKDAYS_EN = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
WEEKDAYS_DE = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)
FIRST_DAY_FREE = (
    "Your plan starts today with a free day; the first session is on {weekday}. "
    "Want to train today already? Tell your coach."
)
FIRST_DAY_FREE_NO_SESSION = "Your plan starts today with a free day. Want to train today already? Tell your coach."
GERMAN = {
    **{
        FIRST_DAY_FREE.format(weekday=weekday): (
            f"Dein Plan startet heute mit einem freien Tag; die erste Einheit ist am {tag}. "
            "Willst du heute schon trainieren? Sag es deinem Coach."
        )
        for weekday, tag in zip(WEEKDAYS_EN, WEEKDAYS_DE)
    },
    FIRST_DAY_FREE_NO_SESSION: (
        "Dein Plan startet heute mit einem freien Tag. Willst du heute schon trainieren? "
        "Sag es deinem Coach."
    ),
    REST_TITLE: "Ruhetag",
    OFF_DAY_REASON: "Keiner deiner Trainingstage, deshalb bleibt er frei.",
    IN_A_ROW_REASON: (
        f"Ein Ruhetag nach {MAX_DAYS_IN_A_ROW} Trainingstagen in Folge, solange der Plan "
        "noch keine Fahrten von dir hat, nach denen er sich richten kann."
    ),
    REST_PURPOSE: "Erholung: Das Training findet an den Tagen davor und danach statt.",
    REST_FOCUS[0]: "Ausruhen oder ein lockerer Spaziergang",
    REST_FOCUS[1]: "Gut schlafen und gut essen",
}


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
        "title": REST_TITLE,
        "description": reason,
        "durationMinutes": 0,
        "durationMinMinutes": None,
        "durationMaxMinutes": None,
        "targetPower": None,
        "targetHeartRate": None,
        "intervals": None,
        "strengthExercises": None,
        "workoutPurpose": REST_PURPOSE,
        "keyFocusPoints": list(REST_FOCUS),
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
            if run < MAX_DAYS_IN_A_ROW or not all(
                changeable(result[i]) for i in indices
            ):
                run += 1
                continue
            for i in indices:
                if not is_rest(result[i]):
                    reasons[(iso, int(result[i].get("slot") or 0))] = IN_A_ROW_REASON
                result[i] = _as_rest(result[i], IN_A_ROW_REASON)
            run = 0

    if first_plan and no_data_yet and _weekday(today) in allowed:
        result = _first_day_free(result, today, protected)
    return result, reasons


def _first_day_free(plan: list[dict], today: str, protected: set[str]) -> list[dict]:
    """``plan`` with a rest day on today, a training day, saying why: the plan
    starts with a free day, the first session is on its weekday, and today is
    the athlete's to ask for. The model's own reason and purpose go, because
    with no rides and no plan before there is none."""
    indices = [i for i, day in enumerate(plan) if str(day.get("date") or "") == today]
    if (
        not indices
        or today in protected
        or not all(is_rest(plan[i]) and not plan[i].get("completed") for i in indices)
    ):
        return plan
    ahead = sorted(
        str(day.get("date"))
        for day in plan
        if str(day.get("date") or "") > today and not is_rest(day)
    )
    text = (
        FIRST_DAY_FREE.format(
            weekday=WEEKDAYS_EN[date.fromisoformat(ahead[0]).weekday()]
        )
        if ahead
        else FIRST_DAY_FREE_NO_SESSION
    )
    result = list(plan)
    for i in indices:
        result[i] = {
            **result[i],
            "description": text,
            "workoutPurpose": None,
            "keyFocusPoints": None,
        }
    return result
