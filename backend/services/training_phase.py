"""Where the athlete is in their season, computed rather than left to the model (ai-trainer-ops#64).

The plan is a rolling 14-day window. Every regeneration starts from scratch and
has no record of which week of a block it is in, so two runs a week apart write
the same kind of fortnight. Without a race the prompt asked for "a balanced
general-fitness plan", and that is what every such athlete got, every fortnight,
all year. Nothing told the writer that October after the season is when most
riders take a few days completely off, or that load goes up for three weeks and
then comes down for one.

This module states those facts from data the app already has:

- **Phase.** From the next race (race calendar or profile race date), the most
  recent race, and the calendar month. The month is read for the athlete's
  hemisphere when their home location is known.
- **Load rhythm.** From actual weekly volume, not from what was planned: how many
  weeks have gone by since the last lighter one, and whether the coming week
  should be the lighter one. A week counts as lighter when its volume is at most
  ``LIGHT_WEEK_RATIO`` of the three weeks before it.
- **Focus.** Without a target event, the emphasis rotates every
  ``FOCUS_BLOCK_DAYS``, so consecutive blocks differ instead of repeating
  "balanced".

Volume is moving time, not TSS. Every activity has a duration; many have no
price (see ``training_monotony._load``), and a recovery week that only counts
priced rides would find recovery weeks in the gaps of unpriced ones.

The module is pure: rows in, a statement out. The athlete stays in charge. A
transition break is proposed, never imposed, and the section says so.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

TRANSITION = "transition"
BASE = "base"
BUILD = "build"
PEAK = "peak"
TAPER = "taper"
MAINTENANCE = "maintenance"

# Days before a race at which each phase begins. Taper is the last ten days; the
# six weeks before that are race-specific; the fourteen weeks before the race are
# build. These are the amateur road table in ``knowledge/periodization.md``.
TAPER_DAYS = 10
PEAK_DAYS = 6 * 7
BUILD_DAYS = 14 * 7
# A race further out than this does not shape this fortnight. The season decides.
RACE_HORIZON_DAYS = 40 * 7

# After a race with nothing close behind it, a short transition.
POST_RACE_TRANSITION_DAYS = 10
# Unless the next race is this close, in which case recovery is part of the build.
POST_RACE_NEXT_RACE_GAP_DAYS = 6 * 7

# Months counted for the northern hemisphere and shifted by six for the southern.
AUTUMN_TRANSITION_MONTHS = frozenset({10, 11})
WINTER_BASE_MONTHS = frozenset({12, 1, 2})
# How far back an autumn break is looked for. A run of empty days inside this
# window counts as the break already taken. Relative rather than a calendar date
# so the southern hemisphere needs no second table.
AUTUMN_BREAK_LOOKBACK_DAYS = 60
AUTUMN_BREAK_MIN_DAYS = 3

# Load rhythm.
WEEK_DAYS = 7
HISTORY_WEEKS = 8
LIGHT_WEEK_RATIO = 0.75
LOADING_WEEKS_BEFORE_RECOVERY = 3

# Focus rotation when there is no target event.
FOCUS_BLOCK_DAYS = 28
MAINTENANCE_FOCI = (
    "aerobic endurance and long, steady climbs",
    "threshold and sweet spot",
    "VO2max and short, hard efforts",
)

_PHASE_GUIDANCE = {
    TRANSITION: (
        "Offer a few days completely off (3-7), then two to three weeks of "
        "unstructured, easy riding or other sport. No intervals. This is a "
        "proposal: if the athlete says they do not want a break, or the coach "
        "memory says they declined one, plan base instead."
    ),
    BASE: (
        "Aerobic volume. Mostly zone 2, longer weekend rides that grow step by "
        "step, strength work, at most one moderate tempo or sweet-spot session a "
        "week. No VO2max blocks."
    ),
    BUILD: (
        "Volume holds, intensity rises: two quality sessions a week (threshold, "
        "sweet spot, later VO2max), shaped toward what the target event demands."
    ),
    PEAK: (
        "Race-specific work: efforts at the duration and terrain of the event, "
        "slightly less volume, full recovery between the key sessions."
    ),
    TAPER: (
        "Cut volume by about 40%, keep short race-pace openers, arrive fresh."
    ),
    MAINTENANCE: (
        "No target event. Hold fitness and keep it interesting; vary the "
        "emphasis by block rather than writing the same balanced fortnight."
    ),
}


@dataclass(frozen=True)
class LoadRhythm:
    """Weekly volume, most recent last, and where the athlete is in the cycle."""

    weekly_hours: tuple[float, ...]
    loading_weeks: int | None
    recovery_due: bool


@dataclass(frozen=True)
class TrainingPhase:
    phase: str
    reason: str
    next_race: date | None
    rhythm: LoadRhythm
    focus: str | None = None


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _season_month(today: date, latitude: float | None) -> int:
    """The month as the northern hemisphere would call it."""
    if latitude is not None and latitude < 0:
        return (today.month + 5) % 12 + 1
    return today.month


def _activity_days(rows: Iterable[object]) -> dict[date, float]:
    """Hours of moving time per day."""
    hours: dict[date, float] = {}
    for row in rows or []:
        day = _as_date(getattr(row, "activity_date", None))
        seconds = getattr(row, "duration_seconds", None)
        if day is None:
            continue
        hours[day] = hours.get(day, 0.0) + (seconds or 0) / 3600
    return hours


def load_rhythm(rows: Iterable[object], today: date) -> LoadRhythm:
    """Weeks of loading since the last lighter week.

    Windows are rolling seven days ending yesterday. Today is incomplete, and an
    incomplete day would make every current week look light. ``loading_weeks`` is
    ``None`` when there is too little history to tell.
    """
    hours = _activity_days(rows)
    end = today - timedelta(days=1)
    weekly = []
    for back in range(HISTORY_WEEKS - 1, -1, -1):
        last = end - timedelta(days=back * WEEK_DAYS)
        weekly.append(
            round(
                sum(hours.get(last - timedelta(days=d), 0.0) for d in range(WEEK_DAYS)),
                1,
            )
        )
    if not hours or min(hours) > end - timedelta(days=4 * WEEK_DAYS - 1):
        return LoadRhythm(tuple(weekly), None, False)

    loading = 0
    for index in range(len(weekly) - 1, 2, -1):
        reference = sum(weekly[index - 3 : index]) / 3
        # A week with no training is the lightest week there is, whatever came
        # before it. Without this, an athlete coming back from three empty weeks
        # would have those weeks counted as loading.
        if weekly[index] == 0 or weekly[index] <= LIGHT_WEEK_RATIO * reference:
            break
        loading += 1
    return LoadRhythm(
        tuple(weekly), loading, loading >= LOADING_WEEKS_BEFORE_RECOVERY
    )


def _autumn_break_taken(rows: Iterable[object], today: date) -> bool:
    """Whether a run of empty days has already happened this autumn."""
    active = set(_activity_days(rows))
    if not active:
        # Nothing synced says nothing about a break, and proposing one to an
        # athlete the app has never seen ride would be a guess.
        return True
    # Days before the first synced ride are missing history, not rest.
    start = max(today - timedelta(days=AUTUMN_BREAK_LOOKBACK_DAYS), min(active))
    run = 0
    current = start
    while current < today:
        run = 0 if current in active else run + 1
        if run >= AUTUMN_BREAK_MIN_DAYS:
            return True
        current += timedelta(days=1)
    return False


def _focus(today: date) -> str:
    return MAINTENANCE_FOCI[(today.toordinal() // FOCUS_BLOCK_DAYS) % len(MAINTENANCE_FOCI)]


def training_phase(
    today: date,
    race_dates: Iterable[object],
    rows: Iterable[object],
    *,
    latitude: float | None = None,
) -> TrainingPhase:
    rows = list(rows or [])
    races = sorted({d for d in (_as_date(r) for r in race_dates or []) if d})
    upcoming = [d for d in races if d >= today]
    past = [d for d in races if d < today]
    next_race = upcoming[0] if upcoming else None
    rhythm = load_rhythm(rows, today)

    if past and (today - past[-1]).days <= POST_RACE_TRANSITION_DAYS and (
        next_race is None or (next_race - today).days > POST_RACE_NEXT_RACE_GAP_DAYS
    ):
        days = (today - past[-1]).days
        return TrainingPhase(
            TRANSITION, f"the last race was {days} day(s) ago", next_race, rhythm
        )

    if next_race is not None and (next_race - today).days <= RACE_HORIZON_DAYS:
        days = (next_race - today).days
        weeks = days // 7
        away = f"the next race is {next_race.isoformat()}, {days} day(s) / {weeks} week(s) away"
        if days <= TAPER_DAYS:
            return TrainingPhase(TAPER, away, next_race, rhythm)
        if days <= PEAK_DAYS:
            return TrainingPhase(PEAK, away, next_race, rhythm)
        if days <= BUILD_DAYS:
            return TrainingPhase(BUILD, away, next_race, rhythm)
        return TrainingPhase(BASE, away, next_race, rhythm)

    month = _season_month(today, latitude)
    if month in AUTUMN_TRANSITION_MONTHS:
        if not _autumn_break_taken(rows, today):
            return TrainingPhase(
                TRANSITION,
                "no race ahead, the season is over and no break has been taken yet this autumn",
                next_race,
                rhythm,
            )
        return TrainingPhase(
            BASE,
            "no race ahead and the autumn break is done, so base building starts",
            next_race,
            rhythm,
        )
    if month in WINTER_BASE_MONTHS:
        return TrainingPhase(
            BASE, "no race ahead and it is winter, the base-building season", next_race, rhythm
        )
    return TrainingPhase(
        MAINTENANCE, "no race in the calendar", next_race, rhythm, focus=_focus(today)
    )


def training_phase_section(phase: TrainingPhase) -> str:
    """The phase as a prompt block every plan writer plans inside."""
    lines = [
        "Training phase (computed from the race calendar, the date and the last "
        "8 weeks of actual training; plan inside it rather than writing a generic fortnight):",
        f"- Phase: {phase.phase}, because {phase.reason}.",
        f"- What this phase asks for: {_PHASE_GUIDANCE[phase.phase]}",
    ]
    if phase.focus:
        lines.append(
            f"- Focus of the current 4-week block: {phase.focus}. The next block "
            "rotates to a different emphasis."
        )
    rhythm = phase.rhythm
    if phase.phase not in (TRANSITION, TAPER) and rhythm.loading_weeks is not None:
        recent = ", ".join(f"{h:g}" for h in rhythm.weekly_hours[-4:])
        if rhythm.weekly_hours[-1] == 0:
            # A week off (injury, illness, travel) is not the first week of a block.
            # "Load may rise" from zero would invite going straight back to the
            # old volume.
            lines.append(
                f"- Load rhythm: no training in the last 7 days (hours in the last 4 "
                f"weeks, oldest first: {recent}). Restart gently: short, easy "
                "sessions first, and return to the earlier volume over one to two "
                "weeks rather than at once."
            )
        elif rhythm.recovery_due:
            lines.append(
                f"- Load rhythm: {rhythm.loading_weeks} loading weeks since the last "
                f"lighter one (hours in the last 4 weeks, oldest first: {recent}). "
                "Make the coming 7 days a recovery week: about 60% of the recent "
                "volume, one short quality session at most, then build again."
            )
        else:
            lines.append(
                f"- Load rhythm: week {rhythm.loading_weeks + 1} of a 3-up, 1-down "
                f"cycle (hours in the last 4 weeks, oldest first: {recent}). "
                "Load may rise modestly; the lighter week comes after the third."
            )
    return "\n".join(lines)
