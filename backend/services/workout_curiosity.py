"""What is worth being curious about in what the athlete just said (#593).

A post-workout message is usually two things at once: numbers, and a sentence
about what happened. The coach reliably answered the numbers and then asked how
the legs felt — correct, and worth almost nothing, because it spent the single
follow-up question the coach is allowed on the least informative thing in the
message. Everything that made the session *this athlete's* session — the rider
they chased, the part they loved, the pacing they chose, the week they spent
ill — went unremarked.

This module finds the interesting part. It is the same shape as the other
deterministic captures that run on every coach turn (#495, #563): rules as data,
no tokens, testable, and every finding carries the athlete's own words so the
belief it feeds can be traced back to the sentence that produced it.

Three steps, and the second and third are borrowed rather than invented:

1. **Extract.** :data:`SIGNAL_RULES` reads the five families the issue names —
   physiology, behaviour, emotion, the social situation, and health context.
   Physiology is read from the numbers rather than from keywords, because
   "power rose across all three intervals" is a fact about the series and no
   pattern can see it.
2. **Rank by novelty.** A trait the athlete's model already records is not
   interesting to discover again. Novelty is exact rather than fuzzy: each rule
   names the durable observation it argues for, and that observation *is* the
   key in ``athlete_memory_facts``.
3. **Decide whether to ask at all.** Through :mod:`services.uncertainty_value`,
   the gate #582 built, on its own channel with its own cost — not a second bar
   with its own opinions. That reuse does the most important work here for free:
   the gate's whole premise is that an uncertainty the data will settle on its
   own is not worth someone's attention, so a physiological observation scores
   badly *as a question* and well as an observation. Which is exactly the issue's
   complaint, stated as arithmetic: the rising power profile belongs in the
   analysis, and the question belongs on the rider up the road.

The restraint matters as much as the curiosity. The stated non-goal is not
becoming chatty, and the gate is what enforces it: on a message with data and no
story, nothing fires and the coach says nothing extra.

What this deliberately does not do is write the question. The rules name a
*topic* to be curious about, never a sentence — a stored question string would be
read out verbatim and every athlete would get the same words, which is the
failure this issue is about, one level up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

import crud
from services import uncertainty_value

# The five families the issue names.
KIND_PHYSIOLOGY = "physiology"
KIND_BEHAVIOUR = "behaviour"
KIND_EMOTION = "emotion"
KIND_SOCIAL = "social"
KIND_HEALTH = "health"

# Where the observations land. Its own category so the rider-identity picture is
# separable from measured facts and from behaviour inferred off the ride stream.
MEMORY_CATEGORY = "rider_identity"

SNIPPET_MAX_LEN = 200


@dataclass(frozen=True, slots=True)
class SignalRule:
    """One thing worth noticing in a post-workout message.

    ``signal`` is what the coach noticed, in words the athlete would recognise.
    ``tells_us`` is the uncertainty it would resolve — this is the text the value
    gate reads, so it is written the way a coach would state the open question,
    not as a label. ``curious_about`` is the *topic* of the follow-up, never the
    follow-up itself.

    ``observation`` is the durable claim repeated sightings would support, and
    doubles as the novelty key: an athlete whose model already holds it has
    nothing left to reveal here.
    """

    name: str
    kind: str
    signal: str
    patterns: tuple[str, ...]
    tells_us: str
    curious_about: str
    observation: str
    # Could the thing this names have *caused* the effort to change inside this
    # session? A rider up the road could; a preference for road cycling could
    # not. This is the issue's "explanatory of performance changes" criterion,
    # and it is what separates two topics the value gate rates identically —
    # correctly, since both are worth asking. Which to ask *now* is the one the
    # numbers are waiting on an explanation for.
    explains_the_numbers: bool = False

    def search(self, text: str) -> re.Match[str] | None:
        for pattern in self.patterns:
            match = re.search(pattern, text)
            if match is not None:
                return match
        return None


# --- The rules, in full -----------------------------------------------------
#
# Patterns carry German alongside English for the same reason the motivation and
# weather captures do (#563, #495): the coach answers in the athlete's language,
# and a capture that only understands English quietly stops working for half the
# messages it was built for.
#
# Word boundaries are not decoration. A bare "ill" matches "will" and a bare
# "cold" matches a description of the weather — #582 learned both the hard way.

SOCIAL_RULES: tuple[SignalRule, ...] = (
    SignalRule(
        name="chased_someone_down",
        kind=KIND_SOCIAL,
        signal="there was a rider up the road and the athlete went after them",
        patterns=(
            r"\bchas(?:e|ed|ing)\b",
            r"\breel(?:ed|ing)?\s+(?:\w+\s+){0,2}in\b",
            r"\bcaught\s+(?:him|her|them|up)\b",
            r"\bhunt(?:ed|ing)\s+(?:him|her|them|down)\b",
            r"\bpull(?:ed)?\s+(?:him|her|them)\s+back\b",
            r"\beingeholt\b",
            r"\brangeholt\b",
            r"\baufgeschlossen\b",
            r"\bhinterher(?:gejagt)?\b",
        ),
        tells_us=(
            "whether a target up the road is what lifts this athlete's effort, or "
            "whether they pace off their own numbers — which changes what sessions "
            "they will actually want to ride"
        ),
        curious_about=(
            "when they first noticed the gap closing, and whether the power went up "
            "deliberately or simply followed the chase"
        ),
        observation=(
            "Rides harder with a target ahead — an external mark lifts the effort "
            "more than a prescribed number does"
        ),
        explains_the_numbers=True,
    ),
    SignalRule(
        name="rode_with_others",
        kind=KIND_SOCIAL,
        signal="the session happened around other riders rather than alone",
        patterns=(
            r"\bgroup\s+ride\b",
            r"\bpeloton\b",
            r"\bbunch\b",
            r"\bgot\s+dropped\b",
            r"\bon\s+(?:his|her|their|the)\s+wheel\b",
            r"\bgruppe\b",
            r"\bpulk\b",
            r"\bwindschatten\b",
            r"\babgeh(?:ä|ae)ngt\b",
        ),
        tells_us=(
            "whether this athlete trains better in company, which decides whether "
            "a hard session should be planned around a group ride they enjoy"
        ),
        curious_about=(
            "whether riding in company changed the effort they were willing to hold, "
            "and whether that is why they went out"
        ),
        observation=(
            "Trains differently in company than alone — the group changes the effort, "
            "not just the enjoyment"
        ),
        explains_the_numbers=True,
    ),
    SignalRule(
        name="raced_a_mark",
        kind=KIND_SOCIAL,
        signal="the effort was aimed at beating something — a segment, a time, a rival",
        patterns=(
            r"\bsegment\b",
            r"\bkom\b",
            r"\bpersonal\s+best\b",
            r"\bpb\b",
            r"\bsprint(?:ed)?\s+(?:for|against)\b",
            r"\bbestzeit\b",
            r"\bpers(?:ö|oe)nliche\s+bestleistung\b",
        ),
        tells_us=(
            "whether competing against a mark is what motivates this athlete, which "
            "changes how a race build should be framed for them"
        ),
        curious_about=(
            "whether they set out to beat it or decided mid-ride, and how much that "
            "decision cost them afterwards"
        ),
        observation=(
            "Motivated by beating a mark — segments, times and rivals pull real "
            "efforts out of them"
        ),
        explains_the_numbers=True,
    ),
)

EMOTION_RULES: tuple[SignalRule, ...] = (
    SignalRule(
        name="named_what_they_love",
        kind=KIND_EMOTION,
        signal="the athlete said what they enjoy about riding, unprompted",
        patterns=(
            r"\bthat.?s\s+(?:the\s+)?(?:part|why)\s+i\s+lov",
            r"\bi\s+lov(?:e|ed)\b",
            r"\bloved\s+(?:that|it|this)\b",
            r"\benjoy(?:ed)?\b",
            r"\bbest\s+part\b",
            r"\bso\s+much\s+fun\b",
            r"\bdaf(?:ü|ue)r\s+liebe\s+ich\b",
            r"\bmacht\s+(?:mir\s+)?spa(?:ß|ss)\b",
            r"\bhat\s+spa(?:ß|ss)\s+gemacht\b",
            r"\bgenossen\b",
        ),
        tells_us=(
            "what this athlete actually enjoys about riding, which decides which "
            "sessions they will keep doing and which ones they will quietly skip"
        ),
        curious_about=(
            "what specifically about that moment they enjoyed, and whether the "
            "sessions they like best all share it"
        ),
        observation=(
            "Names enjoyment as a reason for riding — sessions they like are the "
            "ones that get completed"
        ),
    ),
    SignalRule(
        name="felt_strong",
        kind=KIND_EMOTION,
        signal="the athlete said the body felt good, in their own words",
        patterns=(
            r"\bfelt\s+(?:much\s+)?(?:strong|great|good|amazing|fresh|easy)\b",
            r"\bfelt\s+much\s+better\b",
            r"\bflying\b",
            r"\blegs\s+were\s+(?:there|good|great)\b",
            r"\bf(?:ü|ue)hlte?\s+mich\s+(?:stark|gut|frisch|besser)\b",
            r"\bging\s+(?:richtig\s+)?gut\b",
            r"\blief\s+(?:richtig\s+)?gut\b",
        ),
        tells_us=(
            "what a good day is made of for this athlete — the sleep, the fuelling "
            "or the week behind it — so a good day can be arranged rather than waited for"
        ),
        curious_about=(
            "what was different about the run-up to this session compared with the "
            "one before it"
        ),
        observation=(
            "Reports how the body felt without being asked — self-report is a usable "
            "signal for this athlete"
        ),
    ),
    SignalRule(
        name="struggled",
        kind=KIND_EMOTION,
        signal="the session cost the athlete something they wanted to mention",
        patterns=(
            r"\bfrustrat",
            r"\bannoy",
            r"\bhated\b",
            r"\bmiserable\b",
            r"\bsuffer(?:ed|ing)\b",
            r"\bstruggl",
            r"\bdread",
            r"\bfrust",
            r"\bgenervt\b",
            r"\bgequ(?:ä|ae)lt\b",
            r"\bgelitten\b",
            r"\bz(?:ä|ae)h\b",
        ),
        tells_us=(
            "whether this was a bad day or a session this athlete dislikes, which "
            "decides whether to repeat it or replace it"
        ),
        curious_about=(
            "which part of it was the hard part — the effort, the monotony, or the "
            "day it landed on"
        ),
        observation=(
            "Tells the coach when a session was unpleasant — worth reading as data "
            "about the session, not only about the day"
        ),
    ),
)

BEHAVIOUR_RULES: tuple[SignalRule, ...] = (
    SignalRule(
        name="paced_on_purpose",
        kind=KIND_BEHAVIOUR,
        signal="the athlete made a pacing decision and knows they made it",
        patterns=(
            r"\bheld\s+back\b",
            r"\bsaved\s+(?:something|myself|a\s+bit)\b",
            r"\beased?\s+off\b",
            r"\bbacked\s+off\b",
            r"\bwent\s+(?:out\s+)?harder\b",
            r"\bpushed\s+(?:on|harder)\b",
            r"\bupped\s+(?:the\s+)?(?:pace|power|effort)\b",
            r"\bfull\s+gas\b",
            r"\bzur(?:ü|ue)ckgehalten\b",
            r"\bkraft\s+gespart\b",
            r"\brausgenommen\b",
            r"\bnachgelegt\b",
            r"\bdruck\s+gemacht\b",
            r"\bvollgas\b",
        ),
        tells_us=(
            "whether they chose that pacing deliberately or it happened to them — why "
            "they rode it that way, which the power file never records, and which "
            "decides whether the next interval session prescribes a target or leaves "
            "the pacing to them"
        ),
        curious_about=(
            "whether that was the plan before they started or a decision made in the "
            "moment"
        ),
        observation=(
            "Paces deliberately and can say why — pacing intent is available to ask "
            "about rather than inferred"
        ),
        explains_the_numbers=True,
    ),
    SignalRule(
        name="rode_by_feel",
        kind=KIND_BEHAVIOUR,
        signal="the athlete stopped riding to the numbers at some point",
        patterns=(
            r"\bstopped\s+looking\b",
            r"\bdidn.?t\s+look\s+at\s+(?:the\s+)?(?:power|numbers|watts)\b",
            r"\bby\s+feel\b",
            r"\bforgot\s+(?:about\s+)?the\s+(?:power|numbers|watts)\b",
            r"\bnach\s+gef(?:ü|ue)hl\b",
            r"\bnicht\s+mehr\s+auf\s+(?:die\s+)?(?:watt|zahlen)\s+geschaut\b",
        ),
        tells_us=(
            "whether this athlete rides better when the numbers are hidden, which is "
            "a session design choice and not a personality note"
        ),
        curious_about=(
            "what pulled their attention off the numbers, and whether the effort went "
            "up or down once it did"
        ),
        observation=(
            "Rides by feel when engaged — the power target stops being the thing "
            "driving the effort"
        ),
        explains_the_numbers=True,
    ),
    SignalRule(
        name="changed_the_session",
        kind=KIND_BEHAVIOUR,
        signal="what was ridden was not what was planned, and the athlete said so",
        patterns=(
            r"\bcut\s+(?:it\s+)?(?:\w+\s+){0,2}short\b",
            r"\bbailed\b",
            r"\bskipped\s+(?:the\s+)?(?:last|third|second|final)\b",
            r"\bswapped\s+(?:it\s+)?for\b",
            r"\bchanged\s+(?:the\s+)?(?:route|plan|session)\b",
            r"\babgebrochen\b",
            r"\bfr(?:ü|ue)her\s+beendet\b",
            r"\bausgelassen\b",
            r"\bumgeplant\b",
        ),
        tells_us=(
            "why the session changed — the answer decides whether the plan was wrong, "
            "the day was wrong, or the athlete was protecting something"
        ),
        curious_about=(
            "what made the call for them, and whether they would make the same call "
            "again"
        ),
        observation=(
            "Adjusts a session mid-ride rather than abandoning or forcing it — the "
            "plan is a starting point for them"
        ),
        explains_the_numbers=True,
    ),
)

HEALTH_RULES: tuple[SignalRule, ...] = (
    SignalRule(
        name="coming_back_from_something",
        kind=KIND_HEALTH,
        signal="a recent illness or disturbance is the backdrop to this session",
        patterns=(
            r"\bstomach\b",
            r"\bsick\b",
            r"\bunwell\b",
            r"\bfever\b",
            r"\bhead\s+cold\b",
            r"\bflu\b",
            r"\bnot\s+100\s*%\b",
            r"\bwasn.?t\s+100\b",
            r"\bmagen\b",
            r"\bkrank\b",
            r"\berk(?:ä|ae)ltet\b",
            r"\bfieber\b",
            r"\bmagen.?darm\b",
        ),
        tells_us=(
            "how this athlete's illness recovery actually goes, which decides how "
            "fast to reintroduce intensity the next time and lowers the injury risk "
            "of guessing"
        ),
        curious_about=(
            "how many days it took before the legs felt normal again, and whether "
            "that matches the last time"
        ),
        observation=(
            "Comes back from illness quickly and says so — recovery timelines for "
            "this athlete can be built from their own reports"
        ),
        explains_the_numbers=True,
    ),
    SignalRule(
        name="slept_or_stressed",
        kind=KIND_HEALTH,
        signal="sleep or life stress was mentioned alongside the session",
        patterns=(
            r"\bslept\b",
            r"\bsleep\b",
            r"\bstress",
            r"\bexhaust",
            r"\bknackered\b",
            r"\bgeschlafen\b",
            r"\bschlaf\b",
            r"\bm(?:ü|ue)de\b",
            r"\bkaputt\b",
        ),
        tells_us=(
            "how much sleep and stress move this athlete's session quality, which "
            "decides whether a hard day should move when the week goes wrong"
        ),
        curious_about=(
            "whether they can usually tell before starting how the session will go, "
            "and what tells them"
        ),
        observation=(
            "Session quality tracks sleep and life stress for this athlete, and they "
            "notice it themselves"
        ),
        explains_the_numbers=True,
    ),
    SignalRule(
        name="fuelling",
        kind=KIND_HEALTH,
        signal="fuelling came up — what went in, or what ran out",
        patterns=(
            r"\bbonk",
            r"\bcramp",
            r"\bran\s+out\s+of\b",
            r"\bgels?\b",
            r"\bfuel(?:ling|ing|ed)?\b",
            r"\bhungerast\b",
            r"\bkrampf",
            r"\briegel\b",
            r"\bnichts\s+(?:mehr\s+)?(?:dabei|getrunken|gegessen)\b",
        ),
        tells_us=(
            "what this athlete actually ate and drank on a session of this length, "
            "which decides whether a fade was the legs or the food — and no amount of "
            "riding will ever record it"
        ),
        curious_about=(
            "what they took in and when, and whether that is their usual amount for "
            "a session this long"
        ),
        observation=(
            "Fuelling is a live variable for this athlete — worth checking before "
            "reading a fade as fitness"
        ),
        explains_the_numbers=True,
    ),
)

SIGNAL_RULES: tuple[SignalRule, ...] = (
    SOCIAL_RULES + EMOTION_RULES + BEHAVIOUR_RULES + HEALTH_RULES
)


# --- Physiology, read from the numbers --------------------------------------
#
# Keywords cannot see "power rose across all three intervals" — that is a fact
# about a sequence. This is the small numeric pass that can, and it is kept small
# on purpose: it exists to give the coach the *unusual* thing to name in the
# analysis, not to re-derive the ride file the backend already holds.

_WATTS = re.compile(r"(\d{2,4})\s*(?:w\b|watts?\b)", re.IGNORECASE)
_BPM = re.compile(r"(\d{2,3})\s*(?:bpm\b|hr\b|s?/?min\b)", re.IGNORECASE)
_INTERVAL_SET = re.compile(r"\b(\d{1,2})\s*[x×]\s*(\d{1,3})\b", re.IGNORECASE)

# Below this a "rise" is the noise of a power meter, not a profile.
RISING_POWER_MIN_STEPS = 2
# A final heart-rate step this many times the earlier ones is the thing worth
# naming: the cost of the last block was not in the power.
HR_LATE_JUMP_RATIO = 2.0


@dataclass(frozen=True, slots=True)
class EffortSeries:
    """The numbers the athlete typed out, in the order they typed them."""

    watts: tuple[int, ...] = ()
    bpm: tuple[int, ...] = ()

    @property
    def has_data(self) -> bool:
        return bool(self.watts or self.bpm)


def read_effort_series(message: str) -> EffortSeries:
    """Pull the power and heart-rate numbers out of a message, in order."""
    watts = tuple(
        value
        for value in (int(m.group(1)) for m in _WATTS.finditer(message or ""))
        # A plausible cycling power. Filters years and distances that happen to
        # sit next to a "w".
        if 40 <= value <= 2000
    )
    bpm = tuple(
        value
        for value in (int(m.group(1)) for m in _BPM.finditer(message or ""))
        if 40 <= value <= 230
    )
    return EffortSeries(watts=watts, bpm=bpm)


def _rising(series: Sequence[int]) -> bool:
    if len(series) < RISING_POWER_MIN_STEPS + 1:
        return False
    return all(b >= a for a, b in zip(series, series[1:])) and series[-1] > series[0]


def _late_jump(series: Sequence[int]) -> bool:
    """The last step is much bigger than the ones before it."""
    if len(series) < 3:
        return False
    steps = [b - a for a, b in zip(series, series[1:])]
    earlier = steps[:-1]
    mean_earlier = sum(earlier) / len(earlier)
    if steps[-1] <= 0 or mean_earlier <= 0:
        return False
    return steps[-1] >= HR_LATE_JUMP_RATIO * mean_earlier


def physiology_signals(message: str) -> list["Signal"]:
    """What the numbers themselves say, before anyone reads the sentence."""
    series = read_effort_series(message)
    found: list[Signal] = []

    if _rising(series.watts):
        found.append(
            _numeric_signal(
                name="rising_power_profile",
                signal=(
                    "power rose across the efforts rather than fading: "
                    + " → ".join(f"{w} W" for w in series.watts)
                ),
                snippet=" → ".join(f"{w} W" for w in series.watts),
            )
        )
    if _late_jump(series.bpm):
        found.append(
            _numeric_signal(
                name="heart_rate_paid_late",
                signal=(
                    "heart rate stayed level and then jumped on the last effort: "
                    + " → ".join(f"{b} bpm" for b in series.bpm)
                ),
                snippet=" → ".join(f"{b} bpm" for b in series.bpm),
            )
        )
    match = _INTERVAL_SET.search(message or "")
    if match and series.watts:
        found.append(
            _numeric_signal(
                name="structured_set",
                signal=f"a structured set: {match.group(0)}",
                snippet=match.group(0),
            )
        )
    return found


def _numeric_signal(*, name: str, signal: str, snippet: str) -> "Signal":
    """A physiological reading.

    It carries no ``observation`` and no ``curious_about``: this is material for
    the analysis, not for the question. That is not a rule enforced here — the
    value gate reaches the same verdict on its own, because a trend the training
    stream shows anyway is precisely what it is built to decline. It is stated
    here so a reader knows the two agree.
    """
    return Signal(
        name=name,
        kind=KIND_PHYSIOLOGY,
        signal=signal,
        snippet=snippet,
        tells_us="",
        curious_about="",
        observation="",
    )


# --- What came out of the message -------------------------------------------


@dataclass(frozen=True, slots=True)
class Signal:
    """One thing worth noticing, with the athlete's own words behind it."""

    name: str
    kind: str
    signal: str
    snippet: str
    tells_us: str
    curious_about: str
    observation: str

    @property
    def can_be_a_question(self) -> bool:
        return bool(self.curious_about and self.tells_us)


@dataclass(frozen=True, slots=True)
class Curiosity:
    """The signal the coach should lean forward about, and why it won."""

    signal: Signal
    decision: uncertainty_value.ValueDecision
    novelty: float
    noticed: list[Signal] = field(default_factory=list)

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "noticed": [
                {"kind": item.kind, "signal": item.signal} for item in self.noticed
            ],
            "curiousAbout": self.signal.curious_about,
            "whyItMatters": self.signal.tells_us,
            "theirWords": self.signal.snippet,
            "readingToOffer": self.signal.observation,
        }


def _snippet(message: str, match: re.Match[str]) -> str:
    """The athlete's own sentence around the match, so the finding is traceable."""
    text = message or ""
    start = max(0, match.start() - 80)
    end = min(len(text), match.end() + 80)
    window = " ".join(text[start:end].split())
    # Never open on half a word: the snippet is shown back to the athlete as the
    # evidence for a belief, and "r. In the last interval" reads as a bug.
    if start > 0 and " " in window:
        window = window.split(" ", 1)[1]
    return window[:SNIPPET_MAX_LEN]


def extract_signals(message: str) -> list[Signal]:
    """Everything worth noticing in one message, numbers and narrative alike."""
    text = " ".join((message or "").casefold().split())
    if not text:
        return []

    found: list[Signal] = list(physiology_signals(message))
    for rule in SIGNAL_RULES:
        match = rule.search(text)
        if match is None:
            continue
        found.append(
            Signal(
                name=rule.name,
                kind=rule.kind,
                signal=rule.signal,
                snippet=_snippet(message, match),
                tells_us=rule.tells_us,
                curious_about=rule.curious_about,
                observation=rule.observation,
            )
        )
    return found


def is_curiosity_moment(message: str) -> bool:
    """Data *and* a story — the trigger condition the issue states.

    Both halves are required. Numbers with no narrative is a data dump and the
    coach should answer it as one; a story with no numbers is a conversation the
    coach is already having. It is the combination the old behaviour handled
    worst, because the numbers were the easier half to answer and so they were
    the half that got answered.
    """
    signals = extract_signals(message)
    has_numbers = read_effort_series(message).has_data
    has_narrative = any(signal.kind != KIND_PHYSIOLOGY for signal in signals)
    return has_numbers and has_narrative


# --- Novelty ----------------------------------------------------------------

# What a signal is still worth once its observation is already on file. Not zero:
# a pattern seen twice is worth confirming once more, and an athlete who keeps
# mentioning the same thing is telling the coach it matters to them. But it falls
# fast, because the point of noticing is to learn something.
NOVELTY_BY_SIGHTINGS: tuple[float, ...] = (1.0, 0.6, 0.35)
NOVELTY_FLOOR = 0.2


def novelty(sightings: int) -> float:
    """How much is left to learn from a signal seen this many times before."""
    if sightings < 0:
        sightings = 0
    if sightings < len(NOVELTY_BY_SIGHTINGS):
        return NOVELTY_BY_SIGHTINGS[sightings]
    return NOVELTY_FLOOR


def rank(
    signals: Sequence[Signal],
    *,
    sightings: Mapping[str, int] | None = None,
    weights: Mapping[str, Any] | None = None,
) -> list[tuple[Signal, uncertainty_value.ValueDecision, float]]:
    """Order the askable signals by what asking would actually buy.

    Three judgements, kept separate on purpose:

    * The **gate** answers "is this worth the cost of asking at all" — a
      threshold, shared with every other channel and not re-litigated here.
    * **Novelty** answers "which of them still has something to reveal". It is
      an ordering and only ever an ordering: folded into the gate's value it
      would let a familiar-but-important topic drop below a bar it should clear.
    * **Explaining the numbers** breaks the ties the gate genuinely cannot. On
      the issue's own example the chase and the athlete naming what they love
      score identically, and rightly — both are worth asking. Only one of them
      is a candidate explanation for why the third interval cost what it did,
      and that is the one that is gone by tomorrow if nobody asks.
    """
    seen = sightings or {}
    numbers_want_explaining = any(
        signal.kind == KIND_PHYSIOLOGY for signal in signals
    )
    # Declaration order is the last resort, so the ordering is deterministic
    # without being alphabetical — the rules are authored most-invisible-to-the-
    # data first, which is the same principle the gate scores on.
    order = {rule.name: index for index, rule in enumerate(SIGNAL_RULES)}

    scored: list[tuple[Signal, uncertainty_value.ValueDecision, float]] = []
    for signal in signals:
        if not signal.can_be_a_question:
            continue
        decision = uncertainty_value.evaluate(
            channel=uncertainty_value.CHANNEL_CURIOSITY,
            text=signal.tells_us,
            weights=weights,
        )
        scored.append((signal, decision, novelty(seen.get(signal.observation, 0))))

    def score(item: tuple[Signal, uncertainty_value.ValueDecision, float]) -> float:
        signal, decision, how_novel = item
        explains = (
            numbers_want_explaining and _rule_by_name(signal.name).explains_the_numbers
        )
        return decision.value * how_novel * (EXPLAINS_THE_NUMBERS_BONUS if explains else 1.0)

    scored.sort(key=lambda item: (-score(item), order.get(item[0].name, len(order))))
    return scored


# How much more interesting a candidate explanation is than an equally valuable
# topic that explains nothing about this session. A tiebreaker, not a thumb on
# the scale: it only applies when the numbers actually showed something.
EXPLAINS_THE_NUMBERS_BONUS = 1.5


def _rule_by_name(name: str) -> SignalRule:
    return _RULES_BY_NAME[name]


_RULES_BY_NAME: dict[str, SignalRule] = {rule.name: rule for rule in SIGNAL_RULES}


def select_curiosity(
    message: str,
    *,
    sightings: Mapping[str, int] | None = None,
    weights: Mapping[str, Any] | None = None,
) -> Curiosity | None:
    """The one thing to be curious about, or nothing at all.

    Nothing at all is a real answer and the common one: no numbers, no story, or
    a story whose every thread the training data will answer by itself.
    """
    if not is_curiosity_moment(message):
        return None

    noticed = extract_signals(message)
    for signal, decision, how_novel in rank(
        noticed, sightings=sightings, weights=weights
    ):
        if decision.should_raise:
            return Curiosity(
                signal=signal, decision=decision, novelty=how_novel, noticed=noticed
            )
    return None


# --- The coach turn ---------------------------------------------------------


async def curiosity_for_message(
    db: AsyncSession,
    user_id: str,
    message: str,
    *,
    weights: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Find the interesting part of this message, and remember having seen it.

    Best-effort in the same sense as the captures beside it (#495, #563): a
    coach reply must not fail because a regex did. Returns the prompt section's
    payload, or ``None`` when there is nothing worth being curious about.
    """
    noticed = extract_signals(message)
    askable = [signal for signal in noticed if signal.can_be_a_question]
    if not askable:
        return None

    # Every observation is recorded, not only the winning one: the record is of
    # what the athlete said, not of which question the coach chose. That is also
    # what makes novelty work next time — a trait mentioned three sessions
    # running stops being the interesting thing to ask about.
    for signal in askable:
        await crud.observe_athlete_memory_fact(
            db,
            user_id,
            fact=signal.observation,
            kind="observation",
            category=MEMORY_CATEGORY,
            source_snippet=signal.snippet,
        )

    ranked = rank(
        noticed,
        sightings=await _sightings_for(db, user_id, askable),
        weights=weights,
    )
    signal, decision, how_novel = next(
        (item for item in ranked if item[1].should_raise), ranked[0]
    )

    # Both outcomes go in the record, #582's rule: "we noticed this and decided
    # not to ask" is the half that had nowhere to be legible before.
    await crud.record_value_decision(db, user_id, decision, statement=signal.tells_us)

    if not decision.should_raise or not is_curiosity_moment(message):
        return None
    return Curiosity(
        signal=signal, decision=decision, novelty=how_novel, noticed=noticed
    ).as_prompt_dict()


async def _sightings_for(
    db: AsyncSession, user_id: str, signals: Sequence[Signal]
) -> dict[str, int]:
    """How often each of these observations has been recorded before.

    Exact rather than fuzzy: the rule's ``observation`` is the stored fact, so
    the lookup goes through the same key the write does and novelty cannot drift
    away from what is actually on file.
    """
    return await crud.athlete_memory_observation_counts(
        db,
        user_id,
        [signal.observation for signal in signals],
        category=MEMORY_CATEGORY,
    )
