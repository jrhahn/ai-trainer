"""Sentences that point at a session the plan no longer has (ai-trainer-ops#235).

A day's text is written with its neighbours in view: "Rest today between your
Monday and Wednesday sessions", "This sets up your legs for the weekend block".
When a later change takes such a neighbour's session away, the training-day
guard after a write or a move in the chat, the sentence stays and points at a
day that is now rest. Tom read both kinds in persona runs.

The owner's decision of 2026-10-31: the app strikes such a sentence. No model
call; a sentence is struck only when the day it names has no session on any
date it can mean, and a text left empty gets a fixed one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from services.rest_days import is_rest

_WEEKDAYS = (
    ("monday", "montag"),
    ("tuesday", "dienstag"),
    ("wednesday", "mittwoch"),
    ("thursday", "donnerstag"),
    ("friday", "freitag"),
    ("saturday", "samstag", "sonnabend"),
    ("sunday", "sonntag"),
)
_WEEKDAY_INDEX = {
    name: index for index, names in enumerate(_WEEKDAYS) for name in names
}
_WEEKEND = ("weekend", "wochenende", "wochenendes")
_RELATIVE = {"yesterday": -1, "gestern": -1, "tomorrow": 1, "morgige": 1, "morgigen": 1}
# The words that make a mention one of a session, as the scenario check reads
# them (scripts/coach_scenarios._SESSION_WORDS): "Wednesday sessions", "the
# weekend block", "yesterday's long ride". Not "training": "Wednesday is not
# one of your training days" is true when Wednesday is rest. A lookahead, so
# that "Monday and Wednesday sessions" is a mention of each day.
_SESSION = (
    r"(?:\w+\s+){0,3}?(?:rides?|riding|sessions?|block|efforts?|volume|"
    r"workouts?|intervals?|fahrt\w*|einheit\w*|ausfahrt\w*)\b"
)
_NAMES = "|".join(
    sorted([*_WEEKDAY_INDEX, *_WEEKEND, *_RELATIVE], key=len, reverse=True)
)
_MENTION = re.compile(rf"\b({_NAMES})(?:'s|s)?\b(?=\s*{_SESSION})", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])(\s+)")


def _session_dates(plan: list[dict]) -> tuple[set[str], set[str]]:
    """The dates the plan has, and those among them with a session."""
    dates: set[str] = set()
    sessions: set[str] = set()
    for day in plan:
        when = str(day.get("date") or "")
        if not when:
            continue
        dates.add(when)
        if not is_rest(day):
            sessions.add(when)
    return dates, sessions


@dataclass(frozen=True)
class _Days:
    """What the plan holds: its dates, those with a session, and those that
    had one before this write and have none now; and today."""

    dates: set[str]
    sessions: set[str]
    lost: set[str]
    today: str


def _meant(word: str, on: date) -> list[date]:
    """The dates ``word`` can mean in the text of the day ``on``: yesterday or
    tomorrow exactly, a weekday or the weekend within six days either way."""
    word = word.lower()
    if word in _RELATIVE:
        return [on + timedelta(days=_RELATIVE[word])]
    if word in _WEEKEND:
        indexes = {5, 6}
    else:
        indexes = {_WEEKDAY_INDEX[word]}
    return [
        on + timedelta(days=offset)
        for offset in range(-6, 7)
        if offset and (on + timedelta(days=offset)).weekday() in indexes
    ]


def _stale(sentence: str, on: date, days: _Days, only_lost: bool) -> bool:
    """Whether ``sentence`` names a session on a day that has none. A date the
    plan does not hold says nothing either way, and nor does today or a past
    one, whose sessions no write takes: only a coming day the plan holds as
    rest makes a mention stale, and only when no date it can mean has one.
    With ``only_lost``, also only when one of them lost its session now."""
    for match in _MENTION.finditer(sentence):
        known = [d.isoformat() for d in _meant(match.group(1), on)]
        known = [d for d in known if d in days.dates and d > days.today]
        if not known or any(d in days.sessions for d in known):
            continue
        if not only_lost or any(d in days.lost for d in known):
            return True
    return False


def _kept(text: object, on: date, days: _Days, only_lost: bool) -> object:
    """``text`` without its stale sentences. A kept sentence keeps the space
    or line break that came before it."""
    if not isinstance(text, str) or not _MENTION.search(text):
        return text
    parts = _SENTENCE.split(text.strip())
    sentences, gaps = parts[::2], ["", *parts[1::2]]
    kept = [
        (gap, sentence)
        for gap, sentence in zip(gaps, sentences)
        if not _stale(sentence, on, days, only_lost)
    ]
    if len(kept) == len(sentences):
        return text
    return "".join(
        (gap if i else "") + sentence for i, (gap, sentence) in enumerate(kept)
    )


_TEXT_FIELDS = ("description", "workoutPurpose", "keyFocusPoints")


def _key(day: dict) -> tuple[str, int]:
    return str(day.get("date") or ""), int(day.get("slot") or 0)


def without_stale_mentions(
    plan: list[dict],
    today: str,
    *,
    protected: set[str] = frozenset(),
    before: list[dict] | None = None,
) -> list[dict]:
    """``plan`` with every sentence struck that names a session on a day that
    is now rest, in the days after ``today``.

    Today and the past keep their words: the athlete may be riding today's or
    has ridden the others. A date in ``protected`` keeps its words too: a day
    the athlete set themselves. A description left empty becomes the day's
    title: words the plan already has, in the athlete's language once
    translated, where a fixed English sentence needed a translation of its own
    (review of 2026-11-01, ai-trainer-ops#338 C10). A purpose or focus points
    left empty become ``None``.

    ``before`` is the plan this write replaces. A day whose words the write
    leaves as they were loses a sentence only when a day it names lost its
    session in this write: a sentence that was stale before stays, so that a
    run which changes nothing else does not change texts, and is not narrated
    to the athlete for it. A day the write gives new words is read in full.
    """
    dates, sessions = _session_dates(plan)
    earlier = {_key(d): d for d in before or []}
    _, had = _session_dates(before or [])
    days = _Days(dates, sessions, lost=had - sessions, today=today)
    result: list[dict] = []
    for day in plan:
        when = str(day.get("date") or "")
        if not when or when <= today or when in protected or day.get("completed"):
            result.append(day)
            continue
        try:
            on = date.fromisoformat(when)
        except ValueError:
            # Not this step's to judge: the canonical gate after it is.
            result.append(day)
            continue
        prior = earlier.get(_key(day))
        only_lost = prior is not None and all(
            prior.get(field) == day.get(field) for field in _TEXT_FIELDS
        )
        changed = dict(day)
        for field in ("description", "workoutPurpose"):
            if field in changed:
                changed[field] = _kept(changed[field], on, days, only_lost)
        points = changed.get("keyFocusPoints")
        if isinstance(points, list):
            kept_points = [
                point
                for point in points
                if not (isinstance(point, str) and _stale(point, on, days, only_lost))
            ]
            # Only a strike changes them: an empty list stays one.
            if len(kept_points) != len(points):
                changed["keyFocusPoints"] = kept_points or None
        if day.get("description") and not str(changed.get("description") or "").strip():
            changed["description"] = str(day.get("title") or "")
        # A purpose with nothing left is no purpose: absent, as on a day that
        # never had one.
        if (
            day.get("workoutPurpose")
            and not str(changed.get("workoutPurpose") or "").strip()
        ):
            changed["workoutPurpose"] = None
        result.append(changed if changed != day else day)
    return result
