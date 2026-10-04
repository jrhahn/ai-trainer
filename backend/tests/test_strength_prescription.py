"""Prescribed lifts on a planned gym session (#714).

``PlanDay.strength_exercises`` is how a plan says "3×5 back squat at RIR 2"
instead of only describing it in prose. It went in with no tests at all — the
coverage report on PR #737 showed every ``StrengthPrescription`` validator
unexecuted, which meant the whole prescription path through the canonical
persist gate was unexercised.

These lock in the three things that have to hold there:

**It can never be the reason a write fails.** Every other field on ``PlanDay``
coerces rather than rejects, and this one did not. The cost was not a dropped
day: ``ai_service`` retries on a ``ValidationError``, so a model that wrote
``["3x5 squat"]`` burned all three plan-sized attempts and the athlete got no
plan at all.

**One intensity unit.** RIR and a percentage of e1RM together is an ambiguous
prescription, not a rich one, and they disagree on exactly the days
autoregulation exists for.

**Storage stays byte-stable.** A plan day that prescribes nothing must not
acquire a ``strengthExercises`` key, for the same reason slot 0 and a cycling
sport are omitted (#496/#710).
"""

from __future__ import annotations

import pytest

import crud
import models
import schemas
from auth import hash_password
from services import plan_pipeline
from services.strength_model import MAX_RIR, normalize_exercise_name
from tests.conftest import TestSessionLocal

DATE = "2026-10-08"


def _day(**extra) -> dict:
    day = {
        "date": DATE,
        "sport": "strength",
        "workoutType": "strength",
        "title": "Gym",
        "description": "Lower body",
        "durationMinutes": 45,
    }
    day.update(extra)
    return day


def _prescriptions(day: dict | schemas.PlanDay) -> list[dict]:
    """The prescriptions as stored/serialized, as a list of plain dicts."""
    if isinstance(day, schemas.PlanDay):
        return [p.model_dump() for p in day.strength_exercises or []]
    return list(day.get("strengthExercises") or [])


def _validate(**extra) -> schemas.PlanDay:
    return schemas.PlanDay.model_validate(_day(**extra))


async def _create_user(email: str) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Lifter",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, [])
        await db.commit()
        return user.id


async def _commit(user_id: str, plan: list[dict]) -> list[dict]:
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        await plan_pipeline.commit_plan(
            db, user, plan, base_plan=[], source="generate"
        )
        await db.commit()
    async with TestSessionLocal() as db:
        row = await crud.get_training_plan(db, user_id)
        return row.plan if row is not None else []


# ---------------------------------------------------------------------------
# The prescription survives the gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_prescribed_lift_survives_the_persist_gate():
    user_id = await _create_user("prescribed-lift@example.com")
    saved = await _commit(
        user_id,
        [_day(strengthExercises=[{"exercise": "Back Squat", "sets": 3, "reps": 5, "rir": 2}])],
    )

    assert len(saved) == 1
    assert _prescriptions(saved[0]) == [
        {"exercise": "back squat", "sets": 3, "reps": 5, "rir": 2}
    ]


def test_the_exercise_name_is_normalised_like_a_logged_set():
    """A prescribed "Back  Squat" and a logged "back squat" are one exercise.

    Without this the prescription could never be compared with what was actually
    lifted, which is the only reason to store it structurally at all.
    """
    day = _validate(strengthExercises=[{"exercise": "  Back  SQUAT ", "sets": 3, "reps": 5}])

    assert day.strength_exercises is not None
    assert day.strength_exercises[0].exercise == "back squat"
    assert day.strength_exercises[0].exercise == normalize_exercise_name("Back Squat")


def test_sets_and_reps_default_rather_than_rejecting():
    """An omitted or unreadable count must not fail the day at the gate."""
    day = _validate(strengthExercises=[{"exercise": "squat", "sets": "three", "reps": None}])

    assert _prescriptions(day) == [
        {"exercise": "squat", "sets": 1, "reps": 1, "rir": None, "percent_e1rm": None}
    ]


def test_zero_sets_is_read_as_one():
    """A prescription of zero sets is not a prescription."""
    day = _validate(strengthExercises=[{"exercise": "squat", "sets": 0, "reps": -4}])

    assert [(p.sets, p.reps) for p in day.strength_exercises or []] == [(1, 1)]


# ---------------------------------------------------------------------------
# One intensity unit
# ---------------------------------------------------------------------------


def test_rir_wins_when_both_intensity_units_are_given():
    """Not a richer prescription — an ambiguous one.

    The two name different weights on exactly the days they disagree, which are
    the days autoregulation exists for. RIR survives the athlete being tired,
    which is the whole argument for prescribing in it.
    """
    day = _validate(
        strengthExercises=[
            {"exercise": "squat", "sets": 3, "reps": 5, "rir": 2, "percentE1rm": 80}
        ]
    )

    only = (day.strength_exercises or [])[0]
    assert only.rir == 2
    assert only.percent_e1rm is None


def test_a_percentage_alone_is_kept():
    """It is how a block is often written, so it has to be sayable."""
    day = _validate(strengthExercises=[{"exercise": "squat", "percentE1rm": 80}])

    only = (day.strength_exercises or [])[0]
    assert only.percent_e1rm == 80.0
    assert only.rir is None


def test_a_prescription_with_no_intensity_unit_is_legitimate():
    """"3×12 push-ups" prescribes nothing about load, and that is complete."""
    day = _validate(strengthExercises=[{"exercise": "Push-ups", "sets": 3, "reps": 12}])

    only = (day.strength_exercises or [])[0]
    assert (only.rir, only.percent_e1rm) == (None, None)
    assert (only.sets, only.reps) == (3, 12)


@pytest.mark.parametrize(
    ("given", "expected"),
    [(99, MAX_RIR), (-3, 0), (0, 0), (MAX_RIR, MAX_RIR)],
)
def test_rir_is_clamped_to_the_same_ceiling_as_a_logged_set(given: int, expected: int):
    """A prescription and a log must not disagree about what RIR 12 means."""
    day = _validate(strengthExercises=[{"exercise": "squat", "rir": given}])

    assert (day.strength_exercises or [])[0].rir == expected


@pytest.mark.parametrize("given", [800, 0, -20, 151])
def test_an_impossible_percentage_is_dropped_not_stored(given: float):
    """800 % of an e1RM is a typo, and 0 % is not a set.

    Dropped rather than rejected: the day still has to persist, it just stops
    claiming an intensity nobody can execute.
    """
    day = _validate(strengthExercises=[{"exercise": "squat", "percentE1rm": given}])

    assert (day.strength_exercises or [])[0].percent_e1rm is None


# ---------------------------------------------------------------------------
# It can never be the reason a write fails
# ---------------------------------------------------------------------------


def test_a_lift_written_as_a_bare_string_is_read_not_rejected():
    """The regression that cost a whole plan generation.

    ``ai_service`` retries on a ``ValidationError`` and then raises, so this
    shape used to burn all three plan-sized attempts and leave the athlete with
    no plan. A string is the same information the dict form carries with
    ``sets``/``reps`` omitted, and those already default rather than reject.
    """
    day = _validate(strengthExercises=["Back Squat"])

    assert _prescriptions(day) == [
        {"exercise": "back squat", "sets": 1, "reps": 1, "rir": None, "percent_e1rm": None}
    ]


def test_a_single_prescription_written_without_its_list_still_reads():
    day = _validate(strengthExercises={"exercise": "Bench Press", "sets": 5, "reps": 3})

    assert [(p.exercise, p.sets, p.reps) for p in day.strength_exercises or []] == [
        ("bench press", 5, 3)
    ]


def test_unreadable_entries_are_dropped_and_the_readable_ones_kept():
    """One bad entry must not cost the prescriptions written correctly."""
    day = _validate(
        strengthExercises=[
            42,
            None,
            {"exercise": {"nonsense": 1}},
            {"exercise": "Bench", "sets": 3, "reps": 8, "rir": 2},
        ]
    )

    assert [(p.exercise, p.sets, p.reps, p.rir) for p in day.strength_exercises or []] == [
        ("bench", 3, 8, 2)
    ]


def test_a_nameless_prescription_is_not_one():
    """It renders as a blank row and can never be matched against a logged set."""
    day = _validate(strengthExercises=[{"sets": 3, "reps": 5, "rir": 2}])

    assert day.strength_exercises is None


@pytest.mark.parametrize(
    "given", [[], [42], ["   "], [{"sets": 3}], "", 42, [None]]
)
def test_a_list_with_nothing_readable_becomes_absent_rather_than_empty(given):
    """``[]`` would put a new key into every stored plan day.

    Same byte-stability argument as slot 0 and a cycling sport: a day that
    prescribes nothing has to serialize the way it did before the field existed.
    """
    day = _validate(strengthExercises=given)

    assert day.strength_exercises is None
    assert "strengthExercises" not in day.model_dump(by_alias=True, exclude_none=False)


def test_a_malformed_prescription_no_longer_costs_the_day_its_normalisation():
    """The pipeline symptom of the raise.

    ``_to_canonical_day`` catches a ``ValidationError`` and passes the day
    through *unchanged*, so a day that failed on its lifts silently skipped
    every other normalisation too — the duration window, the slot and sport
    omission. Now it is normalised like any other day.
    """
    canonical = plan_pipeline._to_canonical_day(
        _day(slot=0, sport="cycling", strengthExercises=["squat"])
    )

    # Normalisation actually ran: both storage defaults are gone.
    assert "slot" not in canonical
    assert "sport" not in canonical
    assert _prescriptions(canonical) == [{"exercise": "squat", "sets": 1, "reps": 1}]


# ---------------------------------------------------------------------------
# Storage stays byte-stable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_day_with_no_lifts_is_stored_without_the_key():
    """The cascade this prevents is the expensive part.

    An explicit ``null`` would make every stored plan day compare unequal to its
    own re-dump, rewriting the lot on the first commit and triggering a
    login-summary refresh and a ride-snapshot rebuild behind it.
    """
    user_id = await _create_user("byte-stable-lifts@example.com")
    saved = await _commit(user_id, [_day(workoutType="endurance", sport="cycling")])

    assert "strengthExercises" not in saved[0]
    assert "strength_exercises" not in saved[0]


@pytest.mark.asyncio
async def test_committing_an_unchanged_gym_session_is_byte_identical():
    """The re-dump has to equal what is already stored, or no-op detection lies."""
    user_id = await _create_user("stable-gym@example.com")
    plan = [_day(strengthExercises=[{"exercise": "Back Squat", "sets": 3, "reps": 5, "rir": 2}])]

    first = await _commit(user_id, plan)
    again = await _commit(user_id, first)

    assert again == first


# ---------------------------------------------------------------------------
# Partial updates
# ---------------------------------------------------------------------------


def _update(**extra) -> schemas.PlanDayUpdateSchema:
    return schemas.PlanDayUpdateSchema(date=DATE, **extra)


def test_an_update_that_omits_the_lifts_leaves_them_alone():
    """Absent means unchanged, the same convention as ``sport``.

    Otherwise every coach edit that touched only the title would wipe the
    prescription.
    """
    base = _validate(strengthExercises=[{"exercise": "squat", "sets": 3, "reps": 5}])

    merged = schemas.merge_update(base, _update(title="Heavy lower body"))

    assert merged.title == "Heavy lower body"
    assert [(p.exercise, p.sets) for p in merged.strength_exercises or []] == [("squat", 3)]


def test_an_update_can_replace_the_lifts():
    base = _validate(strengthExercises=[{"exercise": "squat", "sets": 3, "reps": 5}])

    merged = schemas.merge_update(
        base,
        _update(strength_exercises=[{"exercise": "Deadlift", "sets": 1, "reps": 5, "rir": 3}]),
    )

    assert [(p.exercise, p.reps, p.rir) for p in merged.strength_exercises or []] == [
        ("deadlift", 5, 3)
    ]


def test_an_empty_list_clears_the_lifts():
    """The one way to say "this is no longer a lifting session"."""
    base = _validate(strengthExercises=[{"exercise": "squat", "sets": 3, "reps": 5}])

    merged = schemas.merge_update(base, _update(strength_exercises=[]))

    assert merged.strength_exercises is None


# ---------------------------------------------------------------------------
# The coach can write the field
# ---------------------------------------------------------------------------


def test_the_coach_response_schema_offers_the_field():
    """A field the model is never told about is a field it never writes.

    Asserted structurally because the wiring is the whole feature here: the
    prescription reaching ``PlanDay`` depends on ``strengthExercises`` being in
    the plan-update schema the provider is given.
    """
    from services import coach_schema

    plan_update = coach_schema._PLAN_UPDATE
    assert "strengthExercises" in plan_update["properties"]
    assert "strengthExercises" in plan_update["propertyOrdering"]

    exercise = plan_update["properties"]["strengthExercises"]["items"]
    assert "exercise" in exercise["properties"]
    assert "rir" in exercise["properties"]
