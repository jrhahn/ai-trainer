"""Short-lived athlete states said in chat: ill, or a day with less to give (ai-trainer-ops#18).

"I'm ill" used to reach the planner only as chat text, and nothing could check
a plan against it. These states are captured from the message the same way
availability constraints are, and they expire on their own. They are priors,
not a guard (#39 Q1): ``plan_priors`` reports a plan that puts hard work into
an illness or its return, and the coach either brings the plan back or says why
not. Nothing is reverted.

Only the kind and the dates are stored, never the athlete's sentence: an illness
is health data (Art. 9 GDPR), and the planner needs no more than the dates.

Pure: no database, no clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from services.availability import WEEKDAYS, _next_weekday, _target_dates

KIND_ILL = "ill"
KIND_REDUCED = "reduced"

# An illness with no stated end is assumed to be over after this long. Without
# a cap, one "I have a cold" would mark the athlete ill for good.
OPEN_ILLNESS_DAYS = 10
# Days after an illness ends in which hard work is still a departure. Return-to-
# sport consensus (IOC 2016 consensus on load and illness; the "neck check")
# asks for a graded return, with no fixed number of days. Three is the prior.
RETURN_EASY_DAYS = 3

_ILL = re.compile(
    r"\b(?:"
    r"(?:i'?m|i\s+am|i've\s+been|i\s+have\s+been|i\s+feel|feeling)\s+(?:\w+\s+){0,2}(?:sick|ill|unwell)"
    r"|i(?:'ve|\s+have)?\s+(?:got\s+|caught\s+)?(?:a\s+|the\s+)?(?:cold(?!\s+(?:shower|water|weather|ride|start|feet|hands))|flu|fever|covid|virus|stomach\s+bug|infection)"
    r"|(?:ich\s+)?bin\s+(?:\w+\s+){0,2}(?:krank|erkältet|erkaeltet)"
    r"|(?:ich\s+)?(?:habe?|hab)\s+(?:\w+\s+){0,2}(?:fieber|grippe|erkältung|erkaeltung|corona|covid|einen\s+infekt|magen-darm)"
    r")\b",
    re.IGNORECASE,
)
# "sick of intervals" is not an illness, "not sick" and "was ill" are not now.
# Only a negation or a past tense right before the word counts, so "I'm ill and
# not riding" is still an illness.
_ILL_WORD = r"(?:sick|ill|unwell|krank|erkältet|erkaeltet)"
_NOT_ILL = re.compile(
    r"\bsick\s+of\b"
    rf"|\b(?:not|never|no\s+longer|nicht|nie|was|were|war|waren)\s+(?:\w+\s+)?{_ILL_WORD}\b",
    re.IGNORECASE,
)
_RECOVERED = re.compile(
    r"\b(?:"
    r"wieder\s+(?:gesund|fit)|(?:bin|fühle\s+mich)\s+(?:\w+\s+)?gesund|nicht\s+mehr\s+krank"
    r"|no\s+longer\s+(?:sick|ill)|not\s+(?:sick|ill)\s+any\s*more"
    r"|(?:i'?m|i\s+am|i\s+feel|feeling)\s+(?:\w+\s+)?(?:better|healthy|recovered)"
    r"|i'?ve\s+recovered|i\s+have\s+recovered"
    r")\b",
    re.IGNORECASE,
)
# A day with less to give. A party or a late night costs the day after, the
# others cost the day they happen on.
_REDUCED_NEXT_DAY = re.compile(
    r"\b(?:party|feier|geburtstagsfeier|late\s+night|lange\s+nacht)\b", re.IGNORECASE
)
_REDUCED_SAME_DAY = re.compile(
    r"\b(?:"
    r"long\s+(?:day|shift)\s+at\s+work|\d{2}\s*-?\s*h(?:our)?\s+(?:work\s*)?(?:day|shift)"
    r"|langer?\s+(?:arbeitstag|bürotag|buerotag)|\d{2}\s*-?\s*(?:h|stunden)\s*-?\s*(?:tag|schicht)"
    r"|slept\s+(?:badly|poorly)|barely\s+slept|(?:schlecht|kaum)\s+geschlafen"
    r")\b",
    re.IGNORECASE,
)
_YESTERDAY = re.compile(r"\b(?:yesterday|last\s+night|gestern|letzte\s+nacht)\b", re.IGNORECASE)
_UNTIL = re.compile(
    r"\b(?:until|till|bis)\s+(?:(?:on|zum|am)\s+)?("
    + "|".join(WEEKDAYS)
    + r"|tomorrow|morgen)\b",
    re.IGNORECASE,
)
_FOR_DAYS = re.compile(
    r"\b(?:for\s+(?:the\s+next\s+)?|für\s+|die\s+nächsten\s+)?(\d{1,2})\s+(?:days|tage)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass(frozen=True, slots=True)
class StatedState:
    kind: str
    starts_on: date
    ends_on: date | None


def _statements(message: str) -> list[str]:
    """The message's sentences that are not questions."""
    message = message.replace("\u2019", "'")
    return [
        s.group().strip()
        for s in _SENTENCE.finditer(message)
        if s.group().strip() and not s.group().rstrip().endswith("?")
    ]


def _illness_end(sentence: str, today: date) -> date | None:
    until = _UNTIL.search(sentence)
    if until:
        word = until.group(1).lower()
        if word in ("tomorrow", "morgen"):
            return today + timedelta(days=1)
        return _next_weekday(today, WEEKDAYS[word])
    days = _FOR_DAYS.search(sentence)
    if days and 0 < int(days.group(1)) <= OPEN_ILLNESS_DAYS:
        return today + timedelta(days=int(days.group(1)) - 1)
    return None


def extract_states(message: str, today: date) -> list[StatedState]:
    """The states the athlete states in ``message``: at most one illness, any reduced days."""
    found: list[StatedState] = []
    ill = False
    for sentence in _statements(message):
        if not ill and _ILL.search(sentence) and not _NOT_ILL.search(sentence):
            ill = True
            found.append(StatedState(KIND_ILL, today, _illness_end(sentence, today)))
            continue
        # "Party on Saturday" costs Sunday; "late night yesterday" costs today.
        shift = (
            1
            if _REDUCED_NEXT_DAY.search(sentence) and not _YESTERDAY.search(sentence)
            else 0
        )
        if not (_REDUCED_NEXT_DAY.search(sentence) or _REDUCED_SAME_DAY.search(sentence)):
            continue
        named = [date.fromisoformat(iso) for iso, _ in _target_dates(sentence.lower(), today)]
        for day in named or [today]:
            costs = day + timedelta(days=shift)
            found.append(StatedState(KIND_REDUCED, costs, costs))
    return found


def says_recovered(message: str) -> bool:
    """A statement that the athlete is well again."""
    return any(_RECOVERED.search(sentence) for sentence in _statements(message))


def effective_end(kind: str, starts_on: date, ends_on: date | None) -> date:
    """The last day a state holds; an open illness lasts ``OPEN_ILLNESS_DAYS``."""
    if ends_on is not None:
        return ends_on
    return starts_on + timedelta(days=OPEN_ILLNESS_DAYS - 1 if kind == KIND_ILL else 0)


def still_relevant(kind: str, starts_on: date, ends_on: date | None, today: date) -> bool:
    """Whether a state still says anything about the plan from ``today``."""
    tail = RETURN_EASY_DAYS if kind == KIND_ILL else 0
    return effective_end(kind, starts_on, ends_on) + timedelta(days=tail) >= today


def label(kind: str, starts_on: date, ends_on: date | None) -> str:
    """How the reply names a captured state."""
    if kind == KIND_ILL:
        if ends_on is None:
            return f"Ill since {starts_on.isoformat()}"
        return f"Ill until {ends_on.isoformat()}"
    return f"Less to give on {starts_on.isoformat()}"


def recovered_label(ends_on: date) -> str:
    """How the reply names an illness the athlete just said is over."""
    return (
        f"Well again: easy days until "
        f"{(ends_on + timedelta(days=RETURN_EASY_DAYS)).isoformat()}"
    )
