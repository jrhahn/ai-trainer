"""Running-specific exposure, and the ceiling it puts on volume (#717).

Cycling has no eccentric-loading ceiling. A cyclist can add an hour a week for
months and the limit they meet is their time, their motivation or their aerobic
capacity — never their tissue. Running's limit arrives first and arrives from a
different direction: every stride is an eccentric contraction absorbed by bone,
tendon and muscle that adapt on a slower clock than the aerobic system does.

That makes a fit cyclist the *most* exposed athlete this app has, not the least.
A CTL of 90 says their heart, blood and mitochondria can sustain a great deal of
running; it says nothing whatever about whether their tibia can. Without this
module the planner reads CTL 90, concludes the athlete is well trained, and
prescribes a running week that a well-trained runner would handle — to someone
whose legs have never run. The aerobic system would cope. That is the problem.

So the ceiling here is **a function of run history and of nothing else**. Not of
CTL, not of FTP, not of how much the athlete rides. That is enforced
structurally rather than by discipline: :func:`run_volume_ceiling` takes a
:class:`RunExposure` and there is no parameter it could read a fitness figure
from.

**On the "10 % rule".** The familiar advice — never add more than 10 % a week —
is not well evidenced. The trials that tested it (Buist et al. 2008; Nielsen et
al. 2014) did not find it protective, and it has an arithmetic problem besides:
10 % of 30 min/week is three minutes, which is not a progression, while 10 % of
600 min/week is an hour of additional impact, which is not a small step. A rule
whose meaning changes by two orders of magnitude across its own domain is a
round number, not a finding.

What is encoded instead is **exposure and its rate of change**: what the athlete
has actually run over the last four weeks, and how far above that a planned week
sits. The comparison is a planned 7-day load against the four-week mean — the
acute:chronic shape (Gabbett 2016), which at least compares like with like, and
which is also contested (Impellizzeri et al. 2020 on its statistical
foundations). The ratio below is therefore stated as a **convention with its
uncertainty attached**, and every statement this module produces says so to the
athlete. A ceiling presented as physiology would be a worse lie than the 10 %
rule, because it would come with a number and a citation.

Three consequences of taking exposure seriously:

**Rolling 7-day windows, never calendar weeks.** A build-up placed Saturday,
Sunday, Monday is one week's loading to the tissue and two half-weeks to a
calendar. Checking Monday-to-Sunday totals would wave through the only
arrangement that actually needs catching.

**Minutes, not kilometres.** Distance is what a runner calls their weekly
volume, and it is reported wherever it is known — but it is known only for runs
that produced a usable stream, and time on feet is available for every run and
is the quantity the impact count is proportional to. Governing on distance would
mean a ceiling that silently stops applying to treadmill sessions.

**A lapse needs no rule.** The four-week window counts the empty weeks as the
zeros they are, so an athlete returning from three weeks off finds their chronic
figure already down to a quarter of what it was. Adding a decay constant on top
would be modelling the same fact twice.

Nothing here decides what the athlete should run, and nothing here shortens a
session. The ceiling is stated to the planner before it writes, and stated to
the coach so the athlete hears the reasoning — "your aerobic fitness supports
this, your running-specific exposure does not yet" — rather than finding their
long run quietly trimmed. The one correction the gate makes is to hand back a
run session an automated write had lengthened or added, and only to content the
plan already held (see ``plan_pipeline._revert_new_run_overload``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

from schemas import day_slot, day_sport
from services.activity_identity import SPORT_RUNNING, training_sport
from services.duration_range import duration_range
from services.plan_coherence import DEFAULT_HORIZON_DAYS

# How much history the chronic figure is read from. Four weeks is the window the
# acute:chronic construct uses, and it is the shortest window that spans a
# recovery week: a three-week read of a classic 3-up-1-down block lands either on
# three loading weeks or on two plus the easy one, and the ceiling would swing by
# a third depending on which day it was asked.
RUN_EXPOSURE_WINDOW_DAYS = 28

# The length of the window a planned load is measured over. Seven days because
# that is the cycle training is organised in, and rolling because the tissue does
# not know when Monday is.
ROLLING_WINDOW_DAYS = 7

# How far above the four-week mean a single 7-day block may sit. 1.3 is where the
# acute:chronic literature puts the start of elevated injury rates, and it is a
# *convention this app has chosen*, not a measured property of the athlete — see
# the module docstring. It is deliberately more permissive than the 10 % rule,
# because it is measured against four weeks of exposure rather than against one
# week's total, and a single quiet week should not ratchet the allowance down.
MAX_ACUTE_CHRONIC_RATIO = 1.3

# A cap on the step the ratio permits, for the reason the ratio alone is not
# enough.
#
# There is deliberately **no floor** on the weekly step, although the argument
# for one is good: at low volume a proportional step is meaningless, since 30 %
# of 40 min/week is twelve minutes, and one additional short jog cannot be the
# difference between adapting and being injured. The floor is unnecessary because
# :data:`BEGINNER_WEEKLY_MINUTES` is already an absolute one and sits above
# everything the proportional step fails to deliver — a 15-minute floor would
# change the ceiling only for a chronic figure between 45 and 50 min/week, and
# then by under three minutes. A constant that can only ever move a number by
# that much is not policy, and it was removed once the #600 register showed no
# test could tell it was there.
#
# At high volume the ratio permits a step the tissue argument does not support:
# 30 % of 600 min/week is three further hours of impact in one week. The cap is
# what stops the rule becoming more permissive the closer the athlete gets to the
# volume where overuse injuries actually happen.
MAX_WEEKLY_STEP_MINUTES = 90.0

# The long run gets its own pair, because it is the most concentrated exposure in
# the week — the same thirty minutes split across three runs and added to one are
# not the same stimulus, and only the second is a step the tissue meets in one
# session. Ten minutes is the smallest step worth calling one; thirty is already
# generous against the ten-to-fifteen a marathon build conventionally adds.
MIN_LONG_RUN_STEP_MINUTES = 10.0
MAX_LONG_RUN_STEP_MINUTES = 30.0

# What an athlete with no running exposure at all may be given. Roughly three
# twenty-minute runs — the shape every return-to-running protocol starts from,
# and the figure this module exists to put in front of a planner that can see a
# CTL of 90.
#
# It is also a **floor under everyone**: having run once in four weeks cannot
# make an athlete more fragile than never having run, which is what a purely
# proportional ceiling would conclude (one 20-minute run gives a chronic figure
# of 5 min/week, and a 20-minute allowance).
BEGINNER_WEEKLY_MINUTES = 60.0
BEGINNER_LONGEST_RUN_MINUTES = 20.0

# How many rows of activity history cover the window. Generous: the reader
# returns the newest rows across every sport, so a cyclist who trains daily and
# runs on Sundays needs far more than 28 rows before the oldest run in the window
# appears. Read by ``plan_context.athlete_run_exposure``, which does the query —
# everything in this module is pure, so the detection can be tested against a
# list of plain objects rather than against a database.
HISTORY_ROW_LIMIT = 200

# How many overloaded windows the prompt states. Overlapping windows describe the
# same build-up from seven different starting days, and a coach told the same
# thing seven ways stops reading. The heaviest are the ones that matter.
MAX_REPORTED_WINDOWS = 3

# The rule identifiers. Stable strings: they key the gate's before/after
# comparison and name the finding in the coach's prompt.
RULE_WEEKLY_RUN_VOLUME = "weekly-run-volume"
RULE_LONG_RUN_STEP = "long-run-step"

# Which history the ceiling was built from. Not cosmetic: the two bases justify
# the figure in completely different ways, and an athlete told "your ceiling is
# 60 minutes" deserves to know whether that came from their own last four weeks
# or from the fact that we have never seen them run.
BASIS_NO_HISTORY = "no-run-history"
BASIS_RECENT_EXPOSURE = "recent-exposure"

_REST_WORKOUT_TYPES = frozenset({"rest", "off"})


def _parse_date(raw: object) -> date | None:
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _reference_date(today: date | str | None) -> date | None:
    """The day everything here is measured from, or ``None`` if it is unreadable.

    There is deliberately no fallback to ``date.today()``. The athlete's today is
    ``services.dates.app_today``, which applies their timezone and can be a
    different calendar day from the server's — so a quiet fallback would answer
    with a window shifted by a day rather than admitting it had no reference. An
    unreadable reference means every function here says nothing: no exposure, no
    findings, no running in the plan.
    """
    if isinstance(today, date):
        return today
    return _parse_date(today)


def _positive(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def is_run_activity(sport_type: object) -> bool:
    """Whether a recorded activity counts as running exposure.

    Read through ``training_sport`` so every provider spelling of a run — "Run",
    "TrailRun", "VirtualRun", a treadmill session — lands in the same place as it
    does for load (#712) and for the fitness ledger (#713).

    Hiking and walking are deliberately outside it. Both carry some eccentric
    load, and neither carries it at running's rate; folding them in would inflate
    the chronic figure of an athlete who walks to work and hand them a running
    allowance they have not earned.
    """
    return training_sport(sport_type if isinstance(sport_type, str) else None) == (
        SPORT_RUNNING
    )


def activity_run_minutes(row: object) -> float | None:
    """How long this recorded run lasted, in minutes, or ``None``.

    ``duration_seconds`` first because every import path sets it, including for a
    run that produced no usable stream. The stored run signals are the fallback
    rather than the primary source: they exist only when the stream parsed, and
    a run nobody can time is not exposure this module can count.
    """
    seconds = _positive(getattr(row, "duration_seconds", None))
    if seconds is None:
        signals = getattr(row, "perf_signals", None)
        if isinstance(signals, Mapping):
            seconds = _positive(signals.get("moving_duration_s")) or _positive(
                signals.get("duration_s")
            )
    return seconds / 60.0 if seconds is not None else None


def activity_run_distance_km(row: object) -> float | None:
    """How far this recorded run went, in kilometres, or ``None``.

    The *raw* distance, not the grade-adjusted one. GAP distance is what prices
    the session's load (#716); weekly mileage is what a runner means when they
    say how much they ran, and quoting a hill-inflated figure back at them would
    be a number they cannot check against their watch.
    """
    signals = getattr(row, "perf_signals", None)
    if not isinstance(signals, Mapping):
        return None
    metres = _positive(signals.get("distance_m"))
    return metres / 1000.0 if metres is not None else None


@dataclass(frozen=True)
class RunExposure:
    """What running the athlete has actually done. Facts, with no judgement.

    ``minutes_by_date`` is kept rather than only its totals because a rolling
    window that spans today needs the individual days: the current seven days are
    part history and part plan, and a ceiling check that ignored the runs already
    completed this week would permit exactly the week it exists to catch.
    """

    window_days: int
    total_minutes: float
    total_distance_km: float | None
    run_count: int
    runs_with_distance: int
    peak_rolling_minutes: float
    longest_run_minutes: float
    days_since_last_run: int | None
    minutes_by_date: Mapping[date, float] = field(default_factory=dict)

    @property
    def chronic_weekly_minutes(self) -> float:
        """Mean run minutes per 7 days over the window.

        A mean rather than the recent peak. The peak is what the athlete survived
        once; the mean is what they are adapted to, and the distinction is the
        whole of what this module has to say — a single big week followed by three
        empty ones is not a base, it is the spike that produced the injury.

        Divided by the window *this exposure was read over* rather than by a
        constant derived at import, so widening the window cannot leave the
        divisor behind and quietly inflate every chronic figure.
        """
        weeks = max(self.window_days, 1) / float(ROLLING_WINDOW_DAYS)
        return self.total_minutes / weeks

    @property
    def has_history(self) -> bool:
        """Whether there is any running in the window to reason from."""
        return self.run_count > 0 and self.total_minutes > 0


def run_exposure(
    rows: list[object] | None,
    today: date | str | None,
    *,
    window_days: int = RUN_EXPOSURE_WINDOW_DAYS,
) -> RunExposure:
    """Read running exposure out of recorded-activity rows.

    ``rows`` are ``RideMetric``-shaped and may cover every sport; the runs are
    selected here. They must already be **deduplicated** by activity identity —
    ``crud.get_ride_metrics_history`` does that, and :func:`athlete_run_exposure`
    uses it for exactly this reason. A stale duplicate row would double a run's
    minutes, and the direction that errs in is the dangerous one: an inflated
    chronic figure raises the ceiling.

    Rows dated in the future are dropped rather than counted. An activity dated
    tomorrow is a timezone artefact or a bad import, and reading it as exposure
    the tissue has already absorbed would hand out an allowance for a run that
    has not happened.
    """
    reference = _reference_date(today)
    if reference is None:
        return RunExposure(
            window_days=window_days,
            total_minutes=0.0,
            total_distance_km=None,
            run_count=0,
            runs_with_distance=0,
            peak_rolling_minutes=0.0,
            longest_run_minutes=0.0,
            days_since_last_run=None,
        )
    earliest = reference - timedelta(days=max(0, window_days) - 1)

    minutes_by_date: dict[date, float] = {}
    total_distance = 0.0
    run_count = 0
    runs_with_distance = 0
    longest = 0.0
    last_run: date | None = None
    for row in rows or []:
        if not is_run_activity(getattr(row, "sport_type", None)):
            continue
        when = _parse_date(getattr(row, "activity_date", None))
        if when is None or when > reference or when < earliest:
            continue
        minutes = activity_run_minutes(row)
        if minutes is None:
            continue
        run_count += 1
        minutes_by_date[when] = minutes_by_date.get(when, 0.0) + minutes
        longest = max(longest, minutes)
        if last_run is None or when > last_run:
            last_run = when
        distance = activity_run_distance_km(row)
        if distance is not None:
            runs_with_distance += 1
            total_distance += distance

    total_minutes = sum(minutes_by_date.values())
    return RunExposure(
        window_days=window_days,
        total_minutes=total_minutes,
        total_distance_km=total_distance if runs_with_distance else None,
        run_count=run_count,
        runs_with_distance=runs_with_distance,
        peak_rolling_minutes=_peak_rolling_minutes(minutes_by_date, earliest, reference),
        longest_run_minutes=longest,
        days_since_last_run=(reference - last_run).days if last_run else None,
        minutes_by_date=minutes_by_date,
    )


def _peak_rolling_minutes(
    minutes_by_date: Mapping[date, float], earliest: date, latest: date
) -> float:
    """The heaviest 7 consecutive days in the window.

    Reported rather than used in the ceiling: it is the honest answer to "but I
    have run that much before", and the coach needs it to have that conversation
    without the athlete having to produce the evidence themselves.
    """
    if not minutes_by_date:
        return 0.0
    peak = 0.0
    start = earliest
    while start <= latest:
        total = sum(
            minutes_by_date.get(start + timedelta(days=offset), 0.0)
            for offset in range(ROLLING_WINDOW_DAYS)
        )
        peak = max(peak, total)
        start += timedelta(days=1)
    return peak


@dataclass(frozen=True)
class RunCeiling:
    """The most running this athlete's exposure supports, and why.

    ``basis`` is part of the value, not metadata about it. The two bases are
    different claims — one derived from the athlete's own four weeks, one from the
    absence of any — and a figure quoted without saying which is a figure the
    athlete cannot argue with.
    """

    weekly_minutes: float
    longest_run_minutes: float
    basis: str


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def run_volume_ceiling(exposure: RunExposure) -> RunCeiling:
    """The 7-day and single-session ceilings this exposure supports.

    Takes exposure and nothing else. There is deliberately no parameter for CTL,
    FTP or any cycling figure: the failure this module exists to prevent is a
    planner reasoning from aerobic fitness to running volume, and a ceiling that
    *could* read a fitness number would eventually be asked to.

    The single-session ceiling is additionally held at or below the 7-day one. A
    long run longer than the whole week's allowance is not a coherent
    prescription, and the arithmetic can produce one: a beginner's 20-minute
    long-run floor against a chronic figure of nothing is fine, but a lapsed
    runner whose single longest run is most of their four-week total is not.
    """
    if not exposure.has_history:
        return RunCeiling(
            weekly_minutes=BEGINNER_WEEKLY_MINUTES,
            longest_run_minutes=BEGINNER_LONGEST_RUN_MINUTES,
            basis=BASIS_NO_HISTORY,
        )
    chronic = exposure.chronic_weekly_minutes
    weekly_step = min(
        chronic * (MAX_ACUTE_CHRONIC_RATIO - 1.0), MAX_WEEKLY_STEP_MINUTES
    )
    weekly = max(chronic + weekly_step, BEGINNER_WEEKLY_MINUTES)

    longest_base = exposure.longest_run_minutes
    longest_step = _clamp(
        longest_base * (MAX_ACUTE_CHRONIC_RATIO - 1.0),
        MIN_LONG_RUN_STEP_MINUTES,
        MAX_LONG_RUN_STEP_MINUTES,
    )
    longest = max(longest_base + longest_step, BEGINNER_LONGEST_RUN_MINUTES)
    return RunCeiling(
        weekly_minutes=weekly,
        longest_run_minutes=min(longest, weekly),
        basis=BASIS_RECENT_EXPOSURE,
    )


def planned_run_minutes(day: object) -> float | None:
    """The minutes of running ``day`` prescribes, or ``None`` if it prescribes none.

    The **upper** bound of a duration window (#368), not its midpoint. A session
    written as 60–90 min is a session the athlete may legitimately run for 90, so
    a ceiling checked against 75 permits a week that exceeds it — and the athlete
    who took the plan at its word is the one who gets hurt.
    """
    if not isinstance(day, dict):
        return None
    if day_sport(day) != SPORT_RUNNING:
        return None
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "")
    if workout_type.strip().lower() in _REST_WORKOUT_TYPES:
        return None
    _lo, hi = duration_range(day)
    return float(hi) if hi else None


def _planned_runs_by_date(
    plan: list[dict] | None, today: date
) -> dict[date, list[dict]]:
    """Planned run sessions from ``today`` forward, by date, slot-ordered.

    Not horizon-limited. The horizon bounds which *windows* are checked; a window
    that starts inside it has to see every session it contains, and a plan that
    extends past the horizon still loads the tissue on those days.
    """
    by_date: dict[date, list[dict]] = {}
    for day in plan or []:
        if not isinstance(day, dict):
            continue
        when = _parse_date(day.get("date"))
        if when is None or when < today:
            continue
        minutes = planned_run_minutes(day)
        if minutes is None:
            continue
        by_date.setdefault(when, []).append(
            {
                "date": when.isoformat(),
                "slot": day_slot(day),
                "title": day.get("title") or "",
                "minutes": minutes,
            }
        )
    for sessions in by_date.values():
        sessions.sort(key=lambda s: s["slot"])
    return by_date


def plan_has_running(plan: list[dict] | None, today: date | str | None = None) -> bool:
    """Whether the plan prescribes any running from ``today`` forward.

    The other half of "is running in play for this athlete". An athlete with no
    run history and no planned run needs none of this in their prompt; an athlete
    with no run history and a planned run needs it most of all, because theirs is
    the ceiling that comes from nowhere but a convention.
    """
    reference = _reference_date(today)
    if reference is None:
        return False
    return bool(_planned_runs_by_date(plan, reference))


def _finding(
    rule: str,
    *,
    window_start: date,
    window_end: date,
    planned_minutes: float,
    ceiling_minutes: float,
    basis: str,
    sessions: list[dict],
) -> dict:
    return {
        "rule": rule,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "planned_minutes": round(planned_minutes, 1),
        "completed_minutes": 0.0,
        "ceiling_minutes": round(ceiling_minutes, 1),
        "excess_minutes": round(planned_minutes - ceiling_minutes, 1),
        "basis": basis,
        "sessions": sessions,
    }


def find_run_volume_excess(
    plan: list[dict] | None,
    exposure: RunExposure,
    ceiling: RunCeiling,
    today: date | str | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[dict]:
    """Every 7-day window in the plan whose run volume exceeds the ceiling.

    Windows start from six days *before* today, not from today. The seven days
    the athlete is currently in are part history and part plan, and the run they
    did on Tuesday is exposure their legs have already taken — a check that began
    at today would let an automated write stack three more runs onto a week that
    is already at its limit and see nothing wrong.

    Across that boundary each date is counted once: completed minutes before
    today, planned minutes after it, and **today whichever of the two is larger**.
    Today needs that rule rather than one source or the other. Reading today from
    the plan alone loses an ad-hoc run — one the athlete went out and did this
    morning with nothing prescribed, or a run longer than the session it was
    matched to — which is exposure their legs have taken and the window would not
    see. Adding the two would double count the ordinary case, where the run on
    today's plan and the run in today's history are the same run, and would flag
    the athlete for training exactly as instructed.

    The split between ``completed_minutes`` and the planned remainder is for
    reporting only — "40 min of that already run" is worth saying — and does not
    change the total. The residual imprecision is a deliberate floor rather than a
    sum: an athlete who does a prescribed 90 min *and* an unplanned 20 min jog on
    the same day is counted as 90, not 110. Preferring the larger figure to the
    sum means this guard can under-count a genuine double day and never
    over-counts one, and an over-count is what reverts a session nobody should
    have lost.

    One finding per exceeding window, overlaps and all. The overlaps are real —
    seven different seven-day blocks can each be over — and collapsing them here
    would make the finding's identity depend on which of them happened to be
    worst, so a plan write that shifted a run by a day would read as having
    created an overload that was already there. The prompt collapses for display
    instead (:data:`MAX_REPORTED_WINDOWS`).
    """
    reference = _reference_date(today)
    if reference is None:
        return []
    planned = _planned_runs_by_date(plan, reference)
    findings: list[dict] = []
    first_start = reference - timedelta(days=ROLLING_WINDOW_DAYS - 1)
    last_start = reference + timedelta(days=max(0, horizon_days))
    for offset_start in range((last_start - first_start).days + 1):
        start = first_start + timedelta(days=offset_start)
        sessions: list[dict] = []
        planned_total = 0.0
        completed_total = 0.0
        for offset in range(ROLLING_WINDOW_DAYS):
            when = start + timedelta(days=offset)
            if when < reference:
                completed_total += exposure.minutes_by_date.get(when, 0.0)
                continue
            day_sessions = planned.get(when, [])
            # Listed whatever the arithmetic below decides, because these are the
            # sessions the gate can still hand back.
            sessions.extend(day_sessions)
            prescribed = sum(session["minutes"] for session in day_sessions)
            if when != reference:
                planned_total += prescribed
                continue
            already_run = exposure.minutes_by_date.get(when, 0.0)
            completed_total += already_run
            planned_total += max(0.0, prescribed - already_run)
        # A window that starts before today and contains no completed running is
        # a shifted view of a window that starts today — same sessions, same
        # total, one more line in the prompt saying it. Only the windows where
        # history actually contributes are worth scanning backwards for.
        if start < reference and completed_total <= 0:
            continue
        total = planned_total + completed_total
        if total > ceiling.weekly_minutes and sessions:
            finding = _finding(
                RULE_WEEKLY_RUN_VOLUME,
                window_start=start,
                window_end=start + timedelta(days=ROLLING_WINDOW_DAYS - 1),
                planned_minutes=total,
                ceiling_minutes=ceiling.weekly_minutes,
                basis=ceiling.basis,
                sessions=sessions,
            )
            finding["completed_minutes"] = round(completed_total, 1)
            findings.append(finding)
    return findings


def find_long_run_excess(
    plan: list[dict] | None,
    ceiling: RunCeiling,
    today: date | str | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[dict]:
    """Planned single runs longer than the athlete's longest-run ceiling.

    Separate from the weekly rule because a week can be under its total and still
    contain the one session that does the damage: 120 minutes in a single run is a
    different exposure from the same 120 split across four days, and only the
    first asks the tissue to absorb it without a night's recovery in the middle.
    """
    reference = _reference_date(today)
    if reference is None:
        return []
    horizon_end = reference + timedelta(days=max(0, horizon_days))
    planned = _planned_runs_by_date(plan, reference)
    findings: list[dict] = []
    for when in sorted(planned):
        if when > horizon_end:
            continue
        for session in planned[when]:
            if session["minutes"] <= ceiling.longest_run_minutes:
                continue
            findings.append(
                _finding(
                    RULE_LONG_RUN_STEP,
                    window_start=when,
                    window_end=when,
                    planned_minutes=session["minutes"],
                    ceiling_minutes=ceiling.longest_run_minutes,
                    basis=ceiling.basis,
                    sessions=[session],
                )
            )
    return findings


def find_run_overload(
    plan: list[dict] | None,
    exposure: RunExposure,
    ceiling: RunCeiling,
    today: date | str | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[dict]:
    """Every durability finding in the window, largest excess first."""
    findings = [
        *find_run_volume_excess(
            plan, exposure, ceiling, today, horizon_days=horizon_days
        ),
        *find_long_run_excess(plan, ceiling, today, horizon_days=horizon_days),
    ]
    findings.sort(
        key=lambda f: (-f["excess_minutes"], f["window_start"], f["rule"])
    )
    return findings


def finding_key(finding: Mapping) -> tuple:
    """A stable identity for one finding, for the gate's before/after comparison.

    Structural, and deliberately not carrying the minutes: a write that lengthens
    an already-over week by five minutes has not created a new overload, and
    reverting it would punish that write for a week it did not build. For the
    weekly rule the identity is the window; for the long-run rule it is the
    session, because that is the thing the finding is about.
    """
    if finding.get("rule") == RULE_LONG_RUN_STEP:
        session = (finding.get("sessions") or [{}])[0]
        return (
            RULE_LONG_RUN_STEP,
            session.get("date") or "",
            session.get("slot") or 0,
        )
    return (finding.get("rule"), finding.get("window_start"))


def run_overload_keys(
    plan: list[dict] | None,
    exposure: RunExposure,
    ceiling: RunCeiling,
    today: date | str | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> dict[tuple, dict]:
    """Every finding in the window, keyed by :func:`finding_key`.

    A mapping rather than a set, for the reason ``interference_keys`` is one: once
    the gate has decided a finding is new it needs the finding itself, because the
    rationale it records names the sessions and the figures.
    """
    return {
        finding_key(finding): finding
        for finding in find_run_overload(
            plan, exposure, ceiling, today, horizon_days=horizon_days
        )
    }


def _minutes(value: float) -> str:
    return f"{round(value)} min"


def ceiling_statement(exposure: RunExposure, ceiling: RunCeiling) -> str:
    """The ceiling, what it was built from, and what it is not.

    The limitation is in the sentence rather than in a footnote, because this
    figure is going to be read out to an athlete who is being told they may not
    do something. "The evidence for any particular number is weak" is part of the
    claim, not a caveat on it — and an athlete who is told that can argue, which
    is the outcome a silent clamp denies them.
    """
    if ceiling.basis == BASIS_NO_HISTORY:
        return (
            "This athlete has run nothing in the last "
            f"{exposure.window_days} days, so there is no running-specific "
            "exposure to progress from. Their aerobic fitness is not running "
            f"fitness: it says the engine can sustain running, and nothing about "
            "whether the bone, tendon and muscle that absorb each stride can. "
            f"Treat {_minutes(ceiling.weekly_minutes)} across any 7 days and "
            f"{_minutes(ceiling.longest_run_minutes)} in any single run as the "
            "ceiling until there is exposure to reason from — beginner figures, "
            "because on running they are a beginner."
        )
    distance = ""
    if exposure.total_distance_km is not None:
        measured = (
            "" if exposure.runs_with_distance == exposure.run_count else " (measured)"
        )
        distance = f", about {exposure.total_distance_km:.0f} km{measured}"
    return (
        f"Running exposure, last {exposure.window_days} days: "
        f"{exposure.run_count} runs, {_minutes(exposure.total_minutes)} total"
        f"{distance} — a chronic "
        f"{_minutes(exposure.chronic_weekly_minutes)} per 7 days, heaviest 7 days "
        f"{_minutes(exposure.peak_rolling_minutes)}, longest single run "
        f"{_minutes(exposure.longest_run_minutes)}. On that exposure, treat "
        f"{_minutes(ceiling.weekly_minutes)} across any rolling 7 days and "
        f"{_minutes(ceiling.longest_run_minutes)} in a single run as the ceiling. "
        f"That is a {MAX_ACUTE_CHRONIC_RATIO:g}× step on their own four-week mean, "
        "which is a convention this app has chosen and not a measured property of "
        "this athlete — the evidence behind any particular progression figure, the "
        "10 % rule included, is weak. Say it that way if the athlete asks."
    )


def finding_statement(
    finding: Mapping, *, weekdays: Mapping[str, str] | None = None
) -> str:
    """What is over the ceiling, by how much, and why that is the limit.

    Factual and claim-free about the remedy, like ``interference.finding_statement``
    — the detection does not decide which run moves or shortens, so it must not
    say. The gate appends which session it handed back; the prompt appends
    nothing, because there the finding is something the coach weighs.

    ``weekdays`` anchors the dates, because a prompt that shows or writes the
    plan has to (#462): "2026-10-11 to 2026-10-17" tells an LLM nothing about
    which of those days is the weekend the long run belongs on. It is optional
    rather than required because the same statement is recorded in
    ``plan_day_history``, where the row carries its own date column and the
    label would be noise.
    """
    labels = weekdays or {}

    def when(iso: object) -> str:
        text = str(iso or "")
        label = labels.get(text)
        return f"{text} ({label})" if label else text

    ceiling = _minutes(float(finding.get("ceiling_minutes") or 0.0))
    planned = _minutes(float(finding.get("planned_minutes") or 0.0))
    basis = finding.get("basis")
    if finding.get("rule") == RULE_LONG_RUN_STEP:
        session = (finding.get("sessions") or [{}])[0]
        title = session.get("title") or "the run"
        where = (
            "with no previous run to step up from"
            if basis == BASIS_NO_HISTORY
            else "stepped up from their longest recent run"
        )
        return (
            f"{when(finding['window_start'])}: {title} prescribes {planned} in one "
            f"session, against a {ceiling} single-run ceiling {where}. A single "
            "run is the week's most concentrated impact — the same minutes spread "
            "across three days are a different stimulus, because only one session "
            "has to be absorbed without a night's recovery inside it."
        )
    completed = float(finding.get("completed_minutes") or 0.0)
    already = (
        f" ({_minutes(completed)} of that already run)" if completed > 0 else ""
    )
    source = (
        "a beginner allowance, because there is no run history"
        if basis == BASIS_NO_HISTORY
        else "their own four-week running exposure"
    )
    return (
        f"{when(finding['window_start'])} to {when(finding['window_end'])}: "
        f"{planned} of running in 7 days{already}, against a {ceiling} ceiling "
        f"from {source}. Aerobic fitness is not what this limit is about — every "
        "stride is an eccentric contraction, and bone and tendon adapt on a "
        "slower clock than the aerobic system does."
    )


