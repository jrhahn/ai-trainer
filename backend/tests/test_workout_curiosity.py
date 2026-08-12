"""Noticing the interesting part of a workout message (#593).

The reply the issue was opened about:

    Strong execution on those intervals. You were slightly above target and
    your heart rate stayed controlled. How do your legs feel now?

Nothing in it is wrong. It is worth almost nothing, because it spent the one
follow-up question a reply is allowed on the least informative thing available,
while the rider the athlete chased down, the week they spent ill, and the
sentence where they said what they love about cycling went unremarked.

These tests are mostly about restraint, which is the harder half. Being curious
on demand is easy; being curious about the right thing and silent the rest of
the time is what makes it worth anything.
"""

from __future__ import annotations

import pytest

import crud
from services import uncertainty_value, workout_curiosity as wc
from services.prompts import (
    ask_trainer_system,
    coach_static_prefix,
    curiosity_rule,
    workout_curiosity_section,
)
from tests.conftest import TestSessionLocal

# The message from the issue, verbatim.
THE_MESSAGE = """1st interval: 321W, avg 150 bpm
2nd: 324W, avg 153 bpm
3rd: 327W, avg 160 bpm

Last week I had stomach issues and wasn't 100% fit.
Today I felt much better.

In the last interval I had a rider on a time-trial bike ahead of me. I slowly
reeled him in. That's the part I love about road cycling."""


# --- The example ------------------------------------------------------------


def test_the_question_lands_on_the_rider_up_the_road():
    """The headline. Not the legs, not the watts — the chase."""
    curiosity = wc.select_curiosity(THE_MESSAGE)

    assert curiosity is not None
    assert curiosity.signal.name == "chased_someone_down"


def test_everything_the_old_reply_missed_is_seen():
    """The issue lists six missed signals. Five are the extractor's to find; the
    sixth — rider identity — is what the observations accumulate into."""
    kinds = {signal.kind for signal in wc.extract_signals(THE_MESSAGE)}

    assert kinds == {
        wc.KIND_PHYSIOLOGY,
        wc.KIND_SOCIAL,
        wc.KIND_EMOTION,
        wc.KIND_HEALTH,
    }


def test_the_rising_power_profile_is_read_off_the_numbers():
    """No keyword can see this: it is a fact about a sequence, and it is exactly
    the thing the old reply flattened into "strong execution"."""
    series = wc.read_effort_series(THE_MESSAGE)
    assert series.watts == (321, 324, 327)
    assert series.bpm == (150, 153, 160)

    names = {signal.name for signal in wc.physiology_signals(THE_MESSAGE)}
    assert "rising_power_profile" in names
    # 150 → 153 → 160: the last block cost more than the two before it, which is
    # the "unusual" the issue asks for and the reason the chase is worth asking
    # about rather than merely noting.
    assert "heart_rate_paid_late" in names


def test_the_physiology_is_material_for_the_analysis_not_for_the_question():
    for signal in wc.physiology_signals(THE_MESSAGE):
        assert not signal.can_be_a_question


def test_the_gate_would_refuse_the_physiology_on_its_own():
    """Not a hardcode. The physiological signals carry no question because the
    value gate would decline one anyway — a trend the training stream shows by
    itself is precisely what #582 exists to keep out of the athlete's attention.
    The two agree, and this is the test that says so."""
    decision = uncertainty_value.evaluate(
        channel=uncertainty_value.CHANNEL_CURIOSITY,
        text="whether their threshold power is rising across interval sessions",
    )
    assert not decision.should_raise


# --- Restraint --------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "3x12 at 324W avg, 155 bpm. Done.",
        "Rode 2h endurance, 180W avg, 130 bpm.",
        "FTP test: 305W for 20 minutes, 172 bpm.",
    ],
)
def test_numbers_without_a_story_get_no_curiosity(message):
    """The stated non-goal. A data dump is answered as a data dump; inventing
    interest in it is how a coach becomes chatty."""
    assert not wc.is_curiosity_moment(message)
    assert wc.select_curiosity(message) is None


@pytest.mark.parametrize(
    "message",
    [
        "What should I ride tomorrow?",
        "I loved that ride yesterday, it was so much fun.",
        "",
    ],
)
def test_a_story_without_numbers_gets_no_curiosity(message):
    """Both halves are required. A conversation with no session behind it is a
    conversation the coach is already having."""
    assert not wc.is_curiosity_moment(message)
    assert wc.select_curiosity(message) is None


def test_only_one_question_comes_back():
    """Four signals cleared the gate on the issue's message; one is returned.
    The coach is allowed a single follow-up and this is where that is decided,
    not in the model's judgement."""
    ranked = wc.rank(wc.extract_signals(THE_MESSAGE))
    assert sum(1 for _, decision, _ in ranked if decision.should_raise) > 1

    curiosity = wc.select_curiosity(THE_MESSAGE)
    assert isinstance(curiosity, wc.Curiosity)


# --- The rules --------------------------------------------------------------


def test_there_is_something_to_check():
    """Guard the guard: a refactor that empties the rule set must not pass."""
    assert len(wc.SIGNAL_RULES) >= 8
    assert {rule.kind for rule in wc.SIGNAL_RULES} == {
        wc.KIND_SOCIAL,
        wc.KIND_EMOTION,
        wc.KIND_BEHAVIOUR,
        wc.KIND_HEALTH,
    }


@pytest.mark.parametrize("rule", wc.SIGNAL_RULES, ids=lambda r: r.name)
def test_every_rule_could_actually_become_the_question(rule):
    """A rule whose uncertainty can never clear its own gate is dead code that
    reads as a feature.

    This found two of them on the way in. ``paced_on_purpose`` was declined
    because its statement contains the word "pacing" and pacing is recorded —
    but the uncertainty was pacing *intent*, which no file holds. ``fuelling``
    was declined for containing "fitness". Both were fixed in the shared gate
    rather than by rewording around it.
    """
    decision = uncertainty_value.evaluate(
        channel=uncertainty_value.CHANNEL_CURIOSITY, text=rule.tells_us
    )
    assert decision.should_raise, (
        f"{rule.name} can never be asked: {decision.reason}"
    )


@pytest.mark.parametrize("rule", wc.SIGNAL_RULES, ids=lambda r: r.name)
def test_a_rule_names_a_topic_and_never_a_question(rule):
    """A stored question would be read out verbatim and every athlete would get
    the same sentence — the issue's own failure, one level up."""
    assert not rule.curious_about.strip().endswith("?")
    assert rule.observation and rule.tells_us


@pytest.mark.parametrize(
    "message,expected",
    [
        # "will" is not "ill", and a cold day is not a head cold: both traps
        # #582 walked into with bare patterns.
        ("I will ride 3x12 at 320W, 150 bpm.", set()),
        ("Cold and windy, 200W for 2h at 140 bpm.", set()),
        # And the ones that must still fire.
        ("3x12 at 320W, 150 bpm. Stomach was off all week.", {"coming_back_from_something"}),
        ("3x12 at 320W, 150 bpm. I held back on the first one.", {"paced_on_purpose"}),
    ],
)
def test_the_patterns_do_not_match_their_neighbours(message, expected):
    names = {
        signal.name
        for signal in wc.extract_signals(message)
        if signal.kind != wc.KIND_PHYSIOLOGY
    }
    assert names == expected


def test_it_understands_the_language_the_athlete_writes_in():
    """The coach replies in the athlete's language, so a capture that only reads
    English quietly stops working for half the messages (#563, #495)."""
    message = (
        "3x12 Minuten: 321W bei 150 bpm, 324W bei 153 bpm, 327W bei 160 bpm. "
        "Im letzten Intervall hatte ich einen Zeitfahrer vor mir und habe ihn "
        "langsam eingeholt. Genau dafür liebe ich Rennradfahren."
    )
    curiosity = wc.select_curiosity(message)

    assert curiosity is not None
    assert curiosity.signal.name == "chased_someone_down"


# --- Ranking ----------------------------------------------------------------


def test_a_trait_already_on_file_loses_to_a_fresh_one():
    """Novelty, the issue's first ranking criterion. Once the coach knows this
    athlete rides for a target ahead, asking again learns nothing."""
    chase = next(r for r in wc.SIGNAL_RULES if r.name == "chased_someone_down")

    fresh = wc.select_curiosity(THE_MESSAGE)
    familiar = wc.select_curiosity(THE_MESSAGE, sightings={chase.observation: 5})

    assert fresh is not None and fresh.signal.name == "chased_someone_down"
    assert familiar is not None and familiar.signal.name != "chased_someone_down"


def test_novelty_reorders_but_never_vetoes():
    """It is an ordering and only ever an ordering. A topic the coach has heard
    before can still be the most important thing in the message, and folding
    novelty into the gate's value would drop it below a bar it should clear."""
    seen_everything = {rule.observation: 99 for rule in wc.SIGNAL_RULES}

    assert wc.select_curiosity(THE_MESSAGE, sightings=seen_everything) is not None


def test_a_candidate_explanation_outranks_a_more_valuable_topic_that_explains_nothing():
    """The issue's second ranking criterion, and the one that is load-bearing.

    "That's what I love about cycling" scores higher at the gate than "I was ill
    last week" — it moves more of what this athlete trains for. But only one of
    them could be why the third interval cost what it did, and that one is gone
    by tomorrow if nobody asks. So when the numbers showed something, it wins.
    """
    message = (
        "3x12: 321W/150 bpm, 324W/153 bpm, 327W/160 bpm. Stomach was off all "
        "week. That's the part I love about road cycling."
    )
    with_numbers = wc.extract_signals(message)
    without_numbers = [s for s in with_numbers if s.kind != wc.KIND_PHYSIOLOGY]

    illness, love = (
        next(s for s in without_numbers if s.name == "coming_back_from_something"),
        next(s for s in without_numbers if s.name == "named_what_they_love"),
    )
    values = {s.name: d.value for s, d, _ in wc.rank([illness, love])}
    assert values["named_what_they_love"] > values["coming_back_from_something"]

    # With nothing to explain, the more valuable topic wins on its own merits.
    assert wc.rank(without_numbers)[0][0].name == "named_what_they_love"
    # With a rising profile and a late heart-rate jump on the table, the illness
    # is the candidate explanation and takes the question.
    assert wc.rank(with_numbers)[0][0].name == "coming_back_from_something"


def test_the_weights_decide_what_is_worth_asking_this_athlete():
    """"Worth asking" means worth asking *them* — the gate scores relevance
    against their own utility vector (#564/#566), and this rides on that."""
    health_only = {
        "health": 1.0,
        "adaptation": 0.0,
        "consistency": 0.0,
        "enjoyment": 0.0,
        "race_performance": 0.0,
    }
    ranked = wc.rank(wc.extract_signals(THE_MESSAGE), weights=health_only)
    by_name = {signal.name: decision for signal, decision, _ in ranked}

    assert by_name["coming_back_from_something"].value > by_name[
        "named_what_they_love"
    ].value


# --- What gets written down -------------------------------------------------


async def _user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


@pytest.mark.asyncio
async def test_the_observations_carry_the_athletes_own_words(client, auth_headers):
    user_id = await _user_id(client, auth_headers)

    async with TestSessionLocal() as db:
        await wc.curiosity_for_message(db, user_id, THE_MESSAGE)
        await db.commit()

    async with TestSessionLocal() as db:
        facts = [
            fact
            for fact in await crud.list_athlete_memory_facts(db, user_id)
            if fact.category == wc.MEMORY_CATEGORY
        ]

    assert facts
    chase = next(fact for fact in facts if "target ahead" in fact.fact)
    assert "reeled him in" in chase.source_snippet
    # An inference about a person off one sentence is a candidate, not a belief:
    # the memory-fact machinery caps a first sighting below the trust threshold
    # and only recurrence promotes it (#387).
    assert chase.confidence < 0.5
    assert chase.kind == "observation"


@pytest.mark.asyncio
async def test_telling_it_again_builds_the_belief(client, auth_headers):
    """The loop the issue asks for, closed: what the athlete says accumulates
    into the model rather than evaporating with the conversation.

    Repeating the *same* message deliberately does not change the question —
    every topic in it ages equally, so the ordering between them is unchanged,
    which is right. Novelty discriminates between what has been heard and what
    has not; that is
    ``test_a_trait_already_on_file_loses_to_a_fresh_one``.
    """
    user_id = await _user_id(client, auth_headers)

    for _ in range(2):
        async with TestSessionLocal() as db:
            await wc.curiosity_for_message(db, user_id, THE_MESSAGE)
            await db.commit()

    async with TestSessionLocal() as db:
        facts = [
            fact
            for fact in await crud.list_athlete_memory_facts(db, user_id)
            if fact.category == wc.MEMORY_CATEGORY
        ]
    chase = next(fact for fact in facts if "target ahead" in fact.fact)
    assert chase.observation_count == 2
    # A second, independent sighting is what turns a candidate into something the
    # coach may rely on in a prompt: 0.35 on the first, +0.2 on each after (#387).
    assert chase.confidence >= 0.5


@pytest.mark.asyncio
async def test_the_verdict_is_recorded_whichever_way_it_went(client, auth_headers):
    """#582's rule: a decline is the half that had nowhere to be legible."""
    user_id = await _user_id(client, auth_headers)

    async with TestSessionLocal() as db:
        await wc.curiosity_for_message(db, user_id, THE_MESSAGE)
        await db.commit()

    async with TestSessionLocal() as db:
        events = await crud.list_uncertainty_events(
            db, user_id, channel=uncertainty_value.CHANNEL_CURIOSITY
        )

    assert len(events) == 1
    # The rules that fired, by name, the way a weight change carries its
    # activations (#566) — so "why did it ask that?" has an answer on file.
    assert "Rules:" in events[0].reason
    assert "lives_in_the_athlete" in events[0].reason


@pytest.mark.asyncio
async def test_the_lookup_key_is_the_key_the_write_used(client, auth_headers):
    """Novelty is only exact if both sides normalise the same way. They do
    because they go through one crud function rather than two copies of a rule.
    """
    user_id = await _user_id(client, auth_headers)
    fact = "Rides harder with a target ahead — an external mark lifts the effort"

    async with TestSessionLocal() as db:
        await crud.observe_athlete_memory_fact(
            db, user_id, fact=fact, category=wc.MEMORY_CATEGORY
        )
        await db.commit()

    async with TestSessionLocal() as db:
        counts = await crud.athlete_memory_observation_counts(
            db, user_id, [fact, "something never said"], category=wc.MEMORY_CATEGORY
        )

    assert counts[fact] == 1
    assert counts["something never said"] == 0


@pytest.mark.asyncio
async def test_a_message_worth_no_curiosity_writes_nothing(client, auth_headers):
    user_id = await _user_id(client, auth_headers)

    async with TestSessionLocal() as db:
        result = await wc.curiosity_for_message(db, user_id, "3x12 at 324W, 155 bpm.")
        await db.commit()

    assert result is None
    async with TestSessionLocal() as db:
        facts = [
            fact
            for fact in await crud.list_athlete_memory_facts(db, user_id)
            if fact.category == wc.MEMORY_CATEGORY
        ]
    assert facts == []


# --- The prompt -------------------------------------------------------------


@pytest.mark.parametrize(
    "banned",
    ["how do your legs feel", "how is your recovery", "how are you feeling now"],
)
def test_the_generic_questions_are_named_and_banned(banned):
    """Named rather than described. "Avoid generic questions" is advice the
    model can agree with and then ignore; a list of three sentences is not."""
    assert banned in curiosity_rule()
    assert banned in coach_static_prefix()


def test_the_rule_survives_a_message_with_no_curiosity():
    """The rule is static and the finding is not, so the prefix stays cacheable
    (#514) and the coach still knows not to reach for the banned three."""
    assert curiosity_rule() in coach_static_prefix()
    assert workout_curiosity_section(None) == ""


def test_the_finding_reaches_the_prompt_the_coach_is_actually_sent():
    """The section and the rule are useless apart: the rule says "the section
    names your question", so a section that never arrives makes the rule a lie.
    """
    curiosity = wc.select_curiosity(THE_MESSAGE)
    prompt = ask_trainer_system(
        profile={"currentFTP": 280},
        today="2026-08-11",
        last_7_days=[],
        next_n_days=[],
        assessment_section="",
        memory_section="",
        workout_section="",
        plan_updates_rule="",
        workout_curiosity=curiosity.as_prompt_dict(),
    )

    assert "Worth being curious about" in prompt
    assert curiosity.signal.curious_about in prompt
    assert "how do your legs feel" in prompt  # the ban travels with it


def test_the_section_carries_the_topic_and_not_a_question():
    curiosity = wc.select_curiosity(THE_MESSAGE)
    section = workout_curiosity_section(curiosity.as_prompt_dict())

    assert "Worth being curious about" in section
    assert "?" not in section
    # The analysis material and the question material are told apart for the
    # model, because telling them apart is the entire feature.
    assert "power rose across the efforts" in section
    assert "not for the question" in section
    assert "reeled him in" in section
