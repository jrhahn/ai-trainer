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
* **Lasting tiredness** (expert consensus). The consensus on overreaching
  (Meeusen et al. 2013, ECSS and ACSM) asks for less load first when fatigue
  and poor performance persist, with no fixed number of days. The prior is no
  intensity for ``athlete_states.TIRED_EASY_DAYS`` days from the last time the
  athlete said it (ops#103 Q34).
* **Ride length** (heuristic). Running has its own ceiling on the long run
  (``run_durability``). Cycling has none, so a planned ride well beyond the
  longest of the last eight weeks is reported: above 1.5 times that ride, and
  never below 90 minutes. No study backs the numbers; the owner chose them
  (ops#81, Q23). Without a ride in the window nothing is judged.
* **Back-to-back hard days** (heuristic). The hard/easy principle is coaching
  tradition rather than a trial result, and block periodisation stacks hard
  days on purpose (Ronnestad et al. 2014). So two intensity days in a row are
  reported, and a block with a reason is a normal thing to keep.
* **Race eve** (heuristic). The taper priors above judge whole weeks, so a race
  four days out never fills their window, and a four-hour ride the day before
  went unreported (ops#76). Any session over 90 minutes on the two days before
  a race is reported. The numbers are the owner's (ops#81, Q25); openers and
  short intensity stay free.
* **Race week** (heuristic). Race eve judges two days, and taper volume needs
  the week before the taper in the plan, so a 3.5-hour ride three days before
  a race went unreported (scenario run 1587). Any session over 3 hours from
  seven to three days before a race is reported. The number is the owner's
  (ops#103 Q35).

Illness, a day with less to give and lasting tiredness come from states the
athlete said in chat (``services/athlete_states``, #18, #39 Q1).

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
PRIOR_TIREDNESS = "lasting-tiredness"
PRIOR_BACK_TO_BACK = "back-to-back-hard"
PRIOR_RIDE_LENGTH = "ride-length"
PRIOR_RACE_EVE = "race-eve"
PRIOR_RACE_WEEK = "race-week"

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
# Race eve (ops#81, Q25): how many days before a race are judged, and the
# longest session they may hold before it is reported.
RACE_EVE_DAYS = 2
RACE_EVE_MAX_MINUTES = 90
# Race week (ops#103 Q35): the longest session the days before the race eve may
# hold before it is reported.
RACE_WEEK_MAX_MINUTES = 180
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
    # The plan days the departure is about. Filled by the priors a write can be
    # held to, so it is held to the departures on the days it changed
    # (ai-trainer-ops#87).
    dates: tuple[date, ...] = ()


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


def planned_upper_minutes(day: dict) -> int:
    """Minutes a day may take: the top of its duration window; 0 for rest."""
    if _workout_type(day) in ("", "rest", "off"):
        return 0
    raw = day.get("durationMaxMinutes", day.get("duration_max_minutes"))
    if raw in (None, ""):
        raw = day.get("durationMinutes", day.get("duration_minutes"))
    try:
        return max(int(float(raw or 0)), 0)
    except (TypeError, ValueError):
        return 0


def recorded_minutes_by_date(rows, today: date) -> dict[date, int]:
    """Minutes the athlete recorded on each day of this calendar week up to today.

    Hand-logged sessions are in ``rows`` too, as their placeholder load rows.
    """
    first = today - timedelta(days=today.weekday())
    minutes: dict[date, int] = {}
    for row in rows or []:
        day = _as_date(getattr(row, "activity_date", None))
        seconds = getattr(row, "duration_seconds", None) or 0
        if day is not None and first <= day <= today:
            minutes[day] = minutes.get(day, 0) + round(seconds / 60)
    return minutes


def _stated_hours(plan, today, weekly_hours, done_minutes) -> list[PriorDeparture]:
    """Calendar weeks over the athlete's stated hours (ai-trainer-ops#116).

    Judged Monday to Sunday, the week the athlete counts in: what is already
    done this week, then what the plan holds from today, each session at the top
    of its duration window, and a day the plan leaves out as nothing. A rolling
    window from today missed a week whose first days were already ridden, and a
    single missing date stopped it judging at all.
    """
    if not weekly_hours or weekly_hours <= 0:
        return []
    limit = weekly_hours * 60 + STATED_HOURS_SLACK_MINUTES
    planned: dict[date, int] = {}
    for day in plan or []:
        if not isinstance(day, dict) or day.get("completed"):
            continue
        when = _as_date(day.get("date"))
        if when is not None:
            planned[when] = planned.get(when, 0) + planned_upper_minutes(day)
    done = done_minutes or {}
    this_monday = today - timedelta(days=today.weekday())
    found = []
    for monday in (this_monday, this_monday + timedelta(days=WEEK_DAYS)):
        days = [monday + timedelta(days=offset) for offset in range(WEEK_DAYS)]
        minutes = sum(
            done.get(day, 0) + (planned.get(day, 0) if day >= today else 0)
            for day in days
        )
        if minutes <= limit:
            continue
        # The whole week: a chat reply is held on any day of it that it changes.
        # The notes go on its sessions only (``plan_pipeline._with_prior_notes``).
        found.append(
            PriorDeparture(
                PRIOR_STATED_HOURS,
                EVIDENCE_ATHLETE,
                f"The week of {monday.isoformat()} holds {_hours(minutes)}"
                + (" with what is already done" if monday < today else "")
                + f"; the athlete said they can give {weekly_hours:g} h a week.",
                tuple(days),
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
    final_week = tuple(race - timedelta(days=offset) for offset in range(1, WEEK_DAYS + 1))
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
                final_week,
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
                    tuple(window),
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
    # A session already done is history: no write can make it easy any more, and
    # a chat reply is held to these (ai-trainer-ops#103 Q37).
    plan = [day for day in plan or [] if not (isinstance(day, dict) and day.get("completed"))]
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
                        tuple(hard),
                    )
                )
        elif state.kind == athlete_states.KIND_TIRED:
            hard = _intense_dates(plan, max(state.starts_on, today), end)
            if hard:
                found.append(
                    PriorDeparture(
                        PRIOR_TIREDNESS,
                        EVIDENCE_CONSENSUS,
                        f"Intensity on {', '.join(d.isoformat() for d in hard)}; the "
                        f"athlete says they have been tired for days. Load usually "
                        f"comes down first: easy until {end.isoformat()}.",
                        tuple(hard),
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
                        tuple(hard),
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
            tuple(sorted({d for first in pairs for d in (first, first + timedelta(days=1))})),
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
    long_dates = []
    for day in plan or []:
        # Rest by type, not by the scalar: a window may carry only its bounds.
        if not isinstance(day, dict) or _workout_type(day) in ("", "rest", "off"):
            continue
        when = _as_date(day.get("date"))
        # The upper bound of a window: the athlete may take the plan at its word.
        _lo, hi = duration_range(day)
        if when and when >= today and day_sport(day) == SPORT_CYCLING and (hi or 0) > limit:
            long_rides.append(f"{when.isoformat()} ({_hours(hi)})")
            long_dates.append(when)
    if not long_rides:
        return []
    return [
        PriorDeparture(
            PRIOR_RIDE_LENGTH,
            EVIDENCE_HEURISTIC,
            f"Rides of {', '.join(long_rides)} against {_hours(longest)} for the "
            f"longest ride of the last {RIDE_LENGTH_WEEKS} weeks.",
            tuple(long_dates),
        )
    ]


def _long_before_race(plan, today, races, race, offsets, limit) -> list[tuple[date, float]]:
    """The days ``offsets`` before ``race`` with a session over ``limit`` minutes,
    with its minutes."""
    window = {race - timedelta(days=offset) for offset in offsets}
    found = []
    for day in plan or []:
        if not isinstance(day, dict) or _workout_type(day) in ("", "rest", "off", "race"):
            continue
        when = _as_date(day.get("date"))
        # A stage race: the day before one race is the race before it.
        if when not in window or when < today or when in races:
            continue
        _lo, hi = duration_range(day)
        if (hi or 0) > limit:
            found.append((when, hi))
    return sorted(found)


def _listed(sessions: list[tuple[date, float]]) -> str:
    return ", ".join(f"{day.isoformat()} ({_hours(minutes)})" for day, minutes in sessions)


def _race_eve(plan, today, race_dates) -> list[PriorDeparture]:
    races = {d for d in (_as_date(r) for r in race_dates or []) if d and d > today}
    found = []
    for race in sorted(races):
        long = _long_before_race(
            plan, today, races, race, range(1, RACE_EVE_DAYS + 1), RACE_EVE_MAX_MINUTES
        )
        if long:
            found.append(
                PriorDeparture(
                    PRIOR_RACE_EVE,
                    EVIDENCE_HEURISTIC,
                    f"Sessions of {_listed(long)} in the {RACE_EVE_DAYS} days before the "
                    f"race on {race.isoformat()}. "
                    f"These days usually stay short: openers, nothing over "
                    f"{RACE_EVE_MAX_MINUTES} minutes.",
                    tuple(d for d, _m in long),
                )
            )
    return found


def _race_week(plan, today, race_dates) -> list[PriorDeparture]:
    races = {d for d in (_as_date(r) for r in race_dates or []) if d and d > today}
    found = []
    for race in sorted(races):
        # The two days before the race are race eve's, with a lower line.
        long = _long_before_race(
            plan,
            today,
            races,
            race,
            range(RACE_EVE_DAYS + 1, WEEK_DAYS + 1),
            RACE_WEEK_MAX_MINUTES,
        )
        if long:
            found.append(
                PriorDeparture(
                    PRIOR_RACE_WEEK,
                    EVIDENCE_HEURISTIC,
                    f"Sessions of {_listed(long)} in the week before the race on "
                    f"{race.isoformat()}. A race week "
                    f"usually holds nothing over {RACE_WEEK_MAX_MINUTES // 60} hours.",
                    tuple(d for d, _m in long),
                )
            )
    return found


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
        elif state.kind == athlete_states.KIND_TIRED:
            lines.append(
                f"  - Tired for days (said on {(state.said_on or state.starts_on).isoformat()})."
                f" Prior: easy sessions until {athlete_states.effective_end(state).isoformat()},"
                " then ask how they feel before planning hard work."
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
    done_minutes_by_date: dict[date, int] | None = None,
) -> list[PriorDeparture]:
    """Where ``plan`` departs from each prior. Empty when it sits inside all of them."""
    totals = _minutes_by_date(plan)
    return [
        *_stated_hours(plan, today, weekly_hours, done_minutes_by_date),
        *_training_days(plan, today, training_days),
        *_taper(plan, totals, today, race_dates),
        *_race_eve(plan, today, race_dates),
        *_race_week(plan, today, race_dates),
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


# The priors a write is held to rather than only told about (ai-trainer-ops#87,
# the owner's yes to #81 Q26). A finding costs one rewrite with the finding in
# the prompt, and what survives the rewrite reaches the athlete with a note. For
# these writers the stated hours and days stay advice: they are the athlete's
# own words. A chat reply is held to them (REPLY_PRIORS); the states bind the
# writers nobody watches as well (UNATTENDED_PRIORS).
HARD_PRIORS = frozenset(
    {
        PRIOR_TAPER_VOLUME,
        PRIOR_TAPER_INTENSITY,
        PRIOR_RACE_EVE,
        PRIOR_RACE_WEEK,
        PRIOR_BACK_TO_BACK,
        PRIOR_RIDE_LENGTH,
    }
)


# The states a chat reply is held to (ai-trainer-ops#103 Q37, the owner's (c)).
STATE_PRIORS = frozenset({PRIOR_ILLNESS, PRIOR_TIREDNESS, PRIOR_REDUCED_DAY})
# What a chat reply is held to on the days it changes (Q37 (c)): the hard priors,
# the stated hours and the states. A reply that leaves a week it changes over the
# athlete's hours, or puts intensity into an illness, gets one rewrite like any
# hard departure (scenario runs 1593, 1594).
REPLY_PRIORS = HARD_PRIORS | STATE_PRIORS | {PRIOR_STATED_HOURS}

# What a writer nobody watches is held to, and what a day's note names
# (ai-trainer-ops#112): the hard priors and the athlete's states. The nightly
# job, an automatic adaptation, a ride review and a new plan heard no reason for
# putting intensity into an illness, so there is no departure for them to keep.
# Held to the hard priors only, the nightly job put a missed VO2 session two
# days into a stated illness, and the day carried no note.
UNATTENDED_PRIORS = HARD_PRIORS | STATE_PRIORS

# What a day's note names: everything above, and a week over the stated hours,
# which stays advice for the writers nobody watches but is worth a note on the
# week's sessions whoever wrote them (ai-trainer-ops#116).
NOTED_PRIORS = UNATTENDED_PRIORS | {PRIOR_STATED_HOURS}


def hard_departures_on(
    departures: list[PriorDeparture], dates, priors: frozenset[str] = HARD_PRIORS
) -> list[PriorDeparture]:
    """The departures from ``priors`` that concern any of ``dates``: what a write changed.

    A departure on a day the write left alone was there before it, and holding
    the write to it would rewrite every reply in a week that already has one.
    """
    touched = {d for d in (_as_date(value) for value in dates or []) if d}
    return [
        departure
        for departure in departures
        if departure.prior in priors and touched.intersection(departure.dates)
    ]


# The priors a chat reply is held to on the days it leaves alone, and when it
# changes nothing at all (ai-trainer-ops#103 Q33, the owner's (a); race week
# Q35; states Q37). A race is coming and the plan works against arriving fresh,
# or the athlete is ill or tired and the plan holds intensity: the protective
# change the coach is to make without being asked (Q30). A taper without
# intensity is not one. Adding intensity is a change the coach offers rather
# than makes. Stated hours are not one either: a plan a little over them would
# rewrite every reply about something else (Q34 (b), left out).
STANDING_PRIORS = frozenset({PRIOR_TAPER_VOLUME, PRIOR_RACE_EVE, PRIOR_RACE_WEEK}) | STATE_PRIORS


def standing_departures(departures: list[PriorDeparture], today: date) -> list[PriorDeparture]:
    """The race and state departures the coach has to change in the coming week.

    A race departure names days before its race. It counts when its last day
    falls in the coming week, so the coach sees every day it has to change. A
    state departure counts from its first day: an open illness runs past the
    week the coach sees, and the days it sees still have to go easy.
    """
    end = today + timedelta(days=WEEK_DAYS)
    found = []
    for departure in departures:
        if departure.prior not in STANDING_PRIORS or not departure.dates:
            continue
        day = min(departure.dates) if departure.prior in STATE_PRIORS else max(departure.dates)
        if today <= day < end:
            found.append(departure)
    return found


def rewrite_request(departures: list[PriorDeparture]) -> str:
    """What the writer is told when its plan breaks a hard prior."""
    if not departures:
        return ""
    lines = [
        "Your plan change breaks planning priors that this app holds every plan to:"
    ]
    lines.extend(f"  - [{d.evidence}] {d.statement}" for d in departures)
    lines.append(
        "Change the plan so it no longer does. Keep a departure only for a concrete "
        "reason about this athlete, and then say that reason in one sentence."
    )
    return "\n".join(lines)
