"""The chat prompt states each rule once (ai-trainer-ops#257).

The response-quality rules repeated three of the persona's sentences and one of
the outlook rules, the badge rule rode along in the plan-updates rule beside the
ride section that carries it, and the strength prescription went to every
athlete, strength or not. The production model caches nothing, so every copy
was paid for on every turn.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from unittest.mock import patch

import pytest

import services.ai_service as ai_service
from services.prompts import (
    BADGE_GROUNDING_RULE,
    COACH_PERSONA,
    ask_trainer_plan_updates_rule,
    ride_metrics_context_section,
)
from tests.prompt_fixtures import BUILDERS
from tests.test_plan_compliance import _metric


def _long_sentences(text: str) -> Counter:
    return Counter(s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 60)


@pytest.mark.parametrize("name", ["ask_trainer_minimal", "ask_trainer_rich"])
def test_no_long_sentence_appears_twice_in_the_chat_prompt(name):
    twice = [s for s, n in _long_sentences(BUILDERS[name]()).items() if n > 1]

    assert twice == []


def test_the_persona_alone_says_not_to_use_the_name():
    prompt = BUILDERS["ask_trainer_minimal"]()

    assert COACH_PERSONA in prompt
    assert prompt.count("address the athlete by name") == 1


def test_the_badge_rule_comes_with_the_badge_and_only_once():
    section = ride_metrics_context_section([_metric(label_override="Perfect")])

    assert BADGE_GROUNDING_RULE not in ask_trainer_plan_updates_rule(None)
    assert section.count(BADGE_GROUNDING_RULE) == 1


@pytest.mark.parametrize(
    ("profile", "plan", "in_play"),
    [
        ({"sports": ["cycling"]}, [{"date": "2026-10-27", "sport": "cycling"}], False),
        ({}, [], False),
        ({"sports": ["cycling", "strength"]}, [], True),
        ({"sports": ["cycling"]}, [{"date": "2026-10-27", "sport": "strength"}], True),
        ({"sports": ["cycling"]}, [{"date": "2026-10-27", "workoutType": "strength"}], True),
    ],
)
def test_strength_is_in_play_by_sport_or_by_a_plan_day(profile, plan, in_play):
    assert ai_service.strength_in_play(profile, plan) is in_play


@pytest.mark.asyncio
@pytest.mark.parametrize(("sports", "told"), [(["cycling"], False), (["cycling", "strength"], True)])
async def test_the_chat_carries_the_strength_prescription_only_for_strength(sports, told):
    captured: list[str] = []

    async def fake_chat_history(provider, system_prompt, messages, json_mode=False, **kwargs):
        captured.append(system_prompt)
        return json.dumps({"response": "OK.", "planUpdates": [], "sources": []})

    with patch.object(ai_service, "_chat_history", side_effect=fake_chat_history):
        await ai_service.ask_trainer(
            question="Wie sieht meine Woche aus?",
            plan=[{"date": "2026-10-27", "workoutType": "endurance", "title": "Ride", "durationMinutes": 60}],
            profile={"name": "Alice", "sports": sports},
        )

    assert ("reps in reserve" in captured[0]) is told
