"""Tests for surfacing confidence and uncertainty in coach replies (#389).

The coach already *receives* calibrated confidence, evidence, and observation
counts (memory facts, the athlete model, open questions). #389 makes it
*communicate* that uncertainty in athlete-facing language: a derived value is
voiced as an estimate with its confidence, and a thinly-observed behavioural
pattern is hedged rather than asserted.

Like the other prompt-behaviour tests, these assert on what the model
*receives* — the instructions in the system prompt — not on an LLM response.
"""

from __future__ import annotations

from services.prompts import (
    ask_trainer_plan_updates_rule,
    ask_trainer_system,
    confidence_communication_rules,
)


def test_rule_separates_measured_facts_from_estimates():
    rule = confidence_communication_rules()
    lowered = rule.lower()
    # The core distinction: state measured/stated values plainly, voice derived
    # ones as estimates.
    assert "measured" in lowered
    assert "estimate" in lowered
    assert "user-confirmed" in lowered


def test_rule_shows_numeric_confidence_example():
    """The FTP example from the issue must be modelled for the coach, and it
    must be told never to fabricate a confidence it was not given."""
    rule = confidence_communication_rules()
    assert "320 W (confidence 0.67)" in rule
    assert "never" in rule.lower() and "invent" in rule.lower()


def test_rule_scales_language_to_evidence_for_observations():
    """A thinly-evidenced behavioural pattern must be hedged (the heart-rate
    example), a corroborated one stated more firmly."""
    rule = confidence_communication_rules()
    assert "emerging evidence" in rule
    assert "More observations are needed." in rule
    assert "tendency" in rule.lower()


def test_ask_trainer_system_includes_confidence_rule():
    prompt = ask_trainer_system(
        profile={"currentFTP": 280},
        today="2026-07-13",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )
    assert "Communicating confidence and uncertainty:" in prompt
    assert "320 W (confidence 0.67)" in prompt


def test_estimated_model_confidence_reaches_the_prompt():
    """End-to-end at prompt level: a derived athlete model carries its
    confidence into the prompt so the coach can quote it as an estimate."""
    prompt = ask_trainer_system(
        profile={},
        today="2026-07-13",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        athlete_model={"ftpWatts": 320, "confidence": 0.67},
    )
    assert "320" in prompt
    assert "0.67" in prompt
    assert "estimate" in prompt.lower()
