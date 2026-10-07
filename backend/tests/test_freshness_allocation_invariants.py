"""Properties the day board holds for every athlete (ai-trainer-ops#26).

``test_freshness_allocation.py`` holds the worked examples from #602: the trail
rider, the hot Thursday, the default athlete. This file asks the other question
— what holds for *any* athlete — by generating motivation models, ROI gains,
race calendars and heat, and checking prohibitions rather than optima. What the
best day is changes with every model improvement; that a deduction is never
hidden, or that a mild day does not depend on heat tolerance, does not.

Each property is one the module's own docstring promises:

* *Every deduction is named in the output* — the score is exactly the base
  minus the two named deductions, and a charge always says what it competes
  with.
* *A session is not charged for the freshness it is the point of.*
* Heat is a cost of heat, scaled by tolerance — nothing else.
* *Recovery value is a function, not a table* — the board is pure.
* *A default athlete is unaffected* — in the scope it actually holds, which is
  narrower than the sentence reads; see the cold-start test below.
"""

from __future__ import annotations

import copy

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from services import freshness_allocation as fa
from services import motivation_model as mm
from services.roi_recommendation import (
    GAIN_LARGE,
    GAIN_MAINTENANCE,
    GAIN_MODERATE,
    GAIN_SMALL,
    SYSTEM_ENDURANCE,
    SYSTEM_THRESHOLD,
    SYSTEM_VO2MAX,
)
from services.weather_preference import DIRECTION_SENSITIVE, DIRECTION_TOLERANT

_SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

_ARCHETYPES = {archetype.key: archetype for archetype in fa.ARCHETYPES}
_TOLERANCES = (None, "", DIRECTION_TOLERANT, DIRECTION_SENSITIVE, "unknown")

_weight = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)

motivations = st.one_of(
    st.none(),
    st.fixed_dictionaries(
        {},
        optional={
            "utility_weights": st.fixed_dictionaries(
                {}, optional={c: _weight for c in mm.MOTIVATION_COMPONENTS}
            ),
            "modality_affinity": st.fixed_dictionaries(
                {}, optional={m: _weight for m in mm.MODALITIES}
            ),
        },
    ),
)
gain_maps = st.one_of(
    st.none(),
    st.fixed_dictionaries(
        {},
        optional={
            system: st.sampled_from(
                [GAIN_LARGE, GAIN_MODERATE, GAIN_SMALL, GAIN_MAINTENANCE]
            )
            for system in (SYSTEM_THRESHOLD, SYSTEM_VO2MAX, SYSTEM_ENDURANCE)
        },
    ),
)
races = st.integers(min_value=0, max_value=6)
tolerances = st.sampled_from(_TOLERANCES)


def _board(motivation, gain_map, upcoming_races, hot, tolerance):
    return fa.allocate_day(
        motivation,
        gain_map=gain_map,
        upcoming_races=upcoming_races,
        hot=hot,
        heat_tolerance=tolerance,
    )


# ---------------------------------------------------------------------------
# Every deduction is named
# ---------------------------------------------------------------------------


@_SETTINGS
@given(motivations, gain_maps, races, st.booleans(), tolerances)
def test_every_archetype_is_on_the_board_exactly_once(m, g, r, hot, t):
    keys = [option["key"] for option in _board(m, g, r, hot, t)["options"]]
    assert sorted(keys) == sorted(_ARCHETYPES)


@_SETTINGS
@given(motivations, gain_maps, races, st.booleans(), tolerances)
def test_the_score_is_the_base_minus_the_named_deductions(m, g, r, hot, t):
    """Nothing moves a score that the output does not name."""
    for option in _board(m, g, r, hot, t)["options"]:
        expected = option["base"] - option["heat_cost"] - option["freshness_cost"]
        # Each term is rounded to two places on its own.
        assert abs(option["score"] - expected) <= 0.011, option


@_SETTINGS
@given(motivations, gain_maps, races, st.booleans(), tolerances)
def test_the_board_is_ranked_by_score(m, g, r, hot, t):
    ranked = [(o["score"], o["base"]) for o in _board(m, g, r, hot, t)["options"]]
    assert ranked == sorted(ranked, reverse=True)


@_SETTINGS
@given(motivations, gain_maps, races, st.booleans(), tolerances)
def test_a_freshness_charge_always_says_what_it_competes_with(m, g, r, hot, t):
    for option in _board(m, g, r, hot, t)["options"]:
        assert option["freshness_cost"] >= 0
        assert bool(option["competes_with"]) == (option["freshness_cost"] > 0)


@_SETTINGS
@given(motivations, gain_maps, races, st.booleans(), tolerances)
def test_a_session_is_never_charged_for_the_freshness_it_is_the_point_of(
    m, g, r, hot, t
):
    for option in _board(m, g, r, hot, t)["options"]:
        assert option["competes_with"] not in _ARCHETYPES[option["key"]].serves


# ---------------------------------------------------------------------------
# Heat is a cost of heat, and only of heat
# ---------------------------------------------------------------------------


@_SETTINGS
@given(motivations, gain_maps, races, tolerances)
def test_a_mild_day_charges_nothing_for_heat(m, g, r, t):
    assert all(o["heat_cost"] == 0 for o in _board(m, g, r, False, t)["options"])


@_SETTINGS
@given(motivations, gain_maps, races, st.booleans(), tolerances)
def test_only_an_exposed_day_pays_for_heat(m, g, r, hot, t):
    for option in _board(m, g, r, hot, t)["options"]:
        if not _ARCHETYPES[option["key"]].heat_exposed:
            assert option["heat_cost"] == 0, option["key"]


@_SETTINGS
@given(motivations, gain_maps, races, tolerances, tolerances)
def test_on_a_mild_day_heat_tolerance_changes_nothing(m, g, r, t1, t2):
    """Tolerance scales a heat cost; with no heat there is nothing to scale."""
    assert _board(m, g, r, False, t1)["options"] == _board(m, g, r, False, t2)["options"]


@_SETTINGS
@given(motivations, gain_maps, races)
def test_heat_costs_order_by_tolerance(m, g, r):
    """Tolerant pays least, sensitive most, unknown pays the unscaled cost."""
    cost = {
        t: {o["key"]: o["heat_cost"] for o in _board(m, g, r, True, t)["options"]}
        for t in (DIRECTION_TOLERANT, None, DIRECTION_SENSITIVE)
    }
    for key in _ARCHETYPES:
        assert (
            cost[DIRECTION_TOLERANT][key]
            <= cost[None][key]
            <= cost[DIRECTION_SENSITIVE][key]
        )


# ---------------------------------------------------------------------------
# Derived, never stored
# ---------------------------------------------------------------------------


@_SETTINGS
@given(motivations, gain_maps, races, st.booleans(), tolerances)
def test_the_board_is_a_pure_function_of_its_inputs(m, g, r, hot, t):
    before = (copy.deepcopy(m), copy.deepcopy(g))
    first = _board(m, g, r, hot, t)
    second = _board(m, g, r, hot, t)
    assert first == second
    assert (m, g) == before, "the board must not write back into its inputs"


# ---------------------------------------------------------------------------
# The default athlete
# ---------------------------------------------------------------------------


@_SETTINGS
@given(races, tolerances)
def test_a_default_athlete_gets_the_key_session_first_on_a_mild_day(r, t):
    """The cold-start guard from #602, over every race count and tolerance.

    Deliberately scoped to what the module promises *about motivation*: no
    learned weights, no gain information, no heat. With heat, a default
    athlete's board leads with ``recovery`` instead, and with a large endurance
    gain it leads with ``endurance`` — the first is a question about how heat
    should treat an athlete nobody knows anything about (ai-trainer-ops#26), the
    second is physiology working as intended. Neither is pinned here.
    """
    board = _board(None, None, r, False, t)
    assert board["options"][0]["key"] == "key_session"
