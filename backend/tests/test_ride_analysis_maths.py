"""The ride maths in services/analysis: power, heart rate, intervals, load, readiness, and how a ride is classified.

Split out of ``test_ai_service_unit.py`` (ai-trainer-ops#259); its history is there.
"""

from __future__ import annotations



import services.analysis as analysis


# ---------------------------------------------------------------------------
# _best_n_min_power
# ---------------------------------------------------------------------------


def test_best_n_min_power_empty():
    result, s, e = analysis.best_n_min_power([], [], 20)
    assert result is None
    assert s == 0 and e == 0


def test_best_n_min_power_mismatched_lengths():
    result, s, e = analysis.best_n_min_power([200.0, 210.0], [0], 20)
    assert result is None


def test_best_n_min_power_window_too_short():
    # Only 5 seconds of data — can't fill a 20-min window (1200 s × 90% = 1080 s needed)
    watts = [300.0] * 5
    time_stream = list(range(5))
    result, s, e = analysis.best_n_min_power(watts, time_stream, 20)
    assert result is None


def test_best_n_min_power_exact_window():
    # 20 minutes of data at constant 300 W — best 20-min should equal 300
    watts = [300.0] * 1200
    time_stream = list(range(1200))
    result, s, e = analysis.best_n_min_power(watts, time_stream, 20)
    assert result is not None
    assert abs(result - 300.0) < 1.0


def test_best_n_min_power_finds_peak():
    # Mostly 200 W but one 20-min block at 300 W in the middle
    baseline = [200.0] * 600
    peak = [300.0] * 1200
    rest = [200.0] * 600
    watts = baseline + peak + rest
    time_stream = list(range(len(watts)))
    result, s, e = analysis.best_n_min_power(watts, time_stream, 20)
    assert result is not None
    assert result >= 290.0  # should be close to the peak block


# ---------------------------------------------------------------------------
# _hr_corrected_ftp
# ---------------------------------------------------------------------------


def test_hr_corrected_ftp_zero_hr():
    assert analysis.hr_corrected_ftp(300.0, 0.0, 190) is None


def test_hr_corrected_ftp_zero_max_hr():
    assert analysis.hr_corrected_ftp(300.0, 160.0, 0) is None


def test_hr_corrected_ftp_below_70pct():
    # interval_hr = 100 bpm, max_hr = 190 → hr_fraction = 0.526 → < 0.70
    assert analysis.hr_corrected_ftp(300.0, 100.0, 190) is None


def test_hr_corrected_ftp_above_100pct():
    # interval_hr > max_hr → hr_fraction > 1.0
    assert analysis.hr_corrected_ftp(300.0, 200.0, 190) is None


def test_hr_corrected_ftp_at_lthr():
    # interval_hr exactly at LTHR (0.87 × max_hr) → correction factor = 1.0
    max_hr = 190
    lthr = round(max_hr * analysis.LTHR_RATIO)
    result = analysis.hr_corrected_ftp(300.0, float(lthr), max_hr)
    assert result is not None
    assert abs(result - 300) <= 2  # should be ~300 W


def test_hr_corrected_ftp_below_lthr():
    # Interval HR below LTHR → rider had headroom → corrected FTP > interval power
    result = analysis.hr_corrected_ftp(280.0, 150.0, 190)
    assert result is not None
    assert result > 280


# ---------------------------------------------------------------------------
# _compute_hr_zones
# ---------------------------------------------------------------------------


def test_compute_hr_zones_structure():
    zones = analysis.compute_hr_zones(200)
    assert set(zones.keys()) == {"zone1", "zone2", "zone3", "zone4", "zone5"}
    for zone in zones.values():
        assert "low" in zone and "high" in zone


def test_compute_hr_zones_boundaries():
    max_hr = 200
    zones = analysis.compute_hr_zones(max_hr)
    # Zone 1 low should be 0, zone 5 high should be max_hr
    assert zones["zone1"]["low"] == 0
    assert zones["zone5"]["high"] == max_hr
    # Zones should be monotonically increasing
    boundaries = [
        zones["zone1"]["high"],
        zones["zone2"]["high"],
        zones["zone3"]["high"],
        zones["zone4"]["high"],
        zones["zone5"]["high"],
    ]
    assert boundaries == sorted(boundaries)


def test_compute_hr_zones_values():
    zones = analysis.compute_hr_zones(180)
    assert zones["zone1"]["high"] == round(180 * 0.60)
    assert zones["zone2"]["low"] == round(180 * 0.60)
    assert zones["zone2"]["high"] == round(180 * 0.70)
    assert zones["zone5"]["low"] == round(180 * 0.90)


# ---------------------------------------------------------------------------
# _classify_ride_purpose — additional branches
# ---------------------------------------------------------------------------


def test_classify_ride_purpose_empty_watts():
    # Should not raise; insufficient data should not be treated as endurance.
    result = analysis.classify_ride_purpose([], [], 250)
    assert result == "unknown"


def test_classify_ride_purpose_zero_ftp():
    watts = [200.0] * 600
    ts = list(range(600))
    result = analysis.classify_ride_purpose(watts, ts, 0)
    assert result == "unknown"


def test_classify_ride_purpose_missing_watts_with_duration():
    ts = list(range(5 * 60))
    result = analysis.classify_ride_purpose([], ts, 250)
    assert result == "unknown"


def test_classify_ride_purpose_short_easy_spin():
    ftp = 250
    watts = [round(ftp * 0.68)] * (15 * 60)
    ts = list(range(len(watts)))
    result = analysis.classify_ride_purpose(watts, ts, ftp)
    assert result == "short_easy_spin"


def test_classify_ride_purpose_short_hard_effort():
    ftp = 250
    watts = [round(ftp * 0.80)] * (20 * 60)
    ts = list(range(len(watts)))
    result = analysis.classify_ride_purpose(watts, ts, ftp)
    assert result == "short_hard_effort"


def test_classify_ride_purpose_long_z2_still_endurance():
    ftp = 250
    watts = [round(ftp * 0.68)] * (60 * 60)
    ts = list(range(len(watts)))
    result = analysis.classify_ride_purpose(watts, ts, ftp)
    assert result == "endurance"


def test_classify_ride_purpose_sweetspot():
    """10-20 min efforts at 88-95% FTP should be classified as sweetspot."""
    ftp = 250
    work = round(ftp * 0.91)  # 91% FTP — sweetspot
    rest = round(ftp * 0.50)
    watts: list[float] = []
    # 3 × 15 min at sweetspot
    for _ in range(3):
        watts.extend([work] * 900)  # 15 min
        watts.extend([rest] * 300)  # 5 min rest
    ts = list(range(len(watts)))
    result = analysis.classify_ride_purpose(watts, ts, ftp)
    assert result == "interval_sweetspot"


def test_classify_ride_purpose_threshold():
    """5-12 min efforts at 95-105% FTP should be classified as threshold."""
    ftp = 250
    work = round(ftp * 1.00)  # exactly 100% FTP
    rest = round(ftp * 0.50)
    watts: list[float] = []
    # 4 × 8 min at threshold
    for _ in range(4):
        watts.extend([work] * 480)  # 8 min
        watts.extend([rest] * 240)  # 4 min rest
    ts = list(range(len(watts)))
    result = analysis.classify_ride_purpose(watts, ts, ftp)
    assert result == "interval_threshold"


def test_classify_ride_purpose_mixed():
    """A ride with both VO2max and sprint intervals should be 'mixed'."""
    ftp = 250
    vo2_power = round(ftp * 1.15)
    sprint_power = round(ftp * 1.45)
    rest = round(ftp * 0.40)
    watts: list[float] = []
    # 3 × 4 min VO2max
    for _ in range(3):
        watts.extend([vo2_power] * 240)
        watts.extend([rest] * 300)
    # 4 × 90 s sprints (> min_interval_secs=30, duration < 2 min → sprint category)
    for _ in range(4):
        watts.extend([sprint_power] * 90)
        watts.extend([rest] * 240)
    ts = list(range(len(watts)))
    result = analysis.classify_ride_purpose(watts, ts, ftp)
    assert result == "mixed"


def test_classify_ride_purpose_intervals_below_thresholds():
    """Short catch-all efforts (< 2 min, power 85-130% FTP) are classified as 'interval_sprints'."""
    ftp = 250
    # Power at exactly 86% FTP — above detection threshold (85%) but below the
    # primary sprint threshold (130%). Duration 60 s (< 2 min) routes to
    # the catch-all sprint branch in _classify_ride_purpose.
    work = round(ftp * 0.86)
    rest = round(ftp * 0.40)
    watts: list[float] = []
    for _ in range(5):
        watts.extend([work] * 60)   # 60 s effort (< 2 min)
        watts.extend([rest] * 120)  # 2 min rest
    ts = list(range(len(watts)))
    result = analysis.classify_ride_purpose(watts, ts, ftp)
    # Catch-all branch: pct=0.86>0.85 and dur_min<2 → "sprint" → "interval_sprints"
    assert result == "interval_sprints"


# ---------------------------------------------------------------------------
# _detect_intervals — edge cases
# ---------------------------------------------------------------------------


def test_detect_intervals_empty():
    assert analysis.detect_intervals([], [], 250) == []


def test_detect_intervals_zero_ftp():
    watts = [300.0] * 600
    ts = list(range(600))
    assert analysis.detect_intervals(watts, ts, 0) == []


def test_detect_intervals_merges_short_gaps():
    """Two intervals separated by a brief recovery should be merged."""
    ftp = 250
    work = round(ftp * 1.10)
    rest = round(ftp * 0.40)
    # 5 min work, 20 s rest (< recovery_gap_secs=30), 5 min work
    watts: list[float] = [work] * 300 + [rest] * 20 + [work] * 300
    ts = list(range(len(watts)))
    intervals = analysis.detect_intervals(watts, ts, ftp)
    # The two efforts should be merged into one
    assert len(intervals) == 1


def test_detect_intervals_ends_in_high_power():
    """Interval block that ends at the last sample should still be detected."""
    ftp = 250
    work = round(ftp * 1.10)
    rest = round(ftp * 0.40)
    watts: list[float] = [rest] * 300 + [work] * 300
    ts = list(range(len(watts)))
    intervals = analysis.detect_intervals(watts, ts, ftp)
    assert len(intervals) == 1
    assert intervals[0]["duration_secs"] >= 270


# ---------------------------------------------------------------------------
# _build_ride_analysis — edge cases
# ---------------------------------------------------------------------------


def test_build_ride_analysis_empty_streams():
    result = analysis.build_ride_analysis({}, 250.0)
    assert result == {}


def test_build_ride_analysis_no_time_data():
    streams = {"watts": {"data": [200.0] * 600}}
    result = analysis.build_ride_analysis(streams, 250.0)
    assert result == {}


def test_build_ride_analysis_no_hr_data():
    """Without HR data the function should still return a result."""
    ftp = 250.0
    watts = [ftp * 0.70] * 3600
    ts = list(range(3600))
    streams = {"watts": {"data": watts}, "time": {"data": ts}}
    result = analysis.build_ride_analysis(streams, ftp)
    assert "ride_category" in result
    assert result["ride_category"] == "endurance"
    assert result["duration_seconds"] == 3600


def test_build_ride_analysis_missing_watts_returns_unknown_with_duration():
    ts = list(range(5 * 60))
    streams = {"time": {"data": ts}}
    result = analysis.build_ride_analysis(streams, 250.0)
    assert result["ride_category"] == "unknown"
    assert result["duration_seconds"] == 300
    assert result["intervals_detected"] == []


def test_build_rule_based_summary_short_categories():
    summary = analysis.build_rule_based_summary(
        "short_easy_spin",
        15 * 60,
        normalized_power=170,
        tss=8,
        intervals=[],
    )
    assert summary.startswith("Short easy spin")


def test_build_ride_metrics_chain_missing_watts_marks_unknown():
    result = analysis.build_ride_metrics_chain(
        [
            {
                "strava_activity_id": 123,
                "activity_date": "2026-05-01",
                "sport_type": "cycling",
                "duration_seconds": 5 * 60,
                "streams": {"time": {"data": list(range(5 * 60))}},
            }
        ],
        ftp=250,
    )
    assert result[0]["ride_purpose"] == "unknown"
    assert result[0]["summary"].startswith("Unknown ride")


# ---------------------------------------------------------------------------
# _compute_hr_drift — short segment
# ---------------------------------------------------------------------------


def test_compute_hr_drift_too_short():
    assert analysis.compute_hr_drift([150.0, 155.0]) is None


def test_compute_hr_drift_flat():
    # Exactly flat HR — slope should be ~0
    hr = [150.0] * 100
    drift = analysis.compute_hr_drift(hr)
    assert drift is not None
    assert abs(drift) < 0.001


# ---------------------------------------------------------------------------
# compare_planned_vs_actual (analysis.py)
# ---------------------------------------------------------------------------


def test_compare_planned_vs_actual_empty_streams():
    """Returns empty dict when streams have no data."""
    result = analysis.compare_planned_vs_actual(
        planned={"workoutType": "endurance", "durationMinutes": 60},
        streams={},
    )
    assert result == {}


def test_compare_planned_vs_actual_avg_power():
    """avg_power_w is the mean of the watts stream."""
    watts = [200.0, 210.0, 220.0]
    time_stream = [0.0, 1.0, 2.0]
    result = analysis.compare_planned_vs_actual(
        planned={},
        streams={"watts": {"data": watts}, "time": {"data": time_stream}},
    )
    assert result["avg_power_w"] == round(sum(watts) / len(watts))


def test_compare_planned_vs_actual_power_delta():
    """avg_power_delta_pct reflects deviation from target midpoint."""
    # Target midpoint = (190 + 220) / 2 = 205 W
    # Actual avg = 230 W → delta = (230 - 205) / 205 * 100 ≈ +12.2 %
    watts = [230.0] * 10
    time_stream = list(range(10))
    result = analysis.compare_planned_vs_actual(
        planned={"targetPower": {"low": 190, "high": 220}},
        streams={"watts": {"data": watts}, "time": {"data": time_stream}},
    )
    assert result["target_power_low"] == 190
    assert result["target_power_high"] == 220
    assert result["avg_power_delta_pct"] > 0  # over target


def test_compare_planned_vs_actual_time_in_zones():
    """time_in_zones sums to approximately total ride duration."""
    ftp = 200.0
    # 10 seconds each at Z1 (100W), Z2 (130W), Z3 (170W) power
    watts = [100.0] * 10 + [130.0] * 10 + [170.0] * 10
    time_stream = list(range(30))
    result = analysis.compare_planned_vs_actual(
        planned={},
        streams={"watts": {"data": watts}, "time": {"data": time_stream}},
        ftp=ftp,
    )
    tiz = result["time_in_zones"]
    # 100W = 50% FTP → Z1; 130W = 65% FTP → Z2; 170W = 85% FTP → Z3
    assert tiz["z1_secs"] > 0
    assert tiz["z2_secs"] > 0
    assert tiz["z3_secs"] > 0
    # Total time in zones should be close to 29 seconds (30 samples, last sample uses dt=1)
    total = sum(tiz.values())
    assert total >= 28


def test_compare_planned_vs_actual_hr_drift():
    """hr_drift_bpm is positive when HR rises steadily."""
    watts = [200.0] * 20
    time_stream = list(range(20))
    # HR rises from 140 to 159 bpm (steady drift)
    hr = [140.0 + i for i in range(20)]
    result = analysis.compare_planned_vs_actual(
        planned={},
        streams={
            "watts": {"data": watts},
            "time": {"data": time_stream},
            "heartrate": {"data": hr},
        },
    )
    assert result["avg_hr_bpm"] is not None
    # Positive drift expected
    assert result.get("hr_drift_bpm", 0) > 0


def test_compare_planned_vs_actual_intensity_spikes():
    """intensity_spikes is populated when power significantly exceeds target."""
    # 1000 seconds at 250 W, 25 % over the midpoint of a 180–220 W target
    watts = [250.0] * 1000
    time_stream = list(range(1000))
    result = analysis.compare_planned_vs_actual(
        planned={"targetPower": {"low": 180, "high": 220}},
        streams={"watts": {"data": watts}, "time": {"data": time_stream}},
    )
    spikes = result.get("intensity_spikes", [])
    assert len(spikes) > 0
    assert spikes[0]["pct_over_target"] > 10.0


def test_compare_planned_vs_actual_no_spikes_within_target():
    """intensity_spikes is empty when power is within the target range."""
    watts = [205.0] * 1000  # within 180–220 W target
    time_stream = list(range(1000))
    result = analysis.compare_planned_vs_actual(
        planned={"targetPower": {"low": 180, "high": 220}},
        streams={"watts": {"data": watts}, "time": {"data": time_stream}},
    )
    assert result.get("intensity_spikes", []) == []


# ---------------------------------------------------------------------------
# _compute_training_load (Task 1)
# ---------------------------------------------------------------------------


def test_compute_training_load_empty():
    result = analysis.compute_training_load([], 250.0)
    assert result == {
        "ctl": 0.0,
        "atl": 0.0,
        "tsb": 0.0,
        "daily_tss": [],
        "ctl_by_sport": {},
    }


def test_compute_training_load_zero_ftp():
    plan = [{"durationMinutes": 60, "workoutType": "endurance"}]
    result = analysis.compute_training_load(plan, 0.0)
    assert result == {
        "ctl": 0.0,
        "atl": 0.0,
        "tsb": 0.0,
        "daily_tss": [],
        "ctl_by_sport": {},
    }


def test_compute_training_load_returns_expected_keys():
    plan = [
        {"durationMinutes": 90, "workoutType": "endurance"},
        {"durationMinutes": 0, "workoutType": "rest"},
        {"durationMinutes": 60, "workoutType": "intervals", "targetPower": {"low": 280, "high": 320}},
    ]
    result = analysis.compute_training_load(plan, 300.0)
    assert "ctl" in result and "atl" in result and "tsb" in result and "daily_tss" in result
    assert len(result["daily_tss"]) == 3
    # Rest day should produce 0 TSS
    assert result["daily_tss"][1] == 0.0
    # TSB = CTL - ATL (may differ by up to 0.2 due to independent rounding)
    assert abs(result["tsb"] - (result["ctl"] - result["atl"])) < 0.2


def test_compute_training_load_target_power():
    """When targetPower is given, TSS should use mid-point for IF calculation."""
    plan = [{"durationMinutes": 60, "workoutType": "endurance", "targetPower": {"low": 240, "high": 260}}]
    result = analysis.compute_training_load(plan, 250.0)
    # mid-point = 250W, FTP = 250W → IF = 1.0, TSS = (3600 × 250 × 1) / (250 × 3600) × 100 = 100
    assert abs(result["daily_tss"][0] - 100.0) < 1.0


def test_compute_training_load_tsb_equals_ctl_minus_atl():
    plan = [{"durationMinutes": 60, "workoutType": "tempo"}] * 10
    result = analysis.compute_training_load(plan, 250.0)
    # TSB = CTL - ATL (may differ by up to 0.2 due to independent rounding of each value)
    assert abs(result["tsb"] - (result["ctl"] - result["atl"])) < 0.2


# ---------------------------------------------------------------------------
# compute_readiness_score
# ---------------------------------------------------------------------------


def test_readiness_score_returns_expected_keys():
    result = analysis.compute_readiness_score(ctl=50.0, atl=60.0, tsb=-10.0, days_until_race=7)
    for key in ("score", "form_score", "fitness_score", "ctl", "atl", "tsb", "days_until_race"):
        assert key in result


def test_readiness_score_range():
    """Score and component scores must be in [0, 100]."""
    for tsb in (-40, -20, 0, 10, 25, 50):
        result = analysis.compute_readiness_score(ctl=60.0, atl=70.0, tsb=float(tsb), days_until_race=0)
        assert 0.0 <= result["score"] <= 100.0
        assert 0.0 <= result["form_score"] <= 100.0
        assert 0.0 <= result["fitness_score"] <= 100.0


def test_readiness_score_peak_form():
    """TSB near +10 with high CTL should produce a high score."""
    result = analysis.compute_readiness_score(ctl=80.0, atl=70.0, tsb=10.0, days_until_race=0)
    assert result["form_score"] == 100.0
    assert result["score"] > 80.0


def test_readiness_score_severe_fatigue():
    """TSB ≤ -30 should give form_score 0."""
    result = analysis.compute_readiness_score(ctl=50.0, atl=80.0, tsb=-30.0, days_until_race=14)
    assert result["form_score"] == 0.0
    assert result["score"] < 40.0


def test_readiness_score_zero_ctl():
    """Zero CTL and zero ATL (TSB=0) → form_score=50, fitness_score=0, score=32.5."""
    result = analysis.compute_readiness_score(ctl=0.0, atl=0.0, tsb=0.0, days_until_race=0)
    assert result["form_score"] == 50.0
    assert result["fitness_score"] == 0.0
    assert result["score"] == 32.5


def test_readiness_score_days_until_race_preserved():
    result = analysis.compute_readiness_score(ctl=60.0, atl=55.0, tsb=5.0, days_until_race=21)
    assert result["days_until_race"] == 21


# ---------------------------------------------------------------------------
# classify_ride_confidence_and_reason
# ---------------------------------------------------------------------------


def test_classify_ride_confidence_unknown_is_low():
    confidence, reason = analysis.classify_ride_confidence_and_reason("unknown", 600, [])
    assert confidence == "low"
    assert len(reason) > 0


def test_classify_ride_confidence_short_easy_spin_is_low():
    confidence, reason = analysis.classify_ride_confidence_and_reason("short_easy_spin", 15 * 60, [])
    assert confidence == "low"


def test_classify_ride_confidence_short_hard_effort_is_low():
    confidence, reason = analysis.classify_ride_confidence_and_reason("short_hard_effort", 20 * 60, [])
    assert confidence == "low"


def test_classify_ride_confidence_recovery_long_is_high():
    # MIN_ENDURANCE_RIDE_SECS = 30 min
    confidence, reason = analysis.classify_ride_confidence_and_reason("recovery", 45 * 60, [])
    assert confidence == "high"


def test_classify_ride_confidence_recovery_short_is_medium():
    confidence, reason = analysis.classify_ride_confidence_and_reason("recovery", 10 * 60, [])
    assert confidence == "medium"


def test_classify_ride_confidence_endurance_long_is_high():
    confidence, reason = analysis.classify_ride_confidence_and_reason("endurance", 60 * 60, [])
    assert confidence == "high"


def test_classify_ride_confidence_endurance_short_is_medium():
    confidence, reason = analysis.classify_ride_confidence_and_reason("endurance", 30 * 60, [])
    assert confidence == "medium"


def test_classify_ride_confidence_tempo_is_medium():
    confidence, reason = analysis.classify_ride_confidence_and_reason("tempo", 60 * 60, [])
    assert confidence == "medium"


def test_classify_ride_confidence_threshold_two_intervals_is_high():
    fake_intervals = [{"duration_secs": 480}, {"duration_secs": 480}]
    confidence, reason = analysis.classify_ride_confidence_and_reason(
        "interval_threshold", 60 * 60, fake_intervals
    )
    assert confidence == "high"


def test_classify_ride_confidence_sweetspot_one_interval_is_medium():
    fake_intervals = [{"duration_secs": 900}]
    confidence, reason = analysis.classify_ride_confidence_and_reason(
        "interval_sweetspot", 60 * 60, fake_intervals
    )
    assert confidence == "medium"


def test_classify_ride_confidence_mixed_is_medium():
    confidence, reason = analysis.classify_ride_confidence_and_reason("mixed", 90 * 60, [])
    assert confidence == "medium"


def test_classify_ride_confidence_returns_string_reason():
    """reason must always be a non-empty string for every category."""
    categories = [
        "unknown", "short_easy_spin", "short_hard_effort",
        "recovery", "endurance", "tempo",
        "interval_sweetspot", "interval_threshold", "interval_vo2max", "interval_sprints",
        "mixed",
    ]
    for cat in categories:
        _, reason = analysis.classify_ride_confidence_and_reason(cat, 60 * 60, [])
        assert isinstance(reason, str) and len(reason) > 0, f"Empty reason for category {cat!r}"


# ---------------------------------------------------------------------------
# build_ride_analysis — classification_confidence and classification_reason
# ---------------------------------------------------------------------------


def test_build_ride_analysis_includes_confidence_and_reason():
    ftp = 250.0
    watts = [ftp * 0.70] * 3600
    ts = list(range(3600))
    streams = {"watts": {"data": watts}, "time": {"data": ts}}
    result = analysis.build_ride_analysis(streams, ftp)
    assert "classification_confidence" in result
    assert "classification_reason" in result
    assert result["classification_confidence"] in ("high", "medium", "low")
    assert isinstance(result["classification_reason"], str)


def test_build_ride_analysis_missing_watts_includes_confidence():
    ts = list(range(5 * 60))
    streams = {"time": {"data": ts}}
    result = analysis.build_ride_analysis(streams, 250.0)
    assert result["ride_category"] == "unknown"
    assert result["classification_confidence"] == "low"
    assert isinstance(result["classification_reason"], str)


# ---------------------------------------------------------------------------
# build_ride_metrics_chain — classification fields propagated
# ---------------------------------------------------------------------------


def test_build_ride_metrics_chain_includes_classification_fields():
    ftp = 250
    watts = [ftp * 0.68] * (60 * 60)
    ts = list(range(len(watts)))
    rides = [
        {
            "strava_activity_id": 1,
            "activity_date": "2026-05-01",
            "sport_type": "cycling",
            "duration_seconds": 60 * 60,
            "streams": {"watts": {"data": watts}, "time": {"data": ts}},
        }
    ]
    result = analysis.build_ride_metrics_chain(rides, ftp=ftp)
    assert len(result) == 1
    row = result[0]
    assert "classification_confidence" in row
    assert "classification_reason" in row
    assert row["classification_confidence"] in ("high", "medium", "low")
    assert isinstance(row["classification_reason"], str) and len(row["classification_reason"]) > 0


def test_build_ride_metrics_chain_missing_streams_sets_low_confidence():
    rides = [
        {
            "strava_activity_id": 2,
            "activity_date": "2026-05-02",
            "sport_type": "cycling",
            "duration_seconds": 3600,
            "streams": {},
        }
    ]
    result = analysis.build_ride_metrics_chain(rides, ftp=250)
    assert result[0]["ride_purpose"] == "unknown"
    assert result[0]["classification_confidence"] == "low"


# ---------------------------------------------------------------------------
# ride_metrics_context_section — confidence/reason in prompt output
# ---------------------------------------------------------------------------


def test_ride_metrics_context_section_shows_confidence():
    from services.prompts import ride_metrics_context_section

    class FakeMetric:
        activity_date = "2026-05-01"
        ride_purpose = "endurance"
        classification_confidence = "high"
        classification_reason = "Sustained aerobic effort across adequate ride duration."
        tss = 80.0
        normalized_power_w = 190
        ctl_after = 55.0
        atl_after = 60.0
        tsb_after = -5.0
        summary = "60 min Z2 @ 190W NP"
        coach_note = None
        user_note = None

    section = ride_metrics_context_section([FakeMetric()])
    assert "conf:high" in section


def test_ride_metrics_context_section_low_confidence_shows_reason():
    from services.prompts import ride_metrics_context_section

    class FakeMetric:
        activity_date = "2026-05-01"
        ride_purpose = "short_easy_spin"
        classification_confidence = "low"
        classification_reason = "Ride too short for a reliable aerobic classification."
        tss = 8.0
        normalized_power_w = None
        ctl_after = None
        atl_after = None
        tsb_after = None
        summary = None
        coach_note = None
        user_note = None

    section = ride_metrics_context_section([FakeMetric()])
    assert "conf:low" in section
    assert "Ride too short" in section
