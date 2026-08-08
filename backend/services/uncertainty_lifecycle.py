"""Ceilings and expiry for the coach's uncertainty records (#581).

The coach keeps four kinds of uncertainty record. Three of them only grew:
74 hypotheses proposed and none ever confirmed or refuted, 23 open questions
still open, 20 experiments never run. The inflow was ~18 hypotheses a week and
the outflow was zero, so the season would have ended with several hundred rows,
all of which want to be in the coach prompt.

96 % of those hypotheses had ``evidence_count = 1`` — they were never observed a
second time. That is not bad luck, it is the design: the statements were
compound and narrowly conditioned ("performing a yoga session immediately
following a consecutive strength and high-intensity cycling…"), and the dedupe
key is the normalised statement text, so a configuration that never repeats
verbatim can never accrue a second observation. A claim that cannot recur cannot
be confirmed and cannot be refuted.

The counter-example was already in the repo. ``athlete_inquiries`` is the one
healthy channel and the only one with a capacity and a lifecycle
(``ATHLETE_INQUIRY_MAX_PENDING``, ``ATHLETE_INQUIRY_MAX_ASKS``, then hand off to
Settings): 5 asked, 5 answered, 0 pending. The other three had only
``MIN_ACTIVITIES = 8``, which is an entry threshold, not a ceiling.

This module gives the other three the same two things: a stated ceiling, and an
end. Pure — no DB, no LLM — so the crud gates, the generation steps and the
tests all read one rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# The terminal state for a record nothing ever came back to. Distinct from
# ``refuted``/``dismissed``, which mean the athlete or the evidence ruled on it;
# this one means no one ever did, and saying so is the honest record.
STATUS_EXPIRED = "expired"

# Audit vocabulary. ``EVENT_DECLINED`` is written when a channel is full and the
# coach wanted to add anyway — without it, a ceiling is invisible and there is no
# way to tell "the coach had nothing to say" from "the coach was not allowed to".
EVENT_EXPIRED = "expired"
EVENT_DECLINED = "declined_at_capacity"
# The value gate (#582): every candidate that reached it, kept or dropped. Both
# are recorded, because "share of raised uncertainties that actually resolve"
# needs the raise to pair the later expiry or resolution against.
EVENT_RAISED = "raised"
EVENT_DECLINED_LOW_VALUE = "declined_low_value"

CHANNEL_HYPOTHESIS = "hypothesis"
CHANNEL_OPEN_QUESTION = "open_question"
CHANNEL_EXPERIMENT = "experiment"
# Channels the value gate (#582) covers but this module's ceilings do not:
# inquiries already had their own capacity and lifecycle, and the unconfirmed
# session question (#580) belongs to a ride rather than to a pile.
CHANNEL_INQUIRY = "inquiry"
CHANNEL_SESSION_QUESTION = "session_question"


@dataclass(frozen=True)
class ChannelPolicy:
    """How many of these may be open at once, and how long one may sit unread.

    ``unsupported_after_days`` applies to a record nothing has come back to —
    for a hypothesis, ``evidence_count`` still at 1. ``supported_after_days`` is
    the longer grace for one that did recur: those are the only records that
    ever worked, and they are the ground truth any later value gate has to
    calibrate against, so they are not thrown away on the same clock.
    """

    channel: str
    ceiling: int
    unsupported_after_days: int
    supported_after_days: int


# A hypothesis is cheap to form and expensive to keep — every one of them wants
# prompt tokens on every message. Twelve open is already more than a coach can
# hold in mind; four weeks is two full training blocks, long enough for a real
# pattern to show up twice and short enough that a one-off does not outlive its
# relevance.
HYPOTHESIS_POLICY = ChannelPolicy(
    channel=CHANNEL_HYPOTHESIS,
    ceiling=12,
    unsupported_after_days=28,
    supported_after_days=120,
)

# An open question is an admission of what the coach does not know. A handful is
# honest; two dozen is a backlog nobody reads. The window is longer than a
# hypothesis's because a question is meant to wait for evidence.
OPEN_QUESTION_POLICY = ChannelPolicy(
    channel=CHANNEL_OPEN_QUESTION,
    ceiling=8,
    unsupported_after_days=42,
    supported_after_days=120,
)

# An experiment asks the athlete to *do* something. More than a few outstanding
# is not a plan, it is homework — and 20 suggested with 0 run is what that looks
# like.
EXPERIMENT_POLICY = ChannelPolicy(
    channel=CHANNEL_EXPERIMENT,
    ceiling=4,
    unsupported_after_days=42,
    supported_after_days=120,
)

# Two other writers put rows in ``athlete_hypotheses``, and neither is the
# problem this issue describes. ``hypothesis_engine`` (#479) and
# ``weather_preference`` derive their claims deterministically from a stored
# model, re-derive them every pass, and already retire what the model stops
# supporting (``crud.decay_unsupported_model_hypotheses``). They are bounded by
# construction and they already drain.
#
# Governing them here would fight that machinery: their decay pass needs every
# derived statement to have been written, so refusing one at a ceiling would make
# the next pass decay a hypothesis the model still supports. The ceiling and the
# expiry therefore apply to the free-form, LLM-formed hypotheses — the channel
# whose inflow was unbounded and whose outflow was zero.
DETERMINISTIC_HYPOTHESIS_CATEGORIES = frozenset({"performance_model", "weather_preference"})


def governs_hypothesis_category(category: str | None) -> bool:
    """Is this hypothesis one the lifecycle owns, or one a model re-derives?"""
    return (category or "general") not in DETERMINISTIC_HYPOTHESIS_CATEGORIES


POLICIES = {
    CHANNEL_HYPOTHESIS: HYPOTHESIS_POLICY,
    CHANNEL_OPEN_QUESTION: OPEN_QUESTION_POLICY,
    CHANNEL_EXPERIMENT: EXPERIMENT_POLICY,
}

# A statement long enough to be compound is a statement that cannot recur, and a
# claim that cannot recur can never be confirmed or refuted. The prompt asks for
# one condition and one outcome; this is the enforcement, because an instruction
# the pipeline does not check is a suggestion. Measured against the prod
# examples: the unfalsifiable ones ran 150–200 characters.
MAX_FALSIFIABLE_STATEMENT_CHARS = 140


def as_aware_utc(value: datetime) -> datetime:
    """Treat a naive timestamp as UTC — SQLite hands them back without a zone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def age_in_days(last_seen: datetime, now: datetime) -> int:
    return max(0, (as_aware_utc(now) - as_aware_utc(last_seen)).days)


def has_expired(
    *,
    last_seen: datetime,
    evidence_count: int,
    policy: ChannelPolicy,
    now: datetime,
) -> bool:
    """Has nothing come back to this record for long enough to let it go?

    ``last_seen`` is the record's ``updated_at`` — the last time anything
    touched it, which for these channels means the last time the evidence was
    re-observed.
    """
    window = (
        policy.unsupported_after_days
        if evidence_count <= 1
        else policy.supported_after_days
    )
    return as_aware_utc(now) - as_aware_utc(last_seen) >= timedelta(days=window)


def expiry_reason(
    *,
    last_seen: datetime,
    evidence_count: int,
    policy: ChannelPolicy,
    now: datetime,
) -> str:
    """Why this record left the set, in a form a person can read later.

    A record that leaves the set should leave a trace — the same reasoning as
    the weight-event audit trail (#566). Silence here would make the pile drain
    for reasons nobody could reconstruct.
    """
    days = age_in_days(last_seen, now)
    if evidence_count <= 1:
        return (
            f"Never observed a second time in {days} days "
            f"(ceiling {policy.unsupported_after_days} days for a single observation)."
        )
    return (
        f"{evidence_count} observations, but nothing new for {days} days "
        f"(ceiling {policy.supported_after_days} days)."
    )


def capacity_for(*, open_count: int, policy: ChannelPolicy) -> int:
    """How many *new* records this channel may still take. Never negative.

    Strengthening an existing record is deliberately not governed by this: the
    whole point is to make evidence accrue on what is already there, so a full
    channel must still be able to observe one of its own members again.
    """
    return max(0, policy.ceiling - open_count)


def is_falsifiable_statement(statement: str) -> bool:
    """Could this claim plausibly be written again for the same athlete?

    Not a semantic judgement — a length gate. It catches the compound,
    multi-condition sentences that made 96 % of hypotheses unrepeatable by
    construction, and nothing else. False negatives (a long but genuinely
    recurring claim) cost one dropped candidate; false positives cost a row that
    can never resolve, which is the failure this issue is about.
    """
    cleaned = " ".join(statement.split())
    return 0 < len(cleaned) <= MAX_FALSIFIABLE_STATEMENT_CHARS
