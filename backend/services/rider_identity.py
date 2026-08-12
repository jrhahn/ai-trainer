"""Who this athlete is as a rider, for the moments the coach recommends less (#597).

The coach explains a recovery decision almost entirely as risk management: keep
fatigue low, protect the adaptation, stay fresh. All true, and it reads as the
absence of training rather than as a choice — worst of all for an athlete whose
goal is not a bigger number but still enjoying the last descent after four hours.

#565 already told the coach to explain recommendations in the athlete's own
objective. It does not win here, because the rest rules it competes with are
entirely physiological and there is nothing in the prompt to explain *this*
athlete's easy day with. This module supplies that, from three stores that
already exist:

* **Riding style**, from the performance model (#476) — read only in the
  direction the evidence supports; see :func:`riding_style`.
* **Behavioural patterns**, from the ``rider_identity`` observations #593
  accumulates out of what the athlete says, at the confidence where the coach is
  allowed to rely on them.
* **The objective**, which the motivation model (#562) already puts in the
  prompt and which this does not duplicate.

The substantive new thing is :data:`PATTERN_RULES`: what each pattern means *on
an easy day*. "Rides harder with a target ahead" is a fact about the athlete;
"the recovery ride is not at risk in the first twenty minutes, it is at risk the
moment a target appears" is the sentence that makes the recommendation theirs
instead of generic. Declared as data, in the idiom of #566 and #582, so the whole
mapping can be read at once.

What this does not do is decide anything. Whether a given reply recommends less
than the athlete could do is knowable only once the coach has decided, which is
inside the model. So the split is the same as #593's: a static rule saying how to
frame a downgrade, and this volatile section supplying what to frame it with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

import crud
from services import workout_curiosity

# The bar at which the coach may rely on an observation rather than weigh it —
# the same one `athlete_memory_facts_section` uses. Under #593's accrual (0.35 on
# a first sighting, +0.2 each time after) that means a second, independent
# telling, which is also `ATHLETE_MEMORY_MIN_EVIDENCE`. Framing a decision around
# someone's character on one remark is the failure mode this has to avoid.
PATTERN_MIN_CONFIDENCE = 0.5

# Below this the performance attribute is a placeholder, not a reading.
STYLE_MIN_CONFIDENCE = 0.35

_DURABLE_SCORES = {"high", "above_average"}


@dataclass(frozen=True, slots=True)
class Trait:
    """One durable thing about how this athlete rides."""

    key: str
    label: str
    # What it was read off, in words the athlete could check.
    basis: str
    confidence: float


@dataclass(frozen=True, slots=True)
class Pattern:
    """A behavioural pattern, and what it means for a session meant to be easy."""

    key: str
    label: str
    # The sentence that makes a downgrade specific to them.
    on_an_easy_day: str
    confidence: float
    evidence: str


@dataclass(frozen=True, slots=True)
class PatternRule:
    """What one observed pattern implies about an easy session.

    ``signal`` is the name of the :mod:`services.workout_curiosity` rule that
    records it, so the two cannot drift: the observation text is a constant
    shared by the write and this lookup, and a guardrail test asserts every rule
    here still names a real one.
    """

    signal: str
    label: str
    on_an_easy_day: str


# --- The mapping, in full ---------------------------------------------------
#
# Each entry answers one question: if this is true of the athlete, what does it
# change about a session that is supposed to be easy? None of them is a
# criticism, and the framing rule says so explicitly — a pattern is a fact about
# how someone rides, not a discipline problem.

PATTERN_RULES: tuple[PatternRule, ...] = (
    PatternRule(
        signal="chased_someone_down",
        label="a target ahead lifts the effort",
        on_an_easy_day=(
            "the easy ride is not at risk in the first twenty minutes — it is at "
            "risk the moment someone appears up the road and the ride quietly "
            "becomes a pursuit"
        ),
    ),
    PatternRule(
        signal="raced_a_mark",
        label="segments and times pull real efforts out of them",
        on_an_easy_day=(
            "a route with a segment on it is an intensity decision already made; "
            "choosing where to ride matters more than choosing a power cap"
        ),
    ),
    PatternRule(
        signal="rode_with_others",
        label="company changes the effort, not just the enjoyment",
        on_an_easy_day=(
            "who they ride with sets the intensity more reliably than the plan "
            "does, so an easy day is a question of company before it is a "
            "question of watts"
        ),
    ),
    PatternRule(
        signal="rode_by_feel",
        label="the numbers stop governing once they are absorbed",
        on_an_easy_day=(
            "a power ceiling only holds while they are still looking at it, so "
            "the limit has to be built into the session — terrain, or a different "
            "activity — rather than left as an intention"
        ),
    ),
    PatternRule(
        signal="paced_on_purpose",
        label="pacing is a decision they make and can explain",
        on_an_easy_day=(
            "they can be given the reason instead of the number and will hold it, "
            "so an easy day is worth explaining rather than prescribing"
        ),
    ),
    PatternRule(
        signal="named_what_they_love",
        label="they ride for something they can name",
        on_an_easy_day=(
            "an easy day lands far better as protecting the thing they named than "
            "as a reduction in training"
        ),
    ),
    PatternRule(
        signal="coming_back_from_something",
        label="they report illness and its aftermath themselves",
        on_an_easy_day=(
            "their own account of how the week went is available and is better "
            "evidence than the load numbers for how much to hold back"
        ),
    ),
    PatternRule(
        signal="slept_or_stressed",
        label="session quality tracks sleep and life stress, and they notice",
        on_an_easy_day=(
            "the easy day can be placed where the week is worst rather than where "
            "the plan happens to put it"
        ),
    ),
)

_RULES_BY_SIGNAL: dict[str, PatternRule] = {rule.signal: rule for rule in PATTERN_RULES}


def _observation_to_signal() -> dict[str, str]:
    """The stored fact text back to the rule that wrote it.

    Exact rather than fuzzy: both sides are the same constant on
    :class:`workout_curiosity.SignalRule`, so there is nothing to keep in sync.
    """
    return {rule.observation: rule.name for rule in workout_curiosity.SIGNAL_RULES}


# --- Riding style -----------------------------------------------------------


def riding_style(performance_model: Mapping[str, Any] | None) -> Trait | None:
    """The diesel read, and deliberately only that direction.

    Durability and aerobic endurance are measured against things the ride file
    genuinely contains — power held in the second half of a long ride, aerobic
    decoupling — so "rides best under sustained pressure" is a claim the data
    supports.

    The opposite claim is not. ``_infer_anaerobic_capacity`` caps its confidence
    at 0.3 and says why in its own ``missing_information``: nothing in a ride
    file confirms a one-minute effort was maximal. So a high number there means
    "they once rode hard for a minute", which is not evidence of being a puncher,
    and calling someone one on that basis would be the coach inventing an
    identity — the exact failure #565 guards against for objectives. This
    function therefore reads one way or returns nothing.
    """
    attributes = (performance_model or {}).get("attributes") or {}
    if not isinstance(attributes, Mapping):
        return None

    supporting: list[str] = []
    confidences: list[float] = []
    for name, human in (
        ("fatigue_resistance", "power held late in long rides"),
        ("aerobic_endurance", "low aerobic decoupling over 2.5 h+"),
    ):
        attribute = attributes.get(name)
        if not isinstance(attribute, Mapping):
            continue
        confidence = float(attribute.get("confidence") or 0.0)
        if attribute.get("score") in _DURABLE_SCORES and confidence >= STYLE_MIN_CONFIDENCE:
            supporting.append(human)
            confidences.append(confidence)

    if not supporting:
        return None
    return Trait(
        key="diesel",
        label="rides best under sustained pressure rather than in bursts",
        basis=" and ".join(supporting),
        # The weakest supporting attribute bounds the claim, rather than the
        # strongest flattering it.
        confidence=round(min(confidences), 2),
    )


# --- Patterns ---------------------------------------------------------------


def patterns_from_facts(facts: Sequence[Any]) -> list[Pattern]:
    """The behavioural patterns this athlete has actually shown, ranked."""
    by_text = _observation_to_signal()
    found: list[Pattern] = []
    for fact in facts:
        text = getattr(fact, "fact", None)
        signal = by_text.get(text or "")
        rule = _RULES_BY_SIGNAL.get(signal or "")
        if rule is None:
            continue
        status = getattr(fact, "status", "active")
        confidence = float(getattr(fact, "confidence", 0.0) or 0.0)
        if status == "rejected" or (
            status != "user_confirmed" and confidence < PATTERN_MIN_CONFIDENCE
        ):
            continue
        found.append(
            Pattern(
                key=rule.signal,
                label=rule.label,
                on_an_easy_day=rule.on_an_easy_day,
                confidence=round(confidence, 2),
                evidence=str(getattr(fact, "source_snippet", "") or "")[:200],
            )
        )
    found.sort(key=lambda pattern: (-pattern.confidence, pattern.key))
    return found


# --- The coach turn ---------------------------------------------------------


async def identity_for_prompt(
    db: AsyncSession,
    user_id: str,
    *,
    performance_model: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """What is known about this athlete as a rider, or nothing.

    Nothing is the correct answer for a new athlete: a pattern has to be seen
    twice, in separate messages, before the coach may build a sentence about
    someone's character on it.
    """
    facts = await crud.list_athlete_memory_facts(db, user_id)
    patterns = patterns_from_facts(
        [
            fact
            for fact in facts
            if getattr(fact, "category", "") == workout_curiosity.MEMORY_CATEGORY
        ]
    )
    style = riding_style(performance_model)
    if not patterns and style is None:
        return None

    payload: dict[str, Any] = {
        "patterns": [
            {
                "pattern": pattern.label,
                "onAnEasyDay": pattern.on_an_easy_day,
                "confidence": pattern.confidence,
                "theirWords": pattern.evidence,
            }
            for pattern in patterns
        ]
    }
    if style is not None:
        payload["style"] = {
            "reading": style.label,
            "basis": style.basis,
            "confidence": style.confidence,
        }
    return payload
