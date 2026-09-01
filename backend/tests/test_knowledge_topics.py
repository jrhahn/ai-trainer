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


def test_the_power_duration_literature_speaks_its_own_dialect():
    """We fetch W′ papers on purpose, then have to recognise how they write.

    Modelled on the production chunk that exposed the gap: "W′ expenditure and
    reconstitution during severe intensity constant power exercise" named
    critical power once and the severe-intensity domain six times, so it scored
    1 against a floor of 2 and went untagged.
    """
    abstract = (
        "W′ expenditure and reconstitution during severe intensity constant "
        "power exercise. Six participants completed severe intensity trials "
        "above critical power. Recovery below the severe intensity boundary "
        "restored W′ faster, and severe intensity tolerance scaled with it."
    )

    assert kt.topics_for_text(abstract) == [kt.TOPIC_THRESHOLD]


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
# stale_chunk_tags — making a vocabulary change reach the corpus (#632)
#
# A plain re-ingest writes only the rows whose source it fetched, and the paper
# half is whatever Semantic Scholar returns that minute. #631 shipped a keyword
# and the very paper it was written for stayed untagged in production.
# ---------------------------------------------------------------------------


def test_a_row_whose_tags_are_already_right_is_left_alone():
    """Re-running must write nothing, or the operation is not safe to repeat."""
    rows = [(1, "Threshold", THRESHOLD_CHUNK, [kt.TOPIC_THRESHOLD])]

    assert kt.stale_chunk_tags(rows) == []


def test_a_row_the_vocabulary_has_since_learned_about_is_rewritten():
    rows = [(7, "Threshold", THRESHOLD_CHUNK, [])]

    assert kt.stale_chunk_tags(rows) == [(7, [kt.TOPIC_THRESHOLD])]


def test_a_never_tagged_row_is_rewritten_even_when_it_earns_nothing():
    """NULL and "checked, no tags" must stop being the same thing.

    39 production rows sat at NULL and would earn no tag anyway. Leaving them
    NULL keeps them indistinguishable from rows no run has ever reached, which
    is the ambiguity that hid #632 in the first place.
    """
    rows = [(3, "Sleep", "Sleep extension improved reaction time.", None)]

    assert kt.stale_chunk_tags(rows) == [(3, [])]


def test_a_row_that_should_lose_a_tag_is_rewritten():
    """Narrowing the vocabulary has to propagate as readily as widening it."""
    rows = [(9, "Sleep", "Sleep extension improved reaction time.", ["vo2max"])]

    assert kt.stale_chunk_tags(rows) == [(9, [])]


def test_stored_order_does_not_count_as_a_difference():
    """Otherwise every run would rewrite every multi-tagged row forever."""
    both = sorted([kt.TOPIC_THRESHOLD, kt.TOPIC_VO2MAX])
    text = (
        "Threshold power as a fraction of maximal aerobic power tells you whether "
        "to chase FTP or the ceiling: a low ratio means lactate threshold has "
        "room, a high one means maximal aerobic power is what caps you."
    )

    assert kt.topics_for_text(text) == both
    assert kt.stale_chunk_tags([(1, "", text, list(reversed(both)))]) == []


def test_only_the_rows_that_changed_come_back():
    rows = [
        (1, "Threshold", THRESHOLD_CHUNK, [kt.TOPIC_THRESHOLD]),  # fine
        (2, "VO2max", VO2MAX_CHUNK, None),                        # never tagged
        (3, "Durability", DURABILITY_CHUNK, []),                  # missed
    ]

    assert kt.stale_chunk_tags(rows) == [
        (2, [kt.TOPIC_VO2MAX]),
        (3, [kt.TOPIC_DURABILITY]),
    ]


def test_the_severe_intensity_regression_would_have_been_caught():
    """The concrete production row: shipped keyword, stale tag, no way to notice."""
    w_prime = (
        "W′ expenditure and reconstitution during severe intensity constant "
        "power exercise. Six participants completed severe intensity trials "
        "above critical power. Recovery below the severe intensity boundary "
        "restored W′ faster, and severe intensity tolerance scaled with it."
    )

    assert kt.stale_chunk_tags([(42, "", w_prime, [])]) == [(42, [kt.TOPIC_THRESHOLD])]


def test_an_empty_corpus_is_not_an_error():
    assert kt.stale_chunk_tags([]) == []


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
