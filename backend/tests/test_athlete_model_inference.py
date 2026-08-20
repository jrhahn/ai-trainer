"""Unit tests for the deterministic athlete-model inference engine (#476).

The engine is pure and rule-based, so these tests feed synthetic ride signals and
assert on estimates, confidence and evidence directly — no DB or LLM.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
from services.analysis import compute_ride_performance_signals
from services import athlete_model_inference as ami
from tests.conftest import TestSessionLocal

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """Yield a fresh session that is rolled back after each test."""
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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


def _interval_stream(efforts, *, work_s=720, rest_s=300, rest_w=120, step=5):
    """An interval session: warm-up, N efforts at the given watts, cool-down.

    Models the shape that broke FTP inference in #604 — a rolling 20-minute window
    over this stream necessarily straddles work and recovery, so the 20-min point
    of the envelope lands far below any single interval.
    """
    watts: list[float] = [140] * (900 // step)  # 15 min warm-up
    for i, power in enumerate(efforts):
        if i:
            watts += [rest_w] * (rest_s // step)
        watts += [power] * (work_s // step)
    watts += [130] * (600 // step)  # 10 min cool-down
    hr = [round(110 + (w - 140) * 0.14) for w in watts]
    return {
        "time": {"data": [i * step for i in range(len(watts))]},
        "watts": {"data": watts},
        "heartrate": {"data": hr},
    }


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
    # A ~5-min VO2max effort: long enough to be MAP evidence, short enough not to
    # be threshold evidence, which is what "relatively low FTP" needs here (#604 —
    # FTP inference now reads the whole 10-60 min band, not the 20-min point).
    vo2 = _ride(compute_ride_performance_signals(_stream(300, 355, 350, 176, 180)))
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


# --- FTP estimation and validation (#604) ------------------------------------


def _reported_workout():
    """The session from #604: 3 x ~12 min at 337/343/345 W."""
    return _ride(
        compute_ride_performance_signals(_interval_stream([337, 343, 345])),
        activity_date="2026-07-27",
    )


def test_interval_session_does_not_underestimate_ftp():
    """The #604 regression: 3 x 12 min at ~340 W must not read as FTP 261 W.

    The 20-min point of this ride is diluted by the recoveries between the
    intervals; reading FTP off it alone implied the athlete held ~130 % of
    threshold for twelve minutes, three times over.
    """
    attrs = ami.infer_performance_attributes([_reported_workout()], now=NOW)
    ftp = attrs["ftp"]

    # Every completed interval is now a plausible fraction of the estimate.
    for interval_power in (337, 343, 345):
        assert interval_power / ftp["estimate"] < 1.15

    # ... but the estimate is not simply the best interval either.
    assert ftp["estimate"] < 337
    assert ftp["estimate"] > 300


def test_uncertain_ftp_carries_a_range_and_a_validation_test():
    attrs = ami.infer_performance_attributes([_reported_workout()], now=NOW)
    ftp = attrs["ftp"]

    assert ftp["estimate_low"] < ftp["estimate"] <= ftp["estimate_high"]
    # Hard intervals are a floor, not a threshold test: confidence stays modest
    # and the estimate ships with the test that would settle it.
    assert ftp["confidence"] <= ami._INTERVAL_ONLY_CONFIDENCE_CAP
    assert "20-minute threshold test" in ftp["validation_protocol"]
    assert any("floor" in m for m in ftp["missing_information"])


def test_sustained_test_beats_intervals_on_confidence():
    """A maximal 20-min effort is threshold evidence; repeated intervals are not."""
    signals = compute_ride_performance_signals(_stream(1500, 320, 318, 172, 176))
    tested = ami.infer_performance_attributes(
        [_ride(signals, activity_date="2026-07-27")], now=NOW
    )["ftp"]
    intervals = ami.infer_performance_attributes([_reported_workout()], now=NOW)["ftp"]

    assert tested["confidence"] > intervals["confidence"]
    # A real 20-min test still estimates the way it always has, and is asserted
    # without a test attached.
    assert tested["estimate"] == round(signals["power_curve"]["20"] * 0.95)
    assert "validation_protocol" not in tested


def test_sanity_check_corrects_an_implausible_carried_ftp():
    """The plausibility check also guards the carried-``ftp_used`` branch.

    A ride with no threshold-band point cannot produce a candidate, so the stale
    FTP is carried forward — but a 5-min effort far above it still contradicts it.
    """
    short = _ride(
        compute_ride_performance_signals(_stream(540, 340, 338, 178, 182)),
        ftp_used=180,
        activity_date="2026-07-27",
    )
    ftp = ami.infer_performance_attributes([short], now=NOW)["ftp"]
    assert ftp["estimate"] > 180
    assert ftp["confidence"] <= ami._CORRECTED_CONFIDENCE_CAP
    assert any("Raised to the lowest FTP" in e for e in ftp["evidence"])


def test_correction_never_reaches_the_raw_effort_power():
    ftp = ami.infer_performance_attributes(
        [
            _ride(
                compute_ride_performance_signals(_interval_stream([345, 345, 345])),
                ftp_used=200,
            )
        ],
        now=NOW,
    )["ftp"]
    assert ftp["estimate"] < 345


# --- refresh_performance_model (DB, end-to-end incl. limiter #477) -----------


@pytest.mark.asyncio
async def test_refresh_persists_model_and_threshold_limiter(db: AsyncSession) -> None:
    """A strong-engine / relatively-low-FTP history is derived and persisted with
    threshold detected as the likely limiter (#476 → #477 through the DB)."""
    user = await crud.create_user(
        db, email="perf-refresh@example.com", name="R", hashed_password="x"
    )
    # Long, durable, low-decoupling rides (low sustained power) ...
    for i, dur in enumerate((14400, 12600)):
        await crud.upsert_ride_metric(
            db, user.id,
            strava_activity_id=1000 + i,
            activity_date="2026-07-2%d" % (5 + i),
            perf_signals=compute_ride_performance_signals(
                _stream(dur, 215 - i, 212 - i, 130, 133)
            ),
        )
    # ... plus a short maximal ~5 min effort giving a high MAP well above FTP
    # (kept below 10 min so it contributes a MAP point, not an FTP candidate).
    await crud.upsert_ride_metric(
        db, user.id,
        strava_activity_id=1002,
        activity_date="2026-07-27",
        perf_signals=compute_ride_performance_signals(_stream(300, 355, 350, 176, 180)),
    )

    row = await ami.refresh_performance_model(db, user, now=NOW)
    assert row is not None
    assert row.attributes["map"]["estimate"]
    assert row.likely_limiter == "threshold"
    assert row.limiters and row.limiters[0]["limiter"] == "threshold"
    assert row.limiters[0]["evidence"]

    # The snapshot carries the same limiter for the month-over-month history.
    snaps = await crud.get_athlete_performance_snapshots(db, user.id)
    assert snaps and snaps[-1].likely_limiter == "threshold"


@pytest.mark.asyncio
async def test_refresh_returns_none_without_signals(db: AsyncSession) -> None:
    user = await crud.create_user(
        db, email="perf-refresh-empty@example.com", name="E", hashed_password="x"
    )
    assert await ami.refresh_performance_model(db, user, now=NOW) is None
    assert await crud.get_athlete_performance_model(db, user.id) is None
