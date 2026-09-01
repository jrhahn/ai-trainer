"""The bridge between the athlete model and the science corpus (#627).

These tests protect two things that fail silently: a tag that stops matching the
text it was written for (the chunk quietly drops out of limiter-aware retrieval)
and a limiter that maps to no topic (the athlete quietly stops being steered).
"""

from __future__ import annotations

import pytest

from services import knowledge_topics as kt
from services.limiter_detection import (
    LIMITER_DURABILITY,
    LIMITER_INSUFFICIENT,
    LIMITER_THRESHOLD,
    LIMITER_VO2MAX,
)


# ---------------------------------------------------------------------------
# topics_for_text — tagging chunks at ingest
# ---------------------------------------------------------------------------


THRESHOLD_CHUNK = (
    "Raising FTP means training at or near lactate threshold. Sweet spot work "
    "sits just below threshold power and accumulates time at a sustainable cost."
)
VO2MAX_CHUNK = (
    "HIIT raises maximal aerobic power. Four-minute efforts push VO₂max toward "
    "the athlete's maximal oxygen uptake more effectively than steady riding."
)
DURABILITY_CHUNK = (
    "Durability declines after 3000 kJ of work. Cardiac drift and poor fatigue "
    "resistance show up as decoupling once glycogen runs low on a long ride."
)


@pytest.mark.parametrize(
    "text,expected",
    [
        (THRESHOLD_CHUNK, kt.TOPIC_THRESHOLD),
        (VO2MAX_CHUNK, kt.TOPIC_VO2MAX),
        (DURABILITY_CHUNK, kt.TOPIC_DURABILITY),
    ],
)
def test_a_chunk_about_one_topic_is_tagged_with_it(text, expected):
    assert kt.topics_for_text(text) == [expected]


@pytest.mark.parametrize("spelling", ["VO₂max", "VO2 max", "vo2max", "VO₂ max", "VO2max"])
def test_subscripts_and_spacing_do_not_hide_a_match(spelling):
    """The literature writes VO2max five different ways; all mean one topic.

    Asserted on the raw score so this covers normalisation only, and does not
    quietly start failing if the tagging thresholds are retuned.
    """
    assert kt.topic_scores(f"Training raised {spelling} by 8%.")[kt.TOPIC_VO2MAX] == 1


def test_the_title_can_carry_a_chunk_that_never_says_the_word():
    """Chunk 4 of a durability paper is still about durability."""
    body = (
        "Power output in the final hour fell 9% relative to the first, and the "
        "decoupling widened as the ride went on."
    )

    assert kt.topics_for_text(body) == []
    assert kt.topics_for_text("Determinants of durability", body) == [
        kt.TOPIC_DURABILITY
    ]


def test_a_chunk_that_genuinely_weighs_two_topics_keeps_both():
    """Fractional utilization is FTP *against* MAP; both limiters want it."""
    topics = kt.topics_for_text(
        "Threshold power as a fraction of maximal aerobic power tells you whether "
        "to chase FTP or the ceiling: a low ratio means lactate threshold has "
        "room, a high one means maximal aerobic power is what caps you."
    )

    assert topics == sorted([kt.TOPIC_THRESHOLD, kt.TOPIC_VO2MAX])


def test_a_passing_mention_does_not_tag_a_chunk():
    """The bug this rule exists for: one FTP name-drop tagged half the corpus."""
    sleep_chunk = (
        "Sleep extension improved sprint reaction time and reduced perceived "
        "exertion. Athletes who slept under six hours reported worse recovery, "
        "and their FTP test came in lower the following morning."
    )

    assert kt.topic_scores(sleep_chunk)[kt.TOPIC_THRESHOLD] == 1
    assert kt.topics_for_text(sleep_chunk) == []


def test_the_dominant_topic_beats_a_topic_that_is_only_mentioned():
    """A threshold article that references VO₂max once is not a VO₂max chunk."""
    text = THRESHOLD_CHUNK + " Compare this with training that targets VO₂max."

    assert kt.topic_scores(text)[kt.TOPIC_VO2MAX] == 1
    assert kt.topics_for_text(text) == [kt.TOPIC_THRESHOLD]


def test_a_real_but_secondary_topic_loses_to_a_dominant_one():
    """Two mentions clear the floor on their own; against six they are an aside.

    This is the case the floor alone cannot judge — without the dominance ratio
    the chunk would be tagged for both limiters and promoted for either.
    """
    text = (
        "FTP work dominates this block: four weeks of sweet spot, then FTP "
        "intervals, then sweet spot again, then an FTP test to confirm the new "
        "FTP. It touches VO₂max only in passing, and vo2max is not the target."
    )
    scores = kt.topic_scores(text)

    assert scores[kt.TOPIC_THRESHOLD] == 6
    assert scores[kt.TOPIC_VO2MAX] == 2, "the aside must clear MIN_TOPIC_OCCURRENCES"
    assert kt.topics_for_text(text) == [kt.TOPIC_THRESHOLD]


def test_untagged_topics_are_the_normal_case():
    """Sleep, tapering and heat are real chunks that no limiter should promote."""
    assert kt.topics_for_text("An exponential taper over two weeks.") == []
    assert kt.topics_for_text("Heat acclimatisation expands plasma volume.") == []


def test_keywords_match_on_word_boundaries():
    """Substring matching would tag anything containing the letters 'ftp'."""
    assert kt.topic_scores("Papers were transferred over sftp overnight.") == {
        kt.TOPIC_THRESHOLD: 0,
        kt.TOPIC_VO2MAX: 0,
        kt.TOPIC_DURABILITY: 0,
    }


def test_tagging_is_stable_and_deduplicated():
    """A re-ingest that changes nothing must write nothing."""
    assert kt.topics_for_text(THRESHOLD_CHUNK) == [kt.TOPIC_THRESHOLD]
    assert kt.topics_for_text(THRESHOLD_CHUNK) == kt.topics_for_text(THRESHOLD_CHUNK)


def test_empty_and_missing_parts_are_tolerated():
    assert kt.topics_for_text() == []
    assert kt.topics_for_text(None, "", None) == []


# ---------------------------------------------------------------------------
# topics_for_limiter — steering retrieval
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "limiter", [LIMITER_THRESHOLD, LIMITER_VO2MAX, LIMITER_DURABILITY]
)
def test_every_diagnosable_limiter_can_steer_retrieval(limiter):
    """A limiter the detector can produce but nothing maps is a silent dead end."""
    topics = kt.topics_for_limiter(limiter)

    assert topics, f"{limiter} has no topics, so it can never change retrieval"
    assert set(topics) <= set(kt.TOPIC_KEYWORDS), f"{limiter} maps to an untaggable topic"


def test_threshold_and_vo2max_do_not_steer_to_the_same_evidence():
    """The whole point: the same question must reach different chunks."""
    assert not set(kt.topics_for_limiter(LIMITER_THRESHOLD)) & set(
        kt.topics_for_limiter(LIMITER_VO2MAX)
    )


@pytest.mark.parametrize("limiter", [None, "", LIMITER_INSUFFICIENT, "made_up"])
def test_no_believed_limiter_means_no_steering(limiter):
    """Retrieval must fall back to plain similarity rather than guess."""
    assert kt.topics_for_limiter(limiter) == []
