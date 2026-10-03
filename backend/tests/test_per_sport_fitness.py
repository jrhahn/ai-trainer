"""Per-sport fitness, aggregated fatigue (#713).

CTL and ATL were one pair of numbers for the whole athlete. Once a run and a gym
hour carry a load (#712), that pair makes a claim that is simply false: that
running made the athlete better at cycling. #713 keeps a CTL per sport and one
ATL across all of them, and derives TSB per sport as that sport's fitness against
the shared fatigue.

These pin the three things the issue asks for — a pure-cycling history unchanged,
a running week that shows up as cycling *fatigue* and not cycling fitness, and
pre-#713 rows still readable — plus the places the split could leak: the sport a
session is booked to, the two-a-day that must not decay a day twice, and the
import paths that seed an incremental chain from the newest stored row.
"""

from __future__ import annotations

import pytest

from services import analysis, metrics_service
from services.activity_identity import (
    SPORT_CYCLING,
    SPORT_RUNNING,
    SPORT_STRENGTH,
    power_model_applies,
)
from services.fitness_ledger import (
    ALPHA_CTL,
    LoadLedger,
    apply_ctl_atl_decay,
    ledger_from_metric,
    ledger_from_row,
    ledger_sport,
)

HOUR = 3600


def _ride(date: str, *, sport: str = "Ride", tss: float = 70.0, ident: int) -> dict:
    """A ride input whose load the provider already priced.

    Provider load is the top rung of the ladder, so these fixtures state the
    load directly instead of depending on a power stream — this file is about
    what the chain *does* with a load, not where the load came from.
    """
    return {
        "strava_activity_id": ident,
        "activity_date": date,
        "sport_type": sport,
        "duration_seconds": HOUR,
        "streams": {},
        "_summary_tss": tss,
    }


def _chain(rides: list[dict], **kwargs) -> list[dict]:
    return analysis.build_ride_metrics_chain(rides, 250.0, **kwargs)


# ---------------------------------------------------------------------------
# Acceptance criterion 1: a pure-cycling history is untouched
# ---------------------------------------------------------------------------


def test_a_pure_cycling_history_is_unchanged_by_the_split():
    """The old single-pair recurrence, reproduced exactly.

    With one sport there is nothing else to decay, so the ledger has to reduce
    to ``apply_ctl_atl_decay`` applied in sequence. Asserted against the
    primitive rather than against hard-coded numbers, because what matters is
    that the two agree — a number copied out of a previous run would still agree
    with itself after a regression in both.
    """
    rides = [
        _ride(f"2026-03-{day:02d}", tss=70.0, ident=day)
        # Deliberately gappy: 2 days, 1 day, 4 days — silent-day decay is where a
        # reimplementation of the recurrence is most likely to differ.
        for day in (1, 3, 4, 8, 9, 10)
    ]
    chain = _chain(rides)

    expected_ctl = expected_atl = 0.0
    previous = None
    for metric in chain:
        day = int(metric["activity_date"][-2:])
        gap = 1 if previous is None else day - previous
        expected_ctl, expected_atl = apply_ctl_atl_decay(
            expected_ctl, expected_atl, 70.0, gap_days=gap
        )
        previous = day
        assert metric["ctl_after"] == pytest.approx(round(expected_ctl, 2))
        assert metric["atl_after"] == pytest.approx(round(expected_atl, 2))
        assert metric["tsb_after"] == pytest.approx(
            round(expected_ctl - expected_atl, 2)
        )
        # The one new field: a single-sport athlete's ledger has one rung, and it
        # holds exactly what ``ctl_after`` says.
        assert metric["ctl_by_sport"] == {SPORT_CYCLING: metric["ctl_after"]}


def test_the_manual_recalculation_agrees_with_the_import_chain():
    """``recalculate_metrics_for_user`` replays the chain in a second place.

    Two implementations of the same recurrence is how the pre-#712 projections
    drifted apart, so this asserts the replay against the chain rather than
    against its own output.
    """

    class _Row:
        def __init__(self, date: str, sport: str, tss: float):
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

    sports = ["Ride", "Run", "Ride", "WeightTraining", "Ride"]
    rows = [
        _Row(f"2026-04-{i + 1:02d}", sport, 70.0) for i, sport in enumerate(sports)
    ]
    metrics_service._recalculate_metric_chain(rows, 250)

    chain = _chain(
        [
            _ride(f"2026-04-{i + 1:02d}", sport=sport, tss=70.0, ident=i)
            for i, sport in enumerate(sports)
        ]
    )

    for row, metric in zip(rows, chain):
        assert row.ctl_after == metric["ctl_after"]
        assert row.atl_after == metric["atl_after"]
        assert row.tsb_after == metric["tsb_after"]
        assert row.ctl_by_sport == metric["ctl_by_sport"]


# ---------------------------------------------------------------------------
# Acceptance criterion 2: a running week is fatigue, not cycling fitness
# ---------------------------------------------------------------------------


def test_a_week_of_running_is_cycling_fatigue_without_cycling_fitness():
    """The whole point of the split, stated as one comparison.

    Two athletes ride identically for three weeks. One then takes a week off,
    the other runs every day of it. The runner must end the week *more* fatigued
    and *less* fresh on the bike, with no more cycling fitness than the one who
    rested — and strictly less, because cycling CTL decays either way.
    """
    base = [_ride(f"2026-05-{day:02d}", tss=70.0, ident=day) for day in range(1, 22)]
    rest_week = _chain(base)
    running_week = _chain(
        base
        + [
            _ride(f"2026-05-{day:02d}", sport="Run", tss=70.0, ident=day)
            for day in range(22, 29)
        ]
    )

    rested = rest_week[-1]
    ran = running_week[-1]

    # Fitness: the run added none, and the week's decay took some away.
    assert ran["ctl_by_sport"][SPORT_CYCLING] < rested["ctl_after"]
    # Fatigue: shared, so the running week raised it far above where a rest week
    # would have left it.
    assert ran["atl_after"] > rested["atl_after"]
    # Form on the bike: lower, which is what the legs report.
    assert ran["ctl_by_sport"][SPORT_CYCLING] - ran["atl_after"] < rested["tsb_after"]
    # And the run's own row is about running: its ``ctl_after`` is the running
    # rung, not the cycling one.
    assert ran["ctl_after"] == ran["ctl_by_sport"][SPORT_RUNNING]
    assert ran["tsb_after"] == pytest.approx(ran["ctl_after"] - ran["atl_after"])


def test_the_aggregate_fatigue_does_not_care_which_sport_produced_it():
    """Same loads on the same dates, different sports → the same ATL.

    Fatigue is the half of the model that stays shared, and the cleanest way to
    say so is that permuting the sports cannot move it.
    """
    dates = [f"2026-06-{day:02d}" for day in range(1, 15)]
    all_cycling = _chain(
        [_ride(d, tss=65.0, ident=i) for i, d in enumerate(dates)]
    )
    mixed = _chain(
        [
            _ride(d, sport=("Run" if i % 3 else "Ride"), tss=65.0, ident=i)
            for i, d in enumerate(dates)
        ]
    )
    assert mixed[-1]["atl_after"] == all_cycling[-1]["atl_after"]
    # Fitness, by contrast, is split three ways and so each rung is lower.
    assert mixed[-1]["ctl_by_sport"][SPORT_CYCLING] < all_cycling[-1]["ctl_after"]


def test_a_gym_session_builds_strength_fitness_not_cycling_fitness():
    """#579 gave strength work a load; #713 stops it being a cycling load.

    This is the case the epic opened with: an hour in the gym is real fatigue,
    and before #579 it made TSB *rise*. Giving it a load fixed the sign but
    booked it to cycling fitness, which is the remaining half of the error.
    """
    chain = _chain(
        [
            _ride("2026-07-01", tss=80.0, ident=1),
            _ride("2026-07-02", sport="WeightTraining", tss=55.0, ident=2),
        ]
    )
    gym = chain[-1]
    assert set(gym["ctl_by_sport"]) == {SPORT_CYCLING, SPORT_STRENGTH}
    assert gym["ctl_after"] == gym["ctl_by_sport"][SPORT_STRENGTH]
    # Cycling fitness only decayed; the gym hour added nothing to it.
    assert gym["ctl_by_sport"][SPORT_CYCLING] < chain[0]["ctl_after"]
    # The fatigue is still counted in full.
    assert gym["atl_after"] > 0.0


# ---------------------------------------------------------------------------
# Acceptance criterion 3: rows written before the split still read
# ---------------------------------------------------------------------------


def test_a_row_without_a_ledger_reads_back_as_cycling():
    """What a pre-#713 ``ctl_after`` meant: every sport summed into one number
    that the dashboard, the coach and the plan projection all read as cycling.
    """
    ledger = ledger_from_row(None, ctl_after=61.4, atl_after=70.2)
    assert ledger.ctl(SPORT_CYCLING) == 61.4
    assert ledger.atl == 70.2
    assert ledger.tsb(SPORT_CYCLING) == pytest.approx(-8.8)
    # And nothing is invented for the sports that row never mentioned.
    assert ledger.ctl(SPORT_RUNNING) == 0.0


def test_an_incremental_import_continues_every_sport_not_just_the_last_one():
    """The regression the five seeding call sites would otherwise have.

    Each import path seeds the chain from the newest stored row. If it seeds from
    ``ctl_after`` alone, then the first time an athlete's most recent activity is
    a run, the next ride's chain starts from a *running* CTL — and their cycling
    fitness silently collapses to whatever their running fitness happens to be.
    """

    class _StoredRow:
        ctl_after = 9.5           # the last session was a run
        atl_after = 55.0
        ctl_by_sport = {SPORT_CYCLING: 61.4, SPORT_RUNNING: 9.5}

    seeded = _chain(
        [_ride("2026-08-02", tss=70.0, ident=1)],
        initial_ledger=ledger_from_metric(_StoredRow()),
    )[0]

    # Cycling continued from 61.4, not from the run's 9.5.
    assert seeded["ctl_after"] > 61.0
    # Running only decayed — the stored rung was carried, not dropped. (Stored
    # at the 2 dp ``ctl_after`` is rounded to.)
    assert seeded["ctl_by_sport"][SPORT_RUNNING] == round(9.5 * (1 - ALPHA_CTL), 2)

    # Seeding from the scalar instead is what the bug looks like, so the helper
    # has to be the thing every path uses.
    naive = analysis.build_ride_metrics_chain(
        [_ride("2026-08-02", tss=70.0, ident=1)], 250.0, 9.5, 55.0
    )[0]
    assert naive["ctl_after"] < 11.0


def test_a_missing_metric_row_seeds_an_empty_ledger():
    """A first-ever import has no row to continue from, which is not an error."""
    assert ledger_from_metric(None) == LoadLedger()
    assert ledger_from_metric(None).ctl(SPORT_CYCLING) == 0.0


# ---------------------------------------------------------------------------
# The ledger itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sport_type", "expected"),
    [
        ("Ride", SPORT_CYCLING),
        ("VirtualRide", SPORT_CYCLING),
        ("Handcycle", SPORT_CYCLING),
        ("Run", SPORT_RUNNING),
        ("TrailRun", SPORT_RUNNING),
        ("WeightTraining", SPORT_STRENGTH),
        ("Yoga", SPORT_STRENGTH),
        # A label that names no sport has to go somewhere, and everything else in
        # the codebase reads an unlabelled activity as a ride — including the
        # power model, which is what priced its load.
        (None, SPORT_CYCLING),
        ("", SPORT_CYCLING),
        ("Workout", SPORT_CYCLING),
        ("Other", SPORT_CYCLING),
    ],
)
def test_every_session_is_booked_to_a_rung(sport_type, expected):
    assert ledger_sport(sport_type) == expected


def test_the_rung_agrees_with_the_currency_the_load_was_priced_in():
    """The rule that decides the rung is the rule that decided the number.

    A session the cycling power model is entitled to answer for carries cycling
    TSS, computed against the athlete's cycling FTP. Booking that load anywhere
    but the cycling rung would file a number in one currency under a sport it
    says nothing about — which is the whole error #713 exists to undo, committed
    in the other direction.

    ``Handcycle`` and ``Workout`` are the two cases where the planner's
    ``training_sport`` and this question genuinely differ, and both would
    otherwise have gone to a rung of their own (a handcyclist's entire history
    split off from cycling, a catch-all "Workout" ride booked to strength).
    """
    for sport_type in ("Handcycle", "Workout", "Other", "VirtualRide", None):
        assert power_model_applies(sport_type)
        assert ledger_sport(sport_type) == SPORT_CYCLING

    for sport_type in ("Run", "WeightTraining", "Yoga", "Hike"):
        assert not power_model_applies(sport_type)
        assert ledger_sport(sport_type) != SPORT_CYCLING


def test_a_sport_the_athlete_has_not_touched_reads_zero_not_missing():
    """``0.0`` is a true statement about that sport, so callers need no guard."""
    ledger = LoadLedger({SPORT_CYCLING: 50.0}, atl=40.0)
    assert ledger.ctl("Swim") == 0.0
    assert ledger.tsb("Swim") == -40.0


def test_a_two_a_day_does_not_decay_the_other_sports_twice():
    """The reason ``advance`` takes the raw calendar delta.

    ``gap_days`` is clamped to 1 because the primitive has no notion of "no day
    passed". The sports that did *not* happen do need that notion: clamping a
    same-date second session up to a day would quietly decay every other rung
    for a day that never elapsed.
    """
    start = LoadLedger({SPORT_CYCLING: 50.0, SPORT_RUNNING: 20.0}, atl=40.0)
    same_day = start.advance(sport=SPORT_RUNNING, load=60.0, days_since_previous=0)
    next_day = start.advance(sport=SPORT_RUNNING, load=60.0, days_since_previous=1)

    assert same_day.ctl_by_sport[SPORT_CYCLING] == 50.0
    assert next_day.ctl_by_sport[SPORT_CYCLING] < 50.0
    # The sport that did happen, and the shared fatigue, advance either way —
    # that part is the pre-existing clamped behaviour, unchanged.
    assert same_day.ctl_by_sport[SPORT_RUNNING] == next_day.ctl_by_sport[SPORT_RUNNING]
    assert same_day.atl == next_day.atl


def test_a_two_a_day_reaches_the_ledger_as_a_same_day_session():
    """The clamp change, pinned where it actually landed.

    Both chains used to compute ``gap_days = max(1, delta)`` and now compute
    ``days_since_previous = max(0, delta)``, so the ledger can tell a two-a-day
    from a next-day session. Asserting that only on ``LoadLedger.advance`` would
    leave the two callers free to clamp it back on the way in — and the symptom
    would be a silent one: the morning ride's CTL decaying for an afternoon run
    on the same date.

    Plan identity is ``(date, slot)`` for exactly this reason (#496), so a
    two-a-day is a shape the rest of the app already supports.
    """
    morning_then_afternoon = _chain(
        [
            _ride("2026-10-01", tss=80.0, ident=1),
            _ride("2026-10-01", sport="Run", tss=60.0, ident=2),
        ]
    )
    across_midnight = _chain(
        [
            _ride("2026-10-01", tss=80.0, ident=1),
            _ride("2026-10-02", sport="Run", tss=60.0, ident=2),
        ]
    )

    ride_ctl = morning_then_afternoon[0]["ctl_after"]
    # Same day: the ride's own fitness is untouched by the run that followed it.
    assert morning_then_afternoon[1]["ctl_by_sport"][SPORT_CYCLING] == ride_ctl
    # A day later: it has decayed.
    assert across_midnight[1]["ctl_by_sport"][SPORT_CYCLING] < ride_ctl
    # The sport that trained, and the shared fatigue, advance either way — that
    # is the pre-existing clamped behaviour, which must not have moved.
    assert (
        morning_then_afternoon[1]["ctl_by_sport"][SPORT_RUNNING]
        == across_midnight[1]["ctl_by_sport"][SPORT_RUNNING]
    )
    assert morning_then_afternoon[1]["atl_after"] == across_midnight[1]["atl_after"]


def test_the_recalculation_handles_a_two_a_day_the_same_way():
    """The second copy of the clamp, pinned against the first."""

    class _Row:
        def __init__(self, date: str, sport: str, tss: float):
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

    rows = [_Row("2026-10-01", "Ride", 80.0), _Row("2026-10-01", "Run", 60.0)]
    metrics_service._recalculate_metric_chain(rows, 250)

    chain = _chain(
        [
            _ride("2026-10-01", tss=80.0, ident=1),
            _ride("2026-10-01", sport="Run", tss=60.0, ident=2),
        ]
    )
    assert [r.ctl_by_sport for r in rows] == [m["ctl_by_sport"] for m in chain]
    assert [r.atl_after for r in rows] == [m["atl_after"] for m in chain]
    # Anchored to the behaviour, not only to the other copy: reverting the clamp
    # in *both* places would keep them agreeing with each other while both drifted.
    assert rows[1].ctl_by_sport[SPORT_CYCLING] == rows[0].ctl_after


def test_a_decayed_rung_is_dropped_rather_than_stored_as_zero():
    """A sport untouched for years should not keep an entry alive forever, and
    ``ctl()`` already answers 0.0 for one that is absent.
    """
    ledger = LoadLedger({SPORT_CYCLING: 40.0, SPORT_RUNNING: 0.001}, atl=10.0)
    assert ledger.as_dict() == {SPORT_CYCLING: 40.0}


def test_the_ledger_round_trips_through_its_stored_form():
    ledger = LoadLedger({SPORT_CYCLING: 61.44, SPORT_RUNNING: 9.51}, atl=55.2)
    restored = ledger_from_row(
        ledger.as_dict(), ctl_after=None, atl_after=ledger.atl
    )
    assert restored.ctl(SPORT_CYCLING) == pytest.approx(61.44)
    assert restored.ctl(SPORT_RUNNING) == pytest.approx(9.51)
    assert restored.atl == 55.2


def test_a_stored_ledger_with_junk_values_does_not_poison_the_chain():
    """``ctl_by_sport`` is JSON, so a hand-edited or half-written row can hold
    anything. A bad entry is dropped rather than carried into arithmetic.
    """
    ledger = ledger_from_row(
        {SPORT_CYCLING: 50.0, SPORT_RUNNING: None, "swim": "lots"},
        ctl_after=50.0,
        atl_after=40.0,
    )
    assert ledger.as_dict() == {SPORT_CYCLING: 50.0}


# ---------------------------------------------------------------------------
# The forward projections split the same way
# ---------------------------------------------------------------------------


def _plan(sports: list[str], *, start_day: int = 1) -> list[dict]:
    return [
        {
            "date": f"2026-09-{start_day + i:02d}",
            "durationMinutes": 60,
            "workoutType": "endurance",
            "sport": sport,
        }
        for i, sport in enumerate(sports)
    ]


def test_a_planned_run_is_projected_as_fatigue_not_as_cycling_fitness():
    """The plan side had the same defect as the ride chain.

    Projected CTL and measured CTL have to mean the same thing, or the dashboard
    shows a projection the chain can never reach.
    """
    cycling_only = analysis.compute_training_load(_plan(["cycling"] * 14), 250.0)
    with_a_run = analysis.compute_training_load(
        _plan(["cycling"] * 13 + ["running"]), 250.0
    )

    assert with_a_run["atl"] > cycling_only["atl"]
    assert with_a_run["ctl"] < cycling_only["ctl"]
    assert with_a_run["tsb"] < cycling_only["tsb"]
    assert with_a_run["ctl_by_sport"][SPORT_RUNNING] > 0.0


def test_a_cycling_only_plan_projects_exactly_what_it_did_before():
    """Same guard as for the ride chain: one sport, one rung, old numbers."""
    plan = _plan(["cycling"] * 10)
    projected = analysis.compute_training_load(plan, 250.0)

    # Accumulated from the *unrounded* per-day cost, because that is what the
    # EWMA is fed (#712) — reconstructing from the rounded ``daily_tss`` series
    # drifts by a tenth and would only prove the rounding, not the recurrence.
    ctl = atl = 0.0
    for day in plan:
        ctl, atl = apply_ctl_atl_decay(
            ctl, atl, analysis.planned_day_load(day, 250.0), gap_days=1
        )
    assert projected["ctl"] == round(ctl, 1)
    assert projected["atl"] == round(atl, 1)
    assert projected["tsb"] == round(ctl - atl, 1)
    assert projected["ctl_by_sport"] == {SPORT_CYCLING: projected["ctl"]}


def test_the_seeded_projection_continues_each_sport_from_its_own_rung():
    seeded = analysis.project_training_load_from_seed(
        _plan(["running", "cycling"]),
        250.0,
        seed_ledger=LoadLedger({SPORT_CYCLING: 60.0, SPORT_RUNNING: 10.0}, atl=50.0),
    )
    assert seeded["ctl_by_sport"][SPORT_RUNNING] > 10.0
    assert seeded["ctl"] == pytest.approx(seeded["ctl_by_sport"][SPORT_CYCLING])


def test_the_two_seeding_forms_are_mutually_exclusive():
    """Two ways to state the same state is how a rule drifts, so saying it twice
    is refused rather than silently resolved in favour of one.
    """
    with pytest.raises(ValueError):
        analysis.project_training_load_from_seed(
            [], 250.0, 50.0, 40.0, seed_ledger=LoadLedger()
        )
    with pytest.raises(ValueError):
        analysis.build_ride_metrics_chain(
            [], 250.0, 50.0, 40.0, initial_ledger=LoadLedger()
        )


def test_the_legacy_scalar_seed_is_read_as_cycling():
    """The positional form every pre-#713 caller used still means what it meant."""
    projected = analysis.project_training_load_from_seed([], 0.0, 50.0, 40.0)
    assert projected["ctl"] == 50.0
    assert projected["atl"] == 40.0
    assert projected["tsb"] == 10.0
