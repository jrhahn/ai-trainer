"""What a gym session was, in the gym's own units (#714).

A cyclist who lifts has been invisible to this app's numbers. #579 stopped a
strength session counting as a rest day and #713 stopped its load being banked as
cycling fitness, but both treat the session as a quantity of *fatigue* and
nothing else — which is the same as saying the athlete went to the gym and did
something unspecified for an hour.

This module gives the session its own currency, and deliberately keeps it there:
**tonnage and e1RM are never converted into TSS.** The conversion is what the
rest of the epic exists to avoid. A strength session reaches the fitness/fatigue
chain through session-RPE (``services.training_load``) and lands on the strength
rung of the ledger (``services.fitness_ledger``); nothing here feeds cycling
fitness, and ``test_strength_model.py`` pins that as an invariant rather than a
convention.

Three derived quantities, each answering a different question:

``volume_load`` — how much work was moved. Σ reps × kg over every set, the
standard tonnage figure. It is the honest measure of a session's size and the
one a progression can be read from week to week.

``e1rm`` — how strong the athlete is, estimated from a set they actually did.
Progression is tracked on this and never on a true single-rep test: a 1RM attempt
costs a cyclist a day of training and carries an injury risk that an estimate
does not, and the estimate is accurate enough for prescription.

``relative_intensity`` — how hard that weight was for *this* athlete, as %e1RM.
The same 100 kg is a warm-up for one lifter and a maximal effort for another, so
an absolute weight says nothing portable about intensity.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# Epley and Brzycki are the two formulas in general use. They agree closely in
# the 3-10 rep range that strength work for endurance athletes actually lives in,
# and diverge outside it: Brzycki reads lower as reps rise (and collapses
# entirely at 37, see below), Epley reads higher. Neither is "correct" — they are
# regressions on different populations — so both are offered and the caller's
# choice is recorded rather than averaged away.
FORMULA_EPLEY = "epley"
FORMULA_BRZYCKI = "brzycki"
E1RM_FORMULAS: tuple[str, ...] = (FORMULA_EPLEY, FORMULA_BRZYCKI)

# Epley is the default: above ~10 reps Brzycki underestimates badly, and a set of
# 12-15 is common in the accessory work a cyclist does.
DEFAULT_E1RM_FORMULA = FORMULA_EPLEY

# Brzycki is ``w * 36 / (37 - reps)``, which divides by zero at 37 reps and goes
# *negative* beyond it. A 40-rep set is a real thing an athlete can log (bodyweight
# work, high-rep core), and a negative e1RM would propagate into a progression
# chart as a strength collapse. Above this the estimate is refused instead.
BRZYCKI_MAX_REPS = 36

# Beyond roughly 15 reps both formulas are extrapolating well past the data they
# were fitted on: a 30-rep set is a muscular-endurance effort and says little
# about maximal strength. The estimate is still returned — refusing it would
# throw away the only evidence some sessions produce — but it is marked so a
# trend line can show it differently from a 5-rep single-set estimate.
E1RM_CONFIDENT_MAX_REPS = 15

# How many reps the athlete had left. RIR 0 is a set taken to failure, RIR 2
# means two more were available. Zourdos' scale and the RPE scale it maps onto
# are the autoregulation unit strength coaching actually uses, because the
# weight that gives "3x5 at RIR 2" differs by the day and the plan cannot know
# it in advance.
MAX_RIR = 10


def _positive(value: object) -> float | None:
    """A strictly positive float, or ``None`` for anything else.

    Shaped like ``training_load._positive`` on purpose: both are reading
    athlete-supplied numbers that arrive as strings, ``None``, or nonsense from
    a form, and both have to answer "is there a usable number here" rather than
    raising.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return number if number > 0 else None


def _non_negative_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


@dataclass(frozen=True)
class StrengthSet:
    """One set as the athlete performed it.

    ``rir`` and ``rpe`` are both optional and both optional *per set*, because
    that is how autoregulated sessions are actually logged: the first two sets of
    a top-set/back-off scheme are nowhere near failure and nobody records a
    number for them, while the working set is the one that matters.
    """

    exercise: str
    reps: int
    weight_kg: float
    rir: int | None = None
    rpe: float | None = None

    @property
    def volume_load(self) -> float:
        """Work moved by this set: reps × kg."""
        return self.reps * self.weight_kg


def effective_reps(reps: int, rir: int | None) -> int:
    """Reps the set would have had if taken to failure.

    This is what makes RIR usable for strength estimation rather than only for
    prescription. A 5-rep set at RIR 2 is evidence about a 7-rep maximum, and
    feeding the raw 5 into an e1RM formula understates the athlete by exactly the
    reps they left in reserve — which then shows up as a plateau for an athlete
    who is in fact getting stronger while training further from failure.

    ``None`` RIR is not treated as 0. A missing number is missing; assuming the
    set was taken to failure would silently overstate every unannotated set, and
    conservative in the direction of *understating* strength is the right error
    here, because the prescription built on it is a weight the athlete has to
    lift.
    """
    adjusted = _non_negative_int(rir)
    if adjusted is None:
        return reps
    return reps + min(adjusted, MAX_RIR)


def e1rm(
    weight_kg: float,
    reps: int,
    *,
    rir: int | None = None,
    formula: str = DEFAULT_E1RM_FORMULA,
) -> float | None:
    """Estimated one-rep max from a set the athlete actually completed.

    Returns ``None`` when the set cannot support an estimate — a non-positive
    weight, zero reps, or (for Brzycki) a rep count the formula cannot express.
    ``None`` rather than 0.0 for the same reason a missing training load is
    ``None`` (#579): zero would read as "this athlete can lift nothing".

    A single at RIR 0 *is* a 1RM, so both formulas are short-circuited at one
    effective rep. Epley would otherwise return ``w × (1 + 1/30)`` — 3,3 % above
    a weight that was measured, which is the one case where the estimate should
    be exact.
    """
    load = _positive(weight_kg)
    if load is None:
        return None
    if reps <= 0:
        return None

    total_reps = effective_reps(reps, rir)
    if total_reps <= 1:
        return load

    if formula == FORMULA_BRZYCKI:
        if total_reps > BRZYCKI_MAX_REPS:
            return None
        return load * 36.0 / (37.0 - total_reps)
    return load * (1.0 + total_reps / 30.0)


def e1rm_is_confident(reps: int, rir: int | None = None) -> bool:
    """Whether this set's rep count is inside the range the formulas were fitted on.

    Separate from the estimate itself so a caller can show a 25-rep set's e1RM
    without implying it is as good evidence as a 5-rep set's — the same
    measured-versus-estimated distinction ``training_load`` draws for load.
    """
    return 0 < effective_reps(reps, rir) <= E1RM_CONFIDENT_MAX_REPS


def relative_intensity(weight_kg: float, reference_e1rm: float | None) -> float | None:
    """What fraction of the athlete's estimated maximum this weight is, in %.

    ``None`` when there is no reference to compare against, which is the normal
    state for a brand-new exercise — the first session on it *is* the reference,
    and inventing a percentage before one exists would be a number about nothing.
    """
    load = _positive(weight_kg)
    reference = _positive(reference_e1rm)
    if load is None or reference is None:
        return None
    return load / reference * 100.0


def volume_load(sets: Iterable[StrengthSet]) -> float:
    """Total work moved across these sets, in kg-reps.

    Summed across exercises without weighting. A kilo moved on a squat and a kilo
    moved on a calf raise are not equivalent training stimuli, and tonnage does
    not pretend otherwise — it is a measure of session *size*, which is what a
    week-to-week progression needs. The stimulus question is what %e1RM and the
    exercise breakdown are for.
    """
    return sum(one_set.volume_load for one_set in sets)


def best_e1rm_per_exercise(
    sets: Iterable[StrengthSet],
    *,
    formula: str = DEFAULT_E1RM_FORMULA,
) -> dict[str, float]:
    """The strongest estimate each exercise produced in these sets.

    Best rather than last or mean: a session's back-off sets are deliberately
    submaximal, so averaging them reads as a weaker athlete every time the
    session includes any. The top set is the evidence; the rest are volume.
    """
    best: dict[str, float] = {}
    for one_set in sets:
        estimate = e1rm(
            one_set.weight_kg, one_set.reps, rir=one_set.rir, formula=formula
        )
        if estimate is None:
            continue
        current = best.get(one_set.exercise)
        if current is None or estimate > current:
            best[one_set.exercise] = estimate
    return best


@dataclass(frozen=True)
class E1rmPoint:
    """One day's best estimate for one exercise, with the set behind it.

    The set travels with the number so a surprising point can be explained
    rather than only shown: an e1RM that jumps 15 kg because the athlete logged
    a high-RIR set of 20 is a reading about the estimate, not about the athlete,
    and a trend the athlete cannot interrogate is a trend they are asked to take
    on faith.
    """

    date: str
    e1rm_kg: float
    confident: bool
    reps: int
    weight_kg: float
    rir: int | None = None


def e1rm_trend(
    dated_sets: Iterable[tuple[str, StrengthSet]],
    *,
    formula: str = DEFAULT_E1RM_FORMULA,
) -> list[E1rmPoint]:
    """One point per date: that session's best estimate for the exercise.

    Best-per-day rather than every set, because a session's back-off sets would
    otherwise draw a sawtooth that reads as the athlete getting weaker and
    stronger within the hour. Progression is a line through the top sets.

    Dates with no estimable set are absent rather than zero — a session of
    unweighted work says nothing about maximal strength, and plotting it as 0 kg
    would read as a total loss of it.
    """
    best: dict[str, E1rmPoint] = {}
    for date, one_set in dated_sets:
        estimate = e1rm(
            one_set.weight_kg, one_set.reps, rir=one_set.rir, formula=formula
        )
        if estimate is None:
            continue
        current = best.get(date)
        if current is not None and current.e1rm_kg >= estimate:
            continue
        best[date] = E1rmPoint(
            date=date,
            e1rm_kg=estimate,
            confident=e1rm_is_confident(one_set.reps, one_set.rir),
            reps=one_set.reps,
            weight_kg=one_set.weight_kg,
            rir=one_set.rir,
        )
    return [best[date] for date in sorted(best)]


def set_from_row(row: object) -> StrengthSet:
    """Read a stored row back into the dataclass the formulas take.

    So the arithmetic has exactly one input shape and never learns about the ORM
    — which is what keeps this module testable without a database and keeps the
    import-graph invariant in ``test_strength_model.py`` meaningful.
    """
    return StrengthSet(
        exercise=getattr(row, "exercise", "") or "",
        reps=int(getattr(row, "reps", 0) or 0),
        weight_kg=float(getattr(row, "weight_kg", 0.0) or 0.0),
        rir=getattr(row, "rir", None),
        rpe=getattr(row, "rpe", None),
    )


def normalize_exercise_name(name: str | None) -> str:
    """One spelling per exercise, so a trend is not split across three of them.

    "Back Squat", "back squat" and "back  squat" are one exercise with one
    progression. Without this the e1RM history an athlete sees depends on how
    they typed it, and the "trends over time, per exercise" the feature exists
    for silently fragments.
    """
    return " ".join((name or "").strip().casefold().split())


def set_from_payload(payload: Mapping[str, object]) -> StrengthSet | None:
    """Read one logged set from a request body, or ``None`` if it says nothing.

    Validation lives here rather than in the schema because "this set is not
    loggable" and "this request is malformed" are different answers: a form that
    submits an empty trailing row is normal, and rejecting the whole session for
    it would lose the sets the athlete did fill in.
    """
    exercise = normalize_exercise_name(
        payload.get("exercise") if isinstance(payload.get("exercise"), str) else None
    )
    reps = _non_negative_int(payload.get("reps"))
    weight = _positive(payload.get("weight_kg") or payload.get("weightKg"))
    if not exercise or not reps or weight is None:
        return None

    rir = _non_negative_int(payload.get("rir"))
    rpe = _positive(payload.get("rpe"))
    return StrengthSet(
        exercise=exercise,
        reps=reps,
        weight_kg=weight,
        rir=min(rir, MAX_RIR) if rir is not None else None,
        rpe=rpe,
    )
