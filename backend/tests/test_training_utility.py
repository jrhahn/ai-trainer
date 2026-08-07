"""Scoring training options by expected athlete utility (#564).

The epic (#561) turns on one claim: two athletes with identical physiology can
need different coaching, because they train for different things. These tests
hold that claim to the number.

The most important test in the file is the regression guard — an athlete nobody
has learned anything about yet must get exactly the physiology-first ordering
they get today. A motivation-aware planner that quietly changes recommendations
for everyone would be a worse product, not a better one.
"""

from __future__ import annotations

import pytest

from services import motivation_model as mm
from services import training_utility as tu
from services.roi_recommendation import (
    GAIN_LARGE,
    GAIN_MAINTENANCE,
    GAIN_MODERATE,
    recommend_training_roi,
)


def _motivation(**overrides):
    return mm.normalize_model(overrides)


_THRESHOLD_LIMITER = [{"limiter": "threshold", "confidence": 0.8}]
_ATTRS = {
    "ftp": {"estimate": 260, "score": "moderate"},
    "map": {"estimate": 330, "score": "high"},
    "fractional_utilization": {"estimate": 0.79},
    "aerobic_endurance": {"score": "moderate"},
}


# ---------------------------------------------------------------------------
# The regression guard
# ---------------------------------------------------------------------------


def test_no_motivation_model_leaves_the_recommendation_untouched():
    """Opting out must be free: byte-for-byte the previous output."""
    before = recommend_training_roi(_ATTRS, _THRESHOLD_LIMITER)

    assert "utility" not in before


def test_a_default_motivation_model_still_ranks_physiology_first():
    """An athlete nobody has learned anything about keeps today's advice."""
    ranked = recommend_training_roi(
        _ATTRS, _THRESHOLD_LIMITER, motivation=mm.default_model()
    )["utility"]

    top = ranked["options"][0]
    assert top["system"] == "threshold"
    assert top["physiological_score"] == pytest.approx(
        max(o["physiological_score"] for o in ranked["options"])
    )


def test_a_race_focused_athlete_is_not_pushed_off_the_road():
    """The other half of the guard: racers must not be degraded (#564)."""
    racer = _motivation(
        utility_weights={
            "race_performance": 0.45,
            "adaptation": 0.35,
            "consistency": 0.1,
            "health": 0.05,
            "enjoyment": 0.05,
        }
    )

    ranked = tu.rank_options(
        [
            ("threshold", "road", GAIN_LARGE),
            ("threshold", "mtb", GAIN_LARGE),
        ],
        motivation=racer,
        upcoming_races=2,
    )

    assert ranked["options"][0]["modality"] == "road"


# ---------------------------------------------------------------------------
# The decision the epic is about
# ---------------------------------------------------------------------------


def test_the_same_prescription_can_change_modality():
    """#561's worked example: identical physiology, different bike."""
    trail_rider = _motivation(
        utility_weights={
            "enjoyment": 0.45,
            "adaptation": 0.25,
            "consistency": 0.15,
            "health": 0.10,
            "race_performance": 0.05,
        },
        modality_affinity={"mtb": 0.9, "road": 0.2},
    )

    ranked = tu.rank_options(
        [
            ("threshold", "road", GAIN_LARGE),
            ("threshold", "mtb", GAIN_MODERATE),
        ],
        motivation=trail_rider,
    )
    top = ranked["options"][0]

    # The system — the actual physiological prescription — is unchanged.
    assert top["system"] == "threshold"
    # The delivery is not.
    assert top["modality"] == "mtb"
    # And it did not win on physiology, which is exactly what has to be sayable.
    assert top["physiological_score"] < ranked["options"][1]["physiological_score"]
    assert top["motivation_score"] > ranked["options"][1]["motivation_score"]


def test_two_athletes_with_one_performance_model_get_different_advice():
    """The claim from #561, reduced to an assertion."""
    options = [
        ("threshold", "road", GAIN_LARGE),
        ("threshold", "mtb", GAIN_MODERATE),
        ("endurance", "mtb", GAIN_MAINTENANCE),
    ]
    trail_rider = _motivation(
        utility_weights={"enjoyment": 0.55, "adaptation": 0.2, "consistency": 0.15,
                         "health": 0.05, "race_performance": 0.05},
        modality_affinity={"mtb": 0.95, "road": 0.1},
    )
    racer = _motivation(
        utility_weights={"race_performance": 0.4, "adaptation": 0.4, "consistency": 0.1,
                         "health": 0.05, "enjoyment": 0.05},
        modality_affinity={"road": 0.9, "mtb": 0.3},
    )

    trail_top = tu.rank_options(options, motivation=trail_rider)["options"][0]
    racer_top = tu.rank_options(options, motivation=racer, upcoming_races=1)["options"][0]

    assert trail_top["modality"] == "mtb"
    assert racer_top["modality"] == "road"


# ---------------------------------------------------------------------------
# Both sub-scores survive
# ---------------------------------------------------------------------------


def test_every_option_carries_both_sub_scores_and_its_components():
    ranked = tu.rank_options(
        [("threshold", "road", GAIN_LARGE)], motivation=mm.default_model()
    )
    option = ranked["options"][0]

    assert option["physiological_score"] > 0
    assert option["motivation_score"] > 0
    assert set(option["components"]) == set(mm.MOTIVATION_COMPONENTS)


def test_the_weights_used_are_recorded_with_the_ranking():
    """A ranking whose weights are not stored cannot be audited later."""
    ranked = tu.rank_options(
        [("threshold", "road", GAIN_LARGE)],
        motivation=_motivation(utility_weights={"enjoyment": 0.5}),
    )

    assert sum(ranked["weights"].values()) == pytest.approx(1.0)
    assert set(ranked["weights"]) == set(mm.MOTIVATION_COMPONENTS)


def test_the_physiological_score_is_the_unweighted_gain():
    """Physiology is a claim about the body; the weights get no vote on it."""
    indifferent = _motivation(utility_weights={"adaptation": 0.02})

    ranked = tu.rank_options(
        [("threshold", "road", GAIN_LARGE)], motivation=indifferent
    )

    assert ranked["options"][0]["physiological_score"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Constraints filter, they do not discount
# ---------------------------------------------------------------------------


def test_a_crash_risk_constraint_removes_the_high_risk_option():
    cautious = _motivation(
        constraints=[{"text": "Avoid unnecessary crash and injury risk"}],
        modality_affinity={"mtb": 1.0, "road": 0.1},
    )

    ranked = tu.rank_options(
        [("threshold", "mtb", GAIN_LARGE), ("threshold", "road", GAIN_LARGE)],
        motivation=cautious,
    )

    assert [o["modality"] for o in ranked["options"]] == ["road"]
    assert ranked["excluded"][0]["modality"] == "mtb"
    assert "constraint" in ranked["excluded"][0]["excluded_by"]


def test_a_constraint_never_empties_the_board():
    """A filter that leaves nothing to recommend has malfunctioned."""
    cautious = _motivation(constraints=[{"text": "Stay healthy"}])

    ranked = tu.rank_options(
        [("threshold", "mtb", GAIN_LARGE)], motivation=cautious
    )

    assert ranked["options"]
    assert ranked["excluded"] == []


def test_road_riding_survives_a_risk_averse_constraint():
    """"I can't afford a crash" is not "I want to stop cycling"."""
    assert tu.is_risk_averse([{"text": "Stay healthy"}]) is True

    ranked = tu.rank_options(
        [("threshold", "road", GAIN_LARGE), ("endurance", "indoor", GAIN_MAINTENANCE)],
        motivation=_motivation(constraints=[{"text": "Stay healthy"}]),
    )

    assert {o["modality"] for o in ranked["options"]} == {"road", "indoor"}


def test_an_unrelated_constraint_filters_nothing():
    ranked = tu.rank_options(
        [("threshold", "mtb", GAIN_LARGE), ("threshold", "road", GAIN_LARGE)],
        motivation=_motivation(constraints=[{"text": "Keep Sundays free"}]),
    )

    assert ranked["excluded"] == []
    assert len(ranked["options"]) == 2


# ---------------------------------------------------------------------------
# Component scoring details worth pinning
# ---------------------------------------------------------------------------


def test_race_specificity_is_worthless_with_an_empty_calendar():
    """Otherwise a road bias outlives the athlete's interest in racing."""
    with_race = tu.score_components(
        "threshold", "road", gain=GAIN_LARGE,
        affinity=mm.DEFAULT_MODALITY_AFFINITY, upcoming_races=1,
    )
    without = tu.score_components(
        "threshold", "road", gain=GAIN_LARGE,
        affinity=mm.DEFAULT_MODALITY_AFFINITY, upcoming_races=0,
    )

    assert with_race["race_performance"] > 0
    assert without["race_performance"] == 0


def test_affinity_moves_enjoyment_and_consistency_together():
    """An athlete shows up for the rides they like."""
    liked = tu.score_components(
        "endurance", "mtb", gain=GAIN_MAINTENANCE,
        affinity={**mm.DEFAULT_MODALITY_AFFINITY, "mtb": 1.0},
    )
    disliked = tu.score_components(
        "endurance", "mtb", gain=GAIN_MAINTENANCE,
        affinity={**mm.DEFAULT_MODALITY_AFFINITY, "mtb": 0.0},
    )

    assert liked["enjoyment"] > disliked["enjoyment"]
    assert liked["consistency"] > disliked["consistency"]
    # Risk is a property of the activity, not of how much the athlete enjoys it.
    assert liked["health"] == disliked["health"]


@pytest.mark.parametrize(
    ("sport", "expected"),
    [
        ("Ride", "road"),
        ("MountainBikeRide", "mtb"),
        ("GravelRide", "gravel"),
        ("VirtualRide", "indoor"),
        ("WeightTraining", "gym"),
        ("Kayaking", None),
        (None, None),
    ],
)
def test_sport_types_map_onto_modalities(sport, expected):
    assert tu.modality_for_sport(sport) == expected


# ---------------------------------------------------------------------------
# What reaches the coach
# ---------------------------------------------------------------------------


def test_the_prompt_shows_both_sub_scores_and_names_the_trade_off():
    """The coach must be able to say the MTB won on preference, not physiology."""
    from services.prompts import athlete_performance_roi_section

    trail_rider = _motivation(
        utility_weights={"enjoyment": 0.5, "adaptation": 0.2, "consistency": 0.15,
                         "health": 0.1, "race_performance": 0.05},
        modality_affinity={"mtb": 0.95, "road": 0.1},
    )
    section = athlete_performance_roi_section(
        recommend_training_roi(_ATTRS, _THRESHOLD_LIMITER, motivation=trail_rider)
    )

    assert "utility" in section
    assert "physiology" in section
    assert "motivation" in section
    assert "Weights used" in section
    # The honesty instruction is the point of the block, not decoration.
    assert "dishonest" in section


def test_the_prompt_section_is_unchanged_without_a_motivation_model():
    from services.prompts import athlete_performance_roi_section

    section = athlete_performance_roi_section(
        recommend_training_roi(_ATTRS, _THRESHOLD_LIMITER)
    )

    assert "utility" not in section
    assert "Weights used" not in section


def test_the_prompt_names_what_a_constraint_ruled_out():
    from services.prompts import athlete_performance_roi_section

    cautious = _motivation(
        constraints=[{"text": "Avoid unnecessary crash and injury risk"}]
    )
    section = athlete_performance_roi_section(
        recommend_training_roi(_ATTRS, _THRESHOLD_LIMITER, motivation=cautious)
    )

    assert "Ruled out by the athlete's own constraints" in section
