"""Planning priors: defaults with their evidence, not rules (ai-trainer-ops#26).

The guards in ``plan_pipeline`` revert a write. These do not. A prior is where
the plan should sit unless the coach knows something about this athlete that
moves it, and the coach is the one who knows. So this module only measures how
far the plan departs from each prior and hands that to the writer as a fact,
together with how strong the evidence behind the prior is. The writer then
either brings the plan back or keeps the departure and says why. That was the
owner's call: "no rigid rules, rather a prior the agent adjusts from its own
judgement".

The priors and their evidence:

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

* **Illness** (expert consensus). The IOC consensus on load and illness
  (Schwellnus et al. 2016) and the "neck check" ask for no hard work while ill
  and a graded return after, with no fixed number of days. The prior is no
  intensity while ill and for ``athlete_states.RETURN_EASY_DAYS`` days after.
* **A day with less to give** (athlete-stated). After a party, on a long work
  day: no intensity that day.
* **Ride length** (heuristic). Running has its own ceiling on the long run
  (``run_durability``). Cycling has none, so a planned ride well beyond the
  longest of the last eight weeks is reported: above 1.5 times that ride, and
  never below 90 minutes. No study backs the numbers; the owner chose them
  (ops#81, Q23). Without a ride in the window nothing is judged.
* **Back-to-back hard days** (heuristic). The hard/easy principle is coaching
  tradition rather than a trial result, and block periodisation stacks hard
  days on purpose (Ronnestad et al. 2014). So two intensity days in a row are
  reported, and a block with a reason is a normal thing to keep.

Both come from states the athlete said in chat (``services/athlete_states``,
#18, #39 Q1).

Pure: no database, no clock. ``plan_context`` reads the inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from schemas import day_sport
from services import athlete_states
from services.activity_identity import SPORT_CYCLING, training_sport
from services.duration_range import duration_range

EVIDENCE_ATHLETE = "athlete-stated"
EVIDENCE_STRONG = "strong evidence"
EVIDENCE_HEURISTIC = "heuristic"
EVIDENCE_CONSENSUS = "expert consensus"

PRIOR_STATED_HOURS = "stated-hours"
PRIOR_TRAINING_DAYS = "training-days"
PRIOR_TAPER_VOLUME = "taper-volume"
PRIOR_TAPER_INTENSITY = "taper-intensity"
PRIOR_RAMP = "ramp"
PRIOR_ILLNESS = "illness"
PRIOR_REDUCED_DAY = "reduced-day"
PRIOR_BACK_TO_BACK = "back-to-back-hard"
PRIOR_RIDE_LENGTH = "ride-length"

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
# Ride length (ops#81, Q23): the window the longest ride is read from, the
# factor above it that is reported, and the floor below which nothing is.
RIDE_LENGTH_WEEKS = 8
RIDE_LENGTH_RATIO_MAX = 1.5
RIDE_LENGTH_FLOOR_MINUTES = 90
# How far ahead back-to-back hard days are looked for. A plan holds weeks, not
# years; the cap also keeps a bogus far-future date out of date arithmetic.
BACK_TO_BACK_HORIZON_DAYS = 366
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


def _intense_dates(plan, first: date, last: date) -> list[date]:
    return sorted(
        {
            when
            for day in plan or []
            if isinstance(day, dict)
            and _workout_type(day) in INTENSITY_TYPES
            and (when := _as_date(day.get("date"))) is not None
            and first <= when <= last
        }
    )


def _states(plan, today, states) -> list[PriorDeparture]:
    found = []
    for state in states or ():
        end = athlete_states.effective_end(state)
        if state.kind == athlete_states.KIND_ILL:
            last = end + timedelta(days=athlete_states.RETURN_EASY_DAYS)
            hard = _intense_dates(plan, max(state.starts_on, today), last)
            if hard:
                found.append(
                    PriorDeparture(
                        PRIOR_ILLNESS,
                        EVIDENCE_CONSENSUS,
                        f"Intensity on {', '.join(d.isoformat() for d in hard)}; the "
                        f"athlete is ill (until {end.isoformat()} at the latest) and a "
                        f"return usually stays easy until {last.isoformat()}.",
                    )
                )
        else:
            hard = _intense_dates(plan, max(state.starts_on, today), end)
            if hard:
                found.append(
                    PriorDeparture(
                        PRIOR_REDUCED_DAY,
                        EVIDENCE_ATHLETE,
                        f"Intensity on {', '.join(d.isoformat() for d in hard)}; the "
                        f"athlete said they will have less to give that day.",
                    )
                )
    return found


def _back_to_back(plan, today) -> list[PriorDeparture]:
    hard = set(
        _intense_dates(plan, today, today + timedelta(days=BACK_TO_BACK_HORIZON_DAYS))
    )
    pairs = sorted(d for d in hard if d + timedelta(days=1) in hard)
    if not pairs:
        return []
    listed = ", ".join(
        f"{d.isoformat()} and {(d + timedelta(days=1)).isoformat()}" for d in pairs
    )
    return [
        PriorDeparture(
            PRIOR_BACK_TO_BACK,
            EVIDENCE_HEURISTIC,
            f"Intensity on consecutive days: {listed}. Hard days usually have an "
            f"easy day between them, unless this is a deliberate block.",
        )
    ]


def longest_ride_minutes(rows, today: date) -> float | None:
    """The longest recorded ride of the last ``RIDE_LENGTH_WEEKS``, in minutes."""
    first = today - timedelta(weeks=RIDE_LENGTH_WEEKS)
    minutes = [
        seconds / 60
        for row in rows or []
        if training_sport(getattr(row, "sport_type", None)) == SPORT_CYCLING
        and (when := _as_date(getattr(row, "activity_date", None))) is not None
        and first <= when < today
        and (seconds := getattr(row, "duration_seconds", None) or 0) > 0
    ]
    return max(minutes, default=None)


def _ride_length(plan, today, longest) -> list[PriorDeparture]:
    if not longest:
        return []
    limit = max(RIDE_LENGTH_RATIO_MAX * longest, RIDE_LENGTH_FLOOR_MINUTES)
    long_rides = []
    for day in plan or []:
        # Rest by type, not by the scalar: a window may carry only its bounds.
        if not isinstance(day, dict) or _workout_type(day) in ("", "rest", "off"):
            continue
        when = _as_date(day.get("date"))
        # The upper bound of a window: the athlete may take the plan at its word.
        _lo, hi = duration_range(day)
        if when and when >= today and day_sport(day) == SPORT_CYCLING and (hi or 0) > limit:
            long_rides.append(f"{when.isoformat()} ({_hours(hi)})")
    if not long_rides:
        return []
    return [
        PriorDeparture(
            PRIOR_RIDE_LENGTH,
            EVIDENCE_HEURISTIC,
            f"Rides of {', '.join(long_rides)} against {_hours(longest)} for the "
            f"longest ride of the last {RIDE_LENGTH_WEEKS} weeks.",
        )
    ]


def athlete_states_section(states, today: date) -> str:
    """What the athlete said about their state, for a writer that plans from it."""
    lines = []
    for state in states or ():
        if state.kind == athlete_states.KIND_ILL:
            end = athlete_states.effective_end(state)
            easy_until = end + timedelta(days=athlete_states.RETURN_EASY_DAYS)
            if state.ends_on is not None and state.ends_on < today:
                lines.append(
                    f"  - Was ill until {state.ends_on.isoformat()}. Prior: easy "
                    f"sessions until {easy_until.isoformat()}, then a graded return."
                )
            elif state.ends_on is None:
                lines.append(
                    f"  - Ill since {state.starts_on.isoformat()}, no end stated. "
                    f"Prior: rest or easy only while ill, easy for "
                    f"{athlete_states.RETURN_EASY_DAYS} days after. Ask how they feel "
                    f"before planning hard work."
                )
            else:
                lines.append(
                    f"  - Ill until {state.ends_on.isoformat()}. Prior: rest or easy "
                    f"only, then easy until {easy_until.isoformat()}."
                )
        else:
            lines.append(
                f"  - Less to give on {state.starts_on.isoformat()} (the athlete said "
                f"so). Prior: no intensity that day."
            )
    if not lines:
        return ""
    return "\n".join(
        [
            (
                "Athlete state (said in chat; priors from expert consensus, not "
                "rules. The common 'neck check' guidance: symptoms above the neck "
                "may allow easy riding, fever or chest symptoms ask for rest):"
            ),
            *lines,
        ]
    )


def find_prior_departures(
    plan: list[dict] | None,
    today: date,
    *,
    weekly_hours: float | None = None,
    training_days: list[str] | None = None,
    race_dates: list[object] | None = None,
    recent_weekly_hours: tuple[float, ...] | list[float] | None = None,
    states: list[athlete_states.StatedState] | None = None,
    longest_ride: float | None = None,
) -> list[PriorDeparture]:
    """Where ``plan`` departs from each prior. Empty when it sits inside all of them."""
    totals = _minutes_by_date(plan)
    return [
        *_stated_hours(totals, today, weekly_hours),
        *_training_days(plan, today, training_days),
        *_taper(plan, totals, today, race_dates),
        *_ramp(totals, today, recent_weekly_hours),
        *_states(plan, today, states),
        *_back_to_back(plan, today),
        *_ride_length(plan, today, longest_ride),
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
        "bring the plan back towards the prior. Weigh athlete-stated priors, "
        "strong evidence and expert consensus above a heuristic."
    )
    return "\n".join(lines)
