"""Is reducing this uncertainty worth what reducing it costs? (#582)

Four modules decide, independently, to raise an uncertainty: hypotheses, open
questions, validation experiments, and questions put straight to the athlete.
Each had its own entry rule and none of them asked the question that actually
matters. #581 gave them a ceiling and an end; this gives them a bar to clear on
the way in.

The reasoning already existed — inside one prompt, as English. The inquiry
generator makes the model work out what the incoming data stream will tell it
anyway and only ask about the residual, and that gate is the reason inquiries
were the one healthy channel. But prose in a prompt applies to one channel of
four, and cannot be tested, reviewed at a glance, or recorded. This is the same
move #566 made for the utility weights: a rule that is data, evaluated at one
gate, recorded by name.

    value = decision_relevance × (1 − data_share × how much this channel cares)
    raise it when value ≥ the channel's cost

Three parts, and the third is where a single gate earns its keep:

* **Reducibility.** Will more riding settle this on its own? Crucially this is
  *not* a universal penalty. An open question and a hypothesis are the mechanism
  for waiting: being answerable by data is why they exist. It is only a cost when
  the channel spends someone's effort — asking the athlete, or booking a training
  slot for an experiment. "If more riding would settle it, it is an open question
  or an experiment, not a question for the athlete" is the inquiry prompt's own
  wording, and this is that sentence made executable.
* **Decision relevance.** Would a different answer change a plan or a
  recommendation? Scored against the athlete's own utility weights (#564/#566),
  so "worth resolving" means worth resolving *for this athlete*. An uncertainty
  that moves nothing is not worth a token.
* **Cost, per channel.** Asking the athlete spends attention, the scarcest
  resource here. An experiment spends a training slot. A hypothesis spends
  prompt budget and some of the coach's coherence. The same expected information
  is worth acting on at one price and not at another — which is precisely what
  four independent entry rules could never express.

Pure module: rules in, decision out. The caller records it.

A candid limit: the inputs are free text, so the rules read them by keyword.
That is a coarse instrument, and it is chosen over an LLM judgement on purpose —
this gate runs on every candidate from every channel, has to be reviewable as a
whole, and a gate whose verdicts cannot be reproduced is not a gate. Where a
rule cannot tell, it says so and the neutral prior applies rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from services.motivation_model import DEFAULT_WEIGHTS, MOTIVATION_COMPONENTS

CHANNEL_HYPOTHESIS = "hypothesis"
CHANNEL_OPEN_QUESTION = "open_question"
CHANNEL_EXPERIMENT = "experiment"
CHANNEL_INQUIRY = "inquiry"
CHANNEL_SESSION_QUESTION = "session_question"

ASPECT_REDUCIBILITY = "reducibility"
ASPECT_RELEVANCE = "relevance"


@dataclass(frozen=True, slots=True)
class ValueRule:
    """One written-down argument about whether an uncertainty is worth resolving.

    Declared as data for the same reason :class:`~services.motivation_inference.
    WeightRule` is: an if-chain would put the answer to "why was this not asked?"
    only in the reader's head. A firing is recorded by name.

    ``aspect`` says which half of the score the rule argues about.
    ``contribution`` is what it argues for at full strength — for reducibility,
    how much of this the data will settle on its own; for relevance, which
    utility components a different answer would move.
    """

    name: str
    # In words a person would recognise, shown next to the decision.
    signal: str
    aspect: str
    patterns: tuple[str, ...]
    reducibility: float = 0.0
    components: tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        return any(re.search(pattern, text) for pattern in self.patterns)


# --- The rule, in full ------------------------------------------------------
#
# Lifted from the inquiry prompt's STEP 1/STEP 2, which is the only place this
# reasoning was ever written down. STEP 1 named what the next few weeks of riding
# answer for free; STEP 2 named what lives in the athlete's head, body or
# calendar and no volume of data will ever reveal.

REDUCIBILITY_RULES: tuple[ValueRule, ...] = (
    ValueRule(
        name="fitness_trend",
        signal="the training stream shows fitness and threshold moving on its own",
        aspect=ASPECT_REDUCIBILITY,
        patterns=(r"\bftp\b", r"threshold", r"fitness", r"\bctl\b", r"\bvo2", r"power curve"),
        reducibility=0.85,
    ),
    ValueRule(
        name="load_response",
        signal="how they respond to load shows up in the next few weeks of rides",
        aspect=ASPECT_REDUCIBILITY,
        patterns=(r"fatigue", r"recover", r"\btsb\b", r"\batl\b", r"training load", r"adapt"),
        reducibility=0.7,
    ),
    ValueRule(
        name="execution_pattern",
        signal="pacing, durability and which sessions get completed are recorded already",
        aspect=ASPECT_REDUCIBILITY,
        patterns=(r"pacing", r"durabilit", r"complet", r"adheren", r"consisten", r"cadence"),
        reducibility=0.7,
    ),
    ValueRule(
        name="environmental_tolerance",
        signal="weather and terrain tolerance accumulate from the rides themselves",
        aspect=ASPECT_REDUCIBILITY,
        patterns=(r"weather", r"heat", r"cold", r"wind", r"humid"),
        reducibility=0.6,
    ),
    # The other side of the same rule: what no amount of riding will ever say.
    # A negative reducibility pulls the score down, which is what makes these the
    # things worth spending the athlete's attention on.
    ValueRule(
        name="lives_in_the_athlete",
        signal="only the athlete knows this — it is not in their power file",
        aspect=ASPECT_REDUCIBILITY,
        patterns=(
            # Word boundaries are not decoration here: a bare "ill" matches
            # "will" and a bare "work" matches "workout", which would file half
            # the training vocabulary as something only the athlete knows.
            r"\bwhy\b", r"what happened", r"cut .{0,12}short", r"skipp",
            r"pain", r"niggle", r"injur", r"\bill\b", r"sick",
            r"sleep", r"stress", r"motivat", r"enjoy", r"dread", r"\bwant",
            r"\bgoal", r"schedule", r"calendar", r"\bwork\b", r"family",
            r"travel",
        ),
        reducibility=-0.6,
    ),
    ValueRule(
        name="equipment_change",
        signal="a swapped meter or a new bike changes what the numbers mean, and only they know",
        aspect=ASPECT_REDUCIBILITY,
        patterns=(r"power meter", r"new bike", r"swapp", r"calibrat", r"equipment"),
        reducibility=-0.5,
    ),
)

RELEVANCE_RULES: tuple[ValueRule, ...] = (
    ValueRule(
        name="moves_the_plan",
        signal="a different answer would change what the next sessions look like",
        aspect=ASPECT_RELEVANCE,
        patterns=(
            r"interval", r"threshold", r"volume", r"intensit", r"rest day",
            r"recover", r"block", r"session", r"workout", r"\bplan\b",
        ),
        components=("adaptation", "consistency"),
    ),
    ValueRule(
        name="moves_risk",
        signal="a different answer would change how hard it is safe to push",
        aspect=ASPECT_RELEVANCE,
        patterns=(
            r"injur", r"pain", r"niggle", r"\bill\b", r"sick",
            r"overreach", r"overtrain", r"sleep", r"stress",
        ),
        components=("health",),
    ),
    ValueRule(
        name="moves_what_they_will_actually_do",
        signal="a different answer would change what they are willing to ride",
        aspect=ASPECT_RELEVANCE,
        patterns=(r"enjoy", r"dread", r"prefer", r"motivat", r"want", r"skipp", r"adheren"),
        components=("enjoyment", "consistency"),
    ),
    ValueRule(
        name="moves_race_preparation",
        signal="a different answer would change how they are prepared for a race",
        aspect=ASPECT_RELEVANCE,
        patterns=(r"\brace", r"\bevent\b", r"\bgoal", r"\bpeak", r"taper", r"compet"),
        components=("race_performance",),
    ),
)

VALUE_RULES: tuple[ValueRule, ...] = REDUCIBILITY_RULES + RELEVANCE_RULES

# What raising one costs, expressed on the same 0..1 scale as the value.
#
# The ordering is the whole point of having one gate. Asking the athlete is the
# most expensive thing the coach can do: attention is the only resource here that
# does not replenish, and an inquiry that misses buys a rephrasing and then
# silence. An experiment costs a training slot — real, but recoverable. An open
# question costs a line the athlete may read. A hypothesis costs prompt budget
# and a little of the coach's coherence, which is why it is cheapest and also why
# 74 of them accumulated before anyone noticed.
CHANNEL_COST: dict[str, float] = {
    CHANNEL_INQUIRY: 0.45,
    CHANNEL_EXPERIMENT: 0.35,
    CHANNEL_SESSION_QUESTION: 0.30,
    CHANNEL_OPEN_QUESTION: 0.25,
    CHANNEL_HYPOTHESIS: 0.15,
}

# How much each channel is penalised for an uncertainty the data will answer on
# its own. An open question and a hypothesis are *how the coach waits* for that
# data, so for them it is not a cost at all — penalising it would refuse to write
# down the very things the record exists to hold. An experiment is a way of
# producing the data, so it pays most of the discount: if plain riding settles
# it, do not spend a training slot. Asking the athlete pays it in full, which is
# the rule the inquiry prompt states in prose.
CHANNEL_DATA_DISCOUNT: dict[str, float] = {
    CHANNEL_INQUIRY: 1.0,
    CHANNEL_SESSION_QUESTION: 1.0,
    CHANNEL_EXPERIMENT: 0.8,
    CHANNEL_OPEN_QUESTION: 0.0,
    CHANNEL_HYPOTHESIS: 0.0,
}

# What an uncertainty is worth when no relevance rule recognises it. Set above
# every channel cost on purpose: a rule set that cannot read a statement must not
# thereby veto it, or the gate quietly becomes "only topics we wrote patterns
# for". This gate declines what it has a positive reason to decline; silence is
# not a reason.
NEUTRAL_RELEVANCE = 0.5

# Recognising that only the athlete can answer something is itself decision
# relevance, not merely the absence of a penalty — for a channel whose whole
# purpose is asking, it is the strongest argument there is. Without this, naming
# the topic could score *lower* than not recognising it at all, which would
# punish the rules for working.
EXCLUSIVE_KNOWLEDGE_BONUS = 0.3

# The value is a sum of floats compared against a threshold, so a decision that
# lands exactly on the bar must not fall off it for a rounding error 17 digits
# down: 0.15 + 0.3 is not 0.45 in binary floating point, and "is that niggle
# still there?" is not a question to lose that way.
_VALUE_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class RuleFiring:
    """A rule that recognised this uncertainty, and what it argued."""

    rule: ValueRule
    matched: str

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "rule": self.rule.name,
            "signal": self.rule.signal,
            "aspect": self.rule.aspect,
        }
        if self.rule.aspect == ASPECT_REDUCIBILITY:
            record["reducibility"] = round(self.rule.reducibility, 4)
        else:
            record["components"] = list(self.rule.components)
        return record


@dataclass(frozen=True, slots=True)
class ValueDecision:
    """Whether to raise this uncertainty, and the full argument for the verdict."""

    channel: str
    should_raise: bool
    value: float
    threshold: float
    relevance: float
    reducibility: float
    reason: str
    firings: list[RuleFiring] = field(default_factory=list)

    def as_record(self) -> dict[str, Any]:
        """The shape stored in the audit trail (#581's events table)."""
        return {
            "channel": self.channel,
            "raised": self.should_raise,
            "value": round(self.value, 4),
            "threshold": round(self.threshold, 4),
            "relevance": round(self.relevance, 4),
            "reducibility": round(self.reducibility, 4),
            "rules": [firing.as_record() for firing in self.firings],
        }


def _normalised(text: str) -> str:
    return " ".join((text or "").casefold().split())


def resolve_weights(weights: Mapping[str, Any] | None) -> dict[str, float]:
    """The athlete's utility weights, or the cold-start vector.

    Kept local rather than reaching for ``normalize_weights`` so this module
    stays pure and importable from anywhere: a missing or partial vector simply
    falls back per component.
    """
    source = weights if isinstance(weights, Mapping) else {}
    resolved: dict[str, float] = {}
    for component in MOTIVATION_COMPONENTS:
        try:
            value = float(source[component])  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            value = DEFAULT_WEIGHTS[component]
        resolved[component] = max(0.0, value)
    return resolved


def evaluate(
    *,
    channel: str,
    text: str,
    weights: Mapping[str, Any] | None = None,
    threshold: float | None = None,
) -> ValueDecision:
    """Decide whether this uncertainty is worth raising on this channel.

    ``text`` is the uncertainty in the coach's own words — the hypothesis
    statement, the question, the experiment's protocol. ``weights`` is the
    athlete's utility vector; without one the cold-start defaults apply, so a
    brand-new athlete is not gated by a model nobody has built yet.
    """
    haystack = _normalised(text)
    cost = threshold if threshold is not None else CHANNEL_COST.get(channel, 0.3)
    resolved = resolve_weights(weights)

    firings: list[RuleFiring] = []

    # --- Reducibility: will more riding settle this without anyone asking? ---
    data_share = 0.0
    only_the_athlete = False
    for rule in REDUCIBILITY_RULES:
        if not rule.matches(haystack):
            continue
        firings.append(RuleFiring(rule=rule, matched=rule.name))
        if rule.reducibility >= 0:
            # The strongest claim wins rather than summing: two ways of saying
            # "the data will tell you" is not twice as answerable.
            data_share = max(data_share, rule.reducibility)
        else:
            only_the_athlete = True
    data_share = max(0.0, min(1.0, data_share))

    # --- Relevance: what would a different answer actually move? ---
    moved: set[str] = set()
    for rule in RELEVANCE_RULES:
        if not rule.matches(haystack):
            continue
        firings.append(RuleFiring(rule=rule, matched=rule.name))
        moved.update(rule.components)

    if moved:
        total = sum(resolved.values()) or 1.0
        relevance = sum(resolved.get(c, 0.0) for c in moved) / total
    else:
        relevance = NEUTRAL_RELEVANCE
    if only_the_athlete:
        relevance = min(1.0, relevance + EXCLUSIVE_KNOWLEDGE_BONUS)

    discount = CHANNEL_DATA_DISCOUNT.get(channel, 1.0)
    reducibility = data_share * discount
    value = relevance * (1.0 - reducibility)
    should_raise = value + _VALUE_EPSILON >= cost

    return ValueDecision(
        channel=channel,
        should_raise=should_raise,
        value=value,
        threshold=cost,
        relevance=relevance,
        reducibility=reducibility,
        reason=_reason(
            should_raise=should_raise,
            value=value,
            cost=cost,
            relevance=relevance,
            reducibility=reducibility,
            moved=moved,
        ),
        firings=firings,
    )


def _reason(
    *,
    should_raise: bool,
    value: float,
    cost: float,
    relevance: float,
    reducibility: float,
    moved: set[str],
) -> str:
    """Why, in a sentence someone can read six months later."""
    moves = ", ".join(sorted(moved)) if moved else "nothing the rules recognise"
    if should_raise:
        return (
            f"Worth it: value {value:.2f} clears the {cost:.2f} cost of this "
            f"channel (relevance {relevance:.2f} to {moves}, "
            f"{reducibility:.0%} of it answerable from data alone)."
        )
    if reducibility >= 0.5:
        return (
            f"Not worth it: {reducibility:.0%} of this is what the next few weeks "
            f"of riding will show anyway, leaving value {value:.2f} against a "
            f"{cost:.2f} cost."
        )
    return (
        f"Not worth it: value {value:.2f} falls short of the {cost:.2f} cost of "
        f"this channel (relevance {relevance:.2f} to {moves})."
    )
