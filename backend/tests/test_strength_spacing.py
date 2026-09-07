"""Strength is load and needs spacing, not just a note that it isn't rest (#660).

`hard_session_spacing_rules()` was the only rule anywhere in the coach prompt that
reasoned about how sessions follow one another, and every clause in it was scoped
to VO2max/HIIT/threshold/sprint. Strength appeared exactly once, and only to deny
it counts as a rest day — so the rule knew strength adds load while never
constraining where that load may sit. Two strength days in a row passed every
clause, which is how 2026-09-07 and 2026-09-08 both ended up as 45-minute
strength sessions.
"""

from services.prompts import (
    TRAINING_PLAN_PRINCIPLES,
    adapt_plan_system,
    coach_static_prefix,
    generate_plan_system,
    hard_session_spacing_rules,
    next_ride_recommendation_system,
)


def test_strength_is_named_as_load_for_spacing_purposes():
    rules = hard_session_spacing_rules()

    assert "Strength is load" in rules
    assert "consecutive" in rules


def test_the_pre_existing_not_a_rest_day_clause_survives():
    # It was never wrong, only insufficient — it says what strength is *not*.
    assert "Strength training is not a complete rest day" in hard_session_spacing_rules()


def test_strength_beside_a_hard_interval_session_is_addressed():
    # The gap the old wording left: legs do not care which label the load carries.
    rules = hard_session_spacing_rules()

    assert "immediately before or after" in rules
    assert "VO2max/HIIT/threshold session" in rules


def test_upper_body_work_is_treated_as_the_milder_case():
    # Core/upper-body work does not compete with a hard ride for the same legs, so
    # a blanket ban would move sessions for no physiological reason.
    rules = hard_session_spacing_rules()

    assert "Upper-body/core-only work" in rules
    assert "may sit next to a hard ride" in rules


def test_the_rule_guides_rather_than_hard_blocks():
    # The athlete's own week wins; the coach must say the days are stacked, not
    # silently refuse an arrangement they asked for.
    rules = hard_session_spacing_rules()

    assert "strong defaults, not hard blocks" in rules
    assert "the athlete has asked" in rules


def test_every_prompt_carrying_the_spacing_rules_gets_the_new_clause():
    # Both the coach and the next-ride recommender embed these rules verbatim.
    for prompt in (coach_static_prefix(), next_ride_recommendation_system()):
        assert "Strength is load" in prompt


def test_the_planner_itself_stops_stacking_strength():
    # The nightly planner writes the days the coach later edits; leaving it out
    # would just recreate the collision from the other end.
    assert "Strength counts as a loading day" in TRAINING_PLAN_PRINCIPLES
    for prompt in (generate_plan_system(), adapt_plan_system()):
        assert "Strength counts as a loading day" in prompt


def test_the_planner_may_not_repeat_a_session_on_consecutive_days():
    assert "same session (same type, title and duration)" in TRAINING_PLAN_PRINCIPLES
