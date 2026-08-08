"""A hypothesis that cannot recur can never be confirmed or refuted (#581).

96 % of stored hypotheses had ``evidence_count = 1``. That is not bad luck: the
statements were compound and narrowly conditioned, and the dedupe key is the
normalised statement text, so a configuration that never repeats verbatim can
never accrue a second observation. The generator was producing claims that were
unfalsifiable in practice.
"""

from __future__ import annotations

import pytest

from services.ai_service import _normalise_hypothesis_candidate
from services.prompts import generate_athlete_hypotheses_system

# Verbatim from the issue's production sample.
UNFALSIFIABLE = (
    "Performing a yoga session immediately following a consecutive strength and "
    "high-intensity cycling day results in a measurably elevated fatigue response "
    "on the subsequent endurance ride."
)
FALSIFIABLE = "Strength training the day before suppresses heart-rate response."


def _candidate(statement: str) -> dict:
    return {
        "statement": statement,
        "category": "fatigue_response",
        "confidence": 0.4,
        "rationale": "HR ~8 bpm low the day after gym sessions.",
    }


def test_a_repeatable_claim_is_kept():
    candidate = _normalise_hypothesis_candidate(_candidate(FALSIFIABLE))
    assert candidate is not None
    assert candidate["statement"] == FALSIFIABLE


def test_a_claim_that_cannot_recur_is_dropped_at_the_gate():
    """The prompt asks for one condition and one outcome; this is where that is
    enforced, because an instruction the pipeline does not check is a
    suggestion."""
    assert _normalise_hypothesis_candidate(_candidate(UNFALSIFIABLE)) is None


def test_the_rest_of_the_candidate_is_still_validated():
    """The new gate must not have swallowed the existing checks."""
    assert _normalise_hypothesis_candidate({"statement": "   "}) is None
    assert _normalise_hypothesis_candidate("not a dict") is None
    # Hypotheses are unproven ideas, so confidence stays capped low.
    capped = _normalise_hypothesis_candidate(
        {"statement": FALSIFIABLE, "confidence": 0.99}
    )
    assert capped["confidence"] == 0.6


@pytest.mark.parametrize(
    "instruction",
    [
        "MUST be able to recur",
        "ONE condition and ONE outcome",
        "at least monthly",
        "matched by their exact wording",
        "under 140 characters",
    ],
)
def test_the_prompt_states_why_a_claim_has_to_repeat(instruction):
    """Telling the model to be "testable" produced 74 untestable claims. It has
    to be told the mechanism: statements are matched verbatim, so a sentence it
    would not write again next month is a sentence that can never gain support."""
    assert instruction in generate_athlete_hypotheses_system()


def test_the_prompt_asks_for_fewer_hypotheses_not_more():
    system = generate_athlete_hypotheses_system()
    assert "Propose FEWER, more repeatable hypotheses" in system
