"""Unit tests for the deterministic athlete-model inference engine (#476).

The engine is pure and rule-based, so these tests feed synthetic ride signals and
assert on estimates, confidence and evidence directly — no DB or LLM.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from services.analysis import compute_ride_performance_signals
from services import athlete_model_inference as ami

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def _stream(dur_s, watts_first, watts_second, hr_first, hr_second, step=5):
    """A two-phase (first-half / second-half) constant-power/HR stream."""
    n = dur_s // step
    time = [i * step for i in range(n)]
    watts = [watts_first if i < n // 2 else watts_second for i in range(n)]
    hr = [hr_first if i < n // 2 else hr_second for i in range(n)]
    return {
        "time": {"data": time},
        "watts": {"data": watts},
        "heartrate": {"data": hr},
    }


def _ride(signals, *, ftp_used=280, activity_date="2026-07-25"):
    return SimpleNamespace(
        perf_signals=signals, ftp_used=ftp_used, activity_date=activity_date
    )


# --- compute_ride_performance_signals ---------------------------------------


def test_signals_extracts_power_curve_hr_and_halves():
    sig = compute_ride_performance_signals(_stream(10800, 210, 200, 132, 138))
    assert sig is not None
    assert sig["duration_s"] == 10795
    # Envelope has the standard durations the ride is long enough to contain.
    assert set(sig["power_curve"]) >= {"1", "5", "20", "60"}
    assert sig["first_half_power"] == 210
    assert sig["second_half_power"] == 200
    assert sig["first_half_hr"] == 132
    assert sig["second_half_hr"] == 138
    assert "hr_drift_slope" in sig


def test_signals_returns_none_without_power():
    assert compute_ride_performance_signals({"time": {"data": [0, 1, 2]}}) is None
    assert compute_ride_performance_signals({}) is None


# --- infer_performance_attributes -------------------------------------------


def test_no_signals_yields_empty_model():
    rides = [_ride(None), _ride(None)]
    assert ami.infer_performance_attributes(rides, now=NOW) == {}


def test_every_attribute_carries_a_confidence():
    sig = compute_ride_performance_signals(_stream(10800, 210, 205, 132, 134))
    attrs = ami.infer_performance_attributes([_ride(sig)], now=NOW)
    assert set(attrs) == {
        "ftp",
        "map",
        "vo2max",
        "fractional_utilization",
        "aerobic_endurance",
        "fatigue_resistance",
        "anaerobic_capacity",
    }
    for name, attr in attrs.items():
        assert isinstance(attr["confidence"], float), name
        assert 0.0 <= attr["confidence"] <= 1.0, name
        # Something is always populated: an estimate or a score.
        assert attr["estimate"] is not None or attr["score"] is not None, name


def test_estimates_have_evidence_when_data_present():
    vo2_ride = _ride(compute_ride_performance_signals(_stream(1800, 360, 355, 175, 178)))
    long_ride = _ride(compute_ride_performance_signals(_stream(10800, 210, 206, 132, 134)))
    attrs = ami.infer_performance_attributes(
        [vo2_ride, long_ride], weight_kg=70, max_hr=190, now=NOW
    )

    assert attrs["map"]["estimate"] == 360
    assert attrs["map"]["evidence"]
    assert attrs["ftp"]["estimate"] and attrs["ftp"]["evidence"]
    # fractional utilization = FTP / MAP, both present -> numeric with evidence.
    assert attrs["fractional_utilization"]["estimate"] is not None
    assert attrs["fractional_utilization"]["evidence"]
    # VO2max needs weight; provided here -> numeric ml/kg/min estimate.
    assert attrs["vo2max"]["estimate"] is not None
    assert attrs["vo2max"]["unit"] == "ml/kg/min"


def test_vo2max_unknown_without_weight():
    sig = compute_ride_performance_signals(_stream(1800, 360, 355, 175, 178))
    attrs = ami.infer_performance_attributes([_ride(sig)], weight_kg=None, now=NOW)
    vo2 = attrs["vo2max"]
    assert vo2["estimate"] is None
    assert vo2["score"] == "unknown"
    assert vo2["confidence"] <= 0.2
    assert any("weight" in m.lower() for m in vo2["missing_information"])


def test_canonical_strong_engine_relatively_low_ftp():
    """High MAP + strong, durable long rides -> high aerobic endurance and a
    populated fractional utilization the limiter stage (#477) can act on."""
    long1 = _ride(compute_ride_performance_signals(_stream(14400, 215, 212, 130, 133)))
    long2 = _ride(compute_ride_performance_signals(_stream(12600, 210, 208, 128, 131)))
    vo2 = _ride(compute_ride_performance_signals(_stream(1500, 355, 350, 176, 180)))
    attrs = ami.infer_performance_attributes(
        [vo2, long1, long2], weight_kg=72, max_hr=190, now=NOW
    )
    assert attrs["aerobic_endurance"]["score"] in {"high", "above_average"}
    assert attrs["fatigue_resistance"]["score"] in {"high", "above_average"}
    assert 0.0 < attrs["fractional_utilization"]["estimate"] <= 1.2


def test_absent_signals_report_unknown_not_guessed():
    # Short ride only: no long/durable rides, no MAP-length maximal efforts.
    short = _ride(compute_ride_performance_signals(_stream(600, 200, 200, 140, 142)))
    attrs = ami.infer_performance_attributes([short], now=NOW)
    assert attrs["aerobic_endurance"]["score"] == "unknown"
    assert attrs["aerobic_endurance"]["missing_information"]
    assert attrs["fatigue_resistance"]["score"] == "unknown"


def test_recency_penalises_stale_data():
    fresh = ami.infer_performance_attributes(
        [_ride(compute_ride_performance_signals(_stream(3600, 300, 298, 165, 168)),
               activity_date="2026-07-27")],
        now=NOW,
    )
    stale = ami.infer_performance_attributes(
        [_ride(compute_ride_performance_signals(_stream(3600, 300, 298, 165, 168)),
               activity_date="2026-01-01")],
        now=NOW,
    )
    assert fresh["ftp"]["confidence"] > stale["ftp"]["confidence"]
