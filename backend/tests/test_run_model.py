"""Running's own performance model: Critical Speed, D′, GAP, rTSS (#716).

Running has been in this app as a quantity of fatigue and nothing else. These
pin the model that replaces that, and — more than any single number — the places
where it **refuses** to answer: an invented Critical Speed sets the athlete's
threshold pace, their zones and every rTSS figure in their history, and it is
indistinguishable from a measured one once stored.

The synthetic athlete used throughout has CS 4,0 m/s (4:10 /km) and D′ 200 m, so
the speed held for ``t`` seconds is exactly ``4 + 200/t``. Any fit that does not
recover those two numbers from that curve is wrong, not approximate.
"""

from __future__ import annotations

import pytest

from services import run_model
from services.run_model import (
    CriticalSpeedFit,
    MIN_CS_CONFIDENCE_FOR_LOAD,
    critical_speed_confidence,
    critical_speed_from_points,
    distance_with_time,
    format_pace,
    grade_adjusted_distance,
    grade_adjusted_speed,
    grade_cost_factor,
    is_run_signals,
    mean_gap_speed,
    pace_seconds_per_km,
    pace_zone_boundaries,
    run_intensity_factor,
    run_performance_signals,
    run_training_load,
    speed_curve,
    speed_from_pace_seconds,
)

CS = 4.0
D_PRIME = 200.0


def _envelope(*minutes: float) -> dict[float, float]:
    """The synthetic athlete's maximal speed at each duration, in m/s."""
    return {m: CS + D_PRIME / (m * 60.0) for m in minutes}


def _streams(
    *,
    seconds: int,
    speed: float,
    altitude_per_sample: float = 0.0,
    hr: float | None = None,
    use_distance: bool = True,
) -> dict:
    """A run recorded at one sample per second at a constant speed."""
    time_data = list(range(seconds + 1))
    streams: dict = {"time": {"data": [float(t) for t in time_data]}}
    if use_distance:
        streams["distance"] = {"data": [float(t) * speed for t in time_data]}
    else:
        streams["velocity_smooth"] = {"data": [speed for _ in time_data]}
    if altitude_per_sample:
        streams["altitude"] = {
            "data": [float(t) * altitude_per_sample for t in time_data]
        }
    if hr is not None:
        streams["heartrate"] = {"data": [hr for _ in time_data]}
    return streams


# ---------------------------------------------------------------------------
# Pace is speed written upside down, and that is where pace code goes wrong
# ---------------------------------------------------------------------------


def test_pace_and_speed_are_inverses():
    assert pace_seconds_per_km(1000.0 / 240.0) == pytest.approx(240.0)
    assert speed_from_pace_seconds(240.0) == pytest.approx(1000.0 / 240.0)


def test_a_pace_is_rendered_as_minutes_and_seconds():
    """A pace shown as a decimal reads as 4:12 to nobody."""
    assert format_pace(1000.0 / 252.0) == "4:12 /km"
    assert format_pace(1000.0 / 300.0) == "5:00 /km"


@pytest.mark.parametrize("value", [None, 0, -1, "fast", True, float("nan")])
def test_a_non_speed_is_not_a_pace(value):
    assert pace_seconds_per_km(value) is None
    assert format_pace(value) is None
    # And the inverse direction, which is what the chain calls with a threshold
    # pace the athlete has not set.
    assert speed_from_pace_seconds(value) is None


# ---------------------------------------------------------------------------
# Grade adjustment
# ---------------------------------------------------------------------------


def test_flat_ground_costs_exactly_what_it_costs():
    assert grade_cost_factor(0.0) == pytest.approx(1.0)


def test_an_unreadable_grade_leaves_the_pace_alone():
    """A missing gradient must not adjust the pace in either direction."""
    for value in (None, "steep", True, float("nan")):
        assert grade_cost_factor(value) == 1.0


def test_climbing_costs_more_per_metre_and_the_adjustment_agrees_with_practice():
    """Minetti at +5 % and +10 % lands where published GAP implementations do."""
    assert grade_cost_factor(0.05) == pytest.approx(1.30, abs=0.03)
    assert grade_cost_factor(0.10) == pytest.approx(1.66, abs=0.05)


def test_the_equivalent_flat_speed_of_a_climb_is_faster_not_slower():
    """Getting this backwards makes every hill run look easy."""
    uphill = grade_adjusted_speed(3.0, 0.08)
    assert uphill > 3.0


def test_the_downhill_discount_stops_at_the_floor():
    """Raw Minetti prices a long descent as a recovery jog; eccentric load is real."""
    assert grade_cost_factor(-0.10) == run_model.MIN_GRADE_COST_FACTOR
    assert grade_cost_factor(-0.25) == run_model.MIN_GRADE_COST_FACTOR
    assert run_model.MIN_GRADE_COST_FACTOR < 1.0


def test_a_grade_beyond_the_measured_range_is_clamped_not_extrapolated():
    """The polynomial was fitted to ±45 %; a cliff is not a 12× cost."""
    assert grade_cost_factor(3.0) == grade_cost_factor(run_model.MAX_ABS_GRADE)


# ---------------------------------------------------------------------------
# Streams in, envelope out
# ---------------------------------------------------------------------------


def test_the_providers_own_distance_is_preferred_to_an_integrated_speed():
    """The measurement beats the integral of a smoothed stream (#466)."""
    streams = _streams(seconds=60, speed=4.0)
    streams["velocity_smooth"] = {"data": [99.0] * 61}
    distance, _ = distance_with_time(streams)
    assert distance[-1] == pytest.approx(240.0)


def test_speed_is_integrated_when_the_provider_sent_no_distance():
    distance, _ = distance_with_time(
        _streams(seconds=60, speed=4.0, use_distance=False)
    )
    assert distance[-1] == pytest.approx(240.0, rel=0.02)


def test_a_backwards_distance_step_contributes_nothing():
    """A GPS artefact is not the athlete running backwards."""
    streams = {
        "time": {"data": [0.0, 1.0, 2.0, 3.0]},
        "distance": {"data": [0.0, 4.0, 1.0, 8.0]},
    }
    distance, _ = distance_with_time(streams)
    assert distance[-1] == pytest.approx(4.0 + 7.0)


def test_a_run_with_no_distance_at_all_has_no_envelope():
    assert distance_with_time({"time": {"data": [0.0, 1.0]}}) is None
    assert run_performance_signals({"time": {"data": [0.0, 1.0]}}) is None


def test_a_field_on_its_own_clock_is_read_on_that_clock():
    """The importers store a companion time stream when the rates differ.

    A footpod run has a power stream, so the primary ``time`` is the *power*
    clock — and the speed samples, which the device records at standstill where
    power is dropped, no longer line up with it. Reading ``time`` blindly is how
    such a run lost its pace model entirely.
    """
    streams = {
        # 30 power samples on the power clock ...
        "time": {"data": [float(t) for t in range(30)]},
        "watts": {"data": [280.0] * 30},
        # ... and 61 speed samples on their own.
        "velocity_smooth": {"data": [4.0] * 61},
        "velocity_time": {"data": [float(t) for t in range(61)]},
    }
    distance, times = distance_with_time(streams)
    assert times == [float(t) for t in range(61)]
    assert distance[-1] == pytest.approx(240.0, rel=0.02)


def test_two_streams_of_equal_length_on_different_clocks_are_not_paired():
    """Equal lengths are not alignment: each metre would get someone else's hill."""
    streams = {
        "time": {"data": [float(t) for t in range(61)]},
        "distance": {"data": [float(t) * 3.0 for t in range(61)]},
        "altitude": {"data": [float(t) * 0.2 for t in range(61)]},
        "altitude_time": {"data": [float(t) * 2.0 for t in range(61)]},
    }
    assert grade_adjusted_distance(streams) is None


def test_the_best_mean_speed_is_exact_rather_than_an_average_of_samples():
    """Δd/Δt is the true mean speed however irregularly the device recorded."""
    time_stream = [0.0, 1.0, 50.0, 60.0]
    distance = [0.0, 10.0, 200.0, 240.0]
    best, _, _ = run_model.best_mean_speed(distance, time_stream, 1.0)
    assert best == pytest.approx(4.0)


def test_the_curve_only_holds_durations_the_run_was_long_enough_to_contain():
    curve = speed_curve([float(i) * 4.0 for i in range(301)], [float(i) for i in range(301)])
    assert "5" in curve
    assert "10" not in curve


def test_a_steady_run_prices_every_duration_at_its_steady_speed():
    curve = speed_curve(
        [float(i) * 3.5 for i in range(601)], [float(i) for i in range(601)]
    )
    assert curve["5"] == pytest.approx(3.5, abs=0.01)
    assert curve["10"] == pytest.approx(3.5, abs=0.01)


def test_a_one_metre_altitude_wobble_is_not_a_thirty_percent_climb():
    """Unsmoothed GPS altitude turns a flat road into alternating cliffs."""
    time_data = [float(t) for t in range(61)]
    distance = [float(t) * 3.0 for t in range(61)]
    wobble = [1.0 if t % 2 else 0.0 for t in range(61)]
    adjusted = grade_adjusted_distance(
        {
            "time": {"data": time_data},
            "distance": {"data": distance},
            "altitude": {"data": wobble},
        },
        distance,
        time_data,
    )
    assert adjusted[-1] == pytest.approx(distance[-1], rel=0.05)


def test_a_real_climb_is_adjusted_upwards():
    streams = _streams(seconds=600, speed=3.0, altitude_per_sample=0.15)
    distance, times = distance_with_time(streams)
    adjusted = grade_adjusted_distance(streams, distance, times)
    # 0,15 m of rise per 3 m of run is a 5 % gradient throughout.
    assert adjusted[-1] == pytest.approx(distance[-1] * grade_cost_factor(0.05), rel=0.02)


def test_no_altitude_means_no_adjustment_rather_than_a_silent_copy():
    """The caller has to be able to say which figure it is holding."""
    assert grade_adjusted_distance(_streams(seconds=60, speed=4.0)) is None


def test_a_misaligned_altitude_stream_is_refused():
    streams = _streams(seconds=60, speed=4.0)
    streams["altitude"] = {"data": [0.0, 1.0, 2.0]}
    assert grade_adjusted_distance(streams) is None


# ---------------------------------------------------------------------------
# The per-run signals blob
# ---------------------------------------------------------------------------


def test_a_runs_signals_are_marked_as_a_runs():
    signals = run_performance_signals(_streams(seconds=1200, speed=3.8, hr=155))
    assert is_run_signals(signals)
    assert signals["distance_m"] == pytest.approx(1200 * 3.8, rel=0.01)
    assert signals["avg_hr"] == 155


def test_a_run_never_stores_the_keys_the_cycling_engine_reads():
    """A Stryd run landing in power_curve would argue about a cycling threshold."""
    streams = _streams(seconds=1200, speed=3.8, hr=155)
    streams["watts"] = {"data": [280.0] * 1201}
    signals = run_performance_signals(streams)
    assert "power_curve" not in signals
    assert "first_half_power" not in signals
    assert "second_half_power" not in signals


def test_a_rides_signals_are_not_mistaken_for_a_runs():
    assert not is_run_signals({"power_curve": {"20": 280}})
    assert not is_run_signals(None)
    assert not is_run_signals("running")


def test_the_two_halves_of_a_run_are_recorded_for_durability():
    time_data = [float(t) for t in range(1201)]
    # 4,0 m/s for the first half, 3,5 for the second: pace decay, not a stop.
    distance = [0.0]
    for t in range(1, 1201):
        distance.append(distance[-1] + (4.0 if t <= 600 else 3.5))
    signals = run_performance_signals(
        {"time": {"data": time_data}, "distance": {"data": distance}}
    )
    assert signals["first_half_speed"] == pytest.approx(4.0, abs=0.02)
    assert signals["second_half_speed"] == pytest.approx(3.5, abs=0.02)


def test_the_mean_gap_speed_uses_the_recorded_duration_not_the_elapsed_time():
    """A coffee stop inside a long run would otherwise halve the session's load."""
    signals = {"duration_s": 7200, "moving_duration_s": 3600, "distance_m": 14400}
    assert mean_gap_speed(signals) == pytest.approx(4.0)


def test_the_mean_gap_speed_prefers_the_grade_adjusted_distance():
    signals = {"moving_duration_s": 3600, "distance_m": 14400, "gap_distance_m": 15840}
    assert mean_gap_speed(signals) == pytest.approx(4.4)


def test_a_run_with_no_altitude_is_still_priced_rather_than_dropped():
    """A flat run's GAP distance is its distance; a treadmill has no altitude."""
    signals = run_performance_signals(_streams(seconds=3600, speed=4.0))
    assert "gap_distance_m" not in signals
    assert mean_gap_speed(signals) == pytest.approx(4.0, rel=0.01)


@pytest.mark.parametrize("junk", [None, "run", 42, {"nothing": True}])
def test_a_mean_speed_cannot_be_read_out_of_junk(junk):
    assert mean_gap_speed(junk) is None


# ---------------------------------------------------------------------------
# Critical Speed and D′
# ---------------------------------------------------------------------------


def test_the_fit_recovers_the_athlete_it_was_given():
    fit = critical_speed_from_points(_envelope(2, 3, 5, 10, 20))
    assert fit.speed_m_s == pytest.approx(CS, abs=0.01)
    assert fit.d_prime_m == pytest.approx(D_PRIME, abs=1.0)
    assert fit.points_used == 5
    assert fit.max_residual < 0.001


def test_d_prime_is_kept_where_the_cycling_fit_discards_its_intercept():
    """For a runner D′ *is* the kick, not a curiosity next to CS."""
    fit = critical_speed_from_points(_envelope(2, 5, 15))
    assert fit.d_prime_m > 0
    assert fit.critical_pace_seconds_per_km == pytest.approx(1000.0 / fit.speed_m_s)


def test_threshold_pace_sits_just_below_critical_speed():
    """CS is held for ~30 min; the rTSS scale is anchored to the hour."""
    fit = critical_speed_from_points(_envelope(2, 5, 10, 20))
    assert fit.threshold_speed_m_s < fit.speed_m_s
    assert fit.threshold_speed_m_s == pytest.approx(
        fit.speed_m_s * run_model.THRESHOLD_FRACTION_OF_CRITICAL_SPEED
    )
    assert fit.threshold_pace_seconds_per_km > fit.critical_pace_seconds_per_km


def test_two_points_are_not_a_fit():
    assert critical_speed_from_points(_envelope(3, 20)) is None


def test_three_points_crowded_together_are_not_a_fit():
    """Points three minutes apart say almost nothing about the intercept."""
    assert critical_speed_from_points(_envelope(2, 3, 5)) is None


def test_a_flat_curve_is_refused_rather_than_fitted():
    """A runner who held 3-min pace for 20 min did not run a maximal 3 minutes."""
    assert critical_speed_from_points({2: 4.0, 10: 3.99, 20: 3.98}) is None


def test_an_envelope_of_only_long_efforts_cannot_pin_the_intercept():
    """10, 15 and 20 minutes fit a hyperbola cleanly and still say nothing.

    This is the region the decline gate owns on its own. A D′ of 60 m clears the
    plausibility floor and the residual is zero, but the three points lie within
    1,2 % of each other in speed — the athlete ran no short effort at all, so
    the intercept is whatever the arithmetic says rather than a measurement. The
    other guards cannot see it: with a 2-minute point, any D′ above the floor
    implies a ~9 % decline, so they only bite once something short is present.
    """
    cs, small_d_prime = 4.0, 60.0
    points = {m: cs + small_d_prime / (m * 60.0) for m in (10, 15, 20)}
    assert max(points.values()) / min(points.values()) < 1.02
    assert critical_speed_from_points(points) is None


def test_a_curve_the_hyperbola_does_not_describe_is_refused():
    """The residual test is the one that catches a model that does not fit."""
    points = dict(_envelope(2, 10, 20))
    points[10] = points[10] * 1.25
    assert critical_speed_from_points(points) is None


def test_an_implausible_d_prime_is_refused():
    """A D′ of two kilometres would mean any speed is sustainable."""
    points = {2: 12.0, 10: 5.0, 20: 4.2}
    fit = critical_speed_from_points(points)
    assert fit is None or run_model.D_PRIME_MIN_M <= fit.d_prime_m <= run_model.D_PRIME_MAX_M


def test_durations_outside_the_hyperbolic_band_are_not_used():
    """Below 2 min the effort is anaerobic; above ~20 min the curve bends."""
    assert critical_speed_from_points(_envelope(1, 30, 45, 60, 90)) is None


def test_points_outside_the_band_do_not_spoil_a_usable_fit():
    with_extras = {**_envelope(2, 5, 10, 20), 1: 8.0, 60: 3.6}
    fit = critical_speed_from_points(with_extras)
    assert fit is not None
    assert fit.points_used == 4


@pytest.mark.parametrize("junk", [None, {}, {2: None, 5: "fast", 10: 0}])
def test_an_unusable_envelope_is_refused(junk):
    assert critical_speed_from_points(junk) is None


# ---------------------------------------------------------------------------
# Confidence: the fit is never trusted more than the evidence
# ---------------------------------------------------------------------------


def test_a_wide_well_fitted_recent_envelope_earns_the_most_confidence():
    fit = critical_speed_from_points(_envelope(2, 3, 5, 10, 20))
    assert critical_speed_confidence(fit, recent_days=5) >= MIN_CS_CONFIDENCE_FOR_LOAD


def test_a_stale_envelope_is_trusted_less_than_a_fresh_one():
    fit = critical_speed_from_points(_envelope(2, 5, 20))
    assert critical_speed_confidence(fit, recent_days=120) < critical_speed_confidence(
        fit, recent_days=5
    )


def test_confidence_never_reaches_certainty():
    """We never know an effort observed in training was maximal."""
    fit = critical_speed_from_points(_envelope(2, 3, 5, 8, 10, 15, 20))
    assert critical_speed_confidence(fit, recent_days=0) <= 0.8


def test_a_worse_fitting_curve_is_trusted_less():
    good = critical_speed_from_points(_envelope(2, 5, 10, 20))
    sloppy = CriticalSpeedFit(
        speed_m_s=good.speed_m_s,
        d_prime_m=good.d_prime_m,
        points_used=good.points_used,
        span_minutes=good.span_minutes,
        max_residual=run_model.MAX_CS_SPEED_RESIDUAL,
    )
    assert critical_speed_confidence(sloppy, 5) < critical_speed_confidence(good, 5)


# ---------------------------------------------------------------------------
# rTSS
# ---------------------------------------------------------------------------


def test_an_hour_at_threshold_pace_is_one_hundred():
    """The anchor that lets rTSS, hrTSS and cycling TSS share one CTL."""
    threshold = speed_from_pace_seconds(240.0)
    assert run_training_load(
        duration_seconds=3600, gap_speed_m_s=threshold, threshold_speed_m_s=threshold
    ) == pytest.approx(100.0)


def test_rtss_matches_the_published_formula_on_a_known_fixture():
    """TrainingPeaks' rTSS for a steady hour at 5:00 /km against a 4:00 threshold.

    ``duration × NGP × IF / (FTPa × 3600) × 100`` with IF = NGP/FTPa = 0,8
    gives 64 — the same figure the quadratic form produces.
    """
    assert run_training_load(
        duration_seconds=3600,
        gap_speed_m_s=speed_from_pace_seconds(300.0),
        threshold_speed_m_s=speed_from_pace_seconds(240.0),
    ) == pytest.approx(64.0)


def test_rtss_is_quadratic_in_intensity_not_linear():
    threshold = speed_from_pace_seconds(240.0)
    easy = run_training_load(
        duration_seconds=3600,
        gap_speed_m_s=threshold * 0.5,
        threshold_speed_m_s=threshold,
    )
    assert easy == pytest.approx(25.0)


def test_a_hilly_run_is_priced_above_what_its_raw_pace_suggests():
    threshold = speed_from_pace_seconds(240.0)
    raw = speed_from_pace_seconds(300.0)
    flat = run_training_load(
        duration_seconds=3600, gap_speed_m_s=raw, threshold_speed_m_s=threshold
    )
    hilly = run_training_load(
        duration_seconds=3600,
        gap_speed_m_s=grade_adjusted_speed(raw, 0.06),
        threshold_speed_m_s=threshold,
    )
    assert hilly > flat


def test_a_bad_stream_is_capped_rather_than_allowed_to_spike_atl():
    """A GPS jump is not a 40 km/h hour, but the run still happened."""
    threshold = speed_from_pace_seconds(240.0)
    assert run_intensity_factor(
        gap_speed_m_s=threshold * 5, threshold_speed_m_s=threshold
    ) == run_model.MAX_RUN_INTENSITY_FACTOR


def test_without_a_threshold_pace_there_is_no_rtss():
    """The ladder must fall to heart rate rather than assume a threshold."""
    assert (
        run_training_load(
            duration_seconds=3600, gap_speed_m_s=4.0, threshold_speed_m_s=None
        )
        is None
    )
    assert (
        run_training_load(
            duration_seconds=3600, gap_speed_m_s=None, threshold_speed_m_s=4.0
        )
        is None
    )
    assert (
        run_training_load(
            duration_seconds=None, gap_speed_m_s=4.0, threshold_speed_m_s=4.0
        )
        is None
    )


# ---------------------------------------------------------------------------
# Pace zones
# ---------------------------------------------------------------------------


def test_the_zones_cover_the_scale_from_open_below_to_open_above():
    zones = pace_zone_boundaries(speed_from_pace_seconds(240.0))
    assert len(zones) == len(run_model.PACE_ZONE_NAMES)
    assert zones[0]["slow_pace_seconds_per_km"] is None
    assert zones[-1]["fast_pace_seconds_per_km"] is None


def test_a_faster_zone_has_a_smaller_pace_number():
    """The inversion trap: the zone with the higher speed bound reads lower."""
    zones = pace_zone_boundaries(speed_from_pace_seconds(240.0))
    paces = [z["fast_pace_seconds_per_km"] for z in zones if z["fast_pace_seconds_per_km"]]
    assert paces == sorted(paces, reverse=True)


def test_each_zone_is_contiguous_with_the_next():
    zones = pace_zone_boundaries(speed_from_pace_seconds(240.0))
    for lower, upper in zip(zones, zones[1:]):
        assert lower["fast_pace_seconds_per_km"] == upper["slow_pace_seconds_per_km"]


def test_threshold_pace_lands_inside_the_threshold_zone():
    threshold_pace = 240.0
    zones = pace_zone_boundaries(speed_from_pace_seconds(threshold_pace))
    threshold_zone = next(z for z in zones if z["name"] == "Threshold")
    assert (
        threshold_zone["fast_pace_seconds_per_km"]
        <= threshold_pace
        <= threshold_zone["slow_pace_seconds_per_km"]
    )


@pytest.mark.parametrize("value", [None, 0, -4.0, "fast"])
def test_no_threshold_means_no_zones(value):
    assert pace_zone_boundaries(value) == []


def test_the_tail_of_a_run_keeps_the_previous_gradient():
    """A twelve-metre remainder must not be handed a gradient of its own.

    The last stretch of a run is almost never a whole segment long. Computing a
    gradient over it gives the noisiest possible answer for the smallest
    possible distance — and dropping it would lose metres from the total. Here
    the altitude jumps 50 m inside that remainder, which on its own would price
    the final twelve metres at the clamped maximum.
    """
    samples = 45  # 3 m apart: four whole 30 m segments plus a 12 m tail
    time_data = [float(t) for t in range(samples)]
    distance = [float(t) * 3.0 for t in range(samples)]
    altitude = [0.0] * 41 + [50.0] * (samples - 41)
    adjusted = grade_adjusted_distance(
        {
            "time": {"data": time_data},
            "distance": {"data": distance},
            "altitude": {"data": altitude},
        },
        distance,
        time_data,
    )
    assert adjusted[-1] == pytest.approx(distance[-1], rel=0.01)


# ---------------------------------------------------------------------------
# What the readers do with input that is not what they asked for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, 0, -2.0, "slow", True])
def test_a_non_speed_cannot_be_grade_adjusted(value):
    assert grade_adjusted_speed(value, 0.05) is None


@pytest.mark.parametrize(
    "streams",
    [
        None,
        "velocity_smooth",
        42,
        {"time": "not a stream"},
        {"time": {"data": "not a list"}},
        {"time": {}},
    ],
)
def test_a_stream_payload_that_is_not_one_reads_as_empty(streams):
    """Provider payloads arrive malformed; one bad file must not raise."""
    assert run_model._numeric_stream(streams, "time") == []
    assert distance_with_time(streams if isinstance(streams, dict) else {}) is None


def test_a_single_sample_is_not_a_stream():
    """One point has no duration, so it cannot carry a distance or a pace."""
    assert distance_with_time({"time": {"data": [0.0]}, "distance": {"data": [0.0]}}) is None


def test_non_numeric_samples_are_dropped_rather_than_crashing():
    values = run_model._numeric_stream(
        {"time": {"data": [0.0, "x", None, 2.0, True, 3]}}, "time"
    )
    assert values == [0.0, 2.0, 3.0]


def test_grade_adjustment_of_a_run_with_no_distance_is_refused():
    assert grade_adjusted_distance({"altitude": {"data": [0.0, 1.0, 2.0]}}) is None


@pytest.mark.parametrize(
    ("distance", "times", "minutes"),
    [
        ([], [], 1.0),
        ([0.0, 1.0], [0.0], 1.0),
        ([0.0, 1.0], [0.0, 1.0], 0.0),
        ([0.0, 1.0], [0.0, 1.0], -5.0),
    ],
)
def test_a_window_that_cannot_be_measured_has_no_best_speed(distance, times, minutes):
    assert run_model.best_mean_speed(distance, times, minutes) == (None, 0, 0)


@pytest.mark.parametrize(
    ("distance", "times"),
    [([], []), ([0.0, 4.0], [0.0])],
)
def test_a_curve_cannot_be_cut_from_mismatched_streams(distance, times):
    assert speed_curve(distance, times) == {}


def test_a_run_that_covered_no_ground_has_no_signals():
    """A recording with a clock and no movement is a reading, not a run."""
    standing = {
        "time": {"data": [0.0, 1.0, 2.0]},
        "distance": {"data": [0.0, 0.0, 0.0]},
    }
    assert run_performance_signals(standing) is None


def test_a_run_whose_clock_never_advances_has_no_signals():
    frozen = {
        "time": {"data": [5.0, 5.0, 5.0]},
        "distance": {"data": [0.0, 4.0, 8.0]},
    }
    assert run_performance_signals(frozen) is None


def test_a_hilly_runs_signals_carry_both_envelopes():
    """The grade-adjusted curve is what the Critical Speed fit reads."""
    signals = run_performance_signals(
        _streams(seconds=1200, speed=3.0, altitude_per_sample=0.15)
    )
    assert signals["gap_distance_m"] > signals["distance_m"]
    assert signals["gap_speed_curve"]["10"] > signals["speed_curve"]["10"]


def test_a_d_prime_beyond_a_runner_is_refused_outright():
    """Decisive, not "either None or in range": the bound has to be the reason.

    A 2 km D′ would mean the athlete can run two kilometres at any speed at all.
    """
    cs, absurd = 4.0, 2000.0
    points = {m: cs + absurd / (m * 60.0) for m in (2, 5, 10, 20)}
    fit = critical_speed_from_points(points)
    assert fit is None
    # And the same curve with a plausible D′ is accepted, so it is the bound
    # doing the refusing and not some other guard.
    plausible = {m: cs + 250.0 / (m * 60.0) for m in (2, 5, 10, 20)}
    assert critical_speed_from_points(plausible) is not None


def test_a_curve_whose_distance_falls_with_time_is_refused():
    """A negative Critical Speed is arithmetic, not a runner.

    Speed falling faster than 1/t means the athlete covered *less* ground in
    twenty minutes than in two, so the least-squares slope through
    ``distance = CS × t + D′`` comes out negative. The curve is well formed —
    it descends steeply and spans the band — so no earlier guard sees it.
    """
    points = {2: 100.0, 10: 5.0, 20: 1.0}
    assert points[2] / points[20] > run_model.MIN_CS_CURVE_DECLINE
    assert critical_speed_from_points(points) is None


def test_a_critical_speed_far_below_the_slowest_effort_is_refused():
    """A 20-minute maximal effort is run a few percent above CS, not 25 % above.

    A slow runner with a large D′ passes the D′ bound and still implies a CS the
    longest effort cannot be reconciled with — the fit extrapolated rather than
    interpolated, which is what this bound exists to catch.
    """
    cs, big_d_prime = 2.5, 900.0
    points = {m: cs + big_d_prime / (m * 60.0) for m in (2, 5, 10, 20)}
    longest = points[20]
    # The D′ bound is satisfied, so it is the CS bound doing the refusing.
    assert run_model.D_PRIME_MIN_M <= big_d_prime <= run_model.D_PRIME_MAX_M
    assert cs < longest * run_model.CS_LOWER_BOUND_OF_LONGEST
    assert critical_speed_from_points(points) is None
