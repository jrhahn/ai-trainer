"""Regression tests for personal-context coaching decisions (issue #268).

These tests guard against pure metrics optimisation when athlete context
should change the recommendation.  They verify that:

  - Athlete-context sections reach the prompt under each scenario.
  - The reasoning-layer rule explicitly enables personal context to
    override physiology when the two layers diverge.
  - High-confidence / user-confirmed memory facts about psychological
    tendencies survive the filtering pipeline.
  - The coach-memory update system captures durable psychological traits.

None of these tests call a real LLM.  They assert on what the model
*receives* — not on what it returns — which is the testable proxy for
"the coach can reason about the athlete, not only the activity file."
"""

from __future__ import annotations

import json

import pytest

from services.prompts import (
    ask_trainer_plan_updates_rule,
    ask_trainer_system,
    athlete_context_section,
    athlete_memory_facts_section,
    next_ride_recommendation_user,
    update_memory_system,
    update_memory_user,
)


# ---------------------------------------------------------------------------
# Scenario 1: Fresh athlete (positive TSB) who tends to overtrain
# ---------------------------------------------------------------------------


def test_overtraining_context_present_when_athlete_is_fresh():
    """Both the physiology metrics and the overtraining-tendency context must
    appear in the prompt when TSB > 0.

    Without the athlete-context section the model would see only "fresh" and
    recommend more load.  The context flag is the only signal that tells it
    to stay controlled.
    """
    prompt = ask_trainer_system(
        profile={"currentFTP": 280, "fitnessLevel": "intermediate"},
        today="2026-06-23",
        last_7_days=[],
        next_n_days=[
            {"date": "2026-06-23", "workoutType": "endurance", "title": "Aerobic Base", "durationMinutes": 90},
        ],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        athlete_context={
            "trainingTendency": "overtrains",
            "restResponse": "restless",
            "coachingRisks": ["adds extra intervals when feeling good", "ignores rest days"],
            "adherencePattern": "adds_extra",
        },
        training_load={"ctl": 65, "atl": 48, "tsb": 17},
    )

    # Context block must be present
    assert "Structured athlete context (durable coaching model)" in prompt
    assert '"trainingTendency": "overtrains"' in prompt
    assert "adds extra intervals when feeling good" in prompt

    # Training load (freshness signal) must also be present
    assert "CTL" in prompt or "ctl" in prompt or "fitness" in prompt.lower()

    # The reasoning rule must instruct the model to use the athlete-context
    # layer to temper what the numbers alone would suggest
    assert "Athlete-context layer" in prompt
    assert "overtraining tendency" in prompt


def test_overtraining_memory_fact_included_when_high_confidence():
    """A high-confidence 'overtrains when fresh' fact must reach the prompt.

    This is the durable evidence that the coach accumulated over time and
    is the exact signal needed to keep the recommendation controlled.
    """
    section = athlete_memory_facts_section([
        {
            "fact": "Adds extra intervals when feeling fresh — has led to fatigue crashes",
            "category": "coaching_risk",
            "sourceSnippet": "Mentioned adding Z4 work spontaneously after 3 rest days.",
            "confidence": 0.85,
            "status": "active",
            "lastConfirmedAt": "2026-06-20T10:00:00Z",
            "observationCount": 4,
        },
    ])

    assert "Evidence-backed athlete memory" in section
    assert "Adds extra intervals when feeling fresh" in section
    assert "coaching_risk" in section


def test_fresh_athlete_prompt_contains_divergence_rule():
    """When the physiology layer says 'go harder' but context says 'hold back',
    the prompt must contain the divergence rule that surfaces that tension.
    """
    prompt = ask_trainer_system(
        profile={},
        today="2026-06-23",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        athlete_context={"trainingTendency": "overtrains", "coachingRisks": ["pushes too hard when fresh"]},
        training_load={"ctl": 60, "atl": 42, "tsb": 18},
    )

    # The divergence rule must tell the model to surface the conflict
    assert "DIVERGE" in prompt
    assert '"physiologyRationale"' in prompt
    assert '"contextRationale"' in prompt


# ---------------------------------------------------------------------------
# Scenario 2: Rest anxiety — three rest days make the athlete nervous
# ---------------------------------------------------------------------------


def test_rest_anxiety_context_reaches_prompt():
    """When rest_response is 'anxious', that field must appear in the prompt
    so the coach can acknowledge the FOMO while still explaining recovery.
    """
    prompt = ask_trainer_system(
        profile={},
        today="2026-06-23",
        last_7_days=[],
        next_n_days=[
            {"date": "2026-06-23", "workoutType": "rest", "title": "Rest Day", "durationMinutes": 0},
            {"date": "2026-06-24", "workoutType": "rest", "title": "Rest Day", "durationMinutes": 0},
            {"date": "2026-06-25", "workoutType": "rest", "title": "Rest Day", "durationMinutes": 0},
        ],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        athlete_context={
            "restResponse": "anxious",
            "coachingRisks": ["gets FOMO after more than two consecutive rest days"],
        },
    )

    assert '"restResponse": "anxious"' in prompt
    assert "FOMO" in prompt or "anxious" in prompt.lower()
    # The rest-recommendation rule must address this explicitly
    assert "rest" in prompt.lower()


def test_rest_anxiety_memory_fact_survives_filtering():
    """A user-confirmed low-base-confidence fact about rest anxiety must
    pass the prompt filter because user_confirmed overrides the threshold.
    """
    section = athlete_memory_facts_section([
        {
            "fact": "Gets restless and anxious after two or more consecutive rest days — prone to FOMO",
            "category": "coaching_risk",
            "confidence": 0.35,
            "status": "user_confirmed",
            "lastConfirmedAt": "2026-06-01T09:00:00Z",
            "observationCount": 1,
        },
    ])

    assert "Gets restless and anxious" in section
    # user_confirmed status must be present so the model knows this is vetted
    assert "user_confirmed" in section


def test_rest_recommendation_rule_offers_easy_ride_option():
    """The rest recommendation rules must explicitly offer a low-risk easy
    ride option so the coach has a sanctioned alternative for anxious athletes.
    """
    from services.prompts import rest_recommendation_rules

    rule = rest_recommendation_rules()

    # Rule must acknowledge athlete preference for activity during rest
    assert "easy" in rule.lower() or "low" in rule.lower()
    # Rule must not unconditionally block training — it must offer an out
    assert "rest" in rule.lower()


# ---------------------------------------------------------------------------
# Scenario 3: Gym vs group ride — social context is the deciding factor
# ---------------------------------------------------------------------------


def test_social_motivation_context_reaches_ask_trainer_prompt():
    """When motivation_drivers includes social interaction, that context must
    appear in the system prompt so the coach can favour the group ride over
    an equivalent gym session when the athlete needs a mental reset.
    """
    prompt = ask_trainer_system(
        profile={},
        today="2026-06-23",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        athlete_context={
            "motivationDrivers": ["social rides", "group dynamics", "mental reset"],
            "trainingTendency": "balanced",
        },
    )

    assert "social rides" in prompt
    assert "mental reset" in prompt or "group dynamics" in prompt


def test_social_targeted_question_present_in_reasoning_rules():
    """The targeted-question rule must include a social-ride question so the
    coach can ask it when physiology alone doesn't distinguish gym from group.
    """
    prompt = ask_trainer_system(
        profile={},
        today="2026-06-23",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
    )

    assert "Would a social ride help you more" in prompt


def test_social_motivation_memory_fact_included_in_next_ride_prompt():
    """A memory fact about social motivation must reach the next-ride
    recommendation prompt so the model can act on it.
    """
    msg = next_ride_recommendation_user(
        rides=[],
        plan=[],
        profile={},
        athlete_memory_facts=[
            {
                "fact": "Group rides significantly improve motivation and adherence after tough weeks",
                "category": "motivation",
                "confidence": 0.8,
                "status": "active",
                "lastConfirmedAt": "2026-06-15T10:00:00Z",
                "observationCount": 3,
            },
        ],
    )

    assert "Group rides significantly improve motivation" in msg
    assert "motivation" in msg


# ---------------------------------------------------------------------------
# Scenario 4: MTB as psychological recovery
# ---------------------------------------------------------------------------


def test_mtb_preference_user_confirmed_fact_reaches_prompt():
    """A user-confirmed MTB preference fact must appear in the prompt even if
    its base confidence is below the automatic inclusion threshold (0.5).

    user_confirmed overrides the threshold — this is the exact mechanism that
    lets durable personal preferences outlast the automatic confidence decay.
    """
    section = athlete_memory_facts_section([
        {
            "fact": "MTB rides provide strong psychological reset; recommending one after a stressful week noticeably improves adherence",
            "category": "motivation",
            "confidence": 0.45,
            "status": "user_confirmed",
            "lastConfirmedAt": "2026-05-10T08:00:00Z",
            "observationCount": 2,
        },
    ])

    assert "MTB rides provide strong psychological reset" in section
    assert "user_confirmed" in section


def test_mtb_context_field_reaches_next_ride_prompt():
    """When athlete_context includes MTB as motivation driver, the structured
    context block must appear in the next-ride recommendation user message.
    """
    msg = next_ride_recommendation_user(
        rides=[],
        plan=[],
        profile={},
        athlete_context={
            "motivationDrivers": ["MTB", "technical trails"],
            "trainingTendency": "balanced",
        },
    )

    assert "MTB" in msg
    assert "technical trails" in msg


def test_mtb_context_excluded_when_empty():
    """When no athlete_context is provided, the structured context block
    must be absent from the next-ride prompt (no phantom sections).
    """
    msg = next_ride_recommendation_user(
        rides=[],
        plan=[],
        profile={},
        athlete_context=None,
    )

    assert "Structured athlete context" not in msg
    assert "Evidence-backed athlete memory" not in msg


def test_athlete_model_reaches_next_ride_prompt():
    """The durable athlete model (#384) must be threaded into the next-ride
    recommendation user message, not just the ask-trainer prompt (#403).
    """
    msg = next_ride_recommendation_user(
        rides=[],
        plan=[],
        profile={},
        athlete_model={
            "ftp_watts": 268,
            "vo2max": 58.4,
            "threshold_durability": "fades after 40 min at threshold",
            "confidence": 0.7,
            "updated_at": "2026-07-01",
        },
    )

    assert "Long-term athlete model" in msg
    assert "268" in msg
    assert "fades after 40 min at threshold" in msg
    # confidence/updated_at are metadata and must be suppressed.
    assert "0.7" not in msg
    assert "2026-07-01" not in msg


def test_athlete_model_absent_when_not_provided():
    """No athlete model → no phantom model section in the next-ride prompt."""
    msg = next_ride_recommendation_user(
        rides=[],
        plan=[],
        profile={},
        athlete_model=None,
    )

    assert "Long-term athlete model" not in msg


# ---------------------------------------------------------------------------
# Memory extraction — psychological tendencies must be captured
# ---------------------------------------------------------------------------


def test_memory_update_system_captures_rest_anxiety_category():
    """The coach-memory update system prompt must mention rest tolerance /
    anxiety patterns as a category worth persisting.
    """
    system = update_memory_system()
    assert "rest" in system.lower()
    # Fatigue patterns are the umbrella category that includes rest response
    assert "fatigue" in system.lower() or "recovery" in system.lower()


def test_memory_update_system_captures_motivation_patterns():
    """Social needs and motivation drivers must be listed as memory-worthy
    so the coach stores them as durable facts when observed in conversation.
    """
    system = update_memory_system()
    assert "motivation" in system.lower() or "social" in system.lower()


def test_memory_update_user_includes_exchange_for_psychological_observation():
    """The user message for a memory update must carry the conversation
    exchange verbatim so the model can extract psychological observations
    from what the athlete actually said.
    """
    user_msg = update_memory_user(
        current_memory="",
        user_message="I get really anxious when I take more than two rest days in a row.",
        coach_response="That's valuable to know — rest anxiety is common among motivated athletes.",
    )

    assert "anxious" in user_msg
    assert "rest days" in user_msg
    assert "rest anxiety" in user_msg


# ---------------------------------------------------------------------------
# Acceptance criterion: context changes the recommendation (prompt level)
# ---------------------------------------------------------------------------


def test_both_layers_present_for_gym_vs_social_decision():
    """The system prompt must contain both the physiology layer input (training
    load) and the athlete-context layer input (social motivation) simultaneously,
    so the model has everything needed to favour the group ride when the two
    options are physiologically equivalent.
    """
    prompt = ask_trainer_system(
        profile={"currentFTP": 260},
        today="2026-06-23",
        last_7_days=[],
        next_n_days=[
            {"date": "2026-06-24", "workoutType": "strength", "title": "Gym Session", "durationMinutes": 60},
        ],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule=ask_trainer_plan_updates_rule(None),
        athlete_context={
            "motivationDrivers": ["social rides", "group dynamics"],
            "trainingTendency": "balanced",
        },
        athlete_memory_facts=[
            {
                "fact": "Social group rides improve motivation far more than solo gym sessions",
                "category": "motivation",
                "confidence": 0.78,
                "status": "active",
                "lastConfirmedAt": "2026-06-18T10:00:00Z",
                "observationCount": 3,
            },
        ],
        training_load={"ctl": 55, "atl": 52, "tsb": 3},
    )

    # Physiology layer — TSB near zero means either option is viable
    assert "CTL" in prompt or "ctl" in prompt or "fitness" in prompt.lower()

    # Personal context layer — social motivation must be visible
    assert "social rides" in prompt
    assert "Social group rides improve motivation" in prompt

    # The reasoning rule must instruct the model to favour context when
    # the two options are physiologically similar
    assert "If two options are physiologically similar" in prompt
    assert "athlete-context layer" in prompt.lower() or "Athlete-context layer" in prompt
