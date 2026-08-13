"""Aligning the planner with the athlete model the conversation already learned (#602).

The bug this file pins down is not a wrong number, it is two components
disagreeing about who they are working for. The coach knew the athlete was a
trail rider who does not race; the planner put a second threshold session in the
week, 48 h after the first, in 34 °C. Both were internally consistent. Only one
of them was answering the athlete's question.

So the tests are mostly about *ordering under a stated athlete*, and the two most
important ones are the guards:

* a default athlete's board is unchanged — physiology first, exactly as before;
* a heat-tolerant athlete does not get their riding taken away for weather they
  demonstrably handle.

Everything else here is the worked example from the issue, held to the number.
"""

from __future__ import annotations

import pytest

from services import freshness_allocation as fa
from services import motivation_model as mm
from services import prompts
from services.roi_recommendation import (
    GAIN_LARGE,
    GAIN_MAINTENANCE,
    GAIN_SMALL,
    SYSTEM_ENDURANCE,
    SYSTEM_THRESHOLD,
    SYSTEM_VO2MAX,
)


def _keys(allocation: dict) -> list[str]:
    return [option["key"] for option in allocation["options"]]


def _by_key(allocation: dict, key: str) -> dict:
    return next(o for o in allocation["options"] if o["key"] == key)


# The athlete from the issue: rides trails, does not race, values still enjoying
# the last descent over a bigger number.
TRAIL_RIDER = {
    "primary_objective": "Maximise enjoyable technical trail riding",
    "utility_weights": {
        "enjoyment": 0.40,
        "adaptation": 0.20,
        "consistency": 0.20,
        "health": 0.20,
        "race_performance": 0.0,
    },
    "modality_affinity": {
        "mtb": 0.9,
        "gravel": 0.7,
        "road": 0.4,
        "indoor": 0.3,
        "gym": 0.5,
    },
}


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------


def test_a_default_athlete_still_gets_the_key_session_first():
    """Nobody learned anything about them, so nothing about their week changes.

    A planner that quietly reorders every athlete's week the day it ships is a
    worse product, not a better one — the same regression guard #564 holds for
    the option ranking, one level up.
    """
    allocation = fa.allocate_day(None)

    assert _keys(allocation)[0] == "key_session"


def test_a_default_athletes_freshness_is_worth_most_for_the_intervals():
    """The cold-start vector is mostly adaptation, so that is what it protects."""
    values = fa.recovery_value(None)

    assert fa.band(values[fa.DEMAND_INTERVALS]) == "high"
    assert fa.band(values[fa.DEMAND_TRAIL]) == "low"


def test_a_rest_day_never_out_scores_a_key_session_on_physiology():
    """The artefact that would discredit the whole board.

    A recovery day and a long endurance ride are the same *system*. Crediting
    the rest day with the endurance gain would let it win on adaptation, and an
    athlete shown that ranking would be right to stop trusting the tool.
    """
    allocation = fa.allocate_day(None, gain_map={SYSTEM_ENDURANCE: GAIN_LARGE})

    recovery = _by_key(allocation, "recovery")
    key_session = _by_key(allocation, "key_session")
    assert recovery["components"]["adaptation"] < key_session["components"]["adaptation"]


def test_a_heat_tolerant_athlete_keeps_their_hot_day_riding():
    """Learned tolerance is evidence and gets to act like it.

    Nagging an athlete off every warm day they demonstrably handle is its own
    failure mode, and one this codebase has already had to fix once for the
    coach's prose.
    """
    naive = fa.allocate_day(TRAIL_RIDER, hot=True)
    tolerant = fa.allocate_day(
        TRAIL_RIDER, hot=True, heat_tolerance=fa.DIRECTION_TOLERANT
    )

    assert _keys(naive)[0] == "strength"
    assert _keys(tolerant)[0] == "trail"
    assert _by_key(tolerant, "trail")["heat_cost"] < _by_key(naive, "trail")["heat_cost"]


def test_a_heat_sensitive_athlete_is_pushed_further_off_the_hot_day():
    sensitive = fa.allocate_day(
        TRAIL_RIDER, hot=True, heat_tolerance=fa.DIRECTION_SENSITIVE
    )

    assert sensitive["options"][0]["key"] == "strength"
    assert _by_key(sensitive, "key_session")["heat_cost"] > fa.HEAT_PENALTY


# ---------------------------------------------------------------------------
# The worked example from the issue
# ---------------------------------------------------------------------------


def test_the_trail_riders_freshness_is_worth_most_off_road():
    """`RecoveryValue: TrailFreshness high, IntervalFreshness medium, race low`."""
    values = fa.recovery_value(TRAIL_RIDER)

    assert fa.band(values[fa.DEMAND_TRAIL]) == "high"
    assert fa.band(values[fa.DEMAND_INTERVALS]) == "medium"
    assert values[fa.DEMAND_RACE] == 0.0


def test_thursday_in_the_heat_becomes_strength_not_another_threshold_session():
    """The decision the coach had to make by hand, made by the board instead."""
    allocation = fa.allocate_day(
        TRAIL_RIDER,
        gain_map={SYSTEM_THRESHOLD: GAIN_LARGE, SYSTEM_ENDURANCE: GAIN_MAINTENANCE},
        hot=True,
    )

    assert _keys(allocation)[0] == "strength"
    assert _keys(allocation)[-1] == "key_session"


def test_the_same_athlete_on_a_mild_day_still_rides():
    """Not an instruction to train less — an instruction to spend freshness well.

    Take the heat away and the strength day stops winning. If it did not, this
    would be a feature that talks athletes out of riding.
    """
    allocation = fa.allocate_day(TRAIL_RIDER)

    assert _keys(allocation)[0] == "trail"
    assert _keys(allocation).index("strength") > _keys(allocation).index("trail")


def test_a_hard_session_is_charged_for_the_weekend_it_costs():
    """The term the planner was missing entirely."""
    allocation = fa.allocate_day(TRAIL_RIDER)

    key_session = _by_key(allocation, "key_session")
    assert key_session["competes_with"] == fa.DEMAND_TRAIL
    assert key_session["freshness_cost"] > 0
    assert key_session["score"] == pytest.approx(
        key_session["base"] - key_session["freshness_cost"], abs=0.01
    )


def test_a_session_is_not_charged_for_the_freshness_it_is_the_point_of():
    """A threshold day does not compete with interval freshness — it spends it."""
    intervals_first = fa.allocate_day(
        {
            "utility_weights": {
                "enjoyment": 0.10,
                "adaptation": 0.60,
                "consistency": 0.10,
                "health": 0.20,
                "race_performance": 0.0,
            }
        }
    )

    assert _by_key(intervals_first, "key_session")["competes_with"] != fa.DEMAND_INTERVALS
    assert _keys(intervals_first)[0] == "key_session"


def test_race_freshness_is_worthless_with_an_empty_calendar():
    """The rule that stops a taper being defended to an athlete who stopped racing."""
    racer = {
        "utility_weights": {
            "enjoyment": 0.10,
            "adaptation": 0.25,
            "consistency": 0.10,
            "health": 0.10,
            "race_performance": 0.45,
        }
    }

    assert fa.recovery_value(racer, upcoming_races=0)[fa.DEMAND_RACE] == 0.0
    assert fa.band(fa.recovery_value(racer, upcoming_races=2)[fa.DEMAND_RACE]) == "high"


# ---------------------------------------------------------------------------
# The board is tied to the existing chains, not to constants of its own
# ---------------------------------------------------------------------------


def test_the_board_reads_its_adaptation_from_the_roi_chain():
    """The gain comes from #478, so there is one answer to "what pays off"."""
    when_threshold_pays = fa.allocate_day(
        TRAIL_RIDER, gain_map={SYSTEM_THRESHOLD: GAIN_LARGE}
    )
    when_it_does_not = fa.allocate_day(
        TRAIL_RIDER, gain_map={SYSTEM_THRESHOLD: GAIN_SMALL, SYSTEM_VO2MAX: GAIN_SMALL}
    )

    assert (
        _by_key(when_threshold_pays, "key_session")["components"]["adaptation"]
        > _by_key(when_it_does_not, "key_session")["components"]["adaptation"]
    )


def test_a_key_session_takes_the_better_of_threshold_and_vo2max():
    """Pinning it to one system would understate the day for half the athletes."""
    allocation = fa.allocate_day(
        TRAIL_RIDER, gain_map={SYSTEM_THRESHOLD: GAIN_SMALL, SYSTEM_VO2MAX: GAIN_LARGE}
    )
    pinned = fa.allocate_day(TRAIL_RIDER, gain_map={SYSTEM_THRESHOLD: GAIN_LARGE})

    assert (
        _by_key(allocation, "key_session")["components"]["adaptation"]
        == _by_key(pinned, "key_session")["components"]["adaptation"]
    )


def test_the_gain_map_is_read_off_a_real_recommendation():
    from services.roi_recommendation import recommend_training_roi

    recommendation = recommend_training_roi(
        {"ftp": {"estimate": 260}, "map": {"estimate": 330}},
        [{"limiter": "threshold", "confidence": 0.8}],
    )

    assert fa.gain_map_from_recommendation(recommendation)[SYSTEM_THRESHOLD] == GAIN_LARGE


def test_an_insufficient_recommendation_contributes_no_gains():
    """No confident limiter means a flat board, not an invented one."""
    assert fa.gain_map_from_recommendation({"sufficient": False, "expected_gain": []}) == {}
    assert fa.gain_map_from_recommendation(None) == {}


def test_every_archetype_scores_on_the_axes_the_weight_vector_spans():
    """No axis of its own — the weights are learned at one gate (#566) or nowhere."""
    allocation = fa.allocate_day(TRAIL_RIDER)

    for option in allocation["options"]:
        assert set(option["components"]) == set(mm.MOTIVATION_COMPONENTS)


def test_recovery_value_is_derived_and_never_stored():
    """It is a reading of the motivation model, so it moves when that model moves."""
    before = fa.recovery_value(TRAIL_RIDER)
    after = fa.recovery_value(
        {**TRAIL_RIDER, "modality_affinity": {**TRAIL_RIDER["modality_affinity"], "mtb": 0.1, "gravel": 0.1}}
    )

    assert before[fa.DEMAND_TRAIL] > after[fa.DEMAND_TRAIL]


# ---------------------------------------------------------------------------
# What reaches the prompt
# ---------------------------------------------------------------------------


def test_the_section_names_every_deduction():
    """A day that lost to weather and one that lost to the weekend lost differently."""
    section = prompts.freshness_allocation_section(
        fa.allocate_day(TRAIL_RIDER, hot=True)
    )

    assert "heat" in section
    assert "freshness owed to" in section
    assert fa.demand_label(fa.DEMAND_TRAIL) in section


def test_the_section_is_empty_without_an_allocation():
    assert prompts.freshness_allocation_section(None) == ""
    assert prompts.freshness_allocation_section({"options": []}) == ""


def test_the_section_says_the_heat_was_already_scaled_for_tolerance():
    """Otherwise the model discounts a hot day a second time on top of ours."""
    section = prompts.freshness_allocation_section(
        fa.allocate_day(TRAIL_RIDER, hot=True, heat_tolerance=fa.DIRECTION_TOLERANT)
    )

    assert "heat-tolerant" in section
    assert "a second time" in section


def test_the_planner_block_is_empty_when_nothing_is_known():
    """A new athlete gets today's plan prompt, byte for byte."""
    assert prompts.planner_athlete_model_section() == ""


def test_the_planner_block_carries_objective_identity_and_board():
    section = prompts.planner_athlete_model_section(
        motivation=mm.normalize_model(TRAIL_RIDER),
        identity={
            "patterns": [
                {
                    "pattern": "a target ahead lifts the effort",
                    "onAnEasyDay": "the easy ride is at risk the moment someone appears up the road",
                    "confidence": 0.7,
                }
            ]
        },
        allocation=fa.allocate_day(TRAIL_RIDER),
    )

    assert "Maximise enjoyable technical trail riding" in section
    assert "a target ahead lifts the effort" in section
    assert "freshness is worth spending on" in section


# ---------------------------------------------------------------------------
# The rule the planner is given
# ---------------------------------------------------------------------------


def test_the_plan_prompts_carry_the_allocation_rule():
    """Both entry points, or the nightly regen quietly keeps the old behaviour."""
    for system_prompt in (prompts.generate_plan_system(), prompts.adapt_plan_system()):
        assert "highest-value use" in system_prompt


def test_the_rule_bans_the_framings_the_issue_names():
    rule = prompts.plan_allocation_rule()

    for banned in (
        "avoid unnecessary fatigue",
        "keep systemic load low",
        "protect the adaptation",
    ):
        assert banned in rule
    assert "not an instruction to train less" in rule


def test_the_rule_offers_strength_as_a_real_option():
    """Without it, the only way to not schedule intervals was to schedule nothing."""
    rule = prompts.plan_allocation_rule()

    assert "strength" in rule
    assert "not a filler" in rule


def test_every_archetype_prescribes_a_workout_type_the_planner_may_emit():
    """A board entry the planner cannot express as a plan day is advice it drops.

    The permitted ``workoutType`` values live in the plan system prompt and
    nowhere else — there is no enum to assert against — so this checks the board
    against the same string the model is given.
    """
    system_prompt = prompts.generate_plan_system()

    for archetype in fa.ARCHETYPES:
        assert f'"{archetype.prescription}"' in system_prompt
