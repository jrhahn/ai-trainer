"""A gym session in the gym's own units (#714).

A cyclist who lifts was invisible to this app's numbers. #579 stopped a strength
session counting as a rest day and #713 stopped its load being banked as cycling
fitness — but both treat the session as an amount of fatigue and nothing else,
which is the same as recording that the athlete did something unspecified for an
hour.

These pin the three derived quantities, the places the formulas break, and the
invariant the whole epic rests on: **tonnage never becomes TSS and never reaches
cycling fitness.**
"""

from __future__ import annotations

import pytest

from services.strength_model import (
    BRZYCKI_MAX_REPS,
    DEFAULT_E1RM_FORMULA,
    FORMULA_BRZYCKI,
    FORMULA_EPLEY,
    MAX_RIR,
    StrengthSet,
    best_e1rm_per_exercise,
    e1rm,
    e1rm_is_confident,
    e1rm_trend,
    effective_reps,
    normalize_exercise_name,
    relative_intensity,
    set_from_payload,
    volume_load,
)


# ---------------------------------------------------------------------------
# e1RM
# ---------------------------------------------------------------------------


def test_a_true_single_is_not_inflated_by_the_formula():
    """The one case where the estimate must be exact.

    Epley is ``w × (1 + reps/30)``, which at one rep returns 3,3 % above a weight
    that was *measured*. An athlete who tests a single and sees the app credit
    them with more than they lifted has been told something false about the only
    unambiguous data point strength training produces.
    """
    assert e1rm(100.0, 1) == 100.0
    assert e1rm(100.0, 1, formula=FORMULA_BRZYCKI) == 100.0
    # Same for a single with the reserve stated as zero.
    assert e1rm(140.0, 1, rir=0) == 140.0


def test_epley_and_brzycki_agree_where_strength_work_actually_lives():
    """3-10 reps is the range a cyclist's lifting sits in, and the two formulas
    agree there to within a couple of percent — which is why the choice between
    them is not worth agonising over, and why both are offered rather than one
    being declared correct.
    """
    for reps in range(3, 11):
        epley = e1rm(100.0, reps, formula=FORMULA_EPLEY)
        brzycki = e1rm(100.0, reps, formula=FORMULA_BRZYCKI)
        assert epley is not None and brzycki is not None
        assert abs(epley - brzycki) / epley < 0.04


def test_brzycki_refuses_the_rep_count_it_cannot_express():
    """``w × 36 / (37 − reps)`` divides by zero at 37 and goes *negative* past it.

    A 40-rep set is a real thing to log — bodyweight work, high-rep core — and a
    negative e1RM would propagate into a progression chart as a strength
    collapse. ``None`` says "no estimate from this set", which is true.
    """
    assert e1rm(50.0, BRZYCKI_MAX_REPS, formula=FORMULA_BRZYCKI) is not None
    assert e1rm(50.0, 37, formula=FORMULA_BRZYCKI) is None
    assert e1rm(50.0, 40, formula=FORMULA_BRZYCKI) is None
    # Epley has no pole, so it still answers — high, but not nonsense.
    assert e1rm(50.0, 40, formula=FORMULA_EPLEY) > 50.0


def test_rir_is_refused_as_a_way_past_the_brzycki_pole():
    """RIR raises the *effective* rep count, so it can walk a legal set over the
    pole. 30 reps at RIR 8 is 38 effective — the guard has to sit after the
    adjustment, not before it.
    """
    assert e1rm(40.0, 30, rir=6, formula=FORMULA_BRZYCKI) is not None
    assert e1rm(40.0, 30, rir=8, formula=FORMULA_BRZYCKI) is None


@pytest.mark.parametrize("weight", [0.0, -100.0, None, "heavy", float("nan")])
def test_a_set_with_no_usable_weight_yields_no_estimate(weight):
    """``None`` rather than 0.0, for the same reason a missing training load is
    ``None`` (#579): zero would read as "this athlete can lift nothing".
    """
    assert e1rm(weight, 5) is None


def test_zero_reps_is_not_a_set():
    assert e1rm(100.0, 0) is None
    assert e1rm(100.0, -3) is None


# ---------------------------------------------------------------------------
# RIR
# ---------------------------------------------------------------------------


def test_reps_in_reserve_count_towards_the_estimate():
    """What makes RIR usable for estimation and not only for prescription.

    A 5-rep set at RIR 2 is evidence about a 7-rep maximum. Feeding the raw 5
    into the formula understates the athlete by exactly the reps they left in
    reserve — which shows up as a plateau for someone who is in fact getting
    stronger while training further from failure.
    """
    assert effective_reps(5, 2) == 7
    assert e1rm(100.0, 5, rir=2) == e1rm(100.0, 7)


def test_a_missing_reserve_is_not_read_as_failure():
    """``None`` RIR is missing, not zero.

    Assuming an unannotated set was taken to failure would overstate every one of
    them, and the error has to fall the other way: the prescription built on this
    number is a weight the athlete then has to lift.
    """
    assert effective_reps(5, None) == 5
    assert e1rm(100.0, 5, rir=None) == e1rm(100.0, 5)
    assert e1rm(100.0, 5, rir=None) < e1rm(100.0, 5, rir=3)


def test_an_implausible_reserve_is_clamped_rather_than_trusted():
    """A form can submit RIR 50. Honouring it would credit a 5-rep set as a
    55-rep maximum.
    """
    assert effective_reps(5, 99) == 5 + MAX_RIR


@pytest.mark.parametrize("rir", [-1, "two", None])
def test_an_unreadable_reserve_falls_back_to_the_reps_performed(rir):
    assert effective_reps(8, rir) == 8


def test_the_confidence_band_follows_the_effective_reps():
    """Beyond ~15 reps both formulas extrapolate past their fitted data: a 30-rep
    set is muscular endurance and says little about maximal strength. The
    estimate is still returned — refusing it would discard the only evidence some
    sessions produce — but it is marked, the same measured-versus-estimated
    distinction ``training_load`` draws for load.
    """
    assert e1rm_is_confident(5)
    assert e1rm_is_confident(15)
    assert not e1rm_is_confident(16)
    # RIR pushes a nominally confident set out of the band.
    assert not e1rm_is_confident(14, 4)
    assert not e1rm_is_confident(0)


# ---------------------------------------------------------------------------
# Tonnage and relative intensity
# ---------------------------------------------------------------------------


def test_volume_load_is_reps_times_weight_summed():
    sets = [
        StrengthSet(exercise="back squat", reps=5, weight_kg=100.0),
        StrengthSet(exercise="back squat", reps=5, weight_kg=100.0),
        StrengthSet(exercise="romanian deadlift", reps=8, weight_kg=80.0),
    ]
    assert volume_load(sets) == pytest.approx(5 * 100 + 5 * 100 + 8 * 80)


def test_volume_load_of_nothing_is_zero_not_none():
    """Unlike a *load*, a tonnage of zero is a true statement: no sets were
    logged, so no work was moved. The ``None``-not-zero rule from #579 is about
    an absent measurement, which this is not.
    """
    assert volume_load([]) == 0.0


def test_relative_intensity_needs_a_reference_to_exist():
    """The first session on a new exercise *is* the reference. Inventing a
    percentage before one exists would be a number about nothing.
    """
    assert relative_intensity(90.0, 120.0) == pytest.approx(75.0)
    assert relative_intensity(90.0, None) is None
    assert relative_intensity(90.0, 0.0) is None
    assert relative_intensity(0.0, 120.0) is None


def test_the_top_set_is_the_evidence_and_the_rest_are_volume():
    """Best rather than last or mean.

    A top-set/back-off session's later sets are deliberately submaximal, so
    averaging them reads as a weaker athlete every time the session includes any.
    """
    sets = [
        StrengthSet(exercise="back squat", reps=3, weight_kg=140.0, rir=1),
        StrengthSet(exercise="back squat", reps=8, weight_kg=110.0, rir=3),
        StrengthSet(exercise="back squat", reps=8, weight_kg=110.0, rir=2),
        StrengthSet(exercise="bench press", reps=5, weight_kg=90.0),
    ]
    best = best_e1rm_per_exercise(sets)

    assert set(best) == {"back squat", "bench press"}
    assert best["back squat"] == pytest.approx(
        max(
            e1rm(140.0, 3, rir=1),
            e1rm(110.0, 8, rir=3),
            e1rm(110.0, 8, rir=2),
        )
    )
    assert best["bench press"] == pytest.approx(e1rm(90.0, 5))


def test_an_exercise_whose_sets_support_no_estimate_is_absent_not_zero():
    sets = [StrengthSet(exercise="plank", reps=1, weight_kg=0.0)]
    assert best_e1rm_per_exercise(sets) == {}


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("Back Squat", "back squat"),
        ("back  squat", "back squat"),
        ("  BACK SQUAT  ", "back squat"),
        ("back\tsquat", "back squat"),
        (None, ""),
        ("", ""),
    ],
)
def test_one_spelling_per_exercise(written, expected):
    """Without this the e1RM history an athlete sees depends on how they typed
    it, and the per-exercise trend the feature exists for silently fragments
    across three spellings of one lift.
    """
    assert normalize_exercise_name(written) == expected


# ---------------------------------------------------------------------------
# Reading a logged set
# ---------------------------------------------------------------------------


def test_a_logged_set_is_read_with_its_reserve():
    one_set = set_from_payload(
        {"exercise": "Back Squat", "reps": 5, "weight_kg": 102.5, "rir": 2}
    )
    assert one_set == StrengthSet(
        exercise="back squat", reps=5, weight_kg=102.5, rir=2, rpe=None
    )


def test_the_camel_case_spelling_the_browser_sends_is_accepted():
    one_set = set_from_payload({"exercise": "Bench", "reps": 8, "weightKg": 70.0})
    assert one_set is not None and one_set.weight_kg == 70.0


@pytest.mark.parametrize(
    "payload",
    [
        {"exercise": "", "reps": 5, "weight_kg": 100.0},
        {"exercise": "squat", "reps": 0, "weight_kg": 100.0},
        {"exercise": "squat", "reps": 5, "weight_kg": 0.0},
        {"exercise": "squat", "reps": 5},
        {"exercise": None, "reps": 5, "weight_kg": 100.0},
        {},
    ],
)
def test_an_empty_row_is_dropped_rather_than_failing_the_session(payload):
    """A form that submits an empty trailing row is normal. Rejecting the whole
    session for it would lose the sets the athlete did fill in — which is the
    difference between "this set is not loggable" and "this request is
    malformed", and the reason validation sits here rather than in the schema.
    """
    assert set_from_payload(payload) is None


def test_an_implausible_logged_reserve_is_clamped_on_the_way_in():
    one_set = set_from_payload(
        {"exercise": "squat", "reps": 5, "weight_kg": 100.0, "rir": 99}
    )
    assert one_set is not None and one_set.rir == MAX_RIR


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


def test_nothing_here_converts_strength_work_into_cycling_load():
    """The invariant the issue asks to be pinned rather than assumed.

    Tonnage and e1RM are kilos. The moment either is multiplied into a TSS, a
    gym session starts building cycling fitness again — which is precisely the
    error #713 removed, re-committed one layer up. A strength session reaches the
    chain through session-RPE and lands on the strength rung; there is no path
    from this module into cycling CTL, and this test exists so that adding one
    requires deleting a test that says why not.
    """
    import ast
    import inspect

    from services import strength_model

    # Asserted on the import graph rather than on the source text, so a docstring
    # that *mentions* the load machinery does not trip it and an actual import
    # cannot hide behind an alias.
    tree = ast.parse(inspect.getsource(strength_model))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}" for a in node.names)

    forbidden = ("training_load", "fitness_ledger", "analysis")
    assert not [
        name for name in imported if any(part in name for part in forbidden)
    ], "strength_model is upstream of the load chain and must not price itself in its units"

    # And the public surface offers kilos, reps and percentages only — no name
    # here hands back a TSS or touches a fitness figure.
    public = [name for name in dir(strength_model) if not name.startswith("_")]
    assert not [n for n in public if "tss" in n.lower() or "ctl" in n.lower()]


# ---------------------------------------------------------------------------
# The trend line
# ---------------------------------------------------------------------------


def test_the_trend_takes_the_best_estimate_from_each_session():
    """Back-off sets would otherwise draw a sawtooth.

    Within one hour the athlete does not get weaker and stronger again; the top
    set is the session's statement about maximal strength and the back-offs are
    the same session's accumulated volume.
    """
    dated = [
        ("2026-10-01", StrengthSet("back squat", 5, 100.0, rir=2)),
        ("2026-10-01", StrengthSet("back squat", 8, 80.0, rir=2)),
        ("2026-10-08", StrengthSet("back squat", 5, 105.0, rir=2)),
    ]

    points = e1rm_trend(dated)

    assert [p.date for p in points] == ["2026-10-01", "2026-10-08"]
    assert points[0].weight_kg == 100.0
    assert points[1].e1rm_kg > points[0].e1rm_kg


def test_a_session_with_nothing_estimable_is_absent_rather_than_zero():
    """Unweighted work says nothing about maximal strength.

    Plotting it as 0 kg would read as a total loss of it — the same mistake as
    pricing a session the ladder cannot read at zero load (#579).
    """
    dated = [
        ("2026-10-01", StrengthSet("plank", 1, 0.0)),
        ("2026-10-02", StrengthSet("push-ups", 0, 0.0)),
        ("2026-10-03", StrengthSet("back squat", 5, 100.0, rir=2)),
    ]

    points = e1rm_trend(dated)

    assert [p.date for p in points] == ["2026-10-03"]


def test_the_trend_reports_the_set_behind_each_point():
    """So a surprising point can be explained rather than only shown."""
    points = e1rm_trend([("2026-10-01", StrengthSet("deadlift", 3, 140.0, rir=1))])

    only = points[0]
    assert (only.reps, only.weight_kg, only.rir) == (3, 140.0, 1)
    assert only.confident is True


def test_a_rep_count_outside_the_fitted_range_is_marked_not_hidden():
    """A 25-rep set still yields an estimate; it is weaker evidence.

    The trend line has to be able to say so rather than quietly presenting it
    beside a 5-rep top set as though they carried equal weight.
    """
    points = e1rm_trend([("2026-10-01", StrengthSet("leg press", 25, 60.0))])

    assert len(points) == 1
    assert points[0].confident is False


def test_the_trend_can_be_drawn_on_either_formula():
    """Checked at 15 reps, because Epley and Brzycki coincide exactly at 10.

    36/27 == 1 + 10/30, so a formula-choice test anchored at ten reps passes
    whichever formula actually ran.
    """
    dated = [("2026-10-01", StrengthSet("back squat", 15, 80.0))]

    epley = e1rm_trend(dated, formula=FORMULA_EPLEY)[0].e1rm_kg
    brzycki = e1rm_trend(dated, formula=FORMULA_BRZYCKI)[0].e1rm_kg

    assert epley != brzycki
    assert epley == pytest.approx(80.0 * (1 + 15 / 30))
    assert brzycki == pytest.approx(80.0 * 36 / (37 - 15))
