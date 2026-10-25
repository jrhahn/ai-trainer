"""The analysis prompts carry the coach's limits, not its voice (ai-trainer-ops#257).

The whole coach persona, 4,250 characters of tone for a conversation, opened
every analysis step, a classifier that returns indices included. Those steps
open with ``TASK_PERSONA`` now; the prompts whose text the athlete reads keep
the coach.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services import prompts, starting_hypotheses
from tests.prompt_fixtures import BUILDERS

ANALYSIS = {
    "update_memory",
    "extract_athlete_facts",
    "generate_athlete_insights",
    "derive_athlete_model",
    "generate_athlete_hypotheses",
    "generate_open_questions",
    "generate_athlete_predictions",
    "evaluate_athlete_predictions",
    "detect_contradictions",
    "match_observations",
}


def test_only_the_analysis_prompts_open_with_the_task_persona():
    opening = {name for name, build in BUILDERS.items() if build().startswith(prompts.TASK_PERSONA)}

    assert opening == ANALYSIS


@pytest.mark.parametrize("name", sorted(ANALYSIS))
def test_an_analysis_prompt_has_no_coach_voice_and_stays_short(name):
    text = BUILDERS[name]()

    assert prompts.COACH_PERSONA not in text
    assert len(text) <= 3500


def test_the_task_persona_keeps_the_medical_line_and_the_new_athlete():
    text = prompts.TASK_PERSONA.lower()

    assert "not a medical professional" in text and "never diagnose" in text
    assert "new to this app" in text


@pytest.mark.parametrize(
    "name",
    [
        "extract_athlete_facts",
        "generate_athlete_insights",
        "generate_athlete_hypotheses",
        "generate_open_questions",
        "generate_validation_experiments",
        "generate_athlete_predictions",
    ],
)
def test_every_knowledge_writer_offers_the_one_category_list(name):
    assert prompts._ATHLETE_KNOWLEDGE_CATEGORIES in BUILDERS[name]()


def test_each_category_list_has_one_source():
    source = Path(prompts.__file__).read_text(encoding="utf-8")

    assert source.count("fatigue_response, fueling_hydration") == 1
    assert prompts.STARTING_HYPOTHESIS_CATEGORIES is starting_hypotheses.CATEGORIES
