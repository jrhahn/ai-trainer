"""A gym session's fatigue reaches ATL and never cycling fitness (#714).

#712 built the session-RPE rung of the load ladder and nothing fed it: the
athlete would log "that gym hour was a 4 out of 5" and the app went on pricing
the session from a flat per-hour assumption about every gym session anybody has
ever done. These pin the feeder, both directions of it, and the invariant the
issue asks to be a test rather than a convention — **a gym session raises
aggregate fatigue and leaves cycling fitness alone**.
"""

from __future__ import annotations

import pytest

from services import analysis, metrics_service, reported_effort
from services.activity_identity import SPORT_CYCLING, SPORT_STRENGTH
from services.training_load import (
    LOAD_SOURCE_DURATION,
    LOAD_SOURCE_HEART_RATE,
    LOAD_SOURCE_POWER,
    LOAD_SOURCE_RPE,
)

HOUR = 3600


def _ride(
    date: str,
    *,
    sport: str = "Ride",
    ident: int = 1,
    effort: int | None = None,
    tss: float | None = None,
    hr: float | None = None,
) -> dict:
    ride: dict = {
        "strava_activity_id": ident,
        "activity_date": date,
        "sport_type": sport,
        "duration_seconds": HOUR,
        "streams": {},
    }
    if effort is not None:
        ride["_reported_effort"] = effort
    if tss is not None:
        ride["_summary_tss"] = tss
    if hr is not None:
        ride["_summary_avg_hr_bpm"] = hr
    return ride


class _Log:
    """The fields ``annotate_rides_with_reported_effort`` reads off a log."""

    def __init__(self, date: str, sport: str, effort: int | None):
        self.date = date
        self.sport_type = sport
        self.perceived_effort = effort


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


def test_a_gym_session_raises_fatigue_and_leaves_cycling_fitness_alone():
    """The acceptance criterion, and the invariant #714 asks to pin.

    An hour in the gym is real fatigue — before #579 it made TSB *rise*, which
    is the wrong sign — but it is not cycling fitness, which is what #713
    stopped. Both halves have to hold at once, which is why this asserts them
    together rather than trusting either layer on its own.
    """
    rode_only = analysis.build_ride_metrics_chain(
        [_ride("2026-11-02", tss=80.0, ident=1)], 250.0
    )
    then_lifted = analysis.build_ride_metrics_chain(
        [
            _ride("2026-11-02", tss=80.0, ident=1),
            _ride("2026-11-03", sport="WeightTraining", effort=4, ident=2),
        ],
        250.0,
    )

    gym = then_lifted[-1]
    # The gym hour was priced at all — not silently zero (#579).
    assert gym["tss"] is not None and gym["tss"] > 0
    assert gym["tss_source"] == LOAD_SOURCE_RPE
    # Fatigue rose.
    assert gym["atl_after"] > rode_only[-1]["atl_after"]
    # Cycling fitness did not: it only decayed by the day that passed.
    assert gym["ctl_by_sport"][SPORT_CYCLING] < rode_only[-1]["ctl_after"]
    # The session's own fitness is strength, and that is what its row reports.
    assert gym["ctl_after"] == gym["ctl_by_sport"][SPORT_STRENGTH]
    # And the gym hour made the athlete *less* fresh on the bike, not more.
    cycling_tsb_after_gym = (
        gym["ctl_by_sport"][SPORT_CYCLING] - gym["atl_after"]
    )
    assert cycling_tsb_after_gym < rode_only[-1]["tsb_after"]


def test_no_amount_of_gym_work_ever_builds_cycling_fitness():
    """Stated as a limit rather than a single case.

    A month of daily lifting and nothing else must leave the cycling rung
    strictly decaying. If any path ever routes tonnage or gym load into cycling
    CTL, this is the test that has to be deleted to do it.
    """
    month_of_lifting = [
        _ride(f"2026-11-{day:02d}", sport="WeightTraining", effort=5, ident=day)
        for day in range(1, 29)
    ]
    chain = analysis.build_ride_metrics_chain(month_of_lifting, 250.0)
    # And again seeded with real cycling fitness, so there is something to protect.
    seeded = analysis.build_ride_metrics_chain(month_of_lifting, 250.0, 60.0, 40.0)

    assert all(m["ctl_by_sport"].get(SPORT_CYCLING, 0.0) == 0.0 for m in chain)
    # Monotonically decaying from the seed, never once rising.
    cycling = [m["ctl_by_sport"][SPORT_CYCLING] for m in seeded]
    assert cycling == sorted(cycling, reverse=True)
    assert cycling[-1] < 60.0
    # Fatigue, meanwhile, is carrying all of it.
    assert seeded[-1]["atl_after"] > 40.0


# ---------------------------------------------------------------------------
# The feeder: effort reaching the ladder
# ---------------------------------------------------------------------------


def test_the_reported_effort_beats_a_flat_per_hour_assumption():
    """What the rung is *for*.

    Without a reported effort the only thing left is time on task, which prices
    every gym session the same regardless of what happened in it. With one, two
    sessions of equal length are priced differently — which is the entire claim.
    """
    unreported = analysis.build_ride_metrics_chain(
        [_ride("2026-11-05", sport="WeightTraining", ident=1)], 250.0
    )[0]
    easy = analysis.build_ride_metrics_chain(
        [_ride("2026-11-05", sport="WeightTraining", effort=2, ident=1)], 250.0
    )[0]
    hard = analysis.build_ride_metrics_chain(
        [_ride("2026-11-05", sport="WeightTraining", effort=5, ident=1)], 250.0
    )[0]

    assert unreported["tss_source"] == LOAD_SOURCE_DURATION
    assert easy["tss_source"] == hard["tss_source"] == LOAD_SOURCE_RPE
    assert easy["tss"] < hard["tss"]


def test_a_reported_effort_does_not_displace_a_measurement():
    """The ladder's order is the statement of which evidence beats which (#712).

    Heart rate and power are measurements of the session; a reported effort is
    the athlete's opinion of it. Letting the opinion win would be #579's mistake
    with the signs reversed.
    """
    with_hr = analysis.build_ride_metrics_chain(
        [_ride("2026-11-06", sport="WeightTraining", effort=5, hr=125.0, ident=1)],
        250.0,
        max_heart_rate=190,
    )[0]
    assert with_hr["tss_source"] == LOAD_SOURCE_HEART_RATE

    with_provider = analysis.build_ride_metrics_chain(
        [_ride("2026-11-06", sport="Ride", effort=5, tss=94.0, ident=1)], 250.0
    )[0]
    assert with_provider["tss"] == 94.0


# ---------------------------------------------------------------------------
# Matching a log to an activity
# ---------------------------------------------------------------------------


def test_the_gym_effort_does_not_get_attached_to_the_evening_ride():
    """The two-a-day this app already models (#496) is exactly a gym session and
    a ride on one date. Pricing the ride from how hard the lifting felt would be
    worse than not pricing it at all.
    """
    rides = [
        _ride("2026-11-07", sport="Ride", ident=1),
        _ride("2026-11-07", sport="WeightTraining", ident=2),
    ]
    matched = reported_effort.annotate_rides_with_reported_effort(
        rides, [_Log("2026-11-07", "strength", 4)]
    )

    assert matched == 1
    assert rides[0].get("_reported_effort") is None
    assert rides[1]["_reported_effort"] == 4


def test_two_logged_sessions_of_one_sport_on_one_date_are_not_guessed():
    """Date and sport cannot tell two gym sessions apart, and picking one would
    make the stored load depend on row order.
    """
    rides = [_ride("2026-11-08", sport="WeightTraining", ident=1)]
    matched = reported_effort.annotate_rides_with_reported_effort(
        rides,
        [_Log("2026-11-08", "strength", 2), _Log("2026-11-08", "strength", 5)],
    )
    assert matched == 0
    assert rides[0].get("_reported_effort") is None


def test_a_log_with_no_effort_recorded_is_not_a_signal():
    rides = [_ride("2026-11-09", sport="WeightTraining", ident=1)]
    assert (
        reported_effort.annotate_rides_with_reported_effort(
            rides, [_Log("2026-11-09", "strength", None)]
        )
        == 0
    )


def test_an_effort_already_on_the_ride_is_not_overwritten():
    """The caller may know better — a .fit upload carries its own answer."""
    rides = [_ride("2026-11-10", sport="WeightTraining", effort=3, ident=1)]
    reported_effort.annotate_rides_with_reported_effort(
        rides, [_Log("2026-11-10", "strength", 5)]
    )
    assert rides[0]["_reported_effort"] == 3


@pytest.mark.parametrize(
    ("logged", "stored", "expected"),
    [
        ("strength", "WeightTraining", 1),
        ("WeightTraining", "Yoga", 1),  # both are the gym bucket for the planner
        ("strength", "Ride", 0),
        ("cycling", "Run", 0),
        ("", "WeightTraining", 0),
        (None, "WeightTraining", 0),
    ],
)
def test_sport_matching_uses_one_vocabulary(logged, stored, expected):
    """Compared through ``training_sport`` so the planner's word and the
    provider's word for one sport are the same word (#710).
    """
    rides = [_ride("2026-11-11", sport=stored, ident=1)]
    assert (
        reported_effort.annotate_rides_with_reported_effort(
            rides, [_Log("2026-11-11", logged, 4)]
        )
        == expected
    )


# ---------------------------------------------------------------------------
# Replaying the chain after a load changes
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, date: str, sport: str, tss: float | None):
        self.activity_date = date
        self.sport_type = sport
        self.tss = tss
        self.tss_source = "provider"
        self.normalized_power_w = None
        self.duration_seconds = HOUR
        self.intensity_factor = None
        self.ftp_used = None
        self.ctl_after = self.atl_after = self.tsb_after = None
        self.ctl_by_sport = None


def test_replaying_the_chain_reproduces_the_import_chain():
    """One ledger loop, shared (#714).

    ``replay_load_chain`` was extracted so that anything changing a stored load
    has one way to fix up the chain after it. It has to agree with the chain the
    import builds, or re-pricing a session would quietly move every figure after
    it onto a second set of rules.
    """
    sports = ["Ride", "WeightTraining", "Ride", "Run"]
    rows = [_Row(f"2026-12-{i + 1:02d}", s, 70.0) for i, s in enumerate(sports)]
    metrics_service.replay_load_chain(rows)

    chain = analysis.build_ride_metrics_chain(
        [
            _ride(f"2026-12-{i + 1:02d}", sport=s, tss=70.0, ident=i)
            for i, s in enumerate(sports)
        ],
        250.0,
    )

    assert [r.ctl_after for r in rows] == [m["ctl_after"] for m in chain]
    assert [r.atl_after for r in rows] == [m["atl_after"] for m in chain]
    assert [r.ctl_by_sport for r in rows] == [m["ctl_by_sport"] for m in chain]


def test_a_row_with_no_load_enters_the_replay_as_a_rest_day_not_a_crash():
    """``None`` load is a session the ladder could not price at all. It must not
    raise, and it must not invent a figure — it just does not advance anything.
    """
    rows = [_Row("2026-12-01", "Ride", None), _Row("2026-12-02", "Ride", 70.0)]
    metrics_service.replay_load_chain(rows)
    assert rows[0].ctl_after == 0.0
    assert rows[1].ctl_after > 0.0


def test_the_replay_does_not_touch_loads_or_power_fields():
    """Separation of concerns, pinned: ``replay_load_chain`` rewrites the chain
    and nothing else, so an FTP recompute and a re-price can share it without
    either one's rules leaking into the other.
    """
    rows = [_Row("2026-12-01", "WeightTraining", 55.0)]
    rows[0].tss_source = LOAD_SOURCE_RPE
    metrics_service.replay_load_chain(rows)

    assert rows[0].tss == 55.0
    assert rows[0].tss_source == LOAD_SOURCE_RPE
    assert rows[0].ftp_used is None
    assert rows[0].intensity_factor is None


def test_an_ftp_recompute_still_leaves_a_reported_load_alone():
    """The #579 guard, re-checked now that the loop is shared.

    A new FTP moves power-derived load and nothing else. An sRPE-priced gym
    session must not be recomputed from power it never had — which would put it
    straight back to entering the chain as a rest day.
    """
    gym = _Row("2026-12-03", "WeightTraining", 55.0)
    gym.tss_source = LOAD_SOURCE_RPE
    ride = _Row("2026-12-04", "Ride", 80.0)
    ride.tss_source = LOAD_SOURCE_POWER
    ride.normalized_power_w = 230.0

    metrics_service._recalculate_metric_chain([gym, ride], 280)

    assert gym.tss == 55.0
    assert gym.tss_source == LOAD_SOURCE_RPE
    # The ride's power-derived load did move with the new FTP.
    assert ride.tss != 80.0
    assert ride.tss_source == LOAD_SOURCE_POWER
