"""The coach explains recommendations in the athlete's own objective (#565).

The coach already knows *why* a session works — that is what the performance
model and the ROI recommendation are for. What it lacked is what the session is
**for**. "Threshold training increases FTP" is true and, for most athletes,
beside the point: they do not want a bigger number, they want to still enjoy the
last descent after four hours.

These are prompt-level tests in the style of ``test_explainable_coaching.py``:
they assert on what the model *receives* and on the contract it must answer in,
which is the testable proxy for "the coach explains itself in the athlete's
terms". Three of them exist because of specific ways this feature could go
wrong rather than fail — inventing an objective, dressing a preference-driven
pick up as physiology, and quietly costing a request its prompt cache.
"""

from __future__ import annotations

from services.coach_schema import COACH_REPLY_SCHEMA
from services.motivation_model import normalize_model
from services.prompts import (
    ask_trainer_plan_updates_rule,
    ask_trainer_system,
    coach_static_prefix,
    objective_framing_rule,
)


def _prompt(**overrides) -> str:
    kwargs = dict(
        profile={},
        today="2026-08-12",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )
    kwargs.update(overrides)
    return ask_trainer_system(**kwargs)


_TRAIL_RIDER = normalize_model(
    {
        "primary_objective": "Maximize enjoyable technical trail riding",
        "primary_objective_source": "user_set",
        "secondary_objectives": [{"text": "Improve climbing speed"}],
    }
)


# ---------------------------------------------------------------------------
# The rule reaches the coach
# ---------------------------------------------------------------------------


def test_the_framing_rule_is_in_the_coach_prompt():
    assert objective_framing_rule() in _prompt()


def test_the_rule_names_the_mechanism_and_the_payoff():
    """Chain, don't substitute: physiology stays, it just stops being the end."""
    rule = objective_framing_rule()

    assert "MECHANISM" in rule
    assert "PAYOFF" in rule


def test_the_rule_carries_the_worked_example_from_the_issue():
    """The FTP-vs-descent contrast is the whole feature in one sentence."""
    rule = objective_framing_rule()

    assert "increases your FTP" in rule
    assert "technical descent" in rule


def test_the_rule_requires_goals_to_be_restated_in_the_athletes_terms():
    """'Increase FTP' is a means; the goal is what the FTP is for."""
    rule = objective_framing_rule()

    assert "increase FTP" in rule
    assert "one more trail" in rule


# ---------------------------------------------------------------------------
# The ways this could go wrong rather than fail
# ---------------------------------------------------------------------------


def test_the_rule_forbids_inventing_an_objective():
    """Telling an athlete what they want is worse than not knowing."""
    rule = objective_framing_rule()

    assert "NEVER invent an objective" in rule
    # And names the correct fallback, so "no objective" is a defined state
    # rather than a gap the model fills with confident guesswork.
    assert "plain physiological terms" in rule


def test_the_rule_holds_an_inferred_objective_loosely():
    assert "correct it" in objective_framing_rule()


def test_the_rule_forbids_dressing_a_preference_pick_up_as_physiology():
    """#564 can rank a lower-physiology option first. Saying so is the point."""
    rule = objective_framing_rule()

    assert "smaller training stimulus" in rule
    assert "Never present a preference-driven pick as" in rule


# ---------------------------------------------------------------------------
# The reply contract
# ---------------------------------------------------------------------------


def test_the_output_contract_asks_for_the_objective_link():
    prompt = _prompt()

    assert '"objectiveRationale"' in prompt
    assert "the payoff, not the mechanism" in prompt


def test_the_reply_schema_carries_the_new_field():
    """A field the prompt asks for but the schema omits never arrives (#558)."""
    assert "objectiveRationale" in COACH_REPLY_SCHEMA["properties"]
    assert "objectiveRationale" in COACH_REPLY_SCHEMA["propertyOrdering"]


def test_the_objective_rationale_is_ordered_with_the_other_rationales():
    """Gemini emits in propertyOrdering order; the three belong together."""
    ordering = COACH_REPLY_SCHEMA["propertyOrdering"]

    assert ordering.index("objectiveRationale") == (
        ordering.index("contextRationale") + 1
    )
    assert ordering.index("response") < ordering.index("objectiveRationale")


def test_an_empty_objective_rationale_is_a_defined_answer():
    """The model must be told what to write when it knows of no objective."""
    prompt = _prompt()

    assert 'use "" when no objective is known' in prompt


def test_the_parser_reads_the_objective_rationale_back():
    from services.ai_service import _coach_result

    result = _coach_result(
        {"objectiveRationale": "  keeps the last descent enjoyable  "}, "ok"
    )

    assert result["objective_rationale"] == "keeps the last descent enjoyable"


def test_a_blank_objective_rationale_becomes_none():
    from services.ai_service import _coach_result

    assert _coach_result({"objectiveRationale": "   "}, "ok")["objective_rationale"] is None
    assert _coach_result({}, "ok")["objective_rationale"] is None


# ---------------------------------------------------------------------------
# The objective and the rule have to arrive together
# ---------------------------------------------------------------------------


def test_the_objective_and_the_rule_both_reach_the_prompt():
    """Either alone is useless: a rule with no objective, or an objective with
    no instruction to explain in its terms."""
    prompt = _prompt(motivation_model=_TRAIL_RIDER)

    assert "Maximize enjoyable technical trail riding" in prompt
    assert objective_framing_rule() in prompt


def test_the_rule_is_present_even_with_no_objective_known():
    """It is the rule that defines the no-objective fallback, so it must not be
    conditional on having one — and it sits in the cacheable prefix, which
    nothing conditional may enter (#514/#538)."""
    prompt = _prompt()

    assert objective_framing_rule() in prompt
    assert objective_framing_rule() in coach_static_prefix()


# ---------------------------------------------------------------------------
# What it costs
# ---------------------------------------------------------------------------


# ~4.8 chars/token was measured against the live API for this prompt
# (test_coach_prompt_cache.py). The budget is a guard against the rule growing
# by accretion, not a target.
_CHARS_PER_TOKEN = 4.8
_RULE_TOKEN_BUDGET = 450


def test_the_framing_rule_stays_within_its_token_budget():
    """The coach prompt is already ~16k tokens (#510/#556)."""
    approx_tokens = len(objective_framing_rule()) / _CHARS_PER_TOKEN

    assert approx_tokens < _RULE_TOKEN_BUDGET


def test_the_rule_is_paid_for_once_not_per_request():
    """Living in the byte-identical prefix is what makes the cost bearable: it
    is cached across requests rather than re-sent every turn."""
    prefix = coach_static_prefix()

    assert objective_framing_rule() in prefix
    # Nothing athlete-specific may have crept in with it.
    assert "Maximize enjoyable technical trail riding" not in prefix
