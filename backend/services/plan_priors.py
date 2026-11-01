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

* **Stated hours** (athlete-stated). ``weeklyHours`` is a limit the athlete
  stated themselves, in the chat. Onboarding no longer asks (ai-trainer-ops#116),
  so without one this prior is silent and the volume is estimated from the rides.
  Not a science question.
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
  With fever or symptoms below the neck (``athlete_states.KIND_FEVER``): no
  session at all while ill, and easy for at least as many days as it lasted.
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
athlete said in chat (``domain/athlete_states``, #18, #39 Q1).

Pure: no database, no clock. ``plan_context`` reads the inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from schemas import day_sport
from domain import athlete_states
from domain.activity_identity import SPORT_CYCLING, training_sport
from domain.duration_range import duration_range
from domain.plan_day import INTENSITY_TYPES, planned_minutes
from domain.plan_day import workout_type as _workout_type

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
# A session at all while ill with fever (ai-trainer-ops#338).
PRIOR_FEVER = "illness-fever"
# A session before a doctor has checked a warning sign (ai-trainer-ops#338, A1).
PRIOR_CLEARANCE = "medical-clearance"
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
    # A week over the stated hours: the hours it holds and the hours the athlete
    # stated, for the note under a reply that leaves it so.
    week_hours: tuple[float, float] | None = None


def _as_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


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

    def day_minutes(day: date) -> int:
        if day < today:
            return done.get(day, 0)
        if day > today:
            return planned.get(day, 0)
        # A ride this morning may be today's session, not yet marked done: the
        # larger of the two, not both (review of #928).
        return max(done.get(day, 0), planned.get(day, 0))

    def is_done(day: date) -> bool:
        return day < today or (day == today and done.get(day, 0) > planned.get(day, 0))

    this_monday = today - timedelta(days=today.weekday())
    found = []
    for monday in (this_monday, this_monday + timedelta(days=WEEK_DAYS)):
        days = [monday + timedelta(days=offset) for offset in range(WEEK_DAYS)]
        minutes = sum(day_minutes(day) for day in days)
        if minutes <= limit:
            continue
        ridden = sum(day_minutes(day) for day in days if is_done(day))
        sessions = [
            (day, day_minutes(day))
            for day in days
            if not is_done(day) and day_minutes(day) > 0
        ]
        # The whole week: a chat reply is held on any day of it that it changes.
        # The notes go on its sessions only (``plan_pipeline._with_prior_notes``).
        found.append(
            PriorDeparture(
                PRIOR_STATED_HOURS,
                EVIDENCE_ATHLETE,
                f"The week of {monday.isoformat()} holds {_hours(minutes)}"
                # Said only when something is: told so over a week with nothing
                # ridden, the coach counted a planned ride as done (#86).
                + (" with what is already done" if ridden else "")
                + f"; the athlete said they can give {weekly_hours:g} h a week. "
                + _hours_arithmetic(sessions, ridden, round(weekly_hours * 60)),
                tuple(days),
                (round(minutes / 60, 1), weekly_hours),
            )
        )
    return found


def _hours_arithmetic(
    sessions: list[tuple[date, int]], done_minutes: int, stated_minutes: int
) -> str:
    """The sum behind a week over the stated hours, worked out for the coach.

    Told only the week's hours, the coach called a week it had left at 4.3 h
    "exactly three hours" (scenario ``beginner-three-hours``): it did the sum
    itself and got it wrong. So the days, their minutes and what has to come out
    are given, and the coach only chooses where.
    """
    planned = sum(minutes for _, minutes in sessions)
    listed = ", ".join(
        f"{WEEKDAYS[day.weekday()].capitalize()} {day.isoformat()} {minutes} min"
        for day, minutes in sessions
    )
    done = f"Already done: {done_minutes} min. " if done_minutes else ""
    if not sessions:
        return done + "Nothing planned is left in that week to take out."
    allowed = max(stated_minutes - done_minutes, 0)
    return (
        f"{done}Planned: {listed}, {planned} min in all. To keep to the stated "
        f"{stated_minutes} min, the planned days of that week may hold at most "
        f"{allowed} min together: {planned - allowed} min less than now."
    )


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


def _covered_by_states(states, today: date) -> set[date]:
    """The days from ``today`` a stated illness, tiredness or bad day keeps easy.

    The same windows ``_states`` holds intensity off. The taper and the
    back-to-back check asked for intensity there, or held it against them, so a
    plan could not satisfy both and every chat reply got a rewrite
    (ai-trainer-ops#128).
    """
    covered: set[date] = set()
    for state in states or ():
        end = athlete_states.effective_end(state)
        end += timedelta(
            days=athlete_states.return_days(state.kind, state.starts_on, state.ends_on)
        )
        day = max(state.starts_on, today)
        while day <= end:
            covered.add(day)
            day += timedelta(days=1)
    return covered


def _taper(
    plan, totals, today, race_dates, covered=frozenset()
) -> list[PriorDeparture]:
    upcoming = sorted(
        d for d in (_as_date(r) for r in race_dates or []) if d and d > today
    )
    if not upcoming:
        return []
    race = upcoming[0]
    found = []
    final_week = tuple(
        race - timedelta(days=offset) for offset in range(1, WEEK_DAYS + 1)
    )
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
    # Intensity belongs on the days no state keeps easy; with none, the taper
    # gives way to the illness (ai-trainer-ops#128).
    free = [day for day in window if day not in covered]
    if free and all(day in totals for day in window) and window[-1] >= today:
        intense = any(
            isinstance(day, dict)
            and _as_date(day.get("date")) in free
            and _workout_type(day) in INTENSITY_TYPES
            for day in plan or []
        )
        if not intense:
            kept_easy = len(window) - len(free)
            found.append(
                PriorDeparture(
                    PRIOR_TAPER_INTENSITY,
                    EVIDENCE_STRONG,
                    f"No intensity in the {TAPER_INTENSITY_DAYS} days before the race on "
                    f"{race.isoformat()}"
                    + (
                        f" outside the {kept_easy} the athlete's state keeps easy"
                        if kept_easy
                        else ""
                    )
                    + ". A taper usually keeps short race-pace or interval work and "
                    "cuts volume instead.",
                    tuple(free),
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


def _session_dates(plan, first: date, last: date) -> list[date]:
    """The days from ``first`` to ``last`` with any session on them."""
    return sorted(
        {
            when
            for day in plan or []
            if isinstance(day, dict)
            and planned_minutes(day) > 0
            and (when := _as_date(day.get("date"))) is not None
            and first <= when <= last
        }
    )


def _states(plan, today, states) -> list[PriorDeparture]:
    # A session already done is history: no write can make it easy any more, and
    # a chat reply is held to these (ai-trainer-ops#103 Q37).
    plan = [
        day
        for day in plan or []
        if not (isinstance(day, dict) and day.get("completed"))
    ]
    found = []
    for state in states or ():
        end = athlete_states.effective_end(state)
        if state.kind == athlete_states.KIND_FEVER:
            # No session at all while the fever lasts, not only no intensity
            # (ai-trainer-ops#338).
            riding = _session_dates(plan, max(state.starts_on, today), end)
            if riding:
                found.append(
                    PriorDeparture(
                        PRIOR_FEVER,
                        EVIDENCE_CONSENSUS,
                        f"A session on {', '.join(d.isoformat() for d in riding)}; "
                        f"the athlete is ill with fever (until {end.isoformat()} at "
                        "the latest), and a fever means no training at all.",
                        tuple(riding),
                    )
                )
        elif state.kind == athlete_states.KIND_CLEARANCE:
            riding = _session_dates(plan, max(state.starts_on, today), end)
            if riding:
                found.append(
                    PriorDeparture(
                        PRIOR_CLEARANCE,
                        EVIDENCE_CONSENSUS,
                        f"A session on {', '.join(d.isoformat() for d in riding)}; "
                        f"the athlete named a warning sign (chest pain, fainting, a "
                        f"racing heart, breathlessness or dizziness) on "
                        f"{state.starts_on.isoformat()} and no doctor has checked it "
                        "yet: no training until one has.",
                        tuple(riding),
                    )
                )
        if athlete_states.is_ill(state.kind):
            last = end + timedelta(
                days=athlete_states.return_days(
                    state.kind, state.starts_on, state.ends_on
                )
            )
            # With fever, a session in the illness is the fever's finding.
            first = (
                end + timedelta(days=1)
                if state.kind == athlete_states.KIND_FEVER
                else state.starts_on
            )
            hard = _intense_dates(plan, max(first, today), last)
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


def _race_days(plan, race_dates) -> set[date]:
    """The calendar's race days and the plan's own race sessions."""
    days = {d for d in (_as_date(r) for r in race_dates or []) if d}
    days.update(
        when
        for day in plan or []
        if isinstance(day, dict)
        and _workout_type(day) == "race"
        and (when := _as_date(day.get("date"))) is not None
    )
    return days


def _back_to_back(
    plan, today, race_dates=None, covered=frozenset()
) -> list[PriorDeparture]:
    # A race's eve is no hard day beside it: the openers before a race, and each
    # stage of a stage race but the last, are what the race-eve prior asks for
    # (ai-trainer-ops#128). The day after a race is not exempt: intervals then
    # are two hard days. A day a state keeps easy is the state's to report.
    races = _race_days(plan, race_dates)
    exempt = {race - timedelta(days=1) for race in races} | set(covered)
    hard = (
        set(
            _intense_dates(
                plan, today, today + timedelta(days=BACK_TO_BACK_HORIZON_DAYS)
            )
        )
        - exempt
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
            tuple(
                sorted(
                    {d for first in pairs for d in (first, first + timedelta(days=1))}
                )
            ),
        )
    ]


def longest_ride_minutes(rows, today: date) -> float:
    """The longest recorded ride of the last ``RIDE_LENGTH_WEEKS``, in minutes.

    0 when there is none: with no training on record either, the ride-length
    prior judges a first week by it (ai-trainer-ops#128), unlike a length
    nobody passed.
    """
    first = today - timedelta(weeks=RIDE_LENGTH_WEEKS)
    minutes = [
        seconds / 60
        for row in rows or []
        if training_sport(getattr(row, "sport_type", None)) == SPORT_CYCLING
        and (when := _as_date(getattr(row, "activity_date", None))) is not None
        and first <= when < today
        and (seconds := getattr(row, "duration_seconds", None) or 0) > 0
    ]
    return max(minutes, default=0.0)


def _ride_length(
    plan, today, longest, recent_weekly_hours=None
) -> list[PriorDeparture]:
    if longest is None:
        return []
    if longest > 0:
        limit, last = (
            max(RIDE_LENGTH_RATIO_MAX * longest, RIDE_LENGTH_FLOOR_MINUTES),
            None,
        )
    elif recent_weekly_hours is not None and not any(recent_weekly_hours):
        # No training on record at all: the first week stays inside the floor,
        # as the cold-start note promises a moderate first week
        # (ai-trainer-ops#128). As ``season_weeks`` counts a cold start.
        limit, last = RIDE_LENGTH_FLOOR_MINUTES, today + timedelta(days=WEEK_DAYS - 1)
    else:
        # Runs or the gym but no ride say nothing about how long a ride may be
        # (review of #932).
        return []
    long_rides = []
    long_dates = []
    for day in plan or []:
        # Rest by type, not by the scalar: a window may carry only its bounds.
        if not isinstance(day, dict) or _workout_type(day) in ("", "rest", "off"):
            continue
        when = _as_date(day.get("date"))
        # The upper bound of a window: the athlete may take the plan at its word.
        _lo, hi = duration_range(day)
        if (
            when
            and today <= when <= (last or when)
            and day_sport(day) == SPORT_CYCLING
            and (hi or 0) > limit
        ):
            long_rides.append(f"{when.isoformat()} ({_hours(hi)})")
            long_dates.append(when)
    if not long_rides:
        return []
    against = (
        f"against {_hours(longest)} for the longest ride of the last "
        f"{RIDE_LENGTH_WEEKS} weeks."
        if longest
        else f"in the first week with no ride on record in the last {RIDE_LENGTH_WEEKS} "
        f"weeks; a first week usually keeps rides to {_hours(RIDE_LENGTH_FLOOR_MINUTES)}."
    )
    return [
        PriorDeparture(
            PRIOR_RIDE_LENGTH,
            EVIDENCE_HEURISTIC,
            f"Rides of {', '.join(long_rides)} {against}",
            tuple(long_dates),
        )
    ]


def _long_before_race(
    plan, today, races, race, offsets, limit
) -> list[tuple[date, float]]:
    """The days ``offsets`` before ``race`` with a session over ``limit`` minutes,
    with its minutes."""
    window = {race - timedelta(days=offset) for offset in offsets}
    found = []
    for day in plan or []:
        if not isinstance(day, dict) or _workout_type(day) in (
            "",
            "rest",
            "off",
            "race",
        ):
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
    return ", ".join(
        f"{day.isoformat()} ({_hours(minutes)})" for day, minutes in sessions
    )


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
        if athlete_states.is_ill(state.kind):
            end = athlete_states.effective_end(state)
            back = athlete_states.return_days(
                state.kind, state.starts_on, state.ends_on
            )
            easy_until = end + timedelta(days=back)
            if state.kind == athlete_states.KIND_FEVER and (
                state.ends_on is None or state.ends_on >= today
            ):
                lines.append(
                    f"  - Ill with fever or symptoms below the neck since "
                    f"{state.starts_on.isoformat()}. Prior: no training at all while "
                    f"ill (rest days only), then easy for at least as many days as "
                    f"the illness lasted ({back} or more)."
                )
            elif state.ends_on is not None and state.ends_on < today:
                lines.append(
                    f"  - Was ill until {state.ends_on.isoformat()}. Prior: easy "
                    f"sessions until {easy_until.isoformat()}, then a graded return."
                )
            elif state.ends_on is None:
                lines.append(
                    f"  - Ill since {state.starts_on.isoformat()}, no end stated. "
                    f"Prior: rest or easy only while ill, easy for "
                    f"{back} days after. Ask how they feel "
                    f"before planning hard work."
                )
            else:
                lines.append(
                    f"  - Ill until {state.ends_on.isoformat()}. Prior: rest or easy "
                    f"only, then easy until {easy_until.isoformat()}."
                )
        elif state.kind == athlete_states.KIND_CLEARANCE:
            if state.ends_on is None or state.ends_on >= today:
                lines.append(
                    f"  - Named a warning sign (chest pain, fainting, a racing heart, "
                    f"breathlessness or dizziness) on {state.starts_on.isoformat()}; "
                    "no doctor has checked it yet. Prior: no training at all (rest "
                    "days only) until the athlete says a doctor has cleared them."
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
    covered = _covered_by_states(states, today)
    return [
        *_stated_hours(plan, today, weekly_hours, done_minutes_by_date),
        *_training_days(plan, today, training_days),
        *_taper(plan, totals, today, race_dates, covered),
        *_race_eve(plan, today, race_dates),
        *_race_week(plan, today, race_dates),
        *_ramp(totals, today, recent_weekly_hours),
        *_states(plan, today, states),
        *_back_to_back(plan, today, race_dates, covered),
        *_ride_length(plan, today, longest_ride, recent_weekly_hours),
    ]


def plan_priors_section(departures: list[PriorDeparture]) -> str:
    """The departures as a prompt block: the race and state ones as protective
    changes to make, the rest with the freedom to keep them.

    The race and state priors are the ones a reply is sent back for when it
    leaves them in the coming week (``STANDING_PRIORS``). Telling the writer it
    may keep them, and then asking for a rewrite when it did, cost a second
    call of 15-19k input tokens (review of 2026-11-01, ai-trainer-ops#338 C6).
    """
    if not departures:
        return ""
    protective = [d for d in departures if d.prior in STANDING_PRIORS]
    judged = [d for d in departures if d.prior not in STANDING_PRIORS]
    lines: list[str] = []
    if protective:
        lines.append(
            "Protective planning priors (computed from the plan; the app holds "
            "every plan to them). The current plan departs from them here:"
        )
        lines.extend(f"  - [{d.evidence}] {d.statement}" for d in protective)
        lines.append(
            "Bring the plan back to these. Keep such a session only for a reason "
            "the athlete gave you, and then say that reason in one sentence."
        )
    if judged:
        lines.append(
            "Planning priors (computed from the plan; the priors are defaults with "
            "their evidence, not rules). The current plan departs from them here:"
        )
        lines.extend(f"  - [{d.evidence}] {d.statement}" for d in judged)
        lines.append(
            "Use your judgement. If what you know about this athlete justifies a "
            "departure, keep it and tell the athlete why in one sentence. Otherwise "
            "bring the plan back towards the prior. Weigh athlete-stated priors, "
            "strong evidence and expert consensus above a heuristic."
        )
    return "\n".join(lines)


# The priors a write is held to rather than only told about (ai-trainer-ops#87,
# the owner's yes to #81 Q26). A finding costs one rewrite with the finding in
# the prompt, and what survives the rewrite reaches the athlete with a note. The
# stated days are not among them: a session an automatic writer puts on another
# day is turned into rest after the write (``rest_days``). The stated hours and
# the states bind a chat reply (REPLY_PRIORS) and the writers nobody watches
# (UNATTENDED_PRIORS) as well.
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
STATE_PRIORS = frozenset(
    {
        PRIOR_ILLNESS,
        PRIOR_FEVER,
        PRIOR_CLEARANCE,
        PRIOR_TIREDNESS,
        PRIOR_REDUCED_DAY,
    }
)
# What a chat reply is held to on the days it changes (Q37 (c)): the hard priors,
# the stated hours and the states. A reply that leaves a week it changes over the
# athlete's hours, or puts intensity into an illness, gets one rewrite like any
# hard departure (scenario runs 1593, 1594).
REPLY_PRIORS = HARD_PRIORS | STATE_PRIORS | {PRIOR_STATED_HOURS}

# What a writer nobody watches is held to (ai-trainer-ops#112): the hard
# priors, the athlete's states and the stated hours. The nightly job, an
# automatic adaptation, a ride review and a new plan heard no reason for
# putting intensity into an illness, so there is no departure for them to keep.
# Held to the hard priors only, the nightly job put a missed VO2 session two
# days into a stated illness, and the day carried no note. With the hours as
# advice only, the rebuild after a beginner accepted three hours a week planned
# 3.75 to 5.25 h into the next week, in 20 runs of 20 (#321). The owner chose one
# rewrite with the week's sum, as a chat reply gets, over a cut by code
# (2026-10-30).
UNATTENDED_PRIORS = HARD_PRIORS | STATE_PRIORS | {PRIOR_STATED_HOURS}

# What a day's note names: whatever any writer is held to and left in after its
# rewrite. A week over the stated hours is noted on its sessions (#116).
NOTED_PRIORS = UNATTENDED_PRIORS


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
STANDING_PRIORS = (
    frozenset({PRIOR_TAPER_VOLUME, PRIOR_RACE_EVE, PRIOR_RACE_WEEK}) | STATE_PRIORS
)


def standing_departures(
    departures: list[PriorDeparture], today: date
) -> list[PriorDeparture]:
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
        day = (
            min(departure.dates)
            if departure.prior in STATE_PRIORS
            else max(departure.dates)
        )
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
