"""The one edge between what we diagnose about the athlete and what the corpus knows (#627).

Vector retrieval answers "which passage resembles this question?". It cannot
answer "which passage bears on *this* athlete's problem?", because the relation
``chunk --[addresses]--> limiter`` exists nowhere. Two athletes asking "should I
do more intervals?" got the same five chunks even when one was threshold-limited
and the other was already pinned against their aerobic ceiling.

This module is that relation, and deliberately nothing more:

- :func:`topics_for_text` tags a corpus chunk at ingest time, by keyword. No LLM
  extraction, no community summaries, no graph database — at corpus scale those
  cost real money and buy nothing a keyword match does not already give us.
- :func:`topics_for_limiter` names the topics that bear on a limiter the
  deterministic detector produced.

Both halves are pure and unit-testable, which is the point: the tag a chunk
carries has to be reproducible from the chunk, or a re-ingest silently reshuffles
what the coach is shown.

**The vocabulary is the limiter vocabulary.** It covers what
:mod:`services.limiter_detection` can currently diagnose and stops there. A topic
nobody can be diagnosed with would tag chunks that nothing ever asks for. Adding
one is a deliberate act: add the keywords here, map a limiter to it, and re-run
``scripts/ingest_cycling_science.py`` so existing rows pick the tag up.
"""

from __future__ import annotations

import re

from services.limiter_detection import (
    LIMITER_DURABILITY,
    LIMITER_THRESHOLD,
    LIMITER_VO2MAX,
)

# Topic identifiers stored in ``knowledge_chunks.topics``. They deliberately
# share the limiter identifiers' spelling — a topic exists because a limiter
# needs evidence for it — but the mapping below stays explicit so a limiter can
# later draw on more than its namesake.
TOPIC_THRESHOLD = "threshold"
TOPIC_VO2MAX = "vo2max"
TOPIC_DURABILITY = "endurance_durability"

# Keywords are matched against normalised text on word boundaries, so "ftp"
# matches "FTP" but not "sftp", and "vo2 max" matches "VO₂max". Prefer the term
# of art over the generic word: "threshold" alone would tag every chunk that
# mentions a threshold of any kind.
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    TOPIC_THRESHOLD: (
        "ftp",
        "functional threshold power",
        "lactate threshold",
        "anaerobic threshold",
        "threshold power",
        "maximal lactate steady state",
        "lactate steady state",
        "mlss",
        "sweet spot",
        "sweetspot",
        "critical power",
    ),
    TOPIC_VO2MAX: (
        "vo2max",
        "vo2 max",
        "vo2peak",
        "vo2 peak",
        "maximal oxygen uptake",
        "maximal aerobic power",
        "aerobic capacity",
        "aerobic ceiling",
        "hiit",
        "high intensity interval training",
    ),
    # Fuelling sits here on purpose: the durability limiter's own counter-evidence
    # says a late fade may be fuelling rather than fitness, so the science that
    # settles that question is exactly what the coach needs in front of it.
    TOPIC_DURABILITY: (
        "durability",
        "fatigue resistance",
        "cardiac drift",
        "aerobic decoupling",
        "decoupling",
        "glycogen",
        "carbohydrate",
        "fuelling",
        "fueling",
        "substrate utilisation",
        "substrate utilization",
        "long ride",
        "prolonged exercise",
    ),
}

# Which topics bear on a diagnosed limiter. ``insufficient_data`` is absent by
# design — a limiter we do not believe must not steer retrieval.
LIMITER_TOPICS: dict[str, tuple[str, ...]] = {
    LIMITER_THRESHOLD: (TOPIC_THRESHOLD,),
    LIMITER_VO2MAX: (TOPIC_VO2MAX,),
    LIMITER_DURABILITY: (TOPIC_DURABILITY,),
}

# A tag has to be *selective* to be worth anything: if half the corpus is tagged
# "threshold", ranking by it promotes an almost random chunk. Tagging on a single
# keyword did exactly that — measured over the seed corpus at ingest chunk size,
# 52% of chunks came out "vo2max" and 47% "threshold", because a cycling document
# name-drops FTP and VO₂max whatever it is actually about.
#
# So a chunk is tagged with what it is *mostly* about: score each topic by how
# often its vocabulary occurs, require the leader to clear MIN_TOPIC_OCCURRENCES,
# and keep the runners-up only if they stay within TOPIC_DOMINANCE_RATIO of it.
# The same measurement puts that at ~21-29% of chunks per topic with 9%
# multi-tagged — selective, while a chunk that genuinely weighs FTP against MAP
# still earns both. Re-measure when the corpus or the vocabulary changes.
MIN_TOPIC_OCCURRENCES = 2
TOPIC_DOMINANCE_RATIO = 0.5

# Fold the spellings the literature actually uses onto one form before matching:
# subscripts ("VO₂max"), and every separator that could sit inside a term.
_SUBSCRIPT_TWO = "₂"
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _normalise(text: str) -> str:
    """Lower-case *text* and reduce it to space-separated word tokens."""
    return f" {_NON_WORD.sub(' ', text.lower().replace(_SUBSCRIPT_TWO, '2')).strip()} "


def topic_scores(*parts: str | None) -> dict[str, int]:
    """How often each topic's vocabulary occurs across *parts*.

    Occurrences, not distinct keywords: a chunk that says "FTP" six times is
    about threshold, one that says it once on the way to discussing sleep is not.
    Exposed because it is what the tagging rule is tuned against — the numbers in
    :data:`MIN_TOPIC_OCCURRENCES` came out of running this over the real corpus.
    """
    haystack = _normalise(" ".join(part for part in parts if part))
    return {
        topic: sum(haystack.count(f" {keyword} ") for keyword in keywords)
        for topic, keywords in TOPIC_KEYWORDS.items()
    }


def topics_for_text(*parts: str | None) -> list[str]:
    """Tag a corpus chunk with the topic(s) it is predominantly about.

    *parts* are concatenated before matching, so a chunk can be tagged from its
    title as well as its body — a paper titled "Determinants of durability" earns
    the tag even when the abstract chunk in hand never repeats the word.

    Returns a sorted list (stable across runs, so a re-ingest that changes
    nothing writes nothing) and is empty for well over a third of the corpus:
    sleep, tapering and heat are real chunks that no limiter should promote, and
    an untagged chunk is simply ranked as it always was.
    """
    scores = topic_scores(*parts)
    best = max(scores.values(), default=0)
    if best < MIN_TOPIC_OCCURRENCES:
        return []
    cutoff = max(MIN_TOPIC_OCCURRENCES, best * TOPIC_DOMINANCE_RATIO)
    return sorted(topic for topic, score in scores.items() if score >= cutoff)


def topics_for_limiter(limiter: str | None) -> list[str]:
    """Topics worth ranking up for an athlete limited by *limiter*.

    Empty for ``None``, for ``insufficient_data`` and for any limiter without a
    mapping — all of which leave retrieval exactly as it was before #627. That is
    the intended failure mode: no diagnosis, no steering.
    """
    return list(LIMITER_TOPICS.get(limiter or "", ()))
