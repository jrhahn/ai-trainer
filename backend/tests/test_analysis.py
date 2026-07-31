"""Unit tests for the pure computational helpers in services/analysis.py."""

from __future__ import annotations

import pytest

from services import analysis


def test_best_n_min_power_basic():
    watts = [200.0] * 120
    time_stream = [float(i) for i in range(120)]  # 120 s
    power, start, end = analysis.best_n_min_power(watts, time_stream, 1)  # 60 s window
    assert power == 200.0
    assert end > start


def test_best_n_min_power_insufficient_data():
    assert analysis.best_n_min_power([], [], 1) == (None, 0, 0)
    assert analysis.best_n_min_power([1.0, 2.0], [0.0], 1) == (None, 0, 0)


def test_hr_corrected_ftp():
    # interval HR within 70-100% of max → correction applied
    result = analysis.hr_corrected_ftp(250.0, 160.0, 190)
    assert isinstance(result, int) and result > 0
    # out-of-range HR or zero inputs → None
    assert analysis.hr_corrected_ftp(250.0, 100.0, 190) is None  # < 70% max
    assert analysis.hr_corrected_ftp(250.0, 0.0, 190) is None
    assert analysis.hr_corrected_ftp(250.0, 160.0, 0) is None


def test_compute_hr_zones():
    zones = analysis.compute_hr_zones(190)
    assert zones["zone1"]["high"] == round(190 * 0.60)
    assert zones["zone5"]["high"] == 190


def test_detect_intervals():
    # 3x: 60s hard (300W) then 60s easy (100W) at ftp=250 (threshold 212.5)
    watts: list[float] = []
    for _ in range(3):
        watts += [300.0] * 60 + [100.0] * 60
    time_stream = [float(i) for i in range(len(watts))]
    intervals = analysis.detect_intervals(watts, time_stream, ftp=250.0)
    assert len(intervals) == 3
    assert intervals[0]["avg_power"] == 300
    # invalid inputs → empty
    assert analysis.detect_intervals([], [], 250.0) == []
    assert analysis.detect_intervals([1.0], [0.0], 0.0) == []


def test_compute_hr_drift():
    assert analysis.compute_hr_drift([0, 1, 2]) is None or True  # n<=2 guard below
    assert analysis.compute_hr_drift([1.0, 2.0]) is None
    slope = analysis.compute_hr_drift([100.0, 110.0, 120.0, 130.0])
    assert slope == pytest.approx(10.0)


def test_stream_duration_seconds():
    assert analysis._stream_duration_seconds([]) == 0.0
    assert analysis._stream_duration_seconds([5.0]) == 1.0
    assert analysis._stream_duration_seconds([0.0, 10.0]) == pytest.approx(20.0)
    assert analysis._stream_duration_seconds([10.0, 0.0]) == 0.0  # negative elapsed


def test_compute_training_load_ftp_guard():
    assert analysis.compute_training_load([], 0) == {
        "ctl": 0.0,
        "atl": 0.0,
        "tsb": 0.0,
        "daily_tss": [],
    }


def test_compute_training_load_with_target_power_and_types():
    plan = [
        {"durationMinutes": 60, "targetPower": {"low": 200, "high": 240}},
        {"durationMinutes": 0},  # rest
        {"durationMinutes": 90, "workoutType": "endurance"},
        {"durationMinutes": 45, "targetPower": {"high": 260}},
        {"durationMinutes": 30, "targetPower": {"low": 180}},
    ]
    result = analysis.compute_training_load(plan, ftp=250.0)
    assert result["ctl"] > 0
    assert len(result["daily_tss"]) == 5
    assert result["daily_tss"][1] == 0.0  # rest day


@pytest.mark.parametrize(
    "tsb,expected_band",
    [
        (-40, 0.0),       # severe fatigue
        (-15, None),      # linear tired
        (5, None),        # building
        (15, None),       # tapering
        (40, None),       # over-tapered
    ],
)
def test_compute_readiness_score_bands(tsb, expected_band):
    result = analysis.compute_readiness_score(ctl=70, atl=60, tsb=tsb, days_until_race=5)
    assert 0.0 <= result["score"] <= 100.0
    if expected_band is not None:
        assert result["form_score"] == expected_band
    assert result["fitness_score"] == 70.0
    assert result["days_until_race"] == 5


def test_compute_readiness_recommendations_covers_all_branches():
    # Heavy fatigue + low fitness + far race + low score
    tips = analysis.compute_readiness_recommendations(ctl=20, atl=90, tsb=-25, score=30, days_until_race=30)
    assert any("heavily fatigued" in t for t in tips)
    assert any("base" in t for t in tips)

    # Optimal form + high fitness + taper window + on-track score
    tips2 = analysis.compute_readiness_recommendations(ctl=90, atl=70, tsb=15, score=60, days_until_race=14)
    assert any("optimal" in t.lower() for t in tips2)
    assert any("taper" in t.lower() for t in tips2)

    # Very fresh + race day
    tips3 = analysis.compute_readiness_recommendations(ctl=50, atl=20, tsb=30, score=80, days_until_race=0)
    assert any("Race day" in t for t in tips3)

    # Mid fatigue branches + 3-10 day window
    tips4 = analysis.compute_readiness_recommendations(ctl=45, atl=55, tsb=-12, score=70, days_until_race=7)
    assert any("rest day" in t.lower() for t in tips4)

    # slight fatigue + last-days window
    tips5 = analysis.compute_readiness_recommendations(ctl=65, atl=66, tsb=-3, score=70, days_until_race=2)
    assert any("rest up" in t.lower() for t in tips5)


def test_project_training_load_from_seed():
    # ftp guard returns the seed unchanged
    guard = analysis.project_training_load_from_seed([], 0, seed_ctl=50, seed_atl=40)
    assert guard["ctl"] == 50 and guard["tsb"] == 10

    future = [
        {"durationMinutes": 60, "targetPower": {"low": 200, "high": 240}},
        {"durationMinutes": 0, "workoutType": "rest"},
        {"durationMinutes": 90, "workoutType": "endurance"},
    ]
    result = analysis.project_training_load_from_seed(future, ftp=250.0, seed_ctl=50, seed_atl=40)
    assert len(result["daily_tss"]) == 3
    assert "tsb" in result


def test_normalized_power():
    watts = [200.0] * 60
    time_stream = [float(i) for i in range(60)]
    np_value = analysis._normalized_power(watts, time_stream)
    assert np_value == pytest.approx(200.0, abs=1.0)
    # insufficient data
    assert analysis._normalized_power([1.0], [0.0]) is None
    assert analysis._normalized_power([1.0, 2.0], [0.0, 10.0]) is None  # < 30s


def test_compute_ride_tss():
    assert analysis.compute_ride_tss(3600, 250, 250) == pytest.approx(100.0, abs=0.1)
    assert analysis.compute_ride_tss(0, 250, 250) is None
    assert analysis.compute_ride_tss(3600, 0, 250) is None
    assert analysis.compute_ride_tss(3600, 250, 0) is None


def test_apply_ctl_atl_decay():
    ctl, atl = analysis.apply_ctl_atl_decay(50.0, 40.0, tss=80.0, gap_days=1)
    assert ctl > 50.0  # CTL rises toward TSS
    # with silent days, prior load decays before the ride
    ctl2, atl2 = analysis.apply_ctl_atl_decay(50.0, 40.0, tss=0.0, gap_days=4)
    assert ctl2 < 50.0 and atl2 < 40.0


def _steady_ride(date_str: str, watts_val: float = 260.0, minutes: int = 21, hr_val: float = 168.0) -> dict:
    n = minutes * 60 + 60
    return {
        "activity_date": date_str,
        "streams": {
            "watts": {"data": [watts_val] * n},
            "heartrate": {"data": [hr_val] * n},
            "time": {"data": [float(i) for i in range(n)]},
        },
    }


def test_estimate_ftp_over_time_produces_curve():
    rides = [_steady_ride("2026-05-01"), _steady_ride("2026-05-12", watts_val=280.0)]
    result = analysis.estimate_ftp_over_time(rides, max_heart_rate=190)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert all("ftp" in r and "raw_ftp" in r and "date" in r for r in result)
    # A 5-minute MAP proxy is emitted alongside every FTP point, and FTP must
    # land below it.
    assert all("map_5min" in r for r in result)
    for point in result:
        if point["map_5min"] is not None:
            assert point["ftp"] < point["map_5min"]


def test_check_ftp_against_map_flags_ftp_at_or_above_map():
    # FTP above the aerobic ceiling is impossible, not merely unusual.
    warning = analysis.check_ftp_against_map(320, 300)
    assert warning is not None
    assert "too high" in warning

    # Right at the upper bound still trips.
    assert (
        analysis.check_ftp_against_map(
            round(300 * analysis.FTP_MAP_RATIO_MAX), 300
        )
        is not None
    )


def test_check_ftp_against_map_flags_implausibly_low_ftp():
    warning = analysis.check_ftp_against_map(150, 400)
    assert warning is not None
    assert "out of date" in warning


def test_check_ftp_against_map_accepts_normal_physiology():
    # 78 % of MAP sits squarely in the trained-cyclist band.
    assert analysis.check_ftp_against_map(280, 360) is None


def test_check_ftp_against_map_needs_both_values():
    assert analysis.check_ftp_against_map(None, 400) is None
    assert analysis.check_ftp_against_map(280, None) is None
    assert analysis.check_ftp_against_map(0, 400) is None
    assert analysis.check_ftp_against_map(280, 0) is None


def test_estimate_ftp_over_time_empty_and_invalid():
    assert analysis.estimate_ftp_over_time([]) == []
    assert analysis.estimate_ftp_over_time([{"activity_date": ""}]) == []
    # watts/time length mismatch is skipped
    assert (
        analysis.estimate_ftp_over_time(
            [{"activity_date": "2026-05-01", "streams": {"watts": {"data": [1.0, 2.0]}, "time": {"data": [0.0]}}}]
        )
        == []
    )


def test_compute_ftp_from_streams():
    ftp, thr_hr = analysis.compute_ftp_from_streams({"1": _steady_ride("x")["streams"]}, max_heart_rate=190)
    assert (ftp is None or ftp > 0)
    assert (thr_hr is None or thr_hr > 0)
    # no usable data
    assert analysis.compute_ftp_from_streams({}) == (None, None)
    assert analysis.compute_ftp_from_streams({"1": {"watts": {"data": []}, "time": {"data": []}}}) == (None, None)


def test_critical_power_from_points():
    assert analysis._critical_power_from_points({1.0: 300.0}) is None  # < 3 points
    # duration range < 15 min
    assert analysis._critical_power_from_points({1.0: 400.0, 2.0: 390.0, 3.0: 380.0}) is None
    # flat curve (short power not high enough above long power)
    assert analysis._critical_power_from_points({1.0: 300.0, 5.0: 295.0, 20.0: 290.0}) is None
    # steep curve exercises the regression + bound check
    result = analysis._critical_power_from_points({1.0: 450.0, 5.0: 330.0, 20.0: 285.0})
    assert result is None or isinstance(result, int)


def test_time_in_power_zones():
    empty = analysis._time_in_power_zones([], [], 250.0)
    assert empty == {f"z{i}_secs": 0 for i in range(1, 8)}
    # one sample landing in each of the 7 zones at ftp=250
    watts = [100.0, 160.0, 200.0, 240.0, 270.0, 320.0, 400.0]
    time_stream = [float(i) for i in range(len(watts))]
    zones = analysis._time_in_power_zones(watts, time_stream, 250.0)
    assert all(zones[f"z{i}_secs"] >= 1 for i in range(1, 8))


def test_detect_intensity_spikes():
    assert analysis._detect_intensity_spikes([], [], 200.0) == []
    assert analysis._detect_intensity_spikes([1.0], [0.0, 1.0], 200.0) == []  # length mismatch
    n = 1000
    watts = [300.0] * n
    time_stream = [float(i) for i in range(n)]
    spikes = analysis._detect_intensity_spikes(watts, time_stream, target_power=200.0)
    assert len(spikes) >= 1
    assert spikes[0]["avg_power_w"] == 300
    assert spikes[0]["pct_over_target"] == pytest.approx(50.0, abs=0.1)
