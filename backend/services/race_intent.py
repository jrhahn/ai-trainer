"""The athlete saying they have no race, as a proposal to confirm (ai-trainer-ops#71).

A race entered at setup stays on the profile until the athlete changes it in
Settings, and for months the coach planned toward one the athlete had said was
off. Clearing it automatically on a chat sentence would delete a real goal on a
false match, so the owner chose a confirm step (#63, Q21): the chat reply offers
to remove the race and the athlete presses the button or not.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

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


# --- A race the athlete says is coming (ai-trainer-ops#175) -------------------
#
# "I have a race in 3 weeks" changed one day and left the race in May: the chat
# had no way to take a race in. Now the sentence is offered as a calendar race,
# with the date resolved here so the athlete sees it and can correct it before
# anything is saved. A wrong match costs a "No thanks", so the words are broad;
# a date is only taken from a sentence that names a race.

_RACE_WORD = re.compile(
    r"\b(?:races?|rennen|radrennen|wettkampf|wettkämpfe|gran\s*fondo|granfondo"
    r"|sportive|radmarathon|crit(?:erium)?)\b(?!\s+(?:pace|bike|wheels?|tyres?|tires?))",
    re.IGNORECASE,
)
# "My race on 12 October went well" is history, not a race to add next year.
_RACE_PAST = re.compile(
    r"\b(?:was|went|had|did|raced|finished|ago|last|war|hatte|bin\s+\w+\s+gefahren"
    r"|gefahren|letzte[nmrs]?|vor\s+\d+|vor\s+(?:einer|einem|zwei|drei|vier)\b)",
    re.IGNORECASE,
)

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "ein": 1, "eine": 1, "einer": 1, "einem": 1, "einen": 1, "zwei": 2, "drei": 3,
    "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
    "zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12,
}
_IN_RELATIVE = re.compile(
    r"\b(?:in|within|in\s+about|in\s+etwa|in\s+ca\.?)\s+"
    r"(?P<n>\d{1,3}|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\s+"
    r"(?P<unit>days?|weeks?|months?|tagen?|wochen?|woche|monaten?|monat)\b",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1, "january": 1, "januar": 1, "jän": 1, "jänner": 1,
    "feb": 2, "february": 2, "februar": 2,
    "mar": 3, "march": 3, "märz": 3, "maerz": 3, "mär": 3,
    "apr": 4, "april": 4,
    "may": 5, "mai": 5,
    "jun": 6, "june": 6, "juni": 6,
    "jul": 7, "july": 7, "juli": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12, "dez": 12, "dezember": 12,
}
_MONTH = "(?P<month>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY_MONTH = re.compile(
    r"\b(?P<day>\d{1,2})(?:st|nd|rd|th|\.)?\s+(?:of\s+)?" + _MONTH
    + r"(?:\s+(?P<year>20\d\d))?\b",
    re.IGNORECASE,
)
_MONTH_DAY = re.compile(
    r"\b" + _MONTH + r"\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(?P<year>20\d\d))?",
    re.IGNORECASE,
)
_ISO = re.compile(r"\b(?P<year>20\d\d)-(?P<month>\d\d)-(?P<day>\d\d)\b")
# 15.11. or 15.11.2026; never a decimal ("7.5 h"): the day needs its own dot.
_NUMERIC = re.compile(
    r"(?<![\d.])(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>(?:20)?\d\d)?(?![\d])"
)

# A full stop ends a sentence after a word, not after a day ("am 3. Mai") and
# not before one ("Nov. 12").
_SENTENCE_END = re.compile(r"(?<=[!?\n])\s*|(?<=[^\W\d_]{2}\.)\s+(?!\d)")

# How far ahead a stated race may be and still be read as one.
RACE_HORIZON_DAYS = 730


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _relative(sentence: str, today: date) -> date | None:
    match = _IN_RELATIVE.search(sentence)
    if match is None:
        return None
    raw = match.group("n").lower()
    count = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
    unit = match.group("unit").lower()
    if unit.startswith(("day", "tag")):
        return today + timedelta(days=count)
    if unit.startswith(("week", "woche")):
        return today + timedelta(weeks=count)
    return _add_months(today, count)


def _calendar_date(year: int | None, month: int, day: int, today: date) -> date | None:
    """The date named; without a year, the next one from today."""
    try:
        if year is not None:
            return date(year + 2000 if year < 100 else year, month, day)
        named = date(today.year, month, day)
    except ValueError:
        return None
    return named if named >= today else _calendar_date(today.year + 1, month, day, today)


def _absolute(sentence: str, today: date) -> date | None:
    for pattern in (_ISO, _NUMERIC):
        match = pattern.search(sentence)
        if match:
            year = match.group("year")
            return _calendar_date(
                int(year) if year else None,
                int(match.group("month")),
                int(match.group("day")),
                today,
            )
    for pattern in (_DAY_MONTH, _MONTH_DAY):
        match = pattern.search(sentence)
        if match:
            year = match.group("year")
            return _calendar_date(
                int(year) if year else None,
                _MONTHS[match.group("month").lower()],
                int(match.group("day")),
                today,
            )
    return None


def _race_sentence(message: str, today: date) -> tuple[str, date] | None:
    """The first sentence that names a race and a date ahead, with that date.

    A "no race" sentence never counts: that is :func:`no_race_scope`'s offer.
    """
    for sentence in _SENTENCE_END.split(message.replace("’", "'")):
        if not _RACE_WORD.search(sentence) or _NO_RACE.search(sentence):
            continue
        if _RACE_PAST.search(sentence):
            continue
        day = _relative(sentence, today) or _absolute(sentence, today)
        if day is not None and today < day <= today + timedelta(days=RACE_HORIZON_DAYS):
            return sentence, day
    return None


def stated_race_date(message: str, today: date) -> date | None:
    """The date of a race the athlete says is ahead, or ``None``."""
    found = _race_sentence(message, today)
    return found[1] if found else None


# "Das Rennen wird verschoben: neues Datum ist der 17. Januar" moves the race
# the athlete has; it is not a second one. The persona run of 2026-10-21 got an
# offer for 17 January and kept the profile's 30 December beside it.
_MOVED = re.compile(
    r"\b(?:verschoben|verschiebt|verlegt|verlegen|neues\s+datum|neuer\s+termin"
    r"|ist\s+jetzt\s+am|findet\s+jetzt|statt\s+am|moved|postponed|rescheduled"
    r"|pushed\s+back|brought\s+forward|new\s+date|is\s+now\s+on)\b",
    re.IGNORECASE,
)


def stated_race_move(message: str, today: date) -> bool:
    """Whether the sentence that gives the race's date says the race moves."""
    found = _race_sentence(message, today)
    return bool(found and _MOVED.search(found[0]))
