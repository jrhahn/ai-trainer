"""The load ladder: how much did this session cost, and how do we know? (#579)

Zero used to be the answer whenever power was missing. Zero is not a gap in the
data, it is the claim that the athlete rested — which is why an hour of strength
training made the app report them *fresher*.
"""

from __future__ import annotations

import pytest

from services.training_load import (
    LOAD_SOURCE_DURATION,
    LOAD_SOURCE_HEART_RATE,
    LOAD_SOURCE_POWER,
    LOAD_SOURCE_PROVIDER,
    duration_training_load,
    format_load,
    format_load_field,
    hr_training_load,
    resolve_training_load,
)

MAX_HR = 185
RESTING_HR = 50


# --- The ladder, rung by rung ---------------------------------------------


def test_the_provider_figure_wins_over_everything_we_could_derive():
    """It was computed from full-resolution data we never received."""
    load = resolve_training_load(
        provider_tss=88.0,
        power_tss=94.0,
        duration_seconds=3600,
        sport_type="Ride",
        avg_hr_bpm=150,
        max_heart_rate=MAX_HR,
    )
    assert (load.tss, load.source) == (88.0, LOAD_SOURCE_PROVIDER)


def test_power_wins_over_heart_rate():
    load = resolve_training_load(
        power_tss=94.0,
        duration_seconds=3600,
        sport_type="Ride",
        avg_hr_bpm=150,
        max_heart_rate=MAX_HR,
    )
    assert (load.tss, load.source) == (94.0, LOAD_SOURCE_POWER)


def test_heart_rate_answers_when_there_is_no_power():
    load = resolve_training_load(
        duration_seconds=58 * 60,
        sport_type="WeightTraining",
        avg_hr_bpm=118,
        max_heart_rate=MAX_HR,
        resting_heart_rate=RESTING_HR,
    )
    assert load is not None
    assert load.source == LOAD_SOURCE_HEART_RATE
    assert load.tss > 0


def test_duration_is_the_last_rung_before_zero():
    """A gym session that shipped no HR at all still is not a rest day."""
    load = resolve_training_load(
        duration_seconds=58 * 60,
        sport_type="WeightTraining",
    )
    assert load is not None
    assert load.source == LOAD_SOURCE_DURATION
    assert load.tss > 0


def test_no_duration_means_no_load_rather_than_an_invented_one():
    """Nothing to work from is not the same as nothing happening — but with no
    duration there is no honest estimate either, so the caller gets None and
    leaves the load unset."""
    assert resolve_training_load(sport_type="WeightTraining") is None
    assert resolve_training_load(duration_seconds=0, sport_type="Yoga") is None


def test_a_derived_load_never_displaces_a_measured_one():
    """The acceptance criterion stated as a property: adding heart rate and a
    sport to the inputs cannot change an answer that power already gave."""
    with_power_only = resolve_training_load(power_tss=94.0, duration_seconds=3600)
    with_everything = resolve_training_load(
        power_tss=94.0,
        duration_seconds=3600,
        sport_type="WeightTraining",
        avg_hr_bpm=170,
        max_heart_rate=MAX_HR,
        resting_heart_rate=RESTING_HR,
    )
    assert with_power_only == with_everything


def test_measured_and_estimated_are_distinguishable():
    measured = resolve_training_load(provider_tss=88.0)
    estimated = resolve_training_load(duration_seconds=3600, sport_type="Yoga")
    assert measured is not None and estimated is not None
    assert measured.is_measured
    assert not estimated.is_measured


# --- hrTSS -----------------------------------------------------------------


def test_an_hour_at_threshold_heart_rate_is_about_one_hundred():
    """The anchor the whole scale hangs off."""
    lthr = round(MAX_HR * 0.87)
    load = hr_training_load(
        duration_seconds=3600,
        avg_hr_bpm=lthr,
        max_heart_rate=MAX_HR,
        resting_heart_rate=RESTING_HR,
    )
    assert load == pytest.approx(100, abs=2)


def test_easy_work_costs_much_less_than_hard_work():
    easy = hr_training_load(
        duration_seconds=3600, avg_hr_bpm=115, max_heart_rate=MAX_HR
    )
    hard = hr_training_load(
        duration_seconds=3600, avg_hr_bpm=165, max_heart_rate=MAX_HR
    )
    assert easy is not None and hard is not None
    assert easy < hard / 2


def test_heart_rate_reserve_is_used_when_a_resting_rate_is_known():
    """A fraction of maximum overstates easy work, and squaring doubles the
    error — 118 bpm is 64 % of a 185 max but only 50 % of reserve."""
    without_resting = hr_training_load(
        duration_seconds=3600, avg_hr_bpm=118, max_heart_rate=MAX_HR
    )
    with_resting = hr_training_load(
        duration_seconds=3600,
        avg_hr_bpm=118,
        max_heart_rate=MAX_HR,
        resting_heart_rate=RESTING_HR,
    )
    assert without_resting is not None and with_resting is not None
    assert with_resting < without_resting


def test_an_absurd_reading_is_capped_not_believed():
    """A dropout or a mis-paired strap must not spike ATL. The session still
    happened, so it is capped rather than discarded."""
    capped = hr_training_load(
        duration_seconds=3600, avg_hr_bpm=MAX_HR * 3, max_heart_rate=MAX_HR
    )
    ceiling = hr_training_load(
        duration_seconds=3600, avg_hr_bpm=round(MAX_HR * 1.15 * 0.87),
        max_heart_rate=MAX_HR,
    )
    assert capped is not None and ceiling is not None
    assert capped == pytest.approx(ceiling, abs=2)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration_seconds": None, "avg_hr_bpm": 140, "max_heart_rate": MAX_HR},
        {"duration_seconds": 3600, "avg_hr_bpm": None, "max_heart_rate": MAX_HR},
        {"duration_seconds": 3600, "avg_hr_bpm": 140, "max_heart_rate": None},
        # An average at or below resting is a reading, not a session.
        {
            "duration_seconds": 3600,
            "avg_hr_bpm": 45,
            "max_heart_rate": MAX_HR,
            "resting_heart_rate": RESTING_HR,
        },
    ],
)
def test_heart_rate_declines_to_answer_without_the_inputs(kwargs):
    assert hr_training_load(**kwargs) is None


# --- The duration rung -----------------------------------------------------


def test_sport_decides_the_assumed_intensity():
    hour = 3600
    running = duration_training_load(duration_seconds=hour, sport_type="Run")
    yoga = duration_training_load(duration_seconds=hour, sport_type="Yoga")
    assert running is not None and yoga is not None
    assert running > yoga


def test_an_unknown_sport_still_gets_a_load():
    """Not knowing the sport is a reason to be conservative, not to claim rest."""
    load = duration_training_load(duration_seconds=3600, sport_type="Kitesurfing")
    assert load is not None and load > 0


# --- Presentation ----------------------------------------------------------


def test_a_measured_load_reads_as_tss_and_an_estimate_does_not():
    assert format_load(94, LOAD_SOURCE_POWER) == "TSS 94"
    assert format_load(88, LOAD_SOURCE_PROVIDER) == "TSS 88"
    assert format_load(28, LOAD_SOURCE_HEART_RATE) == "load ~28 (estimated from HR)"
    assert (
        format_load(34, LOAD_SOURCE_DURATION)
        == "load ~34 (estimated from duration)"
    )
    assert format_load(None, LOAD_SOURCE_POWER) is None


def test_the_labelled_form_does_not_call_an_estimate_tss():
    """Calling it "TSS" is exactly the conflation this exists to prevent."""
    assert format_load_field(94, LOAD_SOURCE_POWER) == "TSS: 94"
    assert format_load_field(28, LOAD_SOURCE_HEART_RATE) == (
        "Load: ~28 (estimated from HR)"
    )


def test_a_load_with_no_recorded_source_is_shown_as_measured():
    """Legacy rows written before the column existed only ever held power or
    provider figures, so this is the truthful default rather than a guess."""
    assert format_load(94, None) == "TSS 94"
