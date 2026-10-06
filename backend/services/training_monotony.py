"""How evenly the athlete's load was spread, which no other signal describes (#747).

Every load figure this app computes describes the *level* of training. CTL is the
42-day weighted mean, ATL the 7-day one, TSB their difference — and because those
time constants make the ledger an exponentially-weighted acute:chronic model,
``ATL/CTL`` is already the ratio form of ACWR in the parametrisation Williams et
al. (2017) prefer to the rolling-average one. Measured over twelve weeks of each
pattern, against ``services/fitness_ledger``:

===========================  ===========  ====  ====  =====  =======
week                         weekly load   CTL   ATL    TSB  ATL/CTL
===========================  ===========  ====  ====  =====  =======
flat, 7 × 60                         420  51.9  60.0   −8.1     1.16
polarised, 3 hard + 4 rest           500  61.2  67.8   −6.6     1.11
===========================  ===========  ====  ====  =====  =======

The polarised week carries 19 % more load and reports as *fresher*. That is
arithmetically right and coaching-blind: the two weeks differ in the one way that
decides whether the load becomes adaptation, and no number above says so. An
athlete who has not had an easy day in three months reads TSB −8 and is told they
are training sustainably.

What is missing is the *distribution*, and that is the whole content of Foster's
monotony (1998): the mean daily load divided by its standard deviation, over a
rolling week, with rest days counted as the zeros they are. Excluding them inverts
the metric, because the rest days are the variance.

**Monotony is dimensionless**, a mean over an SD in the same units, so it is the
one figure here that transfers across currencies and cohorts; Foster's flag near
2.0 can be quoted. **Strain — weekly load × monotony — is not.** It carries the
currency's units, so a strain threshold in TSS citing Foster, whose figures came
from daily sRPE, would be a number dressed as a finding. Strain is therefore
reported against *this athlete's own* trailing windows and never against a
constant, the same argument ``services/run_durability`` makes about the
10 %-per-week rule.

Three things that are not obvious until the arithmetic is run:

**Monotony is unbounded, and not only when the SD is zero.** A perfectly flat week
divides by zero, but a week of ``[60, 60, 60, 60, 60, 60, 55]`` already computes to
34 and ``[60]*7`` with one day a single point off computes to 170. Those are not
different cases from ``inf``; they are the same numerical instability approached
from slightly further away, and any of them reported verbatim is noise with a
decimal point. So the value is capped and says that it was, which covers the
division by zero as the limit of the behaviour rather than as a special case. The
defensive-looking ``if sd == 0: return 0`` is the one answer that must never be
given: it reports the worst week obtainable as the best one.

**One rest day in seven does not get under 2.0.** ``[60]*6 + [0]`` is 2.45;
``[60]*5 + [0, 0]`` is 1.58. The threshold is not arbitrary in practice — it
amounts to "two genuinely easy days a week, or real variation in the hard ones",
which is a coaching line an athlete can act on.

**The window ends yesterday.** Today is incomplete, and an incomplete day enters
the series as a low number, raising the SD and *lowering* monotony. That errs in
the dangerous direction — silently under-reporting the relentless week this module
exists to catch — so the last complete day is the last day counted.

The module is pure: rows in, facts out. It decides nothing about the plan and
reverts nothing. Its output is a statement for the coach to weigh, in the slot
``services/plan_context`` already builds for deterministic audits.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

# The window a distribution is measured over. Seven days because that is the cycle
# training is organised in, and rolling rather than Monday-to-Sunday for the reason
# ``run_durability`` uses rolling windows: a hard block placed Saturday-Sunday-
# Monday is one week's loading to the athlete and two half-weeks to a calendar.
ROLLING_WINDOW_DAYS = 7

# How much history the athlete's own strain baseline is read from. Four weeks, the
# same window and the same justification as ``run_durability``: it is the shortest
# span that contains a recovery week, so a 3-up-1-down block does not swing the
# baseline by a third depending on which day it was asked.
BASELINE_WINDOW_DAYS = 28

# Foster's monotony flag. Quotable as literature because monotony is dimensionless
# — unlike strain, this constant does not change meaning when the currency does.
# The caveat that travels with it: his distribution was daily session-RPE and this
# one is the unified load currency, so the shapes are not identical even though the
# units cancel in both.
MONOTONY_FLAG = 2.0

# The largest monotony reported. Above this the quotient is numerically unstable
# (see the module docstring: a one-point difference across a flat week computes to
# 170) and coaching-identical — every value here means "no day was easier than the
# others". Reported as a floor, never as a measurement, and it is also what the
# zero-SD week gets, that being the limit of the same behaviour.
MONOTONY_CAP = 5.0

# The least daily load at which a flat week is a risk rather than a routine. An
# aggregate 40 a day sustains a CTL near 40 — roughly an hour of endurance work
# daily — and below it the athlete doing the same small thing every day is
# describing a habit, not accumulating strain. This is **this app's convention in
# this app's currency**, stated as such wherever it reaches the athlete, in the
# same spirit as ``run_durability.MAX_ACUTE_CHRONIC_RATIO``. It is deliberately not
# dressed up with a citation: Foster's strain thresholds are in sRPE and do not
# convert.
MIN_DAILY_MEAN_LOAD = 40.0

# How far a strain ratio may sit from 1.0 and still be called unchanged. Not
# cosmetic: an athlete repeating one week exactly computes to 0,9999999999999999,
# and without a band the sentence tells them their strain is *below* their own
# baseline on the strength of floating-point error. Ten per cent is also about the
# width within which the comparison means nothing to a coach.
STRAIN_BASELINE_TOLERANCE = 0.1

# How many rows to read. Enough to cover the baseline window several times over for
# an athlete who trains twice a day, since the reader returns rows and not days.
HISTORY_ROW_LIMIT = 200


def _as_date(value: date | str | None) -> date | None:
    """A ``date`` from either of the two shapes rows and callers carry."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _load(row: object) -> float | None:
    """One row's load, or ``None`` when it never got a price.

    ``None`` is not zero and must not be flattened into one. A row with no load is
    an activity this app failed to price, whereas zero is the assertion that the
    athlete rested — the #579 confusion, which raised TSB across an hour of
    training. The caller counts these rather than summing them.
    """
    value = getattr(row, "tss", None)
    if value is None:
        return None
    try:
        load = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(load) or load < 0:
        return None
    return load


@dataclass(frozen=True)
class LoadDistribution:
    """How one window's load was spread. Facts, with no judgement.

    ``daily_load`` holds every day in the window including the empty ones, because
    the zeros are the signal: a dictionary of only the days that had activities
    describes a different week from the one the athlete lived.
    """

    window_start: date
    window_end: date
    window_days: int
    daily_load: Mapping[date, float]
    weekly_load: float
    monotony: float
    monotony_is_capped: bool
    strain: float
    baseline_strain: float
    baseline_windows: int
    rest_days: int
    unpriced_activities: int

    @property
    def daily_mean(self) -> float:
        """Mean load per day across the window, empty days included."""
        return self.weekly_load / max(self.window_days, 1)

    @property
    def is_measurable(self) -> bool:
        """Whether the window's series can be trusted to describe the week.

        False while any activity in it has no load. A missing price is not a quiet
        zero — treating it as one both understates the day and overstates the
        variance, so the honest answer is that this week cannot be described yet
        rather than a figure derived from a hole.
        """
        return self.weekly_load > 0 and self.unpriced_activities == 0

    @property
    def strain_ratio(self) -> float | None:
        """This window's strain over the athlete's own baseline, or ``None``.

        ``None`` when there is not enough history to have a baseline, which is a
        different statement from "average" and must not be rendered as 1.0.
        """
        if self.baseline_windows < 2 or self.baseline_strain <= 0:
            return None
        return self.strain / self.baseline_strain


def _monotony(series: list[float]) -> tuple[float, bool]:
    """Foster's monotony for one series, capped, with whether the cap was hit.

    The population SD, not the sample one: the seven days *are* the week, not a
    sample drawn from it.
    """
    if not series:
        return 0.0, False
    mean = statistics.fmean(series)
    if mean <= 0:
        return 0.0, False
    deviation = statistics.pstdev(series)
    if deviation <= 0:
        return MONOTONY_CAP, True
    value = mean / deviation
    if value >= MONOTONY_CAP:
        return MONOTONY_CAP, True
    return value, False


def _daily_totals(rows: list[object] | None) -> tuple[dict[date, float], dict[date, int]]:
    """Aggregate load per calendar day, and unpriced activities per day.

    Aggregate across every sport, matching ``atl_after`` rather than any one
    sport's CTL: monotony is a claim about the athlete's week. Two sessions on one
    day are one day's load, which is also why this cannot be keyed on activity.
    """
    totals: dict[date, float] = {}
    unpriced: dict[date, int] = {}
    for row in rows or []:
        day = _as_date(getattr(row, "activity_date", None))
        if day is None:
            continue
        load = _load(row)
        if load is None:
            unpriced[day] = unpriced.get(day, 0) + 1
            continue
        totals[day] = totals.get(day, 0.0) + load
    return totals, unpriced


def _window_series(
    totals: Mapping[date, float], start: date, days: int
) -> list[float]:
    """The window's daily loads in order, absent days filled with zero."""
    return [totals.get(start + timedelta(days=offset), 0.0) for offset in range(days)]


def load_distribution(
    rows: list[object] | None,
    today: date | str | None,
    *,
    window_days: int | None = None,
    baseline_days: int | None = None,
) -> LoadDistribution | None:
    """Read the athlete's most recent complete window out of recorded-activity rows.

    ``rows`` are ``RideMetric``-shaped, every sport, and must already be
    **deduplicated** by activity identity — ``crud.get_ride_metrics_history`` does
    that and :func:`plan_context.athlete_load_distribution` uses it for exactly
    this reason. A re-imported activity counted twice inflates one day, which
    raises the SD and *lowers* monotony: the direction that hides the finding.

    ``today`` is required. ``None`` returns ``None`` rather than defaulting to the
    system date, because a reference date silently taken from the clock would read
    a different window from the one the rest of the prompt was built for, and a
    distribution is only interpretable next to the plan it is quoted beside.

    The window ends on the day *before* ``today`` — see the module docstring.
    """
    reference = _as_date(today)
    if reference is None:
        return None
    # The two windows are read from the module here rather than bound as default
    # arguments. A default is evaluated once at import, which makes a constant that
    # looks adjustable silently frozen — and in particular unreachable by the #600
    # register, which moves the module attribute and requires the owning tests to
    # notice. A constant whose perturbation cannot reach the code is not guarded,
    # it only appears to be. Do not fold these back into the signature.
    window_days = max(
        int(ROLLING_WINDOW_DAYS if window_days is None else window_days), 1
    )
    baseline_days = max(
        int(BASELINE_WINDOW_DAYS if baseline_days is None else baseline_days),
        window_days,
    )

    end = reference - timedelta(days=1)
    start = end - timedelta(days=window_days - 1)
    totals, unpriced = _daily_totals(rows)

    series = _window_series(totals, start, window_days)
    weekly_load = sum(series)
    monotony, capped = _monotony(series)
    strain = weekly_load * monotony

    # The baseline is every complete window that fits before this one, so the
    # current week is never part of the history it is compared against — a week
    # that is its own baseline can only ever look average.
    baseline: list[float] = []
    offset = window_days
    while offset + window_days <= baseline_days:
        earlier_start = start - timedelta(days=offset)
        earlier = _window_series(totals, earlier_start, window_days)
        earlier_load = sum(earlier)
        if earlier_load > 0:
            earlier_monotony, _ = _monotony(earlier)
            baseline.append(earlier_load * earlier_monotony)
        offset += window_days

    return LoadDistribution(
        window_start=start,
        window_end=end,
        window_days=window_days,
        daily_load={
            start + timedelta(days=offset): value
            for offset, value in enumerate(series)
        },
        weekly_load=weekly_load,
        monotony=monotony,
        monotony_is_capped=capped,
        strain=strain,
        baseline_strain=statistics.fmean(baseline) if baseline else 0.0,
        baseline_windows=len(baseline) + 1,
        rest_days=sum(1 for value in series if value <= 0),
        unpriced_activities=sum(
            count
            for day, count in unpriced.items()
            if start <= day <= end
        ),
    )


def is_monotonous(distribution: LoadDistribution | None) -> bool:
    """Whether this week is both monotonous and loaded enough for that to matter.

    A conjunction, deliberately. Monotony alone condemns a genuinely easy flat
    week — twenty minutes of the same thing daily scores the same as an hour, and
    only one of those is a problem — so the load half is what separates a risk from
    a routine. It is the only part of the rule carried in the app's currency, and
    it is a convention rather than a finding; the statement says so.
    """
    if distribution is None or not distribution.is_measurable:
        return False
    if distribution.daily_mean < MIN_DAILY_MEAN_LOAD:
        return False
    return distribution.monotony >= MONOTONY_FLAG


def _number(value: float) -> str:
    """One decimal, in the comma convention the rest of the prompts use."""
    return f"{value:.1f}".replace(".", ",")


def distribution_statement(distribution: LoadDistribution) -> str:
    """The week's shape, what it means, and which part of it is a convention.

    The limitation travels inside the sentence rather than below it, because this
    reaches an athlete who is being told their week was wrong. Monotony is quoted
    as literature and the load floor as a house rule, and the two are not blurred
    together: an athlete told which half is which can argue with the right one.
    """
    heaviest = max(distribution.daily_load.values(), default=0.0)
    monotony = ("at least " if distribution.monotony_is_capped else "") + _number(
        distribution.monotony
    )
    rest = (
        "no day off at all"
        if distribution.rest_days == 0
        else f"{distribution.rest_days} day{'s' if distribution.rest_days > 1 else ''} off"
    )
    lines = [
        f"Load distribution, the 7 complete days to {distribution.window_end.isoformat()}: "
        f"{round(distribution.weekly_load)} total across every sport, "
        f"{round(distribution.daily_mean)} a day on average, heaviest day "
        f"{round(heaviest)}, {rest}. Monotony {monotony} "
        f"(Foster's mean ÷ standard deviation of the daily loads, rest days "
        f"counted as zero), against a flag at {_number(MONOTONY_FLAG)}.",
    ]
    if distribution.monotony_is_capped:
        lines.append(
            "The quotient is reported as a floor because every day carried "
            "near-identical load, which makes the exact figure arithmetic noise "
            "rather than a measurement."
        )
    ratio = distribution.strain_ratio
    if ratio is not None:
        if abs(ratio - 1.0) <= STRAIN_BASELINE_TOLERANCE:
            direction = "in line with"
        elif ratio > 1.0:
            direction = "above"
        else:
            direction = "below"
        lines.append(
            f"Strain (weekly load × monotony) is {_number(ratio)}× this athlete's "
            f"own mean over the previous "
            f"{distribution.baseline_windows - 1} weeks — {direction} their "
            "baseline. Strain is stated as a ratio to their own history on "
            "purpose: it carries the load unit, so published strain thresholds, "
            "which come from session-RPE, do not convert into it."
        )
    else:
        lines.append(
            "There is not yet enough history to say whether that is unusual for "
            "this athlete, so the monotony figure stands on its own."
        )
    lines.append(
        "Monotony itself is a ratio of two quantities in the same unit, so it does "
        "carry across sports and currencies — that is why it is the figure quoted. "
        "The judgement that it matters at this volume "
        f"(at least {round(MIN_DAILY_MEAN_LOAD)} a day on average) is a convention "
        "this app has chosen, not a measured property of this athlete. What a high "
        "monotony means concretely is that nothing in the week was easy: hard and "
        "easy days at the same cost give the body no contrast to adapt to, and in "
        "Foster's cohorts that pattern tracked illness and stagnation better than "
        "the total did. Two genuinely easy days, or real variation in the hard "
        "ones, is what brings it down — more total load is not the problem and "
        "cutting volume is not automatically the fix. Say it that way if the "
        "athlete asks, and name the figures."
    )
    return "\n".join(lines)
