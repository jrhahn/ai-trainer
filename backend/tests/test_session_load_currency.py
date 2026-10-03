"""One load currency that can price any session, and says how (#712).

``build_ride_metrics_chain`` once knew exactly one answer: TSS from a power
stream, or nothing — and "nothing" entered the fitness chain as ``0.0``, the
assertion that the athlete rested (#579). The ladder fixed that for imported
activities. What was still missing is the rung for a session no device described
at all, and a *planned* day in a sport that is not cycling: both were priced in
watts the athlete never produced.

These pin the four things #712 asks for: never a silent zero, provenance and
weight travelling with every number, cycling unchanged to the digit, and a
session with nothing but a reported effort still carrying load.
"""

from __future__ import annotations

import pytest

from services import analysis
from services.training_load import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    LOAD_SOURCE_DURATION,
    LOAD_SOURCE_HEART_RATE,
    LOAD_SOURCE_POWER,
    LOAD_SOURCE_PROVIDER,
    LOAD_SOURCE_RPE,
    LOAD_UNIT_TSS,
    LOAD_UNIT_TSS_EQUIVALENT,
    LoadSignals,
    format_load,
    rpe_training_load,
    session_load,
)

HOUR = 3600


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def test_the_rungs_are_consulted_strongest_first():
    """A derived load must never displace a measured one, whatever else is known."""
    everything = {
        "sport_type": "Ride",
        "duration_seconds": HOUR,
        "provider_load": 88.0,
        "power_tss": 94.0,
        "avg_hr_bpm": 150,
        "max_heart_rate": 190,
        "perceived_effort": 4,
    }
    assert session_load(LoadSignals(**everything)).source == LOAD_SOURCE_PROVIDER

    without_provider = {**everything, "provider_load": None}
    assert session_load(LoadSignals(**without_provider)).source == LOAD_SOURCE_POWER

    without_power = {**without_provider, "power_tss": None}
    assert session_load(LoadSignals(**without_power)).source == LOAD_SOURCE_HEART_RATE

    without_hr = {**without_power, "avg_hr_bpm": None}
    assert session_load(LoadSignals(**without_hr)).source == LOAD_SOURCE_RPE

    without_rpe = {**without_hr, "perceived_effort": None}
    assert session_load(LoadSignals(**without_rpe)).source == LOAD_SOURCE_DURATION


def test_a_session_with_only_a_reported_effort_still_carries_load():
    """The acceptance criterion, and the reason the rung exists: a gym hour with
    no power meter and no strap is still an hour of training."""
    load = session_load(
        LoadSignals(
            sport_type="WeightTraining", duration_seconds=HOUR, perceived_effort=4
        )
    )

    assert load is not None
    assert load.tss > 0
    assert load.source == LOAD_SOURCE_RPE


def test_nothing_known_is_no_load_rather_than_a_zero():
    """Zero is a claim about the athlete; absence is a claim about our data. The
    first is wrong by a sign, which is the whole of #579."""
    assert session_load(LoadSignals(sport_type="Run")) is None


@pytest.mark.parametrize(
    "sport_type",
    ["Ride", "Run", "WeightTraining", "Yoga", "Hike", "Swim", "Rowing", "", None],
)
def test_every_sport_yields_a_load_once_a_duration_is_known(sport_type):
    """"Never a silent zero": time on task is the last rung, and it answers for
    any sport — including one the tables have never heard of."""
    load = session_load(LoadSignals(sport_type=sport_type, duration_seconds=HOUR))

    assert load is not None
    assert load.tss > 0


# ---------------------------------------------------------------------------
# Provenance and weight travel with the number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,unit,confidence",
    [
        (LOAD_SOURCE_PROVIDER, LOAD_UNIT_TSS, CONFIDENCE_HIGH),
        (LOAD_SOURCE_POWER, LOAD_UNIT_TSS, CONFIDENCE_HIGH),
        (LOAD_SOURCE_HEART_RATE, LOAD_UNIT_TSS_EQUIVALENT, CONFIDENCE_MEDIUM),
        (LOAD_SOURCE_RPE, LOAD_UNIT_TSS_EQUIVALENT, CONFIDENCE_MEDIUM),
        (LOAD_SOURCE_DURATION, LOAD_UNIT_TSS_EQUIVALENT, CONFIDENCE_LOW),
    ],
)
def test_each_rung_declares_its_unit_and_its_confidence(source, unit, confidence):
    """Everything lands on one scale so CTL/ATL can add it up, but only the top
    two are TSS in the sense an athlete's training software means it."""
    signals = {
        LOAD_SOURCE_PROVIDER: LoadSignals(provider_load=80.0),
        LOAD_SOURCE_POWER: LoadSignals(power_tss=80.0),
        LOAD_SOURCE_HEART_RATE: LoadSignals(
            duration_seconds=HOUR, avg_hr_bpm=150, max_heart_rate=190
        ),
        LOAD_SOURCE_RPE: LoadSignals(duration_seconds=HOUR, perceived_effort=3),
        LOAD_SOURCE_DURATION: LoadSignals(duration_seconds=HOUR, sport_type="Hike"),
    }[source]

    load = session_load(signals)
    assert load.source == source
    assert load.unit == unit
    assert load.confidence == confidence


def test_an_rpe_load_can_never_be_read_as_a_measurement():
    load = session_load(LoadSignals(duration_seconds=HOUR, perceived_effort=4))

    assert load.is_measured is False
    rendered = format_load(load.tss, load.source)
    assert "TSS" not in rendered
    assert "from reported effort" in rendered
    assert "medium confidence" in rendered


# ---------------------------------------------------------------------------
# Session-RPE (Foster)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "effort,expected",
    [(1, 22.0), (2, 44.0), (3, 66.0), (4, 88.0), (5, 110.0)],
)
def test_the_reported_effort_scale_maps_to_a_defensible_hour(effort, expected):
    """sRPE is linear and TSS is quadratic, so the two scales are aligned at the
    top — an hour at a reported maximum is 110, about what a maximal hour costs.
    Anchoring at threshold instead put an hour at 5/5 at 143, which no hour of
    training has ever cost. The middle of the scale then lands beside the assumed
    per-hour figures, which is the sanity check that matters."""
    assert rpe_training_load(duration_seconds=HOUR, perceived_effort=effort) == expected


def test_an_effort_outside_the_scale_is_not_a_load():
    """The stored scale is 1-5. A 0 or a 7 is a data-entry artefact, and clamping
    it would quietly turn one into a training figure."""
    assert rpe_training_load(duration_seconds=HOUR, perceived_effort=0) is None
    assert rpe_training_load(duration_seconds=HOUR, perceived_effort=7) is None
    assert rpe_training_load(duration_seconds=HOUR, perceived_effort=None) is None
    assert rpe_training_load(duration_seconds=0, perceived_effort=4) is None


def test_reported_effort_beats_an_assumption_about_the_sport():
    """Both price an hour of lifting, but one of them asked the athlete."""
    reported = session_load(
        LoadSignals(
            sport_type="WeightTraining", duration_seconds=HOUR, perceived_effort=4
        )
    )
    assumed = session_load(
        LoadSignals(sport_type="WeightTraining", duration_seconds=HOUR)
    )

    assert reported.source == LOAD_SOURCE_RPE
    assert assumed.source == LOAD_SOURCE_DURATION
    assert reported.confidence != assumed.confidence


# ---------------------------------------------------------------------------
# A planned day is priced in its own sport
# ---------------------------------------------------------------------------


def _day(**kwargs) -> dict:
    return {"date": "2026-10-05", "durationMinutes": 60, **kwargs}


def test_a_planned_cycling_day_is_priced_exactly_as_before():
    """The regression guard. mid-point 250 W against FTP 250 → IF 1.0 → 100 TSS."""
    day = _day(workoutType="endurance", targetPower={"low": 240, "high": 260})

    assert analysis.planned_day_load(day, 250.0) == pytest.approx(100.0, abs=0.5)


def test_a_planned_cycling_day_without_a_target_still_uses_the_ftp_table():
    day = _day(workoutType="endurance", durationMinutes=90)

    # 68 % of FTP for 90 minutes, unchanged.
    assert analysis.planned_day_load(day, 250.0) == pytest.approx(69.4, abs=0.5)


def test_a_planned_run_is_not_priced_as_a_bike_ride():
    """It used to be 68 % of the athlete's FTP for the duration — a number about
    cycling that went straight into CTL (#710 gave the day a sport to ask)."""
    run = _day(sport="running", workoutType="endurance", durationMinutes=45)

    priced = analysis.planned_day_load(run, 250.0)
    as_a_ride = analysis.planned_day_load(
        _day(workoutType="endurance", durationMinutes=45), 250.0
    )

    assert priced != as_a_ride
    assert priced > 0


def test_a_planned_gym_day_is_priced_the_same_way_the_logged_one_is():
    """They disagreed: a planned gym hour came out at 65 % of FTP in watts while
    the same hour, once logged, was priced from the per-sport table. One session
    cannot cost two different amounts depending on which side of it you ask."""
    planned = analysis.planned_day_load(
        _day(sport="strength", workoutType="strength"), 250.0
    )
    logged = session_load(
        LoadSignals(sport_type="strength", duration_seconds=HOUR)
    )

    assert planned == pytest.approx(logged.tss)


def test_a_legacy_strength_day_with_no_sport_field_is_priced_as_strength():
    """Every plan written before #710 says ``workoutType: "strength"`` and
    nothing else, and that *is* a statement about the sport."""
    assert analysis.planned_day_load(
        _day(workoutType="strength"), 250.0
    ) == analysis.planned_day_load(
        _day(sport="strength", workoutType="strength"), 250.0
    )


def test_a_planned_rest_day_is_a_real_zero():
    """Unlike an absent load, a rest day genuinely costs nothing — whatever its
    sport says, and whatever duration a malformed day carries."""
    assert analysis.planned_day_load(_day(workoutType="rest", durationMinutes=0), 250.0) == 0.0
    assert analysis.planned_day_load(_day(workoutType="rest", durationMinutes=60), 250.0) == 0.0
    assert analysis.planned_day_load(_day(sport="running", workoutType="rest"), 250.0) == 0.0


def test_the_two_projections_price_a_plan_identically():
    """``compute_training_load`` and ``project_training_load_from_seed`` carried a
    copy each of the estimator, which is two places for one rule to drift."""
    plan = [
        _day(workoutType="endurance", durationMinutes=90),
        _day(sport="strength", workoutType="strength"),
        _day(sport="running", workoutType="endurance", durationMinutes=45),
        _day(workoutType="rest", durationMinutes=0),
    ]

    simulated = analysis.compute_training_load(plan, 250.0)["daily_tss"]
    seeded = analysis.project_training_load_from_seed(
        plan, 250.0, seed_ctl=0.0, seed_atl=0.0
    )["daily_tss"]

    assert simulated == seeded


def test_a_plan_of_gym_sessions_does_not_project_as_rest():
    """A week of strength work used to move CTL only through a watt figure
    invented from the athlete's FTP; now it moves it through the sport."""
    week = [_day(sport="strength", workoutType="strength") for _ in range(7)]

    assert analysis.compute_training_load(week, 250.0)["ctl"] > 0


# ---------------------------------------------------------------------------
# The HR rung can finally fire
# ---------------------------------------------------------------------------


def _ride(sport_type: str, *, minutes: int = 45, hr: float | None = 150.0) -> dict:
    n = minutes * 60
    streams: dict = {"time": {"data": [float(i) for i in range(n)]}}
    if hr is not None:
        streams["heartrate"] = {"data": [hr] * n}
    return {
        "strava_activity_id": 7700,
        "activity_date": "2026-10-05",
        "sport_type": sport_type,
        "duration_seconds": n,
        "streams": streams,
    }


def test_a_run_with_a_heart_rate_stream_is_priced_from_it():
    """The rung had never once fired from stream data: the ladder looked for
    ``perf_signals["avg_hr_bpm"]`` while the writer stored ``avg_hr``, and since
    #711 ``perf_signals`` is ``None`` for every non-cycling activity anyway —
    exactly the sessions whose load has to come from heart rate."""
    metrics = analysis.build_ride_metrics_chain(
        [_ride("Run")], ftp=250.0, max_heart_rate=190
    )[0]

    assert metrics["tss_source"] == LOAD_SOURCE_HEART_RATE
    assert metrics["tss"] > 0


def test_a_run_without_a_heart_rate_stream_falls_to_its_sport():
    metrics = analysis.build_ride_metrics_chain(
        [_ride("Run", hr=None)], ftp=250.0, max_heart_rate=190
    )[0]

    assert metrics["tss_source"] == LOAD_SOURCE_DURATION
    assert metrics["tss"] > 0


def test_a_misaligned_heart_rate_stream_is_not_averaged():
    """A length mismatch means the samples do not correspond, so a mean over
    them is a number about nothing."""
    ride = _ride("Run")
    ride["streams"]["heartrate"]["data"] = ride["streams"]["heartrate"]["data"][:10]

    metrics = analysis.build_ride_metrics_chain(
        [ride], ftp=250.0, max_heart_rate=190
    )[0]

    assert metrics["tss_source"] == LOAD_SOURCE_DURATION
