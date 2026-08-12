"""Explaining a downgrade through the rider, not through what to avoid (#597).

The reply this issue was opened about:

    That self-awareness is spot on, and protecting an easy day from your
    competitive streak is the smartest call you can make right now. Let's swap
    today's recovery spin for yoga so you can take care of your lower back and
    keep your systemic fatigue completely flat before tomorrow's aerobic
    endurance work.

It makes the right decision and gives the wrong reason. Every clause after the
decision is risk management — protect, keep flat — so the easier choice reads as
the absence of training, which is the worst possible framing for an athlete whose
goal is trail quality rather than weekly load. Worse, "protecting an easy day
from your competitive streak" makes a behavioural pattern sound like a character
flaw the athlete is being helped to suppress.

These tests are mostly about the two ways this feature could be harmful rather
than merely useless: telling an athlete who they are on thin evidence, and
framing how they ride as something to overcome.
"""

from __future__ import annotations

import pytest

import crud
from services import rider_identity as ri
from services import workout_curiosity as wc
from services.prompts import (
    ask_trainer_system,
    coach_static_prefix,
    recovery_framing_rule,
    rest_recommendation_rules,
    rider_identity_section,
)
from tests.conftest import TestSessionLocal


def _rule(name: str) -> wc.SignalRule:
    return next(rule for rule in wc.SIGNAL_RULES if rule.name == name)


class _Fact:
    """The shape ``list_athlete_memory_facts`` returns, for the pure tests."""

    def __init__(self, fact, confidence, snippet="", status="active"):
        self.fact = fact
        self.confidence = confidence
        self.source_snippet = snippet
        self.status = status
        self.category = wc.MEMORY_CATEGORY


DIESEL = {
    "attributes": {
        "fatigue_resistance": {"score": "high", "confidence": 0.6},
        "aerobic_endurance": {"score": "above_average", "confidence": 0.55},
    }
}


# --- The example ------------------------------------------------------------


def test_the_pattern_behind_the_decision_reaches_the_coach():
    """The athlete said the recovery part would become negotiable if a rider
    appeared. That is the observation #593 records, and this is the sentence
    that turns it into a reason."""
    chase = _rule("chased_someone_down")
    patterns = ri.patterns_from_facts(
        [_Fact(chase.observation, 0.62, "I slowly reeled him in.")]
    )

    assert [pattern.key for pattern in patterns] == ["chased_someone_down"]
    assert "the moment someone appears up the road" in patterns[0].on_an_easy_day
    # Not "the first twenty minutes" — the point is that the risk is not where
    # the physiology would put it.
    assert "first twenty minutes" in patterns[0].on_an_easy_day


def test_a_pattern_is_stated_as_how_they_ride_and_never_as_a_failing():
    """The register the old reply got wrong. "Protecting an easy day from your
    competitive streak" makes a behavioural fact into something to be defended
    against; none of these may read that way."""
    forbidden = ("streak", "weakness", "discipline", "resist", "temptation", "fault")
    for rule in ri.PATTERN_RULES:
        text = f"{rule.label} {rule.on_an_easy_day}".casefold()
        for word in forbidden:
            assert word not in text, f"{rule.signal} reads as a character flaw"


@pytest.mark.parametrize(
    "banned",
    [
        "protect your recovery",
        "keep your fatigue low",
        "keep systemic load flat",
        "don't overdo it",
        "skipping training",
    ],
)
def test_the_banned_framings_are_named_literally(banned):
    """The issue lists bare words — "don't", "avoid", "protect". Banning those
    as words would mangle ordinary English and be rightly ignored, so what is
    named is the phrasing, in full. Same reason #593 spelled out its three
    questions instead of saying "avoid generic ones"."""
    assert banned in recovery_framing_rule()
    assert banned in coach_static_prefix()


def test_what_the_easier_choice_buys_is_offered_in_its_place():
    """A prohibition with no replacement is how a model ends up saying nothing."""
    rule = recovery_framing_rule()
    for replacement in ("invests in how the next key ride feels", "arriving fresher"):
        assert replacement in rule


def test_the_rest_rules_hand_the_wording_over():
    """`rest_recommendation_rules` is where the risk-management vocabulary comes
    from, and it wins by being the rule that is actually about this situation.
    It now says explicitly that it decides *whether*, not *how*."""
    assert "do not decide how to say it" in rest_recommendation_rules()


# --- Not inventing an identity ----------------------------------------------


@pytest.mark.asyncio
async def test_a_new_athlete_gets_no_identity_section(client, auth_headers):
    """Nothing is the right answer, and stays it for a long time."""
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = response.json()["id"]

    async with TestSessionLocal() as db:
        assert await ri.identity_for_prompt(db, user_id) is None


def test_the_bar_is_where_it_says_it_is():
    """This function is about the threshold, not about how a fact got there —
    so it is stated as the threshold, either side of it. What one and two
    tellings actually score is the accrual's business, and is asserted against
    the real write path in the integration tests below.
    """
    chase = _rule("chased_someone_down")
    bar = ri.PATTERN_MIN_CONFIDENCE

    assert ri.patterns_from_facts([_Fact(chase.observation, bar - 0.01)]) == []
    assert ri.patterns_from_facts([_Fact(chase.observation, bar)]) != []


def test_one_telling_lands_below_that_bar_and_two_land_above_it():
    """The claim the prompt section makes, tied to the constants that decide it
    rather than to two numbers copied out of them. Change the accrual and this
    fails here, loudly, instead of at a distance."""
    first = crud.ATHLETE_MEMORY_DEFAULT_CONFIDENCE
    second = first + crud.ATHLETE_MEMORY_CONFIDENCE_STEP

    assert first < ri.PATTERN_MIN_CONFIDENCE <= second


def test_the_athletes_own_confirmation_outranks_the_confidence_bar():
    chase = _rule("chased_someone_down")
    confirmed = _Fact(chase.observation, 0.2, status="user_confirmed")

    assert len(ri.patterns_from_facts([confirmed])) == 1


def test_a_rejected_observation_is_not_used():
    chase = _rule("chased_someone_down")
    assert ri.patterns_from_facts([_Fact(chase.observation, 0.9, status="rejected")]) == []


def test_the_section_is_empty_when_there_is_nothing_to_say():
    assert rider_identity_section(None) == ""
    assert rider_identity_section({"patterns": []}) == ""


# --- Riding style -----------------------------------------------------------


def test_the_diesel_read_comes_off_what_the_ride_file_actually_holds():
    style = ri.riding_style(DIESEL)

    assert style is not None and style.key == "diesel"
    assert "power held late in long rides" in style.basis


def test_it_never_calls_anyone_a_puncher():
    """The asymmetry is the honest part, not an omission.

    Durability is measured against something a ride file contains — power held
    in the second half of a long ride. The opposite claim is not:
    ``_infer_anaerobic_capacity`` caps its confidence at 0.3 and says why in its
    own ``missing_information`` — nothing confirms a one-minute effort was
    maximal. A high number there means "they once rode hard for a minute", and
    naming someone a puncher on that is the coach inventing an identity.
    """
    punchy = {
        "attributes": {
            "anaerobic_capacity": {"score": "above_average", "confidence": 0.3},
            "fatigue_resistance": {"score": "fades", "confidence": 0.6},
        }
    }
    assert ri.riding_style(punchy) is None


@pytest.mark.parametrize(
    "attributes",
    [
        {},
        {"fatigue_resistance": {"score": "unknown", "confidence": 0.1}},
        # Right score, but the attribute is a placeholder rather than a reading.
        {"fatigue_resistance": {"score": "high", "confidence": 0.2}},
    ],
)
def test_a_thin_performance_model_produces_no_style(attributes):
    assert ri.riding_style({"attributes": attributes}) is None


def test_the_weakest_supporting_attribute_bounds_the_claim():
    """Not the strongest, which would let one confident number carry a reading
    that two are needed for."""
    style = ri.riding_style(DIESEL)
    assert style is not None
    assert style.confidence == 0.55


# --- The mapping ------------------------------------------------------------


def test_there_is_something_to_check():
    assert len(ri.PATTERN_RULES) >= 5


@pytest.mark.parametrize("rule", ri.PATTERN_RULES, ids=lambda r: r.signal)
def test_every_mapping_names_a_pattern_that_is_actually_observed(rule):
    """The two modules are joined by the observation text, which is one constant
    on the #593 rule. Renaming or rewording a signal rule would silently orphan
    the mapping here and the pattern would stop reaching the coach with no error
    anywhere — this is the test that turns that into a failure."""
    assert rule.signal in {signal.name for signal in wc.SIGNAL_RULES}


@pytest.mark.parametrize("rule", ri.PATTERN_RULES, ids=lambda r: r.signal)
def test_every_mapping_says_what_changes_on_an_easy_day(rule):
    assert rule.on_an_easy_day and rule.label
    assert not rule.on_an_easy_day.endswith("?")


# --- End to end -------------------------------------------------------------


@pytest.mark.asyncio
async def test_what_the_athlete_said_becomes_what_the_coach_frames_with(
    client, auth_headers
):
    """The whole chain, through the real write path.

    The join between #593 and this module is the observation text, and that text
    goes through ``_normalise_athlete_memory_fact_key`` on the way in. If the
    round trip did not hold, patterns would silently never be found.
    """
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = response.json()["id"]
    message = (
        "3x12: 321W/150 bpm, 324W/153 bpm, 327W/160 bpm. In the last interval a "
        "rider on a TT bike was ahead of me and I slowly reeled him in."
    )

    # Two tellings is what #593's accrual needs before the coach may rely on it.
    for _ in range(2):
        async with TestSessionLocal() as db:
            await wc.curiosity_for_message(db, user_id, message)
            await db.commit()

    async with TestSessionLocal() as db:
        identity = await ri.identity_for_prompt(db, user_id, performance_model=DIESEL)

    assert identity is not None
    patterns = [entry["pattern"] for entry in identity["patterns"]]
    assert "a target ahead lifts the effort" in patterns
    assert identity["style"]["reading"].startswith("rides best under sustained")


@pytest.mark.asyncio
async def test_one_telling_is_recorded_but_is_not_yet_a_description_of_someone(
    client, auth_headers
):
    """The other side of the same integration: the bar is real end to end, not
    only in the pure unit test. The observation is on file after one message —
    it has to be, or it could never accrue — and is deliberately not usable yet.
    """
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = response.json()["id"]
    message = (
        "3x12 at 320W, 150 bpm. A rider was ahead of me and I chased him down."
    )

    async with TestSessionLocal() as db:
        await wc.curiosity_for_message(db, user_id, message)
        await db.commit()

    async with TestSessionLocal() as db:
        facts = [
            fact
            for fact in await crud.list_athlete_memory_facts(db, user_id)
            if fact.category == wc.MEMORY_CATEGORY
        ]
        identity = await ri.identity_for_prompt(db, user_id)

    assert facts, "the observation was recorded"
    assert identity is None, "but it is not yet something to describe them with"


def test_the_section_reaches_the_prompt_the_coach_is_sent():
    """The rule points at the section by name, so a section that never arrives
    would make the rule a lie."""
    chase = _rule("chased_someone_down")
    patterns = ri.patterns_from_facts([_Fact(chase.observation, 0.62, "reeled him in")])
    prompt = ask_trainer_system(
        profile={"currentFTP": 280},
        today="2026-08-12",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule="",
        rider_identity={
            "patterns": [
                {
                    "pattern": pattern.label,
                    "onAnEasyDay": pattern.on_an_easy_day,
                    "confidence": pattern.confidence,
                    "theirWords": pattern.evidence,
                }
                for pattern in patterns
            ]
        },
    )

    assert "Who this athlete is as a rider" in prompt
    assert "the ride quietly becomes a pursuit" in prompt
    # And the rule that says what to do with it travels with every prompt.
    assert "Framing a recommendation to do less" in prompt
