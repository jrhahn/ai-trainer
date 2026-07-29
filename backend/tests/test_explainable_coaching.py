"""Prompt-level tests for explainable coaching conversation (#480, Level 2).

The Athlete Performance Model, its detected limiter, the ROI recommendation and
the active hypotheses are fed into the coach's system prompt as structured
context, and the prompt gains rules for drill-down explanation and proactive
insight. These tests assert that evidence + uncertainty reach the prompt and that
the explainability/proactive rules are present — the core of the epic's Level 2.
"""

from services import prompts
from services.prompts import (
    active_hypotheses_section,
    coach_explainability_rule,
    performance_model_section,
    reveal_uncertainty_rule,
)


def _perf_model() -> dict:
    return {
        "attributes": {
            "ftp": {"estimate": 250, "confidence": 0.72, "unit": "W", "evidence": []},
            "map": {"estimate": 360, "confidence": 0.6, "unit": "W"},
            "fractional_utilization": {
                "estimate": 0.69,
                "confidence": 0.55,
                "missing_information": ["a recent 20-min max effort"],
            },
            "vo2max": {"score": "unknown", "confidence": 0.1},
        },
        "limiters": [
            {
                "limiter": "threshold",
                "confidence": 0.62,
                "evidence": ["FTP ~250 W sits far below MAP ~360 W"],
                "counter_evidence": ["few long rides to confirm durability"],
            }
        ],
    }


def _recommendation() -> dict:
    return {
        "sufficient": True,
        "limiter": "threshold",
        "hypothesis": "Threshold is the higher-return target than more VO₂max work.",
        "rationale": "MAP ~360 W sits well above FTP ~250 W.",
        "expected_gain": [
            {"system": "threshold", "gain": "large"},
            {"system": "vo2max", "gain": "small"},
        ],
        "weekly_emphasis": [
            {"system": "threshold", "label": "Threshold", "sessions": 2},
            {"system": "endurance", "label": "Long endurance", "sessions": 1},
        ],
    }


def _hypotheses() -> list[dict]:
    return [
        {
            "statement": "The current limiter is likely threshold utilization.",
            "status": "proposed",
            "confidence": 0.62,
            "evidence": ["FTP ~250 W vs MAP ~360 W (69% of the ceiling)."],
            "alternative_explanations": [
                "The MAP estimate may be inflated by one short effort."
            ],
        }
    ]


# --- section renderers -------------------------------------------------------


def test_performance_model_section_exposes_evidence_and_confidence():
    section = performance_model_section(_perf_model())
    assert "threshold" in section
    # The concrete numbers and the confidence both reach the prompt.
    assert "250" in section and "360" in section
    assert "62%" in section  # limiter confidence
    assert "72%" in section  # ftp attribute confidence
    # Missing information and counter-evidence surface (uncertainty, not fact).
    assert "still missing" in section
    assert "but note" in section
    # An attribute with score "unknown" is dropped rather than asserted.
    assert "vo2max" not in section.lower()


def test_performance_model_section_empty_when_no_model():
    assert performance_model_section(None) == ""
    assert performance_model_section({"attributes": {}, "limiters": []}) == ""


def test_active_hypotheses_section_carries_evidence_and_alternatives():
    section = active_hypotheses_section(_hypotheses())
    assert "threshold utilization" in section
    assert "62%" in section
    assert "Evidence:" in section
    assert "Could also be:" in section
    assert "NOT settled" in section  # framed as tentative, not fact


def test_active_hypotheses_section_skips_resolved():
    resolved = [{"statement": "confirmed thing", "status": "confirmed", "confidence": 0.9}]
    assert active_hypotheses_section(resolved) == ""


def test_explainability_rule_covers_drilldown_and_proactive_insight():
    rule = coach_explainability_rule()
    lowered = rule.lower()
    assert "why" in lowered and "what makes you say that" in lowered
    assert "confidence" in lowered and "uncertain" in lowered
    # Proactive higher-return surfacing, phrased as a hypothesis.
    assert "proactive" in lowered
    assert "hypothesis" in lowered


def test_reveal_uncertainty_rule_demands_both_sides_and_open_unknowns():
    rule = reveal_uncertainty_rule()
    lowered = rule.lower()
    # The coach must not present a recommendation as the single truth (#490).
    assert "single truth" in lowered
    assert "not a verdict" in lowered or "never a verdict" in lowered
    # Both supporting AND contradicting evidence, plus what is unknown.
    assert "supporting" in lowered and "contradicting" in lowered
    assert "do not know" in lowered or "unknown" in lowered
    assert "confidence" in lowered
    # Deferring under genuine uncertainty is endorsed, not treated as weakness.
    assert "not weakness" in lowered or "not insisting" in lowered or "decide" in lowered
    # But high-confidence, one-sided calls must not manufacture doubt.
    assert "manufacture doubt" in lowered or "do not manufacture" in lowered


# --- assembled system prompt -------------------------------------------------


def _system_prompt(**kwargs) -> str:
    return prompts.ask_trainer_system(
        profile={"currentFTP": 250},
        today="2026-07-29",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule="",
        **kwargs,
    )


def test_ask_trainer_system_surfaces_full_reasoning_chain():
    prompt = _system_prompt(
        performance_model=_perf_model(),
        performance_recommendation=_recommendation(),
        hypotheses=_hypotheses(),
    )
    # Model attributes + limiter evidence (the drill-down substrate).
    assert "Athlete performance model" in prompt
    assert "250" in prompt and "360" in prompt
    # ROI recommendation + its working hypothesis.
    assert "Performance-model ROI" in prompt
    assert "higher-return target" in prompt or "higher expected return" in prompt.lower()
    # Active hypotheses with uncertainty.
    assert "Active coaching hypotheses" in prompt
    assert "Could also be:" in prompt
    # Explainability + proactive rules present.
    assert "Explainable coaching rules" in prompt
    assert "want me to explain" in prompt.lower()
    # Uncertainty-revealing rule is wired in too (#490).
    assert "Reveal your uncertainty" in prompt


def test_ask_trainer_system_omits_sections_without_model():
    prompt = _system_prompt()
    assert "Athlete performance model" not in prompt
    assert "Performance-model ROI" not in prompt
    assert "Active coaching hypotheses" not in prompt
    # The rule itself is always present so the coach stays explainable on demand.
    assert "Explainable coaching rules" in prompt
    # Owning uncertainty is unconditional — always present, model or not (#490).
    assert "Reveal your uncertainty" in prompt


def test_ask_trainer_system_omits_roi_when_insufficient():
    prompt = _system_prompt(
        performance_model=_perf_model(),
        performance_recommendation={"sufficient": False},
        hypotheses=[],
    )
    assert "Performance-model ROI" not in prompt
    # The model section still renders so the coach can still explain the limiter.
    assert "Athlete performance model" in prompt
