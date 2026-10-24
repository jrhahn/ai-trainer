"""The weeks ahead: targets the daily plan fits into (ai-trainer-ops#98).

#64 tells every plan writer the phase, the load rhythm and a focus. It works
them out for "this fortnight", afresh on every run. So nothing said what the
weeks after the plan window are for, the athlete saw no direction, and nothing
checked the days against the week they fall in. The owner on 2026-10-14:
planning every day for months makes no sense. A coarse plan by week, with a
clear goal and focus, is the direction, and the days must fit it.

This module lays out the current calendar week and the ones after it, with the
rules #64 already has:

- **Phase and focus.** ``training_phase`` at the week's Monday. The current
  week uses today instead, so a taper starts on the day the prompt says it does.
  The focus is the maintenance rotation or the emphasis of the phase.
- **Load or recovery.** The 3-up, 1-down rhythm, continued from the calendar
  weeks actually ridden. A transition or a taper starts a new cycle.
- **Hours.** A band around the stated weekly hours, or around the recent
  average when none is stated. From what was ridden lately it grows by at most
  ``PROGRESSION_PER_WEEK`` a week, so a target never asks for the jump the ramp
  prior reports.
- **Hard sessions** as a minimum and a maximum, and the key session in words.

The model writes one goal sentence per week and nothing else. The goal is
stored with :func:`fingerprint` and rewritten only when the week's phase, focus,
rhythm or next race changes. A new race therefore rewrites the goals of the
weeks it moves, and no others.

Where the plan departs from the targets is reported to the writer like the
planning priors (#26). Nothing is enforced.

Pure: rows in, weeks out. ``plan_context`` reads the database.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from schemas import day_sport
from services import plan_priors
from services.activity_identity import SPORT_CYCLING
from services.duration_range import duration_range
from services.prompts import PLAN_HORIZON_DAYS
from services.training_phase import (
    BASE,
    BUILD,
    LOADING_WEEKS_BEFORE_RECOVERY,
    MAINTENANCE,
    PEAK,
    PEAK_DAYS,
    TAPER,
    TRANSITION,
    WEEK_DAYS,
    TrainingPhase,
    _as_date,
    load_rhythm,
    training_phase,
)

SEASON_WEEKS = 12

# The share of the reference hours a week holds. Recovery is #64's "about 60 %".
LOAD_BAND = (0.8, 1.0)
RECOVERY_BAND = (0.5, 0.65)
# The first week of an athlete with no training on record (ai-trainer-ops#128).
# It aimed at 80-100 % of the stated hours with two hard sessions and a long ride
# of a quarter of the week, while the phase prompt and the dashboard promise a
# moderate first week: inside the hours, at most one harder session. Heuristic.
COLD_START_BAND = (0.6, 0.8)
COLD_START_MAX_HARD_SESSIONS = 1
COLD_START_KEY_SESSION = (
    "one easy long ride of about 90 minutes; the first rides measure the athlete"
)
PHASE_BANDS = {PEAK: (0.7, 0.9), TAPER: (0.4, 0.6), TRANSITION: (0.0, 0.5)}
# Heuristic: from the recent average, a target grows by at most a tenth of it a
# week. The current week then stays under the ramp prior's +30 %.
PROGRESSION_PER_WEEK = 0.1
# Without hours the athlete stated (ai-trainer-ops#116), the target is an
# estimate from what is ridden, not a cap: it may reach the ramp prior's +30 %
# above the recent average, so an athlete with more time is invited to use it,
# and it moves with the rides from there.
UNSTATED_HEADROOM = plan_priors.RAMP_RATIO_MAX - 1

HARD_SESSIONS = {
    TRANSITION: (0, 0),
    BASE: (0, 1),
    BUILD: (2, 2),
    PEAK: (1, 2),
    # The taper prior already reports a taper that drops intensity.
    TAPER: (0, 2),
    MAINTENANCE: (1, 2),
}
RECOVERY_HARD_SESSIONS = (0, 1)

KEY_SESSIONS = {
    TRANSITION: "none; easy riding or another sport, if the athlete wants to",
    BASE: "one long endurance ride",
    BUILD: "two quality sessions and one long ride",
    PEAK: "one session at the event's duration and terrain",
    TAPER: "short race-pace openers",
    MAINTENANCE: "one session for the block's focus and one long ride",
}
RECOVERY_KEY_SESSION = "none; easy riding and at most one short quality session"
# The weeks whose key session holds a long ride, and how long it has to be: a
# quarter of the week's lower bound, never under 90 minutes. Heuristic.
LONG_RIDE_PHASES = frozenset({BASE, BUILD, MAINTENANCE})
LONG_RIDE_SHARE = 0.25
LONG_RIDE_FLOOR_MINUTES = 90

PHASE_FOCUS = {
    TRANSITION: "rest and unstructured riding",
    BASE: "aerobic endurance",
    PEAK: "race-specific efforts",
    TAPER: "freshness for the race",
}
# #64's build guidance: threshold and sweet spot, "later VO2max". Later is the
# last four build weeks.
LATE_BUILD_DAYS = PEAK_DAYS + 4 * WEEK_DAYS
EARLY_BUILD_FOCUS = "threshold and sweet spot"
LATE_BUILD_FOCUS = "VO2max and race intensity"

# What a stored goal may hold, for the prompt and the page alike.
GOAL_MAX_CHARS = 300


@dataclass(frozen=True, slots=True)
class SeasonWeek:
    start: date
    phase: str
    focus: str
    recovery: bool
    # 1 to 3 in a load week; None in a recovery, transition or taper week.
    load_week: int | None
    hours: tuple[float, float] | None
    hard_sessions: tuple[int, int]
    key_session: str
    next_race: date | None
    # The first week of an athlete with no training on record.
    cold_start: bool = False


@dataclass(frozen=True, slots=True)
class WeekDeparture:
    week: date
    statement: str


def _focus(phase: TrainingPhase, reference: date) -> str:
    if phase.phase == MAINTENANCE:
        return phase.focus or ""
    if phase.phase == BUILD:
        days = (phase.next_race - reference).days
        return EARLY_BUILD_FOCUS if days > LATE_BUILD_DAYS else LATE_BUILD_FOCUS
    return PHASE_FOCUS[phase.phase]


def _half_hours(hours: float) -> float:
    return round(hours * 2) / 2


def season_weeks(
    today: date,
    race_dates: Iterable[object],
    rows: Iterable[object],
    *,
    weekly_hours: float | None = None,
    latitude: float | None = None,
    count: int = SEASON_WEEKS,
) -> list[SeasonWeek]:
    rows = list(rows or [])
    races = list(race_dates or [])
    this_monday = today - timedelta(days=today.weekday())
    # Calendar weeks, the last one ending on Sunday, so a week's rhythm does not
    # change on Thursday.
    rhythm = load_rhythm(rows, this_monday)
    # As the phase prompt's cold-start note counts it.
    no_history = not any(rhythm.weekly_hours)
    recent = sum(rhythm.weekly_hours[-4:]) / 4
    ridden = recent >= plan_priors.RAMP_MIN_BASELINE_HOURS
    stated = weekly_hours if weekly_hours and weekly_hours > 0 else None
    reference = stated or (recent if ridden else None)
    done = rhythm.loading_weeks or 0

    weeks = []
    for index in range(count):
        monday = this_monday + timedelta(weeks=index)
        # Later weeks have no rides yet, and an empty history reads as "the
        # autumn break is taken", which is the projection: base after it.
        day = today if index == 0 else monday
        phase = training_phase(day, races, rows if index == 0 else (), latitude=latitude)
        if phase.phase in (TRANSITION, TAPER):
            recovery, load_week, done = False, None, 0
        elif done >= LOADING_WEEKS_BEFORE_RECOVERY:
            recovery, load_week, done = True, None, 0
        else:
            done += 1
            recovery, load_week = False, done
        cold_start = no_history and index == 0
        hours = None
        if reference is not None:
            growth = PROGRESSION_PER_WEEK * (index + 1)
            if stated is None:
                ceiling = recent * (1 + min(growth, UNSTATED_HEADROOM))
            elif ridden:
                ceiling = min(stated, recent * (1 + growth))
            else:
                ceiling = stated
            low, high = RECOVERY_BAND if recovery else PHASE_BANDS.get(phase.phase, LOAD_BAND)
            if cold_start:
                low, high = min(low, COLD_START_BAND[0]), min(high, COLD_START_BAND[1])
            hours = (_half_hours(low * ceiling), _half_hours(high * ceiling))
        hard_sessions = RECOVERY_HARD_SESSIONS if recovery else HARD_SESSIONS[phase.phase]
        key_session = RECOVERY_KEY_SESSION if recovery else KEY_SESSIONS[phase.phase]
        if cold_start:
            hard_sessions = (0, min(hard_sessions[1], COLD_START_MAX_HARD_SESSIONS))
            if phase.phase in LONG_RIDE_PHASES or phase.phase == PEAK:
                key_session = COLD_START_KEY_SESSION
        weeks.append(
            SeasonWeek(
                start=monday,
                phase=phase.phase,
                focus=_focus(phase, day),
                recovery=recovery,
                load_week=load_week,
                hours=hours,
                hard_sessions=hard_sessions,
                key_session=key_session,
                next_race=phase.next_race,
                cold_start=cold_start,
            )
        )
    return weeks


def fingerprint(week: SeasonWeek) -> str:
    """What a week's goal was written for. Hours are left out: they follow the
    rides, and a goal that names no number does not go stale with them."""
    race = week.next_race.isoformat() if week.next_race else "-"
    return f"{week.phase}|{week.focus}|{'recovery' if week.recovery else 'load'}|{race}"


def current_goals(weeks: list[SeasonWeek], stored: dict) -> dict[date, str]:
    """The stored goals that still fit their week.

    ``stored`` maps the ISO Monday to a row with ``fingerprint`` and ``goal``.
    """
    return {
        week.start: row.goal
        for week in weeks
        if (row := stored.get(week.start.isoformat())) is not None
        and row.fingerprint == fingerprint(week)
    }


def week_label(week: SeasonWeek) -> str:
    if week.recovery:
        return f"{week.phase}, recovery week"
    if week.load_week is not None:
        return f"{week.phase}, load week {week.load_week} of {LOADING_WEEKS_BEFORE_RECOVERY}"
    return week.phase


def _hard_label(low: int, high: int) -> str:
    if high == 0:
        return "no hard sessions"
    if low == high:
        return f"{high} hard sessions" if high > 1 else "1 hard session"
    if low == 0:
        return f"at most {high} hard session{'s' if high > 1 else ''}"
    return f"{low}-{high} hard sessions"


def week_summary(week: SeasonWeek) -> str:
    """The week's targets in one line, for the writer and the goal writer.

    A week the plan holds moderate says so before the block's focus: Lena's
    cold-start week read only "Focus: VO2max and race intensity", and its goal
    promised the groundwork for both (ai-trainer-ops#238).
    """
    focus = week.focus
    if week.cold_start:
        focus = (
            "a moderate first week, the first rides measure the athlete; "
            f"the block's focus ({week.focus}) comes after it"
        )
    elif week.recovery:
        focus = f"recovery, to absorb the load; the block's focus ({week.focus}) goes on after it"
    parts = [f"{week_label(week)}.", f"Focus: {focus}."]
    if week.hours is not None:
        parts.append(f"{week.hours[0]:g}-{week.hours[1]:g} h.")
    parts.append(f"{_hard_label(*week.hard_sessions).capitalize()}.")
    parts.append(f"Key session: {week.key_session}.")
    return " ".join(parts)


def _workout_type(day: dict) -> str:
    return str(day.get("workoutType") or day.get("workout_type") or "").strip().lower()


def find_week_departures(
    plan: list[dict] | None, weeks: list[SeasonWeek], today: date
) -> list[WeekDeparture]:
    """Where ``plan`` leaves the targets of a week it covers from today to Sunday.

    A full regeneration keeps only the past days the athlete trained, so the
    current week often starts at today. What the plan holds is then a floor:
    too many hours or hard sessions is already a departure. Too few, or no long
    ride, is judged only on a week the plan holds from Monday to Sunday.

    A race is the event, not the training around it, so it counts toward
    neither the hours nor the hard sessions.
    """
    by_date: dict[date, list[dict]] = {}
    for day in plan or []:
        if isinstance(day, dict) and (when := _as_date(day.get("date"))) is not None:
            by_date.setdefault(when, []).append(day)
    found = []
    for week in weeks:
        days = [week.start + timedelta(days=offset) for offset in range(WEEK_DAYS)]
        if days[-1] < today or not all(day in by_date for day in days if day >= today):
            continue
        whole = all(day in by_date for day in days)
        sessions = [
            s for day in days for s in by_date.get(day, []) if _workout_type(s) != "race"
        ]
        start = week.start.isoformat()
        label = week_label(week)
        minutes = sum(plan_priors.planned_minutes(s) for s in sessions)
        if (
            week.hours is not None
            and minutes > week.hours[1] * 60 + plan_priors.STATED_HOURS_SLACK_MINUTES
        ):
            found.append(
                WeekDeparture(
                    week.start,
                    f"The week from {start} plans {minutes / 60:.1f} h; its target is "
                    f"{week.hours[0]:g}-{week.hours[1]:g} h ({label}).",
                )
            )
        hard = sum(_workout_type(s) in plan_priors.INTENSITY_TYPES for s in sessions)
        low, high = week.hard_sessions
        if hard > high or (whole and hard < low):
            found.append(
                WeekDeparture(
                    week.start,
                    f"The week from {start} plans {hard} hard session(s); this week "
                    f"({label}) asks for {_hard_label(low, high)}.",
                )
            )
        if whole and not week.recovery and week.phase in LONG_RIDE_PHASES:
            floor = LONG_RIDE_FLOOR_MINUTES
            # A cold start's long ride is the floor, which is also the most the
            # ride-length prior allows that week (ai-trainer-ops#128).
            if week.hours is not None and not week.cold_start:
                floor = max(floor, LONG_RIDE_SHARE * week.hours[0] * 60)
            longest = max(
                (
                    duration_range(s)[1] or 0
                    for s in sessions
                    if _workout_type(s) not in ("", "rest", "off", "strength")
                    and day_sport(s) == SPORT_CYCLING
                ),
                default=0,
            )
            if longest < floor:
                found.append(
                    WeekDeparture(
                        week.start,
                        f"The week from {start} has no ride of {floor / 60:.1f} h or "
                        f"longer; its key session is {week.key_session}.",
                    )
                )
    return found


def season_section(
    weeks: list[SeasonWeek],
    goals: dict[date, str],
    departures: list[WeekDeparture],
    today: date,
) -> str:
    """The weeks as a prompt block: the plan window in full, the rest in a line."""
    if not weeks:
        return ""
    last_planned = today + timedelta(days=PLAN_HORIZON_DAYS - 1)
    lines = [
        "Season plan (week targets computed from the race calendar, the stated "
        "weekly hours and the calendar weeks ridden). The days you write must fit "
        "the week they fall in. The athlete's state and the planning priors come "
        "first; these weeks are the direction:"
    ]
    later = []
    for week in weeks:
        if week.start > last_planned:
            later.append(f"{week.start.isoformat()} {week_label(week)}")
            continue
        when = "This week" if week.start <= today else "Week"
        line = f"- {when} from {week.start.isoformat()}: {week_summary(week)}"
        if goal := goals.get(week.start):
            line += f' Goal: "{goal}"'
        lines.append(line)
    if later:
        lines.append(f"- Then: {'; '.join(later)}.")
    if departures:
        lines.append("The plan departs from the week targets here:")
        lines.extend(f"  - {d.statement}" for d in departures)
        lines.append(
            "Bring those days inside their week's targets, or keep the departure "
            "and tell the athlete why in one sentence."
        )
    return "\n".join(lines)
