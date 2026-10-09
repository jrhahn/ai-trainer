"""Property-based invariants for the constraint gate.

``tests/test_plan_constraints.py`` covers this module with 24 examples and they
are good examples. What they cannot do is answer the question the gate actually
has to answer in production: given a plan an LLM wrote, which may hold any
combination of key styles, slots, repeated dates and partial fields, does the
gate still hold the line? Examples check the cases someone thought of. These
check the cases nobody thought of.

Every property here is a **prohibition**. None of them asserts what the right
session is — that is a coaching decision, it changes legitimately as the model
improves, and a suite that pins it has to be edited on every improvement and is
then switched off. What a day may never become does not change.

The four families, and why each one is a real failure mode rather than a
hypothetical:

* **The line itself.** No output session trains on a ``no_training`` date. This
  is the one promise the gate makes, and it has to hold for every plan shape,
  not for the shapes in the example file.
* **Identity.** The gate rewrites *content*; it must never add, drop or reorder
  a session. A guard that silently loses a day costs the athlete a session,
  which is the #651 class of bug the coherence module was built after.
* **Idempotence.** The pipeline is not the only caller and a plan can pass a
  gate twice. If sanitising twice differs from sanitising once, a plan drifts
  for no reason anybody can see at the call site.
* **Key style.** ``PlanDay`` is lenient on input by design (#368/#422): a day
  may arrive camelCase or snake_case. Every reader in the module honours both.
  If one reader does not, the gate's behaviour depends on which spelling the LLM
  happened to emit — and that is a bug that no example written in one spelling
  can find.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import schemas
from services.plan_constraints import (
    blocked_dates,
    day_satisfies_required_workout,
    day_violates_constraint,
    sanitize_plan_for_constraints,
)

# A short, dense date pool. Overlap between plan dates and constraint dates is
# the interesting part, so the pool is deliberately small enough that Hypothesis
# hits collisions constantly rather than generating two disjoint sets.
_BASE = date(2026, 6, 1)
DATES = [(_BASE + timedelta(days=i)).isoformat() for i in range(6)]

WORKOUT_TYPES = ["rest", "endurance", "intervals", "threshold", "strength", "vo2max"]
TRAINING_TYPES = [t for t in WORKOUT_TYPES if t != "rest"]


def _is_training(day: dict) -> bool:
    """Read a day the way the module reads it: either spelling, type or duration."""
    workout_type = str(day.get("workoutType") or day.get("workout_type") or "").lower()
    duration = day.get("durationMinutes") or day.get("duration_minutes") or 0
    return workout_type not in {"", "rest"} or int(duration or 0) > 0


def _identity(day: dict) -> tuple[str, int]:
    """A session's identity is ``(date, slot)`` — see ``schemas.session_key``."""
    return str(day.get("date") or ""), int(day.get("slot") or 0)


@st.composite
def days(draw, *, camel: bool | None = None, twins: bool = False) -> dict:
    """One plan session.

    ``camel`` fixes the key spelling; left as ``None`` it is drawn, so a single
    generated plan can mix spellings the way a plan assembled from an LLM write
    and a stored day does.

    ``twins`` writes *both* spellings of the same field, with values drawn
    independently so they may disagree. That is the shape this generator could
    not produce before ai-trainer-ops#38, and it is the only one in which the
    defect class is still alive: a day carrying one spelling canonicalises
    cleanly, while a day carrying two keeps the loser as an "extra" that says
    something the day no longer means.
    """
    use_camel = draw(st.booleans()) if camel is None else camel
    workout_type = draw(st.sampled_from(WORKOUT_TYPES))
    duration = draw(st.integers(min_value=0, max_value=400))
    day: dict = {
        "date": draw(st.sampled_from(DATES)),
        "slot": draw(st.integers(min_value=0, max_value=2)),
        "title": draw(st.sampled_from(["", "Morning ride", "Gym"])),
        "description": draw(st.sampled_from(["", "4x8 at threshold"])),
    }
    if use_camel:
        day["workoutType"] = workout_type
        day["durationMinutes"] = duration
    else:
        day["workout_type"] = workout_type
        day["duration_minutes"] = duration
    if twins:
        day["workoutType"] = workout_type
        day["durationMinutes"] = duration
        day["workout_type"] = draw(st.sampled_from(WORKOUT_TYPES))
        day["duration_minutes"] = draw(st.integers(min_value=0, max_value=400))
    # ``PlanDay`` sets ``extra="allow"`` precisely so unmodelled keys survive a
    # round trip. The gate is upstream of that model and must not drop them either.
    if draw(st.booleans()):
        day["plannerNote"] = "carried through"
    # A snake_case key that is *not* a modelled field. The twin drop must tell
    # the two apart: ``workout_type`` is a second name for a field the model
    # owns, ``planner_note`` is stored data nobody modelled. A filter that went
    # by the underscore rather than by the field list would pass every test
    # above and lose this.
    if draw(st.booleans()):
        day["planner_note"] = "also carried through"
    return day


@st.composite
def plans(draw, *, camel: bool | None = None, twins: bool = False) -> list[dict]:
    return draw(st.lists(days(camel=camel, twins=twins), min_size=0, max_size=8))


def _snake_twins(day: dict) -> list[str]:
    """Modelled fields this day spells in both camelCase and snake_case."""
    return [
        name
        for name, field in schemas.PlanDay.model_fields.items()
        if name in day
        and field.alias
        and field.alias != name
        and field.alias in day
    ]


@st.composite
def constraint_lists(draw) -> list[dict]:
    """At most one constraint of each type per date.

    Two ``required_workout`` constraints on one date is ambiguous input and
    ``_required_workout_for_day`` resolves it by taking the first match, which is
    a defensible choice rather than a defect. Generating that case only produces
    a test that argues with a decision already made, so the generator respects
    it: the interesting space is *which* dates carry *which* constraints, and how
    the two types interact on a shared date.
    """
    blocked = draw(st.lists(st.sampled_from(DATES), unique=True, max_size=3))
    required = draw(st.lists(st.sampled_from(DATES), unique=True, max_size=3))
    constraints = [
        {"constraintType": "no_training", "constraintDate": d} for d in blocked
    ]
    for target in required:
        constraints.append(
            {
                "constraintType": "required_workout",
                "constraintDate": target,
                "requiredWorkout": {
                    "workoutType": draw(st.sampled_from(TRAINING_TYPES)),
                    "minDurationMinutes": draw(st.integers(min_value=0, max_value=180)),
                },
            }
        )
    return draw(st.permutations(constraints))


# Generating plans and constraints is cheap; the default deadline trips on the
# first call while imports warm up, which is noise rather than signal.
_SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# --------------------------------------------------------------------------
# The line itself
# --------------------------------------------------------------------------


@_SETTINGS
@given(plan=plans(), constraints=constraint_lists())
def test_no_output_session_trains_on_an_unavailable_date(plan, constraints):
    """The one promise: a ``no_training`` date comes back free of training.

    Holds regardless of what else is asked of the same date — a required-workout
    constraint on an unavailable day must lose, because the athlete said they
    cannot train, and no optimisation outranks that.
    """
    blocked = {
        c["constraintDate"] for c in constraints if c["constraintType"] == "no_training"
    }
    for out in sanitize_plan_for_constraints(plan, constraints):
        if str(out.get("date") or "") in blocked:
            assert not _is_training(out), f"trained on a blocked date: {out}"


@_SETTINGS
@given(plan=plans(), constraints=constraint_lists())
def test_required_session_is_present_unless_the_day_is_blocked(plan, constraints):
    """A required session is a constraint on the *date*, not on every session.

    So the assertion is that the date holds at least one qualifying session
    afterwards — not that every session on it qualifies, which would rewrite both
    halves of a two-a-day (#496).
    """
    blocked = {
        c["constraintDate"] for c in constraints if c["constraintType"] == "no_training"
    }
    required = {
        c["constraintDate"]: c["requiredWorkout"]
        for c in constraints
        if c["constraintType"] == "required_workout"
    }
    result = sanitize_plan_for_constraints(plan, constraints)
    for target, spec in required.items():
        if target in blocked:
            continue
        sessions = [d for d in result if str(d.get("date") or "") == target]
        if not sessions:
            continue  # the gate rewrites days, it does not invent dates
        assert any(day_satisfies_required_workout(d, spec) for d in sessions), (
            f"no session satisfies {spec} on {target}: {sessions}"
        )


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


@_SETTINGS
@given(plan=plans(), constraints=constraint_lists())
def test_sessions_are_never_added_dropped_or_reordered(plan, constraints):
    """Content may change; the sequence of ``(date, slot)`` may not.

    A gate that loses a session costs the athlete a training day and says
    nothing. That failure is silent in production and obvious here.
    """
    result = sanitize_plan_for_constraints(plan, constraints)
    assert [_identity(d) for d in result] == [_identity(d) for d in plan]


@_SETTINGS
@given(plan=plans(), constraints=constraint_lists())
def test_unmodelled_keys_survive(plan, constraints):
    """``extra="allow"`` is a promise the gate upstream of it has to keep too."""
    result = sanitize_plan_for_constraints(plan, constraints)
    for before, after in zip(plan, result):
        if "plannerNote" in before:
            assert after.get("plannerNote") == before["plannerNote"]
        if "planner_note" in before:
            assert after.get("planner_note") == before["planner_note"]


@_SETTINGS
@given(plan=plans(), constraints=constraint_lists())
def test_days_on_unconstrained_dates_keep_their_content(plan, constraints):
    """No constraint on a date means hands off — the gate must not over-reach.

    "Hands off" is about content, not representation. Since ai-trainer-ops#38 the
    gate returns canonical days, so an unconstrained day can come back spelled
    differently from how it arrived; what it must not come back as is a day that
    *says* something else. Comparing against the canonicalised input is what
    separates those two, and it is still a real assertion: a gate that blanked an
    unconstrained day, or coerced one it should not have, fails it.
    """
    constrained = {c["constraintDate"] for c in constraints}
    result = sanitize_plan_for_constraints(plan, constraints)
    for before, after in zip(plan, result):
        if str(before.get("date") or "") not in constrained:
            assert after == schemas.canonical_plan_day(before)


# --------------------------------------------------------------------------
# Idempotence
# --------------------------------------------------------------------------


@_SETTINGS
@given(plan=plans(), constraints=constraint_lists())
def test_sanitising_twice_is_sanitising_once(plan, constraints):
    """The gate is reachable more than once per plan, so it has to be stable.

    If a second pass differs from the first, a plan drifts on a path nobody can
    see from the call site — the drift-bug class ``PlanDay`` was made canonical
    to end (#368, #422).
    """
    once = sanitize_plan_for_constraints(plan, constraints)
    twice = sanitize_plan_for_constraints(once, constraints)
    assert twice == once


# --------------------------------------------------------------------------
# Key style
# --------------------------------------------------------------------------


@_SETTINGS
@given(plan=plans(camel=True), constraints=constraint_lists())
def test_behaviour_does_not_depend_on_key_spelling(plan, constraints):
    """The same plan in snake_case must be judged the same way.

    ``PlanDay`` accepts either spelling on purpose, so the gate's verdict cannot
    depend on which one the writer used. This compares the *decisions* — is this
    a training day, does it satisfy its requirement — rather than the dicts,
    because the gate legitimately writes camelCase keys either way.
    """
    snake = []
    for day in plan:
        converted = {k: v for k, v in day.items() if k not in {"workoutType", "durationMinutes"}}
        converted["workout_type"] = day["workoutType"]
        converted["duration_minutes"] = day["durationMinutes"]
        snake.append(converted)

    from_camel = sanitize_plan_for_constraints(plan, constraints)
    from_snake = sanitize_plan_for_constraints(snake, constraints)

    for camel_day, snake_day in zip(from_camel, from_snake):
        assert _is_training(camel_day) == _is_training(snake_day), (
            f"spelling changed the verdict:\n camel={camel_day}\n snake={snake_day}"
        )
        assert str(camel_day.get("title") or "") == str(snake_day.get("title") or ""), (
            f"spelling changed the title:\n camel={camel_day}\n snake={snake_day}"
        )


@_SETTINGS
@given(day=days(), constraints=constraint_lists())
def test_a_sanitised_day_does_not_contradict_itself(day, constraints):
    """After the gate, the two spellings on one day must not disagree.

    The gate writes ``workoutType``; a day that arrived in snake_case keeps its
    ``workout_type``. If the gate rewrote the day, those two now say different
    things, and which one a consumer believes depends on the order it reads them
    in. Every reader in this module tries camelCase first, so the stale key is
    invisible here — and that is exactly why it is worth asserting.
    """
    (out,) = sanitize_plan_for_constraints([day], constraints)
    if "workoutType" in out and "workout_type" in out:
        assert str(out["workoutType"]).lower() == str(out["workout_type"]).lower(), (
            f"day says two different things about itself: {out}"
        )


@_SETTINGS
@given(plan=plans(twins=True), constraints=constraint_lists())
def test_the_output_holds_at_most_one_spelling_of_every_field(plan, constraints):
    """The property ai-trainer-ops#38 exists for, and the reason #29 was possible.

    Stronger than ``test_a_sanitised_day_does_not_contradict_itself``, and the
    difference is the whole point. *Agreement* between two spellings was
    maintained by this module remembering to drop each twin it wrote — a rule
    three modules had already forgotten when #29 found them, and one a future
    writer can forget again. *Absence* is maintained by construction, so there
    is nothing left to forget.

    Asserted over every modelled field rather than over ``workoutType`` alone,
    because the next twin will be on whichever field someone adds next.
    """
    for day in plan:
        assert _snake_twins(day), "generator no longer produces the shape under test"

    result = sanitize_plan_for_constraints(plan, constraints)

    for day in result:
        assert not _snake_twins(day), f"day carries two spellings of one field: {day}"


@_SETTINGS
@given(day=days(twins=True), constraints=constraint_lists())
def test_the_surviving_spelling_is_the_camel_one(day, constraints):
    """Which of the two wins is not arbitrary, and must not drift.

    camelCase is what Pydantic's alias resolution already chose before the twin
    was dropped, so keeping it removes a stale key without changing a single
    value. If this ever flips, every stored plan written through a day that
    carried both spellings changes meaning silently.
    """
    expected = str(day["workoutType"]).lower()
    (out,) = sanitize_plan_for_constraints([day], constraints)
    if str(day.get("date") or "") not in blocked_dates(constraints) and not any(
        c.get("constraintType") == "required_workout"
        and c.get("constraintDate") == day.get("date")
        for c in constraints
    ):
        assert str(out.get("workoutType", "")).lower() == expected


@_SETTINGS
@given(day=days(), constraints=constraint_lists())
def test_violation_verdict_matches_what_the_gate_did(day, constraints):
    """``day_violates_constraint`` and ``sanitize`` must agree on every day.

    They are used independently — the router filters updates with the predicate
    and the pipeline rewrites plans with the gate. If the two ever disagree, an
    update is accepted on a day a plan would have had blanked.
    """
    (out,) = sanitize_plan_for_constraints([day], constraints)
    if day_violates_constraint(day, constraints):
        assert not _is_training(out)
