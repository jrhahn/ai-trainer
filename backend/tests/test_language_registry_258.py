"""Every prompt whose text the athlete reads is told their language (ai-trainer-ops#258).

Five athlete-facing prompts had no language instruction at all; they were found
by hand. This walks every prompt in ``prompt_fixtures.BUILDERS``: each one carries
``ATHLETE_LANGUAGE_RULE``, or names the language it was given, or is listed below
with the reason it needs neither. A new prompt has to be one of the three.
"""

from __future__ import annotations

import pytest

from services.prompts import ATHLETE_LANGUAGE_RULE
from tests.prompt_fixtures import BUILDERS

# The prompts that write in a language they are handed, and where they name it.
NAMED = {
    "opening_message_en_cycling": "to them in English",
    "opening_message_de_running": "to them in German",
    "plan_text_translation_de": "German, the language the athlete uses the app in",
}

_KNOWLEDGE = (
    "an analysis step whose text is stored and read back by the coach; whether it is "
    "written in the athlete's language is open on ai-trainer-ops#136"
)
EXEMPT = {
    "generate_plan": "plan texts are written in English and translated for the screen (#121)",
    "adapt_plan": "plan texts are written in English and translated for the screen (#121)",
    "ask_trainer_classify": "a classifier: its answer is a category",
    "update_memory": _KNOWLEDGE,
    "extract_athlete_facts": _KNOWLEDGE,
    "generate_athlete_insights": _KNOWLEDGE,
    "derive_athlete_model": _KNOWLEDGE,
    "generate_athlete_hypotheses": _KNOWLEDGE,
    "generate_open_questions": _KNOWLEDGE,
    "generate_validation_experiments": _KNOWLEDGE,
    "generate_athlete_predictions": _KNOWLEDGE,
    "evaluate_athlete_predictions": "an analysis step: its answer is verdicts",
    "detect_contradictions": "an analysis step: its answer is indices and reasons",
    "match_observations": "an analysis step: its answer is indices",
}


@pytest.mark.parametrize("name", sorted(set(BUILDERS) - set(EXEMPT)))
def test_every_prompt_the_athlete_reads_is_told_the_language(name):
    text = BUILDERS[name]()

    assert ATHLETE_LANGUAGE_RULE in text or (name in NAMED and NAMED[name] in text)


def test_the_lists_name_only_prompts_that_exist():
    assert set(EXEMPT) | set(NAMED) <= set(BUILDERS)
    assert not set(EXEMPT) & set(NAMED)
