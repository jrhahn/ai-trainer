"""Power-derived statements are gated on sport (#711).

Every number the cycling model produces — FTP and its power–duration envelope,
watt zones, intensity factor, cycling TSS, the interval classifier — is
calibrated against cycling FTP. Run over another sport they are not approximate,
they are about a different quantity: a footpod reports real watts that have
nothing to do with the threshold the zones are cut from.

The gate used to be "does this activity have a power stream", which is not the
same question and never was. These lock in the one it is now, and the two cases
the distinction turns on: an affirmatively named non-cycling sport is withheld
from the power model, while a missing or catch-all label is evidence of nothing
and still gets it.
"""

from __future__ import annotations

import pytest

from services import analysis, prompts
from services.activity_identity import power_model_applies

DATE = "2026-10-02"
FTP = 320.0


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sport_type",
    ["cycling", "Ride", "VirtualRide", "MountainBikeRide", "GravelRide", "EBikeRide"],
)
def test_the_power_model_applies_to_bike_rides(sport_type):
    assert power_model_applies(sport_type) is True


@pytest.mark.parametrize(
    "sport_type", ["Run", "TrailRun", "WeightTraining", "Yoga", "Hike", "Swim", "Rowing"]
)
def test_the_power_model_does_not_apply_to_other_sports(sport_type):
    assert power_model_applies(sport_type) is False


@pytest.mark.parametrize("sport_type", [None, "", "   ", "Workout", "Other", "Training"])
def test_a_label_that_names_no_sport_keeps_the_power_model(sport_type):
    """A missing or catch-all label is evidence of nothing, not a claim that the
    activity was not a ride. Strava's "Workout" is an explicit catch-all, so a
    steady 300 W hour recorded under it is a bike whatever the label says."""
    assert power_model_applies(sport_type) is True


# ---------------------------------------------------------------------------
# The ride-metrics chain
# ---------------------------------------------------------------------------


def _ride_with_power(
    sport_type: str,
    *,
    activity_id: int = 90,
    watts_val: float = 260.0,
    minutes: int = 40,
    **extra,
) -> dict:
    """An activity carrying a usable power stream, in ``sport_type``.

    The case the old gate could not see: a run recorded with a footpod looks
    exactly like this.
    """
    n = minutes * 60
    ride = {
        "strava_activity_id": activity_id,
        "activity_date": DATE,
        "sport_type": sport_type,
        "duration_seconds": n,
        "streams": {
            "watts": {"data": [watts_val] * n},
            "heartrate": {"data": [150.0] * n},
            "time": {"data": [float(i) for i in range(n)]},
        },
    }
    ride.update(extra)
    return ride


@pytest.mark.parametrize("sport_type", ["Run", "WeightTraining", "Yoga"])
def test_a_non_cycling_activity_with_watts_gets_no_cycling_power_numbers(sport_type):
    metrics = analysis.build_ride_metrics_chain(
        [_ride_with_power(sport_type)], ftp=FTP
    )[0]

    assert metrics["avg_power_w"] is None
    assert metrics["normalized_power_w"] is None
    # The one that made the coach confidently wrong: watts from another sport
    # divided by the athlete's cycling threshold.
    assert metrics["intensity_factor"] is None


@pytest.mark.parametrize("sport_type", ["Run", "WeightTraining"])
def test_a_non_cycling_activity_is_not_given_a_cycling_classification(sport_type):
    metrics = analysis.build_ride_metrics_chain(
        [_ride_with_power(sport_type)], ftp=FTP
    )[0]

    assert metrics["ride_purpose"] not in {
        "endurance",
        "tempo",
        "recovery",
        "interval_threshold",
        "interval_vo2max",
        "interval_sweetspot",
        "interval_sprints",
        "mixed",
        "unknown",
    }
    # The sport was stated, so the classification is not a guess.
    assert metrics["classification_confidence"] == "high"
    assert "not a power-based bike ride" in metrics["classification_reason"]
    # And the rule-based summary quotes no watt figure for it.
    assert " W" not in metrics["summary"]


def test_a_non_cycling_activity_contributes_no_power_curve_points():
    """``perf_signals.power_curve`` is the envelope FTP inference and the
    FTP-vs-curve check are read from, so one run in the window would otherwise
    argue about a cycling threshold it knows nothing about."""
    metrics = analysis.build_ride_metrics_chain(
        [_ride_with_power("Run", watts_val=400.0)], ftp=FTP
    )[0]

    assert metrics["perf_signals"] is None


def test_a_non_cycling_activity_keeps_the_provider_load():
    """The provider computed it *for the sport it was* — intervals.icu's figure
    for a run is its own run load, not a cycling TSS. Dropping it would replace
    a better number with our own estimate on the sessions with least to go on."""
    metrics = analysis.build_ride_metrics_chain(
        [_ride_with_power("Run", _summary_tss=58.0)], ftp=FTP
    )[0]

    assert metrics["tss"] == pytest.approx(58.0)
    assert metrics["tss_source"] is not None


def test_a_provider_watt_summary_is_withheld_from_a_non_cycling_activity():
    """Provider-ness is not what makes a watt number comparable to a cycling
    FTP — the provider reports average power for a footpod run too."""
    metrics = analysis.build_ride_metrics_chain(
        [_ride_with_power("Run", _summary_avg_power_w=255.0, _summary_np_w=270.0)],
        ftp=FTP,
    )[0]

    assert metrics["avg_power_w"] is None
    assert metrics["normalized_power_w"] is None
    assert metrics["intensity_factor"] is None


def test_a_bike_ride_is_untouched():
    """The regression guard: the whole point is that cycling behaves as before."""
    metrics = analysis.build_ride_metrics_chain(
        [_ride_with_power("Ride")], ftp=FTP
    )[0]

    assert metrics["avg_power_w"] == 260
    assert metrics["normalized_power_w"] is not None
    assert metrics["intensity_factor"] is not None
    assert metrics["ride_purpose"] not in (None, "running", "strength")
    assert metrics["perf_signals"] is not None
    assert metrics["perf_signals"]["power_curve"]


# ---------------------------------------------------------------------------
# FTP estimation over a mixed history
# ---------------------------------------------------------------------------


def _steady(
    date_str: str, sport_type: str, watts_val: float = 260.0, minutes: int = 21
) -> dict:
    n = minutes * 60 + 60
    return {
        "activity_date": date_str,
        "sport_type": sport_type,
        "streams": {
            "watts": {"data": [watts_val] * n},
            "heartrate": {"data": [168.0] * n},
            "time": {"data": [float(i) for i in range(n)]},
        },
    }


def test_ftp_over_a_mixed_history_equals_ftp_over_its_cycling_subset():
    """The acceptance criterion. A hard footpod run used to raise the estimate of
    a threshold it says nothing about."""
    cycling_only = [
        _steady("2026-05-01", "Ride"),
        _steady("2026-05-12", "Ride", watts_val=280.0),
    ]
    mixed = cycling_only + [
        _steady("2026-05-05", "Run", watts_val=420.0),
        _steady("2026-05-14", "WeightTraining", watts_val=500.0),
    ]

    assert analysis.estimate_ftp_over_time(
        mixed, max_heart_rate=190
    ) == analysis.estimate_ftp_over_time(cycling_only, max_heart_rate=190)


def test_a_history_of_only_runs_yields_no_ftp_estimate():
    assert (
        analysis.estimate_ftp_over_time(
            [_steady("2026-05-01", "Run", watts_val=420.0)], max_heart_rate=190
        )
        == []
    )


def test_a_ride_with_no_sport_type_still_counts_toward_ftp():
    """Unknown is not "not a ride" — a provider that simply did not say must not
    cost the athlete their power analysis."""
    assert analysis.estimate_ftp_over_time(
        [_steady("2026-05-01", "")], max_heart_rate=190
    ) != []


def test_compute_ftp_from_streams_skips_the_sports_it_is_told_about():
    cycling = _steady("2026-05-01", "Ride")["streams"]
    run = _steady("2026-05-01", "Run", watts_val=420.0)["streams"]

    mixed, _ = analysis.compute_ftp_from_streams(
        {"1": cycling, "2": run},
        max_heart_rate=190,
        sport_by_id={"1": "Ride", "2": "Run"},
    )
    cycling_only, _ = analysis.compute_ftp_from_streams(
        {"1": cycling}, max_heart_rate=190, sport_by_id={"1": "Ride"}
    )

    assert mixed == cycling_only
    assert mixed is not None


def test_compute_ftp_from_streams_without_a_sport_map_is_unchanged():
    """Every existing caller passes nothing, and must keep behaving as before."""
    cycling = _steady("2026-05-01", "Ride")["streams"]

    assert analysis.compute_ftp_from_streams(
        {"1": cycling}, max_heart_rate=190
    ) == analysis.compute_ftp_from_streams(
        {"1": cycling}, max_heart_rate=190, sport_by_id={}
    )


# ---------------------------------------------------------------------------
# Per-ride analysis and the planned-vs-actual comparison
# ---------------------------------------------------------------------------


def _streams(minutes: int = 40, watts_val: float = 260.0) -> dict:
    n = minutes * 60
    return {
        "watts": {"data": [watts_val] * n},
        "heartrate": {"data": [150.0] * n},
        "time": {"data": [float(i) for i in range(n)]},
    }


def test_build_ride_analysis_states_the_sport_instead_of_classifying_power():
    result = analysis.build_ride_analysis(_streams(), FTP, sport_type="WeightTraining")

    assert result["ride_category"] == "strength"
    assert result["classification_confidence"] == "high"
    assert result["intervals_detected"] == []
    # No %FTP annotations, and no average power presented as a cycling figure.
    assert "avg_power_w" not in result


def test_build_ride_analysis_without_a_sport_is_unchanged():
    assert analysis.build_ride_analysis(
        _streams(), FTP
    ) == analysis.build_ride_analysis(_streams(), FTP, sport_type="Ride")


def test_planned_vs_actual_drops_the_power_half_and_keeps_heart_rate():
    """"Either skip the block or substitute the HR equivalent": heart rate means
    the same thing on a bike, on the road and in the gym."""
    planned = {
        "targetPower": {"low": 200, "high": 240},
        "targetHeartRate": {"low": 140, "high": 155},
        "durationMinutes": 40,
    }

    result = analysis.compare_planned_vs_actual(
        planned, _streams(), ftp=FTP, sport_type="Run"
    )

    for power_key in (
        "avg_power_w",
        "normalized_power_w",
        "target_power_low",
        "avg_power_delta_pct",
        "normalized_power_delta_pct",
        "time_in_zones",
        "intensity_spikes",
    ):
        assert power_key not in result
    assert result["avg_hr_bpm"] == 150
    assert result["target_hr_low"] == 140
    assert "avg_hr_delta_pct" in result


def test_planned_vs_actual_for_a_ride_is_unchanged():
    planned = {
        "targetPower": {"low": 200, "high": 240},
        "targetHeartRate": {"low": 140, "high": 155},
    }

    assert analysis.compare_planned_vs_actual(
        planned, _streams(), ftp=FTP
    ) == analysis.compare_planned_vs_actual(
        planned, _streams(), ftp=FTP, sport_type="VirtualRide"
    )


# ---------------------------------------------------------------------------
# The prompt blocks
# ---------------------------------------------------------------------------


def _activity(activity_id: int, sport_type: str, **extra) -> dict:
    activity = {
        "id": activity_id,
        "name": f"{sport_type} session",
        "type": sport_type,
        "sport_type": sport_type,
        "start_date_local": f"{DATE}T07:00:00",
    }
    activity.update(extra)
    return activity


def test_the_power_block_skips_a_non_cycling_activity():
    """These figures are handed to the coach as authoritative and it is told to
    cite them verbatim — including an intensity factor against cycling FTP."""
    block = prompts.activity_power_metrics_block(
        [
            _activity(1, "Run", average_watts=280, weighted_average_watts=295),
            _activity(2, "Ride", average_watts=201, weighted_average_watts=215),
        ],
        user_ftp=320,
    )

    assert "201 W" in block
    assert "280 W" not in block
    assert "295 W" not in block


def test_the_power_block_is_empty_when_nothing_in_the_batch_is_a_ride():
    assert (
        prompts.activity_power_metrics_block(
            [_activity(1, "Run", average_watts=280)], user_ftp=320
        )
        == ""
    )


def test_no_power_zone_block_for_a_batch_of_non_cycling_sessions():
    """The acceptance criterion: the prompt for a non-cycling session carries no
    power-zone block. Strength sessions used to get watt zones — the old gate
    only asked whether the batch was running."""
    user_msg = prompts.analyse_activities_user(
        [_activity(1, "WeightTraining"), _activity(2, "Yoga")],
        computed_section="",
        ride_analyses_section="",
        sport_type="WeightTraining",
        user_ftp=320,
    )

    assert "power zones at FTP" not in user_msg
    assert "Zone 2 / endurance ceiling" not in user_msg


def test_a_mixed_batch_keeps_the_zones_it_needs_for_its_rides():
    """A batch that is mostly runs still has to discuss the ride in it."""
    user_msg = prompts.analyse_activities_user(
        [_activity(1, "Run"), _activity(2, "Run"), _activity(3, "Ride")],
        computed_section="",
        ride_analyses_section="",
        sport_type="Run",
        user_ftp=320,
    )

    assert "power zones at FTP 320 W" in user_msg


def test_a_cycling_batch_keeps_its_power_zone_block():
    user_msg = prompts.analyse_activities_user(
        [_activity(1, "Ride")],
        computed_section="",
        ride_analyses_section="",
        sport_type="cycling",
        user_ftp=320,
    )

    assert "power zones at FTP 320 W" in user_msg
