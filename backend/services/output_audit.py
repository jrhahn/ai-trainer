"""Check what the language layer said against what the model computed (ai-trainer-ops#28).

The architecture separates on purpose. ``services/athlete_model_inference`` is
deterministic and says so outright — *"An LLM is deliberately not used here —
the numbers must be reproducible and explainable"* — and the language layer
phrases what it computed. That separation is the product promise, and until this
module nothing verified it held.

Two things can go wrong at that seam, and both are decidable without an LLM:

**A number appears in the reply that was never in the context.** If the coach
writes "your FTP is 285" and the model computed 278, that is precisely the
invention the deterministic core exists to prevent, and it is the one failure an
athlete cannot catch — a plausible number reads exactly like a real one.

**The reply reads as medicine.** Diagnosis, therapy and prevention of disease
are the line this product must not cross (ai-trainer-ops#5). The prompt asks the
coach to stay off it. A prompt is a request, not a guarantee.

Report, do not gate
-------------------
Both checks are heuristics over prose, so both have a false-positive rate, and a
check that is red on day one gets switched off. :func:`report` counts findings
and logs their shape; nothing is blocked and nothing is rewritten. Turning
either into a gate is a decision to take once the reports have been read, with
the composition rules and allowlists extended by what actually turned up rather
than by what seemed likely while writing this.

The asymmetry that makes that stance safe: every rule here is biased towards
*not* reporting. An ambiguous token counts as explained if any reading of it is,
numbers are compared on absolute value, and the tolerance accepts rounding. A
missed report costs a line in a dashboard. A false one costs the credibility of
the whole check, and then the check.

Findings do not reach a log
---------------------------
A coach reply is the athlete's health data (#499), and so is every number in it.
:func:`audit` returns the detail, for tests and for a scenario suite where the
data is synthetic. :func:`report` is what production calls, and it emits counts
and check names only — not the text, not the numbers, and not even which
forbidden term matched, because *which* condition a reply named is itself
information about the athlete who received it.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left
from dataclasses import dataclass
from typing import Any, Iterator, Sequence

from . import metrics
from .analysis import power_zone_boundaries

logger = logging.getLogger(__name__)

CHECK_NUMBERS = "number_provenance"
CHECK_CLAIMS = "forbidden_claim"


@dataclass(frozen=True)
class Finding:
    """One thing wrong with one piece of coach output.

    ``detail`` is the offending token exactly as written, which is health data —
    see the module docstring for which of the two entry points may see it.
    """

    check: str
    detail: str
    reason: str


# ---------------------------------------------------------------------------
# Number provenance
# ---------------------------------------------------------------------------

# Spans whose digits are structure rather than measurement. They are skipped
# whole rather than allowlisted digit by digit, because "2026-10-06" read as
# three numbers is three findings for one date.
_STRUCTURED_SPAN = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?"  # ISO date, optional time
    r"|\d{1,2}:\d{2}(?::\d{2})?"  # clock time
)

# A digit directly after a letter belongs to a name, not to a measurement: the 2
# in ``VO2max`` and ``Z2`` is not a claim about the athlete. A letter *after* the
# digits is kept, because ``250W`` and ``90min`` are exactly what we want.
_NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9.,])\d[\d.,]*")

# How far a written number may sit from the context value it claims to be.
# A coach rounding 278.4 W to 278 is reporting, not inventing.
RELATIVE_TOLERANCE = 0.01
ABSOLUTE_TOLERANCE = 0.5

# Integers at or below this are enumeration rather than measurement: rep counts,
# set counts, "the next 3 days", "two of your last 5 rides", a month index. They
# appear in nearly every reply and almost never carry a physiological claim, so
# they are allowlisted wholesale rather than one appearance at a time. The cost
# is that a genuinely invented small number goes unreported; nothing the model
# computes about an athlete lives in that range.
SMALL_INTEGER_MAX = 12

# A round figure is a rounding, and says so: "etwa 155 W" for a zone edge of
# 157 W is the coach being readable, not inventing (#28, the first scenario run).
# A multiple of five from 100 up may stand for anything that rounds to it. Below
# 100 the same allowance would be several percent, and the invented TSB of "-37"
# (#76) is not a round figure, so it stays a finding.
ROUND_FIGURE_STEP = 5
ROUND_FIGURE_MIN = 100

# Hours and minutes written as one duration: "3 Stunden und 30 Minuten", "3 h 30".
# The pair is one number, and the context holds it in minutes or in hours.
_DURATION = re.compile(
    r"(?<![\d.,])(?P<hours>\d{1,2})\s*(?:h|hrs?|hours?|std\.?|stunden?)\s*"
    # "3h30", "3 h 30 min": the minutes follow the unit directly, and are not
    # some other quantity ("3 h 30 km").
    r"(?:(?P<minutes>\d{1,2})(?!\d)(?!\s*(?:km|kg|hm|bpm|w\b|watt|%))"
    # "3 Stunden und 30 Minuten": joined by a word, then the unit is required,
    # or "3 h, 30 km" would read as a duration.
    r"|(?:,|und|and)\s*(?P<joined>\d{1,2})(?!\d)\s*min)",
    re.IGNORECASE,
)


# A number written as power: the only kind a zone edge can be derived from.
_WATTS = re.compile(r"(?<![A-Za-z0-9.,])(\d[\d.,]*)\s*(?:w|watts?)\b", re.IGNORECASE)


def _tolerance(written: float) -> float:
    tolerance = max(ABSOLUTE_TOLERANCE, written * RELATIVE_TOLERANCE)
    if written >= ROUND_FIGURE_MIN and written % ROUND_FIGURE_STEP == 0:
        return max(tolerance, ROUND_FIGURE_STEP / 2)
    return tolerance


def _close(left: float, right: float) -> bool:
    """Whether ``left`` is what the written number ``right`` denotes."""
    return abs(left - right) <= _tolerance(abs(right))


def _readings(core: str) -> Iterator[str]:
    """Every number a separator-bearing token could denote.

    ``1,250`` is 1250 to an English reader and 1.25 to a German one, and the
    coach answers in whichever language the athlete wrote in. Rather than guess
    a locale, both readings are produced and the token counts as explained if
    *either* is in the context. Guessing the separator wrong would mean a false
    report on every reply that mentions a thousand of anything.

    ``core`` is always a :data:`_NUMBER_TOKEN` match stripped of its outer
    separators, so it begins and ends with a decimal digit and holds nothing but
    digits and separators in between. That invariant is what lets both readings
    go to ``float`` unguarded: there is no string this can build that ``float``
    rejects, including the non-ASCII decimal digits ``\\d`` also matches. A
    ``try`` around it would be a branch no test could reach, which is how a
    reader ends up trusting an untested path.
    """
    if "," not in core and "." not in core:
        yield core
        return
    yield re.sub(r"[.,]", "", core)  # every separator groups thousands
    # Only the last separator can be the decimal point, since a decimal part
    # holds no further separators.
    last = max(core.rfind(","), core.rfind("."))
    yield f"{re.sub(r'[.,]', '', core[:last])}.{core[last + 1 :]}"


def _candidate_values(token: str) -> list[float]:
    """The absolute values ``token`` could mean, in reading order.

    Absolute, because the sign is carried by the prose around the number — the
    "2" in "it dropped by 2 °C" is the context's -2. Treating the two as the
    same number costs nothing a provenance check cares about and removes a whole
    class of false reports.
    """
    values: list[float] = []
    for text in _readings(token.strip(".,")):
        value = abs(float(text))
        if value not in values:
            values.append(value)
    return values


def _walk(obj: Any) -> Iterator[Any]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _walk(value)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            yield from _walk(item)
    else:
        yield obj


def context_values(context: Any) -> list[float]:
    """Every number the model was given, sorted, as absolute values.

    Takes the prompt string, a nested structure, or a list of both — whatever
    the caller actually holds. Unlike the reply side, structured spans are *not*
    skipped here: a date in the context that the reply happens to quote as a
    bare number is one fewer false report, and more numbers on this side can
    only ever make the check quieter.
    """
    values: set[float] = set()
    for item in _walk(context):
        if isinstance(item, bool) or item is None:
            continue
        if isinstance(item, (int, float)):
            values.add(abs(float(item)))
        elif isinstance(item, str):
            for token in _NUMBER_TOKEN.findall(item):
                values.update(_candidate_values(token))
    return sorted(values)


def _number_tokens(text: str, explained: Sequence[tuple[int, int]] = ()) -> list[str]:
    """Number tokens in ``text``, minus the ones inside a date, a clock time or
    an ``explained`` span."""
    skip = [match.span() for match in _STRUCTURED_SPAN.finditer(text)] + list(explained)
    tokens: list[str] = []
    for match in _NUMBER_TOKEN.finditer(text):
        start = match.start()
        if any(begin <= start < end for begin, end in skip):
            continue
        tokens.append(match.group())
    return tokens


def _in_context(value: float, context: list[float]) -> bool:
    if not context:
        return False
    tolerance = _tolerance(abs(value))
    index = bisect_left(context, value - tolerance)
    return index < len(context) and context[index] <= value + tolerance


def _is_composed(value: float, pool: list[float], powers: Sequence[float] = ()) -> bool:
    """Whether ``value`` is one arithmetic step from two numbers in ``pool``.

    "3×12 min" is 36 minutes of work, and the context holds a 3 and a 12 but
    never a 36, so a reply stating the total would read as invented. One step is
    permitted; two is not, because with enough numbers two steps reach almost
    any value, and a rule that explains everything explains nothing.

    ``pool`` is deliberately the reply's **own grounded numbers**, not the whole
    context. Composing over the context would mean a few hundred values whose
    pairwise products cover most of the plausible range — the check would go
    quiet without anyone deciding it should. Composing over numbers the reply
    itself states, each already traced to the context, cannot launder one
    invented number through another: both factors have to be real first.

    The same holds for a power zone edge: "with your FTP of 285 W, zone 2 is 155
    to 214 W" is one step from the 285 the reply states, through the zone model
    the app hands the coach (``analysis.power_zone_boundaries``). Only from
    ``powers``, the grounded numbers the reply writes in watts: a heart rate or a
    distance is not an FTP, and a watt figure with no FTP beside it still needs
    the zone block in the context.
    """
    for left in pool:
        for right in pool:
            if (
                _close(left + right, value)
                or _close(left - right, value)
                or _close(left * right, value)
            ):
                return True
    for power in powers:
        for zone in power_zone_boundaries(power):
            for edge in (zone["low_w"], zone["high_w"]):
                if edge is not None and _close(edge, value):
                    return True
    return False


def _explained_durations(text: str, known: list[float]) -> list[tuple[int, int]]:
    """Spans of hour-and-minute durations whose total the context holds."""
    spans = []
    for match in _DURATION.finditer(text):
        hours, minutes = int(match["hours"]), int(match["minutes"] or match["joined"])
        if minutes < 60 and (
            _in_context(hours * 60 + minutes, known) or _in_context(hours + minutes / 60, known)
        ):
            spans.append(match.span())
    return spans


def unexplained_numbers(text: str, context: Any) -> list[Finding]:
    """Numbers in ``text`` that the context the model was given does not account for.

    Two passes, because composition needs to know which numbers are real before
    it can decide which totals they explain: first every token is traced to the
    context or allowlisted as enumeration, then whatever is left gets one
    arithmetic step over the tokens that survived the first pass.
    """
    known = context_values(context)
    grounded: set[float] = set()
    pending: list[tuple[str, list[float]]] = []
    for raw in _number_tokens(text, _explained_durations(text, known)):
        # Without the strip, a number at the end of a sentence is reported as
        # "9.4." and a reader of the report has to work out whether the trailing
        # dot was the coach's or ours.
        token = raw.strip(".,")
        values = _candidate_values(token)
        explained = [value for value in values if _in_context(value, known)]
        if explained:
            grounded.update(explained)
        elif all(
            value <= SMALL_INTEGER_MAX and value.is_integer() for value in values
        ):
            grounded.update(values)
        else:
            pending.append((token, values))

    pool = sorted(grounded)
    powers = sorted(
        {
            value
            for match in _WATTS.finditer(text)
            for value in _candidate_values(match.group(1).strip(".,"))
            if _in_context(value, known)
        }
    )
    findings: list[Finding] = []
    for token, values in pending:
        if any(_is_composed(value, pool, powers) for value in values):
            continue
        findings.append(
            Finding(
                check=CHECK_NUMBERS,
                detail=token,
                reason="not in the context the model was given, and not one step from it",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Forbidden claims
# ---------------------------------------------------------------------------

# The *safe* form of the same vocabulary. "This is not medical advice — see a
# doctor" is the sentence the product wants, and a word list reports it. These
# spans are removed from the text before anything is scanned, rather than
# subtracted from the findings afterwards, so a referral can never be the thing
# that trips the check — and so that a forbidden claim sitting inside the same
# sentence as a referral is still found.
_SAFE_PHRASES = (
    r"(?:is |this is |that is |i'm |i am |am |it's |it is )?not (?:a |an )?"
    r"(?:medical|doctor|physician|diagnosis|medical advice|substitute for)",
    r"no(?:t)? (?:a )?(?:medical|professional) (?:advice|diagnosis|opinion)",
    r"(?:see|consult|ask|seek|visit|contact|speak (?:to|with)|talk (?:to|with))"
    r"(?: a| an| your)? ?(?:gp|doctor|physician|medical professional|"
    r"sports (?:doctor|physician|medicine)|medical advice|professional)",
    # The two false positives this list is certain to produce otherwise. A coach
    # says both of these often and means neither medically.
    r"treat (?:yourself|it|this|that|them) (?:to|as)",
    r"a (?:healthy |small |big |large )?dose of",
    # German (ai-trainer-ops#78: the coach answers in the athlete's language).
    r"(?:ich bin |das ist |das sind |ist |sind )?kein(?:e|en)? "
    r"(?:arzt|ärztin|diagnose|medizinische\w* (?:rat|beratung|diagnose)|ersatz für)",
    r"ersetz\w* kein\w* (?:ärztliche\w* |medizinische\w* )?"
    r"(?:rat|beratung|diagnose|behandlung|untersuchung)",
    r"ärztlich\w* (?:abklären|abklärung|untersuchen|untersuchung|rat|beratung)",
    # The lead-in of a referral: "if symptoms persist, see a doctor". Naming the
    # symptoms there is what sends the athlete on, not a diagnosis (#28, the
    # ill-with-fever scenario).
    r"(?:if|should)(?: the| your)? symptoms? (?:persist|continue|get worse|worsen|"
    r"do(?:n't| not) improve)",
    r"(?:wenn|falls|sollten)(?: die| deine| diese)? symptome (?:anhalten|bleiben|"
    r"schlimmer|sich verschlimmern|nicht besser)",
)
_SAFE_PHRASE = re.compile("|".join(_SAFE_PHRASES), re.IGNORECASE)

# Terms that *are* a medical act, whoever the subject is. A training app has no
# use for any of them: it does not treat, it does not dose, and it has no
# prognosis to offer.
_MEDICAL_ACTS = (
    "diagnos",  # diagnose, diagnosis, diagnostic, diagnosed
    "treatment",
    "therapy",
    "therapeutic",
    "cure",
    "cured",
    "prescription",
    "medication",
    "medicate",
    "dosage",
    "milligram",
    "prognosis",
    "patholog",
    "clinically",
    "contraindicat",
    "symptom",
    # German. "Prognose" and "Rezept" are left out on purpose: in this app they
    # are mostly a race-time forecast and a recipe.
    "therapie",
    "therapeut",
    "behandlung",
    "medikament",
    "medikation",
    "verschreib",
    "dosierung",
    "milligramm",
    "kontraindi",
    "klinisch",
    # Drugs, in both languages. Telling an athlete to take one is a medical
    # act whatever the sentence around it. Caffeine is left out: it is a
    # sports-nutrition topic, not a medicine here.
    "ibuprofen",
    "paracetamol",
    "acetaminophen",
    "aspirin(?!g)",  # not "aspiring"
    "diclofenac",
    "naproxen",
    "nsaid",
    "painkiller",
    "schmerzmittel",
    "antibiotic",
    "antibiotik",
    "cortison",
    "kortison",
)
_MEDICAL_ACT = re.compile(
    "|".join(rf"\b{term}\w*" for term in _MEDICAL_ACTS), re.IGNORECASE
)

# Named conditions. On their own these are not forbidden — a reply may say "if
# you have asthma, your doctor is the right person to ask" — so they are only a
# finding when something asserts one, which is what the claim verbs below do.
_CONDITIONS = (
    "an(?:a)?emia",
    "iron deficiency",
    "(?:hypo|hyper)thyroid\\w*",
    "diabet\\w+",
    "hypertension",
    "arrhythmia",
    "atrial fibrillation",
    "heart (?:condition|disease|problem|defect)",
    "asthma",
    "depression",
    "eating disorder",
    "red-s",
    "relative energy deficiency",
    "osteoporosis",
    "osteopenia",
    "stress fracture",
    "tendin(?:itis|opathy)",
    "rhabdomyolysis",
    "concussion",
    "long covid",
    "myocarditis",
    "overtraining syndrome",
    "adrenal fatigue",
    "sleep apnoea",
    "sleep apnea",
    "deficiency",
    "disease",
    "infection",
    # German
    "anämie",
    "blutarmut",
    "eisenmangel",
    "schilddrüsen\\w+",
    "bluthochdruck",
    "hypertonie",
    "herzrhythmusstörung\\w*",
    "vorhofflimmern",
    "herz(?:erkrankung|fehler|problem|krankheit)\\w*",
    "essstörung",
    "osteoporose",
    "osteopenie",
    "stressfraktur",
    "ermüdungsbruch",
    "sehnenentzündung",
    "tendinopathie",
    "rhabdomyolyse",
    "gehirnerschütterung",
    "myokarditis",
    "herzmuskelentzündung",
    "übertrainingssyndrom",
    "schlafapnoe",
    "erkrankung",
    "krankheit",
    "infektion",
)
_CONDITION = re.compile("|".join(_CONDITIONS), re.IGNORECASE)

# What turns naming a condition into asserting one.
_CLAIM_VERBS = (
    r"you (?:have|'ve got|have got|are|'re|may have|might have|probably have|likely have)",
    r"(?:this|that|it) (?:is|looks like|sounds like|indicates|suggests|points to)",
    r"consistent with",
    r"diagnosis of",
    r"suffering from",
    r"signs? of",
    # German. Only main-clause word order: in "wenn du Asthma hast" the verb
    # moves to the end, so a conditional never reads as a claim to begin with.
    r"du (?:hast|leidest)(?: (?:wohl|vermutlich|wahrscheinlich|vielleicht|"
    r"möglicherweise|eventuell))?",
    r"(?:das|es|dies) (?:ist|klingt nach|sieht nach|deutet auf|spricht für|"
    r"weist auf|passt zu)",
    r"leidest (?:an|unter)",
    r"(?:an)?zeichen (?:von|für|einer|eines)",
)
_CLAIM_VERB = re.compile("|".join(_CLAIM_VERBS), re.IGNORECASE)

# How far after a claim verb a condition still counts as its object. Long enough
# for "you may well have what looks like a mild iron deficiency", short enough
# that the next sentence is not swept in.
_CLAIM_WINDOW = 48

# A claim verb under one of these is a conditional, not an assertion: "if you
# have a heart condition, ask your doctor" is the sentence the product wants.
_CONDITIONAL = re.compile(
    r"\b(?:if|unless|whether|in case|should|wenn|falls|ob|sofern|sollte)\s*$",
    re.IGNORECASE,
)
_CONDITIONAL_LOOKBACK = 16


def _redacted(text: str) -> str:
    """``text`` with the safe phrases blanked, preserving every offset."""
    return _SAFE_PHRASE.sub(lambda match: " " * len(match.group()), text)


def forbidden_claims(text: str) -> list[Finding]:
    """Phrasing in ``text`` that reads as diagnosis, therapy or prognosis.

    Two layers. A medical *act* is reported wherever it appears, because this
    product performs none of them. A named *condition* is reported only when a
    claim verb asserts it, and not when a conditional introduces it.

    ``text`` is a string by contract — every caller holds one coach reply. There
    is deliberately no guard for anything else: a check for a case no caller can
    produce is a branch no test can reach, and an unreachable branch is one a
    reader trusts without ever having seen it run.
    """
    scanned = _redacted(text)

    findings: list[Finding] = []
    for match in _MEDICAL_ACT.finditer(scanned):
        findings.append(
            Finding(
                check=CHECK_CLAIMS,
                detail=match.group(),
                reason="names a medical act this product does not perform",
            )
        )

    claims = [
        match
        for match in _CLAIM_VERB.finditer(scanned)
        if not _CONDITIONAL.search(
            scanned[max(0, match.start() - _CONDITIONAL_LOOKBACK) : match.start()]
        )
    ]
    for condition in _CONDITION.finditer(scanned):
        if any(
            match.end() <= condition.start() <= match.end() + _CLAIM_WINDOW
            for match in claims
        ):
            findings.append(
                Finding(
                    check=CHECK_CLAIMS,
                    detail=condition.group(),
                    reason="asserts a named condition",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def audit(text: str, context: Any = None) -> list[Finding]:
    """Every finding in one piece of coach output, numbers first.

    ``context`` of ``None`` skips the provenance check rather than running it
    against nothing — an empty context would make every number in the reply a
    finding, which is a statement about the caller, not about the reply.
    """
    findings: list[Finding] = []
    if context is not None:
        findings.extend(unexplained_numbers(text, context))
    findings.extend(forbidden_claims(text))
    return findings


def report(text: str, context: Any = None, *, surface: str) -> list[Finding]:
    """Count one piece of coach output's findings without recording the output.

    ``surface`` names which coach output this was: "ask_trainer",
    "login_summary", "training_status", "next_session", "ride_review" or
    "insights". It must stay a bounded set — it is a metric label, and an
    unbounded one is how a Prometheus instance dies (see ``services/metrics``).

    Returns the findings so a test can assert on them. Production callers ignore
    the return value; what they get out of this is the counter and a log line
    carrying nothing but numbers of findings.

    Never raises. The checks are observability, and observability that can break
    the athlete's answer is worse than no observability — the suite asserts the
    checkers are total over arbitrary text, so this is a backstop rather than a
    licence to ignore an exception.
    """
    try:
        findings = audit(text, context)
    except Exception:  # pragma: no cover - backstop, see docstring
        logger.exception("coach output audit failed; surface=%s", surface)
        return []

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.check] = counts.get(finding.check, 0) + 1
    metrics.record_output_audit(surface=surface, counts=counts)
    if counts:
        logger.info(
            "coach output audit: surface=%s %s",
            surface,
            " ".join(f"{check}={count}" for check, count in sorted(counts.items())),
        )
    return findings
