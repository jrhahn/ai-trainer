"""The athlete saying they have no race, as a proposal to confirm (ai-trainer-ops#71).

A race entered at setup stays on the profile until the athlete changes it in
Settings, and for months the coach planned toward one the athlete had said was
off. Clearing it automatically on a chat sentence would delete a real goal on a
false match, so the owner chose a confirm step (#63, Q21): the chat reply offers
to remove the race and the athlete presses the button or not.
"""

import re
from dataclasses import dataclass
from datetime import date

_NO_RACE = re.compile(
    r"\b(?:"
    r"(?:i\s+have\s+|i've\s+got\s+|there\s+is\s+|there's\s+)?no\s+(?:upcoming\s+|more\s+)?races?(?:\s+planned)?"
    r"|(?:not|no\s+longer)\s+(?:doing|racing|riding)\s+(?:the|that|any)\s+race"
    r"|(?:don'?t|do\s+not)\s+have\s+(?:any\s+)?(?:further\s+|more\s+)?races?(?:\s+events?)?"
    r"|kein(?:e|en)?\s+(?:rennen|wettkampf|wettkämpfe)"
    r"|(?:das\s+)?(?:rennen|race)\s+(?:ist\s+)?(?:abgesagt|gestrichen|vorbei|cancelled|canceled|off|over)"
    # "the goal over the winter" and "goal off-road" are not the goal being gone:
    # over/off/vorbei need the verb, only the unambiguous words may stand alone.
    r"|(?:\w+\s+)?(?:ziel|goal)\s+(?:ist\s+|is\s+)?(?:erst\s+mal\s+|erstmal\s+)?(?:weg|gestrichen|gone|dropped)"
    r"|(?:\w+\s+)?(?:ziel|goal)\s+(?:ist|is)\s+(?:erst\s+mal\s+|erstmal\s+)?(?:vorbei|over|off)"
    r")\b",
    re.IGNORECASE,
)


# "No race pace this week", "no race this weekend": training talk about the
# next days, not the season's race. Clearing deletes calendar races, so these
# must not even propose it.
_NOT_THE_SEASON = re.compile(
    r"\brace\s+(?:pace|day|week|weekend|prep|bike|wheels?|tyres?|tires?)\b"
    r"|\b(?:this|next)\s+(?:week|weekend)\b|\b(?:today|tomorrow|tonight)\b"
    r"|\b(?:heute|morgen|diese[ns]?\s+woche|(?:am|dieses?)\s+wochenende)\b",
    re.IGNORECASE,
)


def says_no_race(message: str) -> bool:
    """A statement (not a question) that the athlete has no race ahead."""
    return _no_race(message) is not None


def _no_race(message: str) -> tuple[str, re.Match[str]] | None:
    for sentence in re.split(r"(?<=[.!?\n])", message):
        if sentence.rstrip().endswith("?") or _NOT_THE_SEASON.search(sentence):
            continue
        match = _NO_RACE.search(sentence)
        if match:
            return sentence, match
    return None


@dataclass(frozen=True)
class NoRaceScope:
    """The days a "no race" statement is about; an open bound is ``None``."""

    first: date | None = None
    last: date | None = None

    def covers(self, day: date) -> bool:
        return (self.first is None or day >= self.first) and (
            self.last is None or day <= self.last
        )


_YEAR = re.compile(
    r"\b(?:(?P<this>dieses\s+jahr|diesem\s+jahr|this\s+year|diese\s+saison|dieser\s+saison"
    r"|this\s+season|heuer)"
    r"|(?P<next>n(?:ä|ae)chste[ns]?\s+(?:jahr|saison)|next\s+(?:year|season))"
    r"|(?P<year>20\d\d))\b",
    re.IGNORECASE,
)
_CLAUSE = re.compile(r"[,;]|\s+(?:aber|but|doch|sondern)\s+", re.IGNORECASE)


def no_race_scope(message: str, today: date) -> NoRaceScope | None:
    """The period the athlete says has no race, or ``None`` when they say none.

    "Kein Rennen dieses Jahr, aber nächstes" offered to clear every race,
    next year's included (ai-trainer-ops#129). The year counts when it is in
    the clause that says "no race"; without one, the statement is about every
    race ahead.
    """
    found = _no_race(message)
    if found is None:
        return None
    sentence, match = found
    starts = [m.end() for m in _CLAUSE.finditer(sentence, 0, match.start())]
    ends = [m.start() for m in _CLAUSE.finditer(sentence, match.end())]
    clause = sentence[max(starts, default=0) : min(ends, default=len(sentence))]
    year = _YEAR.search(clause)
    if year is None:
        return NoRaceScope()
    if year.group("this"):
        return NoRaceScope(None, date(today.year, 12, 31))
    named = today.year + 1 if year.group("next") else int(year.group("year"))
    return NoRaceScope(date(named, 1, 1), date(named, 12, 31))
