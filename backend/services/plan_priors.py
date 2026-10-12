"""Planning priors: defaults with their evidence, not rules (ai-trainer-ops#26).

The guards in ``plan_pipeline`` revert a write. These do not. A prior is where
the plan should sit unless the coach knows something about this athlete that
moves it, and the coach is the one who knows. So this module only measures how
far the plan departs from each prior and hands that to the writer as a fact,
together with how strong the evidence behind the prior is. The writer then
either brings the plan back or keeps the departure and says why. That was the
owner's call: "no rigid rules, rather a prior the agent adjusts from its own
judgement".

The four priors and their evidence:

* **Stated hours** (athlete-stated). ``weeklyHours`` is what the athlete said
  they can give. Not a science question.
* **Training days** (athlete-stated). ``trainingDays`` from onboarding (#43).
* **Taper** (strong). Bosquet et al. 2007 (meta-analysis), Mujika & Padilla
  2003: in the last one to two weeks volume drops by about 40-60 %, intensity
  and frequency stay. So two departures: volume that barely drops, and
  intensity that disappears. A taper that cuts intensity is the worse taper.
* **Ramp** (heuristic). The 10 % rule is not supported (Buist 2008). Nielsen
  2014 saw more running injuries only above about 30 % per week, and ACWR is
  disputed (Impellizzeri 2020). Only a wide outlier is reported, and it is
  labelled as a heuristic.

Illness is not here. It needs a structured illness state captured from chat,
which does not exist yet (#18).

Pure: no database, no clock. ``plan_context`` reads the inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

EVIDENCE_ATHLETE = "athlete-stated"
EVIDENCE_STRONG = "strong evidence"
EVIDENCE_HEURISTIC = "heuristic"

PRIOR_STATED_HOURS = "stated-hours"
PRIOR_TRAINING_DAYS = "training-days"
PRIOR_TAPER_VOLUME = "taper-volume"
PRIOR_TAPER_INTENSITY = "taper-intensity"
PRIOR_RAMP = "ramp"

WEEK_DAYS = 7
# The final week should hold at most this share of the week before it. Bosquet's
# optimum is a 41-60 % cut; 0.8 reports only a taper that cuts less than 20 %,
# so a coach working at the gentle end of the range is not flagged.
TAPER_VOLUME_RATIO_MAX = 0.8
# Intensity is looked for in the final ten days before the race, the same window
# ``training_phase`` calls the taper.
TAPER_INTENSITY_DAYS = 10
# Above this week-on-week ratio the ramp is reported. See the module docstring.
RAMP_RATIO_MAX = 1.3
# Below this many hours a week the baseline says nothing a ratio could use: one
# extra ride would read as a 100 % ramp.
RAMP_MIN_BASELINE_HOURS = 2.0
# Tolerance on the athlete's stated hours, so rounding in a description is not a
# finding. Ten minutes, not a percentage: the stated figure is the athlete's.
STATED_HOURS_SLACK_MINUTES = 10

INTENSITY_TYPES = frozenset(
    {"intervals", "threshold", "vo2max", "tempo", "sweet_spot", "sweetspot", "race"}
)
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


@dataclass(frozen=True, slots=True)
class PriorDeparture:
    prior: str
    evidence: str
    statement: str


def _as_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _workout_type(day: dict) -> str:
    return str(day.get("workoutType") or day.get("workout_type") or "").strip().lower()


def planned_minutes(day: dict) -> int:
    """Minutes of training the day holds; 0 for rest."""
    if _workout_type(day) in ("", "rest", "off"):
        return 0
    raw = day.get("durationMinutes", day.get("duration_minutes"))
    try:
        return max(int(float(raw or 0)), 0)
    except (TypeError, ValueError):
        return 0


def _minutes_by_date(plan: list[dict] | None) -> dict[date, int]:
    totals: dict[date, int] = {}
    for day in plan or []:
        if not isinstance(day, dict):
            continue
        when = _as_date(day.get("date"))
        if when is not None:
            totals[when] = totals.get(when, 0) + planned_minutes(day)
    return totals


def _hours(minutes: float) -> str:
    return f"{minutes / 60:.1f} h"


def _window_minutes(totals: dict[date, int], start: date) -> int | None:
    """Planned minutes over seven days from ``start``, or None if the plan does not cover them."""
    days = [start + timedelta(days=offset) for offset in range(WEEK_DAYS)]
    if not all(day in totals for day in days):
        return None
    return sum(totals[day] for day in days)


def _stated_hours(totals, today, weekly_hours) -> list[PriorDeparture]:
    if not weekly_hours or weekly_hours <= 0:
        return []
    limit = weekly_hours * 60 + STATED_HOURS_SLACK_MINUTES
    found = []
    for start in (today, today + timedelta(days=WEEK_DAYS)):
        minutes = _window_minutes(totals, start)
        if minutes is not None and minutes > limit:
            found.append(
                PriorDeparture(
                    PRIOR_STATED_HOURS,
                    EVIDENCE_ATHLETE,
                    f"The 7 days from {start.isoformat()} plan {_hours(minutes)}; "
                    f"the athlete said they can give {weekly_hours:g} h a week.",
                )
            )
    return found


def _training_days(plan, today, training_days) -> list[PriorDeparture]:
    allowed = {str(day).lower() for day in training_days or []}
    if not allowed:
        return []
    off = sorted(
        {
            when
            for day in plan or []
            if isinstance(day, dict)
            and planned_minutes(day) > 0
            and (when := _as_date(day.get("date"))) is not None
            and when >= today
            and WEEKDAYS[when.weekday()] not in allowed
        }
    )
    if not off:
        return []
    listed = ", ".join(f"{d.isoformat()} ({WEEKDAYS[d.weekday()]})" for d in off)
    days = ", ".join(day for day in WEEKDAYS if day in allowed)
    return [
        PriorDeparture(
            PRIOR_TRAINING_DAYS,
            EVIDENCE_ATHLETE,
            f"Sessions on {listed}; the athlete said they train on {days}.",
        )
    ]


def _taper(plan, totals, today, race_dates) -> list[PriorDeparture]:
    upcoming = sorted(
        d for d in (_as_date(r) for r in race_dates or []) if d and d > today
    )
    if not upcoming:
        return []
    race = upcoming[0]
    found = []
    final = _window_minutes(totals, race - timedelta(days=WEEK_DAYS))
    before = _window_minutes(totals, race - timedelta(days=2 * WEEK_DAYS))
    if final is not None and before and final > TAPER_VOLUME_RATIO_MAX * before:
        found.append(
            PriorDeparture(
                PRIOR_TAPER_VOLUME,
                EVIDENCE_STRONG,
                f"The last 7 days before the race on {race.isoformat()} plan "
                f"{_hours(final)} against {_hours(before)} the week before, a cut of "
                f"{round(100 * (1 - final / before))} %. A taper usually cuts volume by "
                f"about 40-60 %.",
            )
        )
    window = [
        race - timedelta(days=offset) for offset in range(1, TAPER_INTENSITY_DAYS + 1)
    ]
    if all(day in totals for day in window) and window[-1] >= today:
        intense = any(
            isinstance(day, dict)
            and _as_date(day.get("date")) in window
            and _workout_type(day) in INTENSITY_TYPES
            for day in plan or []
        )
        if not intense:
            found.append(
                PriorDeparture(
                    PRIOR_TAPER_INTENSITY,
                    EVIDENCE_STRONG,
                    f"No intensity in the {TAPER_INTENSITY_DAYS} days before the race on "
                    f"{race.isoformat()}. A taper usually keeps short race-pace or "
                    f"interval work and cuts volume instead.",
                )
            )
    return found


def _ramp(totals, today, recent_weekly_hours) -> list[PriorDeparture]:
    recent = [h for h in (recent_weekly_hours or ())][-4:]
    if len(recent) < 4:
        return []
    baseline = sum(recent) / len(recent)
    if baseline < RAMP_MIN_BASELINE_HOURS:
        return []
    minutes = _window_minutes(totals, today)
    if minutes is None or minutes <= RAMP_RATIO_MAX * baseline * 60:
        return []
    return [
        PriorDeparture(
            PRIOR_RAMP,
            EVIDENCE_HEURISTIC,
            f"The next 7 days plan {_hours(minutes)} against {baseline:.1f} h a week "
            f"over the last four weeks, +{round(100 * (minutes / 60 / baseline - 1))} %.",
        )
    ]


def find_prior_departures(
    plan: list[dict] | None,
    today: date,
    *,
    weekly_hours: float | None = None,
    training_days: list[str] | None = None,
    race_dates: list[object] | None = None,
    recent_weekly_hours: tuple[float, ...] | list[float] | None = None,
) -> list[PriorDeparture]:
    """Where ``plan`` departs from each prior. Empty when it sits inside all of them."""
    totals = _minutes_by_date(plan)
    return [
        *_stated_hours(totals, today, weekly_hours),
        *_training_days(plan, today, training_days),
        *_taper(plan, totals, today, race_dates),
        *_ramp(totals, today, recent_weekly_hours),
    ]


def plan_priors_section(departures: list[PriorDeparture]) -> str:
    """The departures as a prompt block, with the freedom to keep them."""
    if not departures:
        return ""
    lines = [
        (
            "Planning priors (computed from the plan; the priors are defaults with "
            "their evidence, not rules). The current plan departs from them here:"
        )
    ]
    lines.extend(f"  - [{d.evidence}] {d.statement}" for d in departures)
    lines.append(
        "Use your judgement. If what you know about this athlete justifies a "
        "departure, keep it and tell the athlete why in one sentence. Otherwise "
        "bring the plan back towards the prior. Weigh athlete-stated priors and "
        "strong evidence above a heuristic."
    )
    return "\n".join(lines)
