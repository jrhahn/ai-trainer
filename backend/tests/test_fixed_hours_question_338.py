"""A starting hours question that cannot work is replaced by the fixed one
(review of 2026-11-01, ai-trainer-ops#338 T4)."""

from __future__ import annotations

import pytest

from services import starting_hypotheses
from services.starting_hypotheses import StartingHypothesis
from services.starting_hypothesis_check import (
    hours_cap,
    volume_question_usable,
    with_fixed_volume,
)
from tests.test_opening_assumptions_230 import _compose, athlete, model  # noqa: F401
from tests.test_opening_message_153 import MODEL_HYPOTHESES

# Tom's run of 2026-10-31: the window differs from the statement's, and the
# answers are words.
TOMS = StartingHypothesis(
    category="weekly_volume",
    statement="Has ridden 3 to 6 hours a week over the last two months",
    question="Wie viele Stunden bist du in den letzten zwei Wochen ungefähr auf dem Rad gesessen?",
    options=[
        {"label": "Etwa drei bis sechs Stunden", "stance": "confirms"},
        {"label": "Wenig bis gar nicht", "stance": "refutes"},
        {"label": "Deutlich mehr als das", "stance": "refutes"},
    ],
)
GOOD = StartingHypothesis(**MODEL_HYPOTHESES[0])
RECOVERY = StartingHypothesis(**MODEL_HYPOTHESES[1])


@pytest.mark.parametrize(
    ("question", "labels", "usable"),
    [
        (GOOD.question, ["unter 3 h", "3–6 h", "mehr als 6 h"], True),
        ("How many hours a week do you ride?", ["under 3 h", "3–6 h"], True),
        # Another window.
        (TOMS.question, ["unter 3 h", "3–6 h", "mehr als 6 h"], False),
        # Words, not digits.
        (GOOD.question, ["Etwa drei bis sechs Stunden", "3–6 h"], False),
        # Digits, but no answer that sets a limit.
        (GOOD.question, ["mehr als 6 h", "mehr als 10 h"], False),
    ],
)
def test_a_volume_question_is_usable_only_per_week_and_in_digits(
    question, labels, usable
):
    hypothesis = StartingHypothesis(
        category="weekly_volume",
        statement=GOOD.statement,
        question=question,
        options=[{"label": label, "stance": "neutral"} for label in labels],
    )

    assert volume_question_usable(hypothesis) is usable


def test_toms_question_is_replaced_and_the_other_assumption_kept():
    kept = with_fixed_volume([TOMS, RECOVERY], {"level": "intermediate"}, "German")

    fixed = starting_hypotheses.fallback({"level": "intermediate"}, "German")[0]
    assert kept == [fixed, RECOVERY]
    assert "pro Woche" in kept[0].question
    # Every answer that bounds the hours sets them when tapped.
    assert [hours_cap(o["label"]) for o in kept[0].options] == [3, 6, None]


def test_a_usable_model_question_stays_the_models():
    assert with_fixed_volume([GOOD, RECOVERY], {}, "German") == [GOOD, RECOVERY]


@pytest.mark.asyncio
async def test_the_opening_message_pins_the_fixed_question_for_toms_wording(
    model,  # noqa: F811
    athlete,  # noqa: F811
    monkeypatch,
):
    from services import ai_service

    toms = [
        {
            "category": TOMS.category,
            "statement": TOMS.statement,
            "question": TOMS.question,
            "options": TOMS.options,
        },
        MODEL_HYPOTHESES[1],
    ]

    async def write(facts, language, sport, provider="openai"):
        return "Hallo, ich bin dein Coach.", toms

    monkeypatch.setattr(ai_service, "write_opening_message", write)

    opening = await _compose(athlete, "German")

    assert [h.category for h in opening.hypotheses] == ["weekly_volume", "recovery"]
    assert "pro Woche" in opening.hypotheses[0].question
    assert opening.hypotheses[1].question == MODEL_HYPOTHESES[1]["question"]
