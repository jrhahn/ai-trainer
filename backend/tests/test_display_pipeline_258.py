"""Every text of the coach's knowledge the athlete reads is owed a translation.

The owner's rule (ai-trainer-ops#258): inside, everything stays English, and
everything shown passes one display pipeline, which today translates. That
pipeline is the plan-text translation: ``plan_translations.coach_texts`` names
what is owed, and the page shows it through ``usePlanText``.

Each text field of the knowledge schemas is listed here as owed, or as exempt
with the reason. A new field fails until it is listed; an owed field fails
until ``coach_texts`` carries it.
"""

from __future__ import annotations

import typing
from datetime import datetime, timezone

import pytest

import crud
import schemas
from services import plan_translations

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)

SCHEMAS = (
    schemas.AthleteHypothesisSchema,
    schemas.AthleteOpenQuestionSchema,
    schemas.AthleteExperimentSchema,
    schemas.AthletePredictionSchema,
    schemas.AthleteMemoryFactSchema,
)

OWED = {
    "AthleteHypothesisSchema": {
        "statement",
        "rationale",
        "evidence",
        "alternative_explanations",
    },
    "AthleteOpenQuestionSchema": {"question", "evidence", "needs"},
    "AthleteExperimentSchema": {"question", "protocol", "rationale"},
    "AthletePredictionSchema": {
        "prediction",
        "expected_outcome",
        "actual_outcome",
        "horizon",
    },
    "AthleteMemoryFactSchema": {"fact", "contradiction_note"},
}

EXEMPT = {
    "id": "an id",
    "hypothesis_id": "an id",
    "source_exchange_id": "an id",
    "category": "a key; the page names it with t()",
    "resolution": "not shown",
    "source_snippet": "the athlete's own words, shown as they wrote them",
}


def _is_text(annotation: object) -> bool:
    if annotation is str:
        return True
    args = typing.get_args(annotation)
    if typing.get_origin(annotation) is list:
        return args == (str,)
    return str in args and all(a in (str, type(None)) for a in args)


def _text_fields(schema) -> set[str]:
    return {
        name
        for name, field in schema.model_fields.items()
        if _is_text(field.annotation)
    }


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda s: s.__name__)
def test_every_text_field_is_owed_or_exempt_with_a_reason(schema):
    owed = OWED[schema.__name__]
    fields = _text_fields(schema)

    assert fields - owed - EXEMPT.keys() == set(), (
        "list each new field as owed or exempt"
    )
    assert owed - fields == set(), "an owed field is gone from the schema"
    assert owed & EXEMPT.keys() == set()


@pytest.mark.asyncio
async def test_every_owed_field_is_in_the_pipeline(db, user):
    """One row of each kind, each owed field with its own text: all of them
    come out of ``coach_texts``."""
    sentinel = {
        (schema.__name__, field): f"{schema.__name__}.{field}"
        for schema in SCHEMAS
        for field in OWED[schema.__name__]
    }

    def text(schema: str, field: str) -> str:
        return sentinel[(schema, field)]

    h = "AthleteHypothesisSchema"
    await crud.propose_athlete_hypothesis(
        db,
        user.id,
        statement=text(h, "statement"),
        rationale=text(h, "rationale"),
        evidence=[text(h, "evidence")],
        alternative_explanations=[text(h, "alternative_explanations")],
        observed_at=NOW,
    )
    q = "AthleteOpenQuestionSchema"
    await crud.record_athlete_open_question(
        db,
        user.id,
        question=text(q, "question"),
        evidence=text(q, "evidence"),
        needs=text(q, "needs"),
        observed_at=NOW,
    )
    e = "AthleteExperimentSchema"
    await crud.suggest_athlete_experiment(
        db,
        user.id,
        question=text(e, "question"),
        protocol=text(e, "protocol"),
        rationale=text(e, "rationale"),
        observed_at=NOW,
    )
    p = "AthletePredictionSchema"
    prediction = await crud.record_athlete_prediction(
        db,
        user.id,
        prediction=text(p, "prediction"),
        expected_outcome=text(p, "expected_outcome"),
        horizon=text(p, "horizon"),
        category="performance",
        confidence=0.6,
        observed_at=NOW,
    )
    await crud.evaluate_athlete_prediction(
        db,
        user.id,
        prediction.id,
        correct=False,
        actual_outcome=text(p, "actual_outcome"),
        evaluated_at=NOW,
    )
    m = "AthleteMemoryFactSchema"
    fact = await crud.observe_athlete_memory_fact(
        db, user.id, fact=text(m, "fact"), observed_at=NOW
    )
    await crud.record_athlete_memory_fact_contradiction(
        db, fact, reason=text(m, "contradiction_note"), now=NOW
    )

    owed = set(await plan_translations.coach_texts(db, user.id))

    assert set(sentinel.values()) - owed == set()


@pytest.mark.asyncio
async def test_the_titles_a_plan_change_names_are_owed(db, user):
    """The timeline says what a change did to a day, old title and new; the
    old one is no longer in the plan (ai-trainer-ops#258)."""
    await crud.record_plan_day_changes(
        db,
        user.id,
        [
            {
                "date": "2026-10-27",
                "old_day": {"title": "Endurance Ride", "workoutType": "endurance"},
                "new_day": {"title": "Recovery Spin", "workoutType": "recovery"},
            },
            # Added and removed days have one side only.
            {"date": "2026-10-28", "old_day": None, "new_day": {"title": "Tempo"}},
        ],
        source="nightly_maintenance",
    )

    owed = await plan_translations.coach_texts(db, user.id)

    assert {"Endurance Ride", "Recovery Spin", "Tempo"} <= set(owed)


def test_the_timeline_reads_as_many_changes_as_are_owed():
    from routers.users import coach_timeline

    assert (
        "plan_translations.TIMELINE_HISTORY_ROWS"
        in open(coach_timeline.__file__).read()
    )
