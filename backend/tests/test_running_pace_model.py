"""Running reaches CTL through its own tier, and keeps its units apart (#716).

``test_run_model.py`` pins the model's arithmetic. These pin the wiring: that a
run is priced by rTSS rather than by a heart-rate guess once the athlete has a
threshold pace, that it still gets a load when they do not, that the Critical
Speed fit is withheld when the evidence is thin, and — the acceptance criterion
that is really an invariant — that pace never appears for a ride and watts never
for a run.

The synthetic runner is the one from ``test_run_model.py``: CS 4,0 m/s, D′ 200 m.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import schemas
from routers import users as users_router
from services import athlete_model_inference as ami
from services import metrics_service, prompts, run_model
from services.analysis import build_ride_metrics_chain, compute_ride_performance_signals
from services.training_load import (
    CONFIDENCE_HIGH,
    LOAD_SOURCE_HEART_RATE,
    LOAD_SOURCE_PACE,
    LOAD_SOURCE_POWER,
    LOAD_SOURCE_PROVIDER,
    LOAD_UNIT_TSS,
    LoadSignals,
    format_load,
    pace_training_load,
    session_load,
)
from tests.conftest import TestSessionLocal

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)
THRESHOLD_PACE = 250.0  # 4:10 /km, which is this runner's Critical Speed
THRESHOLD_SPEED = 1000.0 / THRESHOLD_PACE
HOUR = 3600


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _run_streams(
    *, seconds: int, speed: float, hr: float | None = None, watts: float | None = None
) -> dict:
    time_data = [float(t) for t in range(seconds + 1)]
    streams: dict = {
        "time": {"data": time_data},
        "distance": {"data": [t * speed for t in time_data]},
    }
    if hr is not None:
        streams["heartrate"] = {"data": [hr] * len(time_data)}
    if watts is not None:
        streams["watts"] = {"data": [watts] * len(time_data)}
    return streams


def _ride(
    *,
    sport: str,
    seconds: int,
    speed: float = 4.0,
    hr: float | None = None,
    watts: float | None = None,
    date: str = "2026-10-01",
    activity_id: int = 1,
) -> dict:
    return {
        "strava_activity_id": activity_id,
        "activity_date": date,
        "sport_type": sport,
        "duration_seconds": seconds,
        "streams": _run_streams(seconds=seconds, speed=speed, hr=hr, watts=watts),
    }


def _envelope_curve(minutes: float) -> float:
    """The synthetic runner's maximal speed for a duration, in m/s."""
    return 4.0 + 200.0 / (minutes * 60.0)


def _run_signals(*, date: str = "2026-10-01", maximal: bool = True) -> dict:
    curve = (
        {str(int(m)): round(_envelope_curve(m), 3) for m in (2, 3, 5, 10, 20)}
        if maximal
        else {str(int(m)): 3.0 for m in (2, 3, 5, 10, 20)}
    )
    return {
        "sport": run_model.RUN_SIGNALS_SPORT,
        "duration_s": 3600,
        "moving_duration_s": 3600,
        "distance_m": 14400,
        "speed_curve": curve,
        "gap_speed_curve": curve,
    }


# ---------------------------------------------------------------------------
# The ladder: where the pace rung sits, and whose sport it answers for
# ---------------------------------------------------------------------------


def test_a_run_with_a_threshold_pace_is_priced_by_pace_not_by_heart_rate():
    load = session_load(
        LoadSignals(
            sport_type="Run",
            duration_seconds=HOUR,
            gap_speed_m_s=THRESHOLD_SPEED,
            threshold_speed_m_s=THRESHOLD_SPEED,
            avg_hr_bpm=150,
            max_heart_rate=190,
        )
    )
    assert load.source == LOAD_SOURCE_PACE
    assert load.tss == pytest.approx(100.0)


def test_the_pace_rung_sits_below_power_and_the_providers_own_figure():
    """A measured figure is never displaced by one we derived."""
    signals = {
        "sport_type": "Run",
        "duration_seconds": HOUR,
        "gap_speed_m_s": THRESHOLD_SPEED,
        "threshold_speed_m_s": THRESHOLD_SPEED,
    }
    assert (
        session_load(LoadSignals(**signals, provider_load=88.0)).source
        == LOAD_SOURCE_PROVIDER
    )
    assert (
        session_load(LoadSignals(**signals, power_tss=94.0)).source
        == LOAD_SOURCE_POWER
    )


def test_a_run_without_a_threshold_pace_falls_to_heart_rate():
    """An rTSS against a threshold nobody established is not a better number."""
    load = session_load(
        LoadSignals(
            sport_type="Run",
            duration_seconds=HOUR,
            gap_speed_m_s=THRESHOLD_SPEED,
            threshold_speed_m_s=None,
            avg_hr_bpm=150,
            max_heart_rate=190,
        )
    )
    assert load.source == LOAD_SOURCE_HEART_RATE


@pytest.mark.parametrize("sport", ["Ride", "VirtualRide", "WeightTraining", "Hike"])
def test_a_threshold_pace_prices_nothing_but_running(sport):
    """A 35 km/h hour would read as IF 1,3 against any runner's threshold."""
    assert (
        pace_training_load(
            sport_type=sport,
            duration_seconds=HOUR,
            gap_speed_m_s=10.0,
            threshold_speed_m_s=THRESHOLD_SPEED,
        )
        is None
    )


def test_an_rtss_is_reported_as_tss_rather_than_as_an_estimate():
    """rTSS is TSS in the sense the athlete's own training software means it."""
    load = session_load(
        LoadSignals(
            sport_type="Run",
            duration_seconds=HOUR,
            gap_speed_m_s=THRESHOLD_SPEED,
            threshold_speed_m_s=THRESHOLD_SPEED,
        )
    )
    assert load.unit == LOAD_UNIT_TSS
    assert load.confidence == CONFIDENCE_HIGH
    assert format_load(load.tss, load.source) == "TSS 100"


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


def test_a_run_reaches_ctl_through_the_running_tier():
    chain = build_ride_metrics_chain(
        [_ride(sport="Run", seconds=HOUR, speed=THRESHOLD_SPEED, hr=150)],
        ftp=250.0,
        max_heart_rate=190,
        threshold_pace_seconds_per_km=THRESHOLD_PACE,
    )
    assert chain[0]["tss_source"] == LOAD_SOURCE_PACE
    assert chain[0]["tss"] == pytest.approx(100.0, abs=1.0)
    # Running fitness, not cycling fitness (#713).
    assert chain[0]["ctl_by_sport"]["running"] > 0
    assert chain[0]["ctl_by_sport"].get("cycling", 0) == 0


def test_the_same_run_without_a_threshold_pace_still_carries_a_load():
    """The point of the ladder is that a missing model costs a rung, not the row."""
    chain = build_ride_metrics_chain(
        [_ride(sport="Run", seconds=HOUR, speed=THRESHOLD_SPEED, hr=150)],
        ftp=250.0,
        max_heart_rate=190,
    )
    assert chain[0]["tss_source"] == LOAD_SOURCE_HEART_RATE
    assert chain[0]["tss"] > 0


def test_a_run_gets_the_running_envelope_and_a_ride_the_power_one():
    chain = build_ride_metrics_chain(
        [
            _ride(sport="Run", seconds=1800, speed=4.0, activity_id=1),
            _ride(
                sport="Ride",
                seconds=1800,
                speed=9.0,
                watts=220.0,
                activity_id=2,
                date="2026-10-02",
            ),
        ],
        ftp=250.0,
        threshold_pace_seconds_per_km=THRESHOLD_PACE,
    )
    by_id = {row["strava_activity_id"]: row for row in chain}
    assert run_model.is_run_signals(by_id[1]["perf_signals"])
    assert "speed_curve" in by_id[1]["perf_signals"]
    assert not run_model.is_run_signals(by_id[2]["perf_signals"])
    assert "power_curve" in by_id[2]["perf_signals"]


def test_a_footpod_run_is_never_priced_from_its_watts():
    """Running watts are a different quantity from a cyclist's watts (#711)."""
    chain = build_ride_metrics_chain(
        [_ride(sport="Run", seconds=HOUR, speed=THRESHOLD_SPEED, watts=280.0, hr=150)],
        ftp=250.0,
        max_heart_rate=190,
        threshold_pace_seconds_per_km=THRESHOLD_PACE,
    )
    assert chain[0]["tss_source"] == LOAD_SOURCE_PACE
    assert chain[0]["intensity_factor"] is None
    assert chain[0]["avg_power_w"] is None


def test_a_cycling_week_is_unchanged_by_the_new_rung():
    """A pace model must not move a single cycling number."""
    ride = _ride(sport="Ride", seconds=HOUR, speed=9.0, watts=220.0, hr=150)
    without = build_ride_metrics_chain([ride], ftp=250.0, max_heart_rate=190)
    with_pace = build_ride_metrics_chain(
        [ride], ftp=250.0, max_heart_rate=190, threshold_pace_seconds_per_km=THRESHOLD_PACE
    )
    assert without == with_pace


# ---------------------------------------------------------------------------
# Critical Speed through the inference engine
# ---------------------------------------------------------------------------


def test_the_engine_reports_critical_speed_d_prime_and_threshold_pace():
    attributes = ami.infer_performance_attributes(
        [SimpleNamespace(perf_signals=_run_signals(), ftp_used=None, activity_date="2026-10-01")],
        now=NOW,
    )
    assert attributes["critical_speed"]["estimate"] == pytest.approx(4.0, abs=0.05)
    assert attributes["critical_speed"]["unit"] == "m/s"
    assert attributes["d_prime"]["estimate"] == pytest.approx(200.0, abs=5.0)
    assert attributes["threshold_pace"]["estimate"] == pytest.approx(
        THRESHOLD_PACE / run_model.THRESHOLD_FRACTION_OF_CRITICAL_SPEED, abs=2.0
    )


def test_every_run_attribute_states_its_confidence_and_its_evidence():
    attributes = ami.infer_performance_attributes(
        [SimpleNamespace(perf_signals=_run_signals(), ftp_used=None, activity_date="2026-10-01")],
        now=NOW,
    )
    for key in ("critical_speed", "d_prime", "threshold_pace"):
        assert attributes[key]["confidence"] > 0
        assert attributes[key]["evidence"]
        assert attributes[key]["missing_information"]


def test_a_thin_envelope_is_withheld_rather_than_guessed():
    attributes = ami.infer_performance_attributes(
        [
            SimpleNamespace(
                perf_signals=_run_signals(maximal=False),
                ftp_used=None,
                activity_date="2026-10-01",
            )
        ],
        now=NOW,
    )
    assert attributes["critical_speed"]["estimate"] is None
    assert attributes["critical_speed"]["score"] == "unknown"
    assert attributes["critical_speed"]["confidence"] <= 0.2
    assert attributes["critical_speed"]["missing_information"]
    assert attributes["critical_speed"]["validation_protocol"]


def test_a_cyclist_who_never_runs_gets_no_running_attributes():
    """Reporting an unknown Critical Speed to someone with no runs is noise."""
    attributes = ami.infer_performance_attributes(
        [
            SimpleNamespace(
                perf_signals=compute_ride_performance_signals(
                    {
                        "time": {"data": [float(t) for t in range(0, 1800, 5)]},
                        "watts": {"data": [230.0] * 360},
                    }
                ),
                ftp_used=250,
                activity_date="2026-10-01",
            )
        ],
        now=NOW,
    )
    assert "critical_speed" not in attributes
    assert attributes["ftp"]


def test_a_run_contributes_nothing_to_a_cycling_attribute():
    """The #711 error one level in: a run's envelope arguing about FTP."""
    runs_only = ami.infer_performance_attributes(
        [SimpleNamespace(perf_signals=_run_signals(), ftp_used=None, activity_date="2026-10-01")],
        now=NOW,
    )
    assert runs_only["ftp"]["estimate"] is None
    assert runs_only["map"]["estimate"] is None
    assert runs_only["aerobic_endurance"]["score"] == "unknown"


def test_the_grade_adjusted_curve_is_what_the_fit_reads():
    """A hilly maximal effort is a maximal effort; the raw curve calls it slow."""
    signals = _run_signals()
    signals["speed_curve"] = {key: 3.0 for key in signals["speed_curve"]}
    attributes = ami.infer_performance_attributes(
        [SimpleNamespace(perf_signals=signals, ftp_used=None, activity_date="2026-10-01")],
        now=NOW,
    )
    assert attributes["critical_speed"]["estimate"] == pytest.approx(4.0, abs=0.05)


@pytest.mark.asyncio
async def test_the_model_persists_a_runners_critical_speed(db: AsyncSession) -> None:
    user = await crud.create_user(
        db, email="runner@example.com", name="R", hashed_password="x"
    )
    await crud.upsert_ride_metric(
        db,
        user.id,
        strava_activity_id=4242,
        activity_date="2026-10-01",
        sport_type="Run",
        perf_signals=_run_signals(),
    )
    row = await ami.refresh_performance_model(db, user, now=NOW)
    assert row is not None
    assert row.attributes["critical_speed"]["estimate"] == pytest.approx(4.0, abs=0.05)


# ---------------------------------------------------------------------------
# Which threshold pace prices the runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_athletes_own_threshold_pace_wins(db: AsyncSession) -> None:
    user = await crud.create_user(
        db, email="own-pace@example.com", name="R", hashed_password="x"
    )
    user.threshold_pace_seconds_per_km = 255.0
    await crud.upsert_athlete_performance_model(
        db,
        user.id,
        attributes={"threshold_pace": {"estimate": 300.0, "confidence": 0.8}},
        likely_limiter=None,
        limiters=[],
        source_window_days=120,
        derived_from_rides=3,
    )
    assert await metrics_service.get_effective_threshold_pace(db, user) == 255.0


@pytest.mark.asyncio
async def test_a_confident_fit_fills_a_gap_the_athlete_left(db: AsyncSession) -> None:
    user = await crud.create_user(
        db, email="fit-pace@example.com", name="R", hashed_password="x"
    )
    await crud.upsert_athlete_performance_model(
        db,
        user.id,
        attributes={"threshold_pace": {"estimate": 260.0, "confidence": 0.7}},
        likely_limiter=None,
        limiters=[],
        source_window_days=120,
        derived_from_rides=3,
    )
    assert await metrics_service.get_effective_threshold_pace(db, user) == 260.0


@pytest.mark.asyncio
async def test_an_unconfident_fit_does_not_price_anything(db: AsyncSession) -> None:
    user = await crud.create_user(
        db, email="unsure-pace@example.com", name="R", hashed_password="x"
    )
    await crud.upsert_athlete_performance_model(
        db,
        user.id,
        attributes={"threshold_pace": {"estimate": 260.0, "confidence": 0.2}},
        likely_limiter=None,
        limiters=[],
        source_window_days=120,
        derived_from_rides=3,
    )
    assert await metrics_service.get_effective_threshold_pace(db, user) is None


@pytest.mark.asyncio
async def test_an_athlete_with_neither_has_no_threshold_pace(db: AsyncSession) -> None:
    user = await crud.create_user(
        db, email="no-pace@example.com", name="R", hashed_password="x"
    )
    assert await metrics_service.get_effective_threshold_pace(db, user) is None


@pytest.mark.parametrize(
    "attributes",
    [
        None,
        "threshold_pace",
        {},
        {"threshold_pace": None},
        {"threshold_pace": {"estimate": None, "confidence": 0.9}},
        {"threshold_pace": {"estimate": 0, "confidence": 0.9}},
        {"threshold_pace": {"estimate": 260.0}},
        {"threshold_pace": {"estimate": True, "confidence": 0.9}},
        {"threshold_pace": {"estimate": 260.0, "confidence": True}},
    ],
)
def test_an_unusable_attribute_map_yields_no_pace(attributes):
    assert metrics_service.threshold_pace_from_attributes(attributes) is None


# ---------------------------------------------------------------------------
# What the coach is shown: pace for runs, watts for rides, never crossed
# ---------------------------------------------------------------------------


def _strava_run(**overrides) -> dict:
    activity = {
        "id": 1,
        "name": "Morning Run",
        "type": "Run",
        "sport_type": "Run",
        "start_date_local": "2026-10-01T07:00:00Z",
        "distance": 10000.0,
        "moving_time": 2500,
        "average_speed": 4.0,
    }
    activity.update(overrides)
    return activity


def _strava_ride(**overrides) -> dict:
    activity = {
        "id": 2,
        "name": "Afternoon Ride",
        "type": "Ride",
        "sport_type": "Ride",
        "start_date_local": "2026-10-01T15:00:00Z",
        "distance": 40000.0,
        "moving_time": 4500,
        "average_speed": 8.9,
        "average_watts": 201,
        "weighted_average_watts": 215,
    }
    activity.update(overrides)
    return activity


def test_a_run_is_shown_its_pace_and_its_zones():
    block = prompts.run_pace_block([_strava_run()], THRESHOLD_PACE)
    assert "4:10 /km" in block  # the threshold itself
    assert "average pace 4:10 /km" in block
    assert "Threshold" in block


def test_a_pace_is_never_shown_as_a_bare_decimal():
    block = prompts.run_pace_block([_strava_run()], THRESHOLD_PACE)
    assert "min:sec per km" in block
    assert "250.0" not in block


def test_pace_zones_never_appear_for_a_cycling_batch():
    assert prompts.run_pace_block([_strava_ride()], THRESHOLD_PACE) == ""


def test_watt_zones_never_appear_for_a_running_batch():
    """The mirror of the gate above, which #711 already built from its side."""
    message = prompts.analyse_activities_user(
        [_strava_run()],
        computed_section="",
        ride_analyses_section="",
        sport_type="running",
        threshold_pace_seconds_per_km=THRESHOLD_PACE,
        user_ftp=250,
    )
    assert "Your power zones" not in message
    assert "Your pace zones" in message


def test_a_mixed_batch_earns_both_blocks():
    """A batch of runs with one ride needs the boundaries to discuss the ride."""
    message = prompts.analyse_activities_user(
        [_strava_run(), _strava_ride()],
        computed_section="",
        ride_analyses_section="",
        sport_type="cycling",
        threshold_pace_seconds_per_km=THRESHOLD_PACE,
        user_ftp=250,
    )
    assert "Your power zones" in message
    assert "Your pace zones" in message


def test_without_a_threshold_pace_the_coach_is_told_to_make_no_judgement():
    """Silence is filled with an invented threshold; saying so is the fix."""
    block = prompts.run_pace_block([_strava_run()], None)
    assert "No running threshold pace is established" in block
    assert "do not state or imply a pace zone" in block


def test_a_runs_elevation_is_named_as_the_reason_a_pace_looks_slow():
    block = prompts.run_pace_block(
        [_strava_run(total_elevation_gain=240.0)], THRESHOLD_PACE
    )
    assert "elevation +240 m" in block
    assert "do not read a slow pace with elevation gain as a drop in form" in block


def test_a_pace_is_derived_from_distance_when_the_provider_sent_no_speed():
    block = prompts.run_pace_block(
        [_strava_run(average_speed=None)], THRESHOLD_PACE
    )
    assert "average pace 4:10 /km" in block


def test_a_run_with_nothing_to_say_still_gets_the_zones():
    block = prompts.run_pace_block(
        [_strava_run(average_speed=None, distance=None, moving_time=None)],
        THRESHOLD_PACE,
    )
    assert "Per-run pace" not in block
    assert "Your pace zones" in block


def test_an_empty_batch_produces_nothing():
    assert prompts.run_pace_block([], THRESHOLD_PACE) == ""


# ---------------------------------------------------------------------------
# A run uploaded as a .fit file
# ---------------------------------------------------------------------------


class _FakeFitRecord:
    """A .fit message, which the parser reads by iterating its fields."""

    def __init__(self, fields: dict) -> None:
        self._fields = [SimpleNamespace(name=k, value=v) for k, v in fields.items()]

    def __iter__(self):
        return iter(self._fields)


class _FakeFitFile:
    """Just enough of the fitdecode interface for ``_parse_fit_activity``."""

    messages: dict[str, list] = {}

    def __init__(self, _stream) -> None:
        pass

    def parse(self) -> None:
        pass

    def get_messages(self, message_type: str):
        return list(type(self).messages.get(message_type, []))


def _fake_run_fit(*, seconds: int = 1800, speed: float = 4.0) -> type:
    start = datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)
    records = [
        _FakeFitRecord(
            {
                "timestamp": start.replace(second=0) + timedelta(seconds=t),
                "enhanced_speed": speed,
                "heart_rate": 150,
            }
        )
        for t in range(seconds + 1)
    ]
    return type(
        "RunFitFile",
        (_FakeFitFile,),
        {
            "messages": {
                "session": [
                    _FakeFitRecord(
                        {
                            "sport": "running",
                            "total_elapsed_time": seconds,
                            "start_time": start,
                            "avg_heart_rate": 150,
                        }
                    )
                ],
                "record": records,
            }
        },
    )


def test_an_uploaded_run_arrives_with_a_time_base_for_its_speed():
    """A speed without a clock is not a pace.

    The .fit parser only emitted ``time`` alongside ``watts``, so a run — which
    has no power stream — arrived with ``velocity_smooth`` and nothing to read it
    against, and the pace model could not see it at all.
    """
    parsed = users_router._parse_fit_activity(b"", "run.fit", _fake_run_fit())
    assert parsed.streams["time"]["data"]
    assert len(parsed.streams["time"]["data"]) == len(
        parsed.streams["velocity_smooth"]["data"]
    )
    assert "watts" not in parsed.streams


def test_an_uploaded_run_is_priced_by_pace_like_an_imported_one():
    parsed = users_router._parse_fit_activity(b"", "run.fit", _fake_run_fit())
    chain = build_ride_metrics_chain(
        [
            {
                "strava_activity_id": 7,
                "activity_date": "2026-10-01",
                "sport_type": parsed.sport_type,
                "duration_seconds": 1800,
                "streams": parsed.streams,
            }
        ],
        ftp=250.0,
        max_heart_rate=190,
        threshold_pace_seconds_per_km=THRESHOLD_PACE,
    )
    assert chain[0]["tss_source"] == LOAD_SOURCE_PACE
    assert run_model.is_run_signals(chain[0]["perf_signals"])


def test_a_ride_upload_still_keeps_its_power_time_base():
    """The new branch must not displace the one it sits beside."""
    start = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
    ride = type(
        "RideFitFile",
        (_FakeFitFile,),
        {
            "messages": {
                "session": [
                    _FakeFitRecord({"sport": "cycling", "total_elapsed_time": 600})
                ],
                "record": [
                    _FakeFitRecord(
                        {
                            "timestamp": start + timedelta(seconds=t),
                            "power": 220,
                            "enhanced_speed": 9.0,
                        }
                    )
                    for t in range(601)
                ],
            }
        },
    )
    parsed = users_router._parse_fit_activity(b"", "ride.fit", ride)
    assert len(parsed.streams["time"]["data"]) == len(parsed.streams["watts"]["data"])


def test_the_profile_the_coach_reads_carries_a_rendered_pace_not_a_number():
    """Every ``UserProfileSchema`` call site dumps it into a prompt as JSON.

    A bare ``250`` there is the unlabelled figure #467 spent an issue removing
    from the power block — and a reader has no way to know whether it is
    seconds, a pace per mile, or a speed.
    """
    import models

    user = models.User(
        email="profile@example.com",
        hashed_password="x",
        bike_type="road",
        training_goal="race",
        fitness_level="intermediate",
        follows_training_plan=False,
        threshold_pace_seconds_per_km=THRESHOLD_PACE,
    )
    dumped = schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True)
    assert dumped["thresholdPace"] == "4:10 /km"
    assert "thresholdPaceSecondsPerKm" not in dumped

    user.threshold_pace_seconds_per_km = None
    assert schemas.UserProfileSchema.from_user(user).threshold_pace is None


def _fake_stryd_run_fit(*, seconds: int = 1800, speed: float = 4.0) -> type:
    """A footpod run: power *and* speed, recorded at different rates.

    The .fit parser drops a power sample of zero and keeps a speed sample of
    zero, so the two streams diverge over any run with a pause in it — which is
    why the power clock cannot be used to read the speed.
    """
    start = datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)
    records = []
    for t in range(seconds + 1):
        fields = {
            "timestamp": start + timedelta(seconds=t),
            "enhanced_speed": speed,
            "heart_rate": 150,
        }
        # Standing at the lights: the footpod reports no power, but the run is
        # still being recorded.
        if not 600 <= t < 660:
            fields["power"] = 280
        records.append(_FakeFitRecord(fields))
    return type(
        "StrydRunFitFile",
        (_FakeFitFile,),
        {
            "messages": {
                "session": [
                    _FakeFitRecord(
                        {"sport": "running", "total_elapsed_time": seconds}
                    )
                ],
                "record": records,
            }
        },
    )


def test_a_footpod_run_still_gets_a_pace_model():
    """Power is what makes this hard: it hands the run a clock of its own.

    With a `watts` stream the parser's primary `time` is the *power* clock, so a
    run with any pause in it has more speed samples than power samples. Reading
    the speed against `time` finds a length mismatch, produces no distance, and
    drops the run to heart rate — the exact failure this PR set out to remove,
    arriving by a different door.
    """
    parsed = users_router._parse_fit_activity(b"", "stryd.fit", _fake_stryd_run_fit())
    assert "watts" in parsed.streams
    assert len(parsed.streams["velocity_smooth"]["data"]) != len(
        parsed.streams["time"]["data"]
    ), "the premise: the two streams really do diverge"

    chain = build_ride_metrics_chain(
        [
            {
                "strava_activity_id": 9,
                "activity_date": "2026-10-01",
                "sport_type": parsed.sport_type,
                "duration_seconds": 1800,
                "streams": parsed.streams,
            }
        ],
        ftp=250.0,
        max_heart_rate=190,
        threshold_pace_seconds_per_km=THRESHOLD_PACE,
    )
    assert chain[0]["tss_source"] == LOAD_SOURCE_PACE
    assert run_model.is_run_signals(chain[0]["perf_signals"])
    assert chain[0]["perf_signals"]["distance_m"] > 0
    # And still no cycling claim from the footpod's watts (#711).
    assert chain[0]["intensity_factor"] is None


# ---------------------------------------------------------------------------
# Correcting a threshold pace re-prices the runs it priced
# ---------------------------------------------------------------------------


class _Row:
    """A ``RideMetric`` as the recalculation loop sees it.

    Lightweight rather than a DB row, matching ``test_per_sport_fitness``: the
    loop is pure, and the relationships a real row carries are what make it
    expensive to build.
    """

    def __init__(self, *, tss: float, source: str, perf_signals: dict | None = None):
        self.activity_date = "2026-10-01"
        self.sport_type = "Run"
        self.tss = tss
        self.tss_source = source
        self.normalized_power_w = None
        self.duration_seconds = HOUR
        self.intensity_factor = None
        self.ftp_used = None
        self.perf_signals = perf_signals
        self.ctl_after = self.atl_after = self.tsb_after = None
        self.ctl_by_sport = None
        self.matched_plan_snapshot = None


def _priced_run() -> _Row:
    return _Row(
        tss=100.0,
        source=LOAD_SOURCE_PACE,
        perf_signals={
            "sport": run_model.RUN_SIGNALS_SPORT,
            "duration_s": HOUR,
            "moving_duration_s": HOUR,
            "distance_m": round(THRESHOLD_SPEED * HOUR),
        },
    )


def test_correcting_a_threshold_pace_reprices_the_runs():
    """A new threshold pace moves every rTSS, as a new FTP moves every TSS.

    A runner who corrects their threshold and finds their history unchanged has
    been given a button that does not do what it says. Here the threshold turns
    out to be 10 % slower, so the same run was done at a higher fraction of it
    and cost 1,1² more.
    """
    row = _priced_run()
    metrics_service._recalculate_metric_chain([row], 250, THRESHOLD_PACE * 1.1)

    assert row.tss_source == LOAD_SOURCE_PACE
    assert row.tss == pytest.approx(121.0, abs=1.0)


def test_the_same_threshold_pace_leaves_the_figure_where_it_was():
    row = _priced_run()
    metrics_service._recalculate_metric_chain([row], 250, THRESHOLD_PACE)
    assert row.tss == pytest.approx(100.0, abs=1.0)


def test_clearing_a_threshold_pace_does_not_wipe_a_runs_load():
    """Re-pricing to nothing would be #579 again by a different route."""
    row = _priced_run()
    metrics_service._recalculate_metric_chain([row], 250, None)
    assert row.tss == pytest.approx(100.0)
    assert row.tss_source == LOAD_SOURCE_PACE


def test_a_run_with_no_stored_envelope_cannot_be_repriced():
    """Rows imported before #716 carry no pace signals; they keep their load."""
    row = _Row(tss=100.0, source=LOAD_SOURCE_PACE, perf_signals=None)
    metrics_service._recalculate_metric_chain([row], 250, THRESHOLD_PACE * 1.1)
    assert row.tss == pytest.approx(100.0)


def test_a_threshold_pace_does_not_touch_a_run_the_pace_rung_never_priced():
    """The mirror of #579's rule: only the rung's own rows are its to rewrite."""
    row = _Row(tss=64.0, source=LOAD_SOURCE_HEART_RATE, perf_signals=_run_signals())
    metrics_service._recalculate_metric_chain([row], 250, THRESHOLD_PACE * 1.1)
    assert row.tss == pytest.approx(64.0)
    assert row.tss_source == LOAD_SOURCE_HEART_RATE


@pytest.mark.asyncio
async def test_the_recalculation_reads_the_athletes_threshold_pace(
    db: AsyncSession,
) -> None:
    """The wiring: the rebuild has to fetch the pace, not just accept one."""
    user = await crud.create_user(
        db, email="reprice-wiring@example.com", name="R", hashed_password="x"
    )
    user.current_ftp = 250
    user.threshold_pace_seconds_per_km = THRESHOLD_PACE
    seen: list[float | None] = []
    original = metrics_service._recalculate_metric_chain

    def _capture(all_metrics, ftp_value, threshold=None):
        seen.append(threshold)
        return original(all_metrics, ftp_value, threshold)

    metrics_service._recalculate_metric_chain = _capture
    try:
        await crud.upsert_ride_metric(
            db,
            user.id,
            strava_activity_id=4711,
            activity_date="2026-10-01",
            sport_type="Run",
            duration_seconds=HOUR,
            tss=100.0,
            tss_source=LOAD_SOURCE_PACE,
        )
        await db.refresh(user, ["rider_assessment"])
        await metrics_service.recalculate_metrics_for_user(db, user)
    finally:
        metrics_service._recalculate_metric_chain = original

    assert seen == [THRESHOLD_PACE]


def test_a_ride_from_today_is_not_treated_as_having_no_recency():
    """0 days old is falsy, and an ``or`` would substitute the other sport's."""
    attributes = ami.infer_performance_attributes(
        [
            SimpleNamespace(
                perf_signals=compute_ride_performance_signals(
                    {
                        "time": {"data": [float(t) for t in range(0, 2400, 5)]},
                        "watts": {"data": [250.0] * 480},
                    }
                ),
                ftp_used=250,
                activity_date=NOW.date().isoformat(),
            )
        ],
        now=NOW,
    )
    fresh = attributes["ftp"]["confidence"]

    stale = ami.infer_performance_attributes(
        [
            SimpleNamespace(
                perf_signals=compute_ride_performance_signals(
                    {
                        "time": {"data": [float(t) for t in range(0, 2400, 5)]},
                        "watts": {"data": [250.0] * 480},
                    }
                ),
                ftp_used=250,
                activity_date="2026-01-01",
            )
        ],
        now=NOW,
    )["ftp"]["confidence"]
    assert fresh > stale


def test_an_unreadable_speed_curve_contributes_nothing_to_the_envelope():
    """A stored blob is whatever an older version wrote; read it defensively."""
    junk = [
        SimpleNamespace(
            perf_signals={
                "sport": run_model.RUN_SIGNALS_SPORT,
                "gap_speed_curve": "not a curve",
            },
            ftp_used=None,
            activity_date="2026-10-01",
        ),
        SimpleNamespace(
            perf_signals={
                "sport": run_model.RUN_SIGNALS_SPORT,
                "speed_curve": {"5": "fast", "10": None, "20": True, "oops": 4.0},
            },
            ftp_used=None,
            activity_date="2026-10-01",
        ),
    ]
    attributes = ami.infer_performance_attributes(junk, now=NOW)
    assert attributes["critical_speed"]["estimate"] is None
    assert attributes["critical_speed"]["score"] == "unknown"


def test_the_envelope_takes_each_durations_best_across_runs():
    """Two runs, each strongest at a different duration, make one envelope."""
    short = {str(int(m)): round(_envelope_curve(m), 3) for m in (2, 3)}
    long = {str(int(m)): round(_envelope_curve(m), 3) for m in (10, 20)}
    attributes = ami.infer_performance_attributes(
        [
            SimpleNamespace(
                perf_signals={"sport": run_model.RUN_SIGNALS_SPORT, "speed_curve": short},
                ftp_used=None,
                activity_date="2026-10-02",
            ),
            SimpleNamespace(
                perf_signals={"sport": run_model.RUN_SIGNALS_SPORT, "speed_curve": long},
                ftp_used=None,
                activity_date="2026-10-01",
            ),
        ],
        now=NOW,
    )
    assert attributes["critical_speed"]["estimate"] == pytest.approx(4.0, abs=0.05)
    assert "2 run(s)" in attributes["critical_speed"]["evidence"][0]
