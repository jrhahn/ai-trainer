"""The load distribution audit: Foster monotony and strain over the unified currency (#747).

The cases that matter are the pairs. A test that only asserts "a flat week is
flagged" would pass with a rule that flags every week, so each flagged pattern
here is tested next to the pattern it must be distinguished from: polarised at the
same or greater load, and flat at a load too small to matter.
"""

from __future__ import annotations

import datetime

import pytest

from services import plan_context, prompts, training_monotony
from services.training_monotony import (
    MIN_DAILY_MEAN_LOAD,
    MONOTONY_CAP,
    MONOTONY_FLAG,
    STRAIN_BASELINE_TOLERANCE,
    is_monotonous,
    load_distribution,
)

TODAY = datetime.date(2026, 10, 6)

# Patterns are given newest-day-first, i.e. index 0 is the last complete day.
FLAT_HARD = [60.0] * 7
FLAT_EASY = [20.0] * 7
POLARISED = [150.0, 0.0, 150.0, 0.0, 0.0, 200.0, 0.0]
ONE_REST_DAY = [60.0] * 6 + [0.0]
TWO_REST_DAYS = [60.0] * 5 + [0.0, 0.0]
VARIED = [120.0, 40.0, 0.0, 110.0, 40.0, 0.0, 70.0]


class _Row:
    """A ``RideMetric``-shaped row. Duck-typed, as the pure module reads it."""

    def __init__(self, day: datetime.date, tss, sport_type: str = "Ride") -> None:
        self.activity_date = day.isoformat()
        self.tss = tss
        self.sport_type = sport_type


def _rows(pattern: list[float], *, weeks: int = 5, sport: str = "Ride") -> list[_Row]:
    """``weeks`` repetitions of ``pattern``, ending on the last complete day."""
    last_complete = TODAY - datetime.timedelta(days=1)
    rows: list[_Row] = []
    for week in range(weeks):
        for offset, load in enumerate(pattern):
            if not load:
                continue
            day = last_complete - datetime.timedelta(days=offset + 7 * week)
            rows.append(_Row(day, load, sport))
    return rows


def _distribution(pattern: list[float], **kwargs):
    return load_distribution(_rows(pattern, **kwargs), TODAY)


# --------------------------------------------------------------------------
# The gap the issue measured: level versus distribution
# --------------------------------------------------------------------------


def test_the_flat_week_is_flagged_and_the_heavier_polarised_week_is_not():
    """The pair from the issue. The polarised week carries *more* load."""
    flat = _distribution(FLAT_HARD)
    polarised = _distribution(POLARISED)

    assert polarised.weekly_load > flat.weekly_load
    assert is_monotonous(flat)
    assert not is_monotonous(polarised)


def test_a_flat_easy_week_is_not_flagged():
    """Monotony alone is not the rule; the conjunction with load is the rule."""
    easy = _distribution(FLAT_EASY)

    assert easy.monotony >= MONOTONY_FLAG
    assert easy.daily_mean < MIN_DAILY_MEAN_LOAD
    assert not is_monotonous(easy)


def test_the_load_floor_is_what_separates_the_two_flat_weeks():
    """Both flat weeks are maximally monotonous; only the loaded one is flagged."""
    easy = _distribution(FLAT_EASY)
    hard = _distribution(FLAT_HARD)

    assert easy.monotony == hard.monotony == MONOTONY_CAP
    assert [is_monotonous(easy), is_monotonous(hard)] == [False, True]


def test_two_rest_days_clear_the_flag_and_one_does_not():
    """The threshold's practical meaning, pinned (see the module docstring)."""
    one = _distribution(ONE_REST_DAY)
    two = _distribution(TWO_REST_DAYS)

    assert one.monotony >= MONOTONY_FLAG
    assert two.monotony < MONOTONY_FLAG
    assert is_monotonous(one)
    assert not is_monotonous(two)


def test_a_varied_five_day_week_is_not_flagged():
    """A well-structured week must not trip the audit, or it says nothing."""
    assert not is_monotonous(_distribution(VARIED))


# --------------------------------------------------------------------------
# Rest days are the variance
# --------------------------------------------------------------------------


def test_rest_days_are_counted_as_zeros():
    distribution = _distribution(TWO_REST_DAYS)

    assert distribution.rest_days == 2
    assert len(distribution.daily_load) == 7
    assert sorted(distribution.daily_load.values()) == [0.0, 0.0] + [60.0] * 5


def test_dropping_the_rest_days_would_invert_the_metric():
    """The reason the series is built over the calendar and not over the rows.

    Scoring only the days that had activities makes the polarised week — three
    identical hard days — look maximally monotonous, and it is the one week in
    these fixtures that is correctly shaped.
    """
    distribution = _distribution(POLARISED)
    trained_only = [v for v in distribution.daily_load.values() if v > 0]

    assert distribution.monotony < MONOTONY_FLAG
    # Same three loads with the zeros removed: two of them are identical, so the
    # deviation collapses and the quotient climbs past the flag.
    assert training_monotony._monotony(trained_only)[0] >= MONOTONY_FLAG


# --------------------------------------------------------------------------
# The unbounded quotient
# --------------------------------------------------------------------------


def test_a_zero_deviation_week_reports_the_cap_not_infinity():
    distribution = _distribution(FLAT_HARD)

    assert distribution.monotony == MONOTONY_CAP
    assert distribution.monotony_is_capped


def test_a_zero_deviation_week_is_never_reported_as_unmonotonous():
    """The defensive ``if sd == 0: return 0`` that must never be written."""
    assert _distribution(FLAT_HARD).monotony >= MONOTONY_FLAG


def test_a_near_flat_week_is_capped_too():
    """The cap is not a division-by-zero guard; the quotient is unstable near it.

    ``[60] * 6 + [55]`` computes to about 34 uncapped — a number with no meaning
    that a coach would nonetheless read as thirty-four times something.
    """
    distribution = _distribution([60.0] * 6 + [55.0])

    assert distribution.monotony == MONOTONY_CAP
    assert distribution.monotony_is_capped
    assert is_monotonous(distribution)


def test_an_uncapped_week_reports_its_own_figure():
    distribution = _distribution(ONE_REST_DAY)

    assert not distribution.monotony_is_capped
    assert MONOTONY_FLAG <= distribution.monotony < MONOTONY_CAP


# --------------------------------------------------------------------------
# The window ends on the last complete day
# --------------------------------------------------------------------------


def test_the_window_ends_yesterday():
    distribution = _distribution(FLAT_HARD)

    assert distribution.window_end == TODAY - datetime.timedelta(days=1)
    assert distribution.window_start == TODAY - datetime.timedelta(days=7)
    assert TODAY not in distribution.daily_load


def test_an_incomplete_today_cannot_dilute_the_week():
    """Today's part-done load would raise the SD and hide the finding."""
    rows = _rows(FLAT_HARD) + [_Row(TODAY, 5.0)]
    distribution = load_distribution(rows, TODAY)

    assert distribution.monotony == MONOTONY_CAP
    assert is_monotonous(distribution)


def test_an_absent_reference_date_returns_nothing():
    """Never the system clock: the window has to match the rest of the prompt."""
    assert load_distribution(_rows(FLAT_HARD), None) is None


def test_the_reference_date_is_accepted_as_a_string():
    assert load_distribution(_rows(FLAT_HARD), TODAY.isoformat()) == _distribution(
        FLAT_HARD
    )


# --------------------------------------------------------------------------
# Aggregate across sports, and the #745 dependency
# --------------------------------------------------------------------------


def test_the_series_aggregates_every_sport_on_one_day():
    """One day's load is one day's load, whichever sports made it up."""
    day = TODAY - datetime.timedelta(days=2)
    rows = [_Row(day, 40.0, "Ride"), _Row(day, 20.0, "WeightTraining")]
    distribution = load_distribution(rows, TODAY)

    assert distribution.daily_load[day] == 60.0


def test_hand_logged_sessions_are_part_of_the_series():
    """Why this could not have been built before #745.

    Rides on alternating days with gym sessions in between is a week with one rest
    day. Before #745 the gym sessions produced no row at all, so the same week read
    as three loads and four zeros — *lower* monotony for the more relentless week,
    which is the direction that hides the finding.
    """
    rides = [60.0, 0.0, 60.0, 0.0, 60.0, 0.0, 0.0]
    gym = [0.0, 55.0, 0.0, 55.0, 0.0, 55.0, 0.0]
    combined = [a + b for a, b in zip(rides, gym)]

    rides_only = _distribution(rides)
    whole_week = load_distribution(
        _rows(rides) + _rows(gym, sport="WeightTraining"), TODAY
    )

    assert sorted(whole_week.daily_load.values()) == sorted(
        load for load in combined
    )
    assert whole_week.monotony > rides_only.monotony
    assert is_monotonous(whole_week)
    assert not is_monotonous(rides_only)


# --------------------------------------------------------------------------
# An unpriced activity is not a rest day
# --------------------------------------------------------------------------


def test_an_unpriced_activity_makes_the_week_unmeasurable():
    """``tss IS NULL`` is "we failed to price it", not "they rested" (#579)."""
    rows = _rows(FLAT_HARD) + [_Row(TODAY - datetime.timedelta(days=3), None)]
    distribution = load_distribution(rows, TODAY)

    assert distribution.unpriced_activities == 1
    assert not distribution.is_measurable
    assert not is_monotonous(distribution)


def test_an_unpriced_activity_outside_the_window_does_not_block_the_flag():
    rows = _rows(FLAT_HARD) + [_Row(TODAY - datetime.timedelta(days=20), None)]
    distribution = load_distribution(rows, TODAY)

    assert distribution.unpriced_activities == 0
    assert is_monotonous(distribution)


def test_a_baseline_window_with_an_unpriced_activity_is_skipped():
    """A hole understates that window's load and so understates its strain.

    Including it drags the baseline down and reports this week as more of a spike
    than it was — the same #579 argument the current window already makes, applied
    to the history it is compared against. Found in review on PR #748.
    """
    clean = _distribution(FLAT_HARD)
    holed = load_distribution(
        _rows(FLAT_HARD) + [_Row(TODAY - datetime.timedelta(days=20), None)], TODAY
    )

    assert clean.baseline_windows == 4
    assert holed.baseline_windows == 3
    assert holed.baseline_strain == pytest.approx(clean.baseline_strain)


def test_losing_every_baseline_window_to_holes_removes_the_comparison():
    """Not enough history is the honest answer, rather than a baseline of one week."""
    holes = [
        _Row(TODAY - datetime.timedelta(days=day), None) for day in (10, 17, 24)
    ]
    distribution = load_distribution(_rows(FLAT_HARD) + holes, TODAY)

    assert distribution.baseline_windows == 1
    assert distribution.strain_ratio is None
    assert is_monotonous(distribution)


def test_a_non_numeric_load_is_treated_as_unpriced():
    rows = _rows(FLAT_HARD) + [_Row(TODAY - datetime.timedelta(days=3), "lots")]

    assert not load_distribution(rows, TODAY).is_measurable


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5.0])
def test_an_unusable_load_never_enters_the_series(bad):
    rows = [_Row(TODAY - datetime.timedelta(days=2), bad)]
    distribution = load_distribution(rows, TODAY)

    assert distribution.weekly_load == 0.0
    assert distribution.unpriced_activities == 1


def test_an_empty_week_is_not_flagged():
    assert not is_monotonous(load_distribution([], TODAY))


@pytest.mark.parametrize("bad_date", [None, "", "not-a-date", 20261006])
def test_a_row_with_an_unreadable_date_is_ignored(bad_date):
    """A row that cannot be placed on the calendar cannot be placed in a window."""
    rows = _rows(FLAT_HARD) + [_Row(TODAY, 99.0)]
    rows[-1].activity_date = bad_date

    assert is_monotonous(load_distribution(rows, TODAY))


def test_an_unreadable_reference_date_returns_nothing():
    assert load_distribution(_rows(FLAT_HARD), "the fourth of never") is None


def test_an_empty_series_is_not_monotonous():
    """Unreachable through ``load_distribution``, whose window is never empty.

    Kept and tested because the alternative to the guard is ``fmean([])`` raising
    out of a prompt build, and because the helper is read directly above.
    """
    assert training_monotony._monotony([]) == (0.0, False)
    assert training_monotony._monotony([0.0] * 7) == (0.0, False)


# --------------------------------------------------------------------------
# Strain is relative to the athlete's own history
# --------------------------------------------------------------------------


def test_strain_is_the_weekly_load_times_the_monotony():
    distribution = _distribution(ONE_REST_DAY)

    assert distribution.strain == pytest.approx(
        distribution.weekly_load * distribution.monotony
    )


def test_the_current_week_is_not_part_of_its_own_baseline():
    """A week that is its own baseline can only ever look average."""
    distribution = _distribution(FLAT_HARD, weeks=5)
    current_window = set(distribution.daily_load)

    # Three complete earlier windows fit in the 28-day baseline, plus this one.
    assert distribution.baseline_windows == 4
    assert distribution.window_end in current_window
    assert distribution.baseline_strain == pytest.approx(distribution.strain)


def test_a_strain_spike_reads_above_the_baseline():
    rows = _rows(FLAT_HARD, weeks=5)
    # Double the most recent week only.
    recent = {
        (TODAY - datetime.timedelta(days=offset)).isoformat() for offset in range(1, 8)
    }
    for row in rows:
        if row.activity_date in recent:
            row.tss = 120.0
    distribution = load_distribution(rows, TODAY)

    assert distribution.strain_ratio > 1.0 + STRAIN_BASELINE_TOLERANCE
    assert "above their baseline" in training_monotony.distribution_statement(
        distribution
    )


def test_an_unchanged_week_is_in_line_with_its_baseline_despite_float_noise():
    """``[60]*6 + [0]`` repeated computes the ratio as 0,9999999999999999.

    Without the tolerance band the athlete is told their strain is *below* their
    own baseline on the strength of floating-point error.
    """
    distribution = _distribution(ONE_REST_DAY)

    assert distribution.strain_ratio != 1.0
    assert "in line with their baseline" in training_monotony.distribution_statement(
        distribution
    )


def test_a_quiet_week_reads_below_the_baseline():
    rows = _rows(FLAT_HARD, weeks=5)
    recent = {
        (TODAY - datetime.timedelta(days=offset)).isoformat() for offset in range(1, 8)
    }
    for row in rows:
        if row.activity_date in recent:
            row.tss = 20.0
    distribution = load_distribution(rows, TODAY)

    assert distribution.strain_ratio < 1.0 - STRAIN_BASELINE_TOLERANCE
    assert "below their baseline" in training_monotony.distribution_statement(
        distribution
    )


def test_a_thin_history_has_no_baseline_rather_than_an_average_one():
    """``None`` is a different statement from 1.0 and must not render as it."""
    distribution = _distribution(FLAT_HARD, weeks=1)

    assert distribution.baseline_windows == 1
    assert distribution.strain_ratio is None
    statement = training_monotony.distribution_statement(distribution)
    assert "not yet enough history" in statement
    assert "× this athlete's own mean" not in statement


# --------------------------------------------------------------------------
# What the coach is told
# --------------------------------------------------------------------------


def test_the_statement_separates_the_literature_from_the_house_rule():
    """The two halves of the rule have different standing and must not be blurred."""
    statement = training_monotony.distribution_statement(_distribution(FLAT_HARD))

    assert "Foster" in statement
    assert "same unit" in statement and "carry across sports" in statement
    assert "convention this app has chosen" in statement
    assert f"at least {round(MIN_DAILY_MEAN_LOAD)} a day" in statement


def test_the_statement_says_what_to_do_and_what_not_to_conclude():
    """A coach told only "monotony 5" cuts volume, which is not the fix."""
    statement = training_monotony.distribution_statement(_distribution(FLAT_HARD))

    assert "Two genuinely easy days" in statement
    assert "cutting volume is not automatically the fix" in statement


def test_a_capped_figure_is_stated_as_a_floor():
    statement = training_monotony.distribution_statement(_distribution(FLAT_HARD))

    assert "at least 5,0" in statement
    assert "arithmetic noise" in statement


def test_an_uncapped_figure_is_stated_as_a_number():
    statement = training_monotony.distribution_statement(_distribution(ONE_REST_DAY))

    assert "Monotony 2,4" in statement
    assert "at least" not in statement.split("Monotony")[1].split("\n")[0]


def test_the_statement_reads_its_window_length_from_the_data():
    """Not a literal "7": the prompt would misstate a moved window.

    The constant is in the #600 register, so it *will* be moved — and a statement
    that says seven while measuring one is the two-readings-of-one-number failure
    this module already carries a comment about. Found in review on PR #748.
    """
    original = training_monotony.ROLLING_WINDOW_DAYS
    try:
        training_monotony.ROLLING_WINDOW_DAYS = 5
        statement = training_monotony.distribution_statement(_distribution(FLAT_HARD))
    finally:
        training_monotony.ROLLING_WINDOW_DAYS = original

    assert "the 5 complete days" in statement
    assert "7 complete days" not in statement


def test_the_statement_names_the_window_and_the_rest_days():
    statement = training_monotony.distribution_statement(_distribution(TWO_REST_DAYS))

    assert (TODAY - datetime.timedelta(days=1)).isoformat() in statement
    assert "2 days off" in statement


def test_a_week_with_no_day_off_says_so_in_words():
    assert "no day off at all" in training_monotony.distribution_statement(
        _distribution(FLAT_HARD)
    )


# --------------------------------------------------------------------------
# The prompt section
# --------------------------------------------------------------------------


def test_the_section_speaks_only_when_the_week_is_flagged():
    assert prompts.training_monotony_section(_distribution(FLAT_HARD))
    assert prompts.training_monotony_section(_distribution(POLARISED)) == ""
    assert prompts.training_monotony_section(_distribution(FLAT_EASY)) == ""
    assert prompts.training_monotony_section(None) == ""


def test_the_section_marks_the_figures_as_computed():
    section = prompts.training_monotony_section(_distribution(FLAT_HARD))

    assert section.startswith("Training monotony (computed from recorded activities")
    assert "treat the figures as fact" in section


def test_the_section_reaches_the_coherence_block():
    """The block is the slot every plan writer already receives (#666)."""
    block = plan_context._coherence_block(
        [], TODAY, _empty_exposure(), _distribution(FLAT_HARD)
    )

    assert "Training monotony" in block


def test_the_coherence_block_is_silent_for_a_well_shaped_week():
    block = plan_context._coherence_block(
        [], TODAY, _empty_exposure(), _distribution(VARIED)
    )

    assert "Training monotony" not in block


def _empty_exposure():
    from services import run_durability

    return run_durability.run_exposure([], TODAY)


# --------------------------------------------------------------------------
# Structural: the audit must keep reaching the prompt
# --------------------------------------------------------------------------


def test_the_distribution_is_required_by_the_coherence_block():
    """No default, so a new caller has to decide rather than silently lose it.

    The failure this prevents is the one #666 was written about: a section that
    exists but reaches only some of the writers. An optional parameter makes
    forgetting it invisible.
    """
    import inspect

    parameter = inspect.signature(plan_context._coherence_block).parameters[
        "distribution"
    ]

    assert parameter.default is inspect.Parameter.empty


def test_the_windows_are_read_at_call_time_not_bound_as_defaults():
    """A default argument freezes the constant at import.

    It then looks adjustable and is not, and — the reason this is a test rather
    than a comment — the #600 register moves the module attribute, so a constant
    bound into the signature cannot be perturbed and reports itself guarded while
    nothing depends on it. Both windows slipped exactly that way before this.
    """
    import inspect

    parameters = inspect.signature(load_distribution).parameters

    for name in ("window_days", "baseline_days"):
        assert parameters[name].default is None, (
            f"{name} must default to None and read the module constant inside the "
            "function, or its policy guard is decorative"
        )


def test_moving_the_window_constant_changes_the_window():
    """The other half of the above: the indirection has to actually be read."""
    original = training_monotony.ROLLING_WINDOW_DAYS
    try:
        training_monotony.ROLLING_WINDOW_DAYS = 3
        assert _distribution(FLAT_HARD).window_days == 3
    finally:
        training_monotony.ROLLING_WINDOW_DAYS = original


def test_the_module_holds_no_load_formula():
    """Monotony describes prices; it must never set one.

    The same guard #745 carries, for the same reason: a second place that decides
    what a session cost is what the single load gate exists to prevent.
    """
    import pathlib

    source = pathlib.Path(training_monotony.__file__).read_text()
    body = source.split('"""', 2)[-1]

    for formula in ("3600", "/ 3600", "** 2", "* 100", "0.95", "ftp", "FTP"):
        assert formula not in body, f"{formula!r} is load arithmetic, not distribution"
