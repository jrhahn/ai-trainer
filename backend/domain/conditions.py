"""A known condition that asks for a doctor's word before hard training
(review of 2026-11-01, training A2, ai-trainer-ops#338).

ACSM's pre-participation screening (Riebe et al. 2015) clears a person with known
cardiovascular or metabolic disease for vigorous exercise only after a medical
check; in Germany the DGSP advises a sports-medical check from 35 on. The prompt
asked the model to suggest that, and the persona run's Werner ("Ich nehme
Betablocker gegen Bluthochdruck") got watt targets and no doctor. The medication
half is a fixed note since #184; this is the condition half: high blood pressure,
a heart condition, diabetes, named as the athlete's own.

This only reads the athlete's message and decides nothing medical: the reply
carries one fixed sentence, once.

Pure: no database, no clock.
"""

from __future__ import annotations

import re

HYPERTENSION = "hypertension"
HEART = "heart"
DIABETES = "diabetes"

_CONDITIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        HYPERTENSION,
        re.compile(
            r"\b(?:bluthochdruck|hypertonie|hoh(?:e[nmr]?|er)\s+blutdruck"
            r"|high\s+blood\s+pressure|hypertension)\b",
            re.IGNORECASE,
        ),
    ),
    (
        HEART,
        re.compile(
            r"\b(?:herz(?:erkrankung|krankheit|schwäche|insuffizienz|fehler|infarkt"
            r"|klappe\w*|kranz\w*|problem)\w*|herzkrank\w*|koronar\w*|vorhofflimmern"
            r"|(?:einen\s+|ein\s+|nen\s+)?stent|bypass"
            r"|heart\s+(?:disease|condition|failure|attack|problems?)|coronary\s+\w+"
            r"|atrial\s+fibrillation|a-?fib|cardiac\s+\w+)\b",
            re.IGNORECASE,
        ),
    ),
    (
        DIABETES,
        re.compile(
            r"\b(?:diabet\w*|zuckerkrank\w*|insulinpflichtig\w*"
            r"|type\s*[12]\s+diabet\w*|typ[\s-]*[12][\s-]+diabet\w*)\b",
            re.IGNORECASE,
        ),
    ),
)

# "kein Bluthochdruck", "no heart problems", "never had diabetes": just before
# the condition, in the same clause.
_DENIED = re.compile(
    r"(?:\b(?:kein\w*|nicht|nie|ohne|no|not|never|without)\b|n't\b)"
    r"(?:[^\S\n]+[^\W\d_]+){0,2}[^\S\n]*$",
    re.IGNORECASE,
)

# Someone else's: "mein Vater hat Diabetes", "my wife has high blood pressure".
_NOT_MINE = re.compile(
    r"\b(?:mein\w*|meine[mnrs]?|unser\w*|my|our)\s+(?:\w+\s+)?"
    r"(?:vater|mutter|papa|mama|eltern|frau|mann|partner\w*|freund\w*|opa|oma"
    r"|bruder|schwester|sohn|tochter|kind\w*|kolleg\w*|nachbar\w*"
    r"|dad|father|mother|mom|mum|parents?|wife|husband|partner|friend|grand\w+"
    r"|brother|sister|son|daughter|kids?|child\w*|colleague|neighbou?r)\b",
    re.IGNORECASE,
)

_CLAUSE_END = re.compile(r"[.!?;\n]|,\s*(?:aber|but|und|and)\b", re.IGNORECASE)


def _clause_before(text: str, start: int) -> str:
    """The text of the match's own clause before it, at most 60 characters."""
    before = text[max(0, start - 60) : start]
    ends = list(_CLAUSE_END.finditer(before))
    return before[ends[-1].end() :] if ends else before


def names_condition(message: str) -> str | None:
    """The first condition the athlete names as their own, or ``None``."""
    text = message or ""
    for kind, pattern in _CONDITIONS:
        for match in pattern.finditer(text):
            clause = _clause_before(text, match.start())
            if _DENIED.search(clause) or _NOT_MINE.search(clause):
                continue
            return kind
    return None


NOTE = {
    "German": (
        "Mit einer Vorerkrankung wie dieser sprich dein Training und deine "
        "Belastungsgrenzen bitte mit deiner Ärztin oder deinem Arzt ab, bevor es "
        "hart wird. Eine sportmedizinische Untersuchung, zum Beispiel ein "
        "Belastungs-EKG, gibt dir auch Bereiche, die zu dir passen."
    ),
    "English": (
        "With a condition like this, please talk your training and its limits "
        "through with your doctor before it gets hard. A sports-medical check, "
        "such as an exercise ECG, also gives you ranges that fit you."
    ),
}


def note(german: bool) -> str:
    return NOTE["German" if german else "English"]
