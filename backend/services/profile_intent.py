"""Hours, training days, sports, level and max heart rate said in chat, as a
profile change to confirm (ai-trainer-ops#43, #202).

Onboarding asks for weekly hours, training days and sports, and the planner
treats them as priors (#26). They change: a new job, a winter without the bike.
The owner chose to change them through the chat rather than a Settings editor,
as a proposal under the reply (#39), the same confirm step as clearing a race
(#71): a sentence read wrong must cost a button press, not the profile.

Only what differs from the profile is proposed. Pure: no database, no clock.
"""

from __future__ import annotations

import re

from schemas import MAX_HR_MAX_BPM, MAX_HR_MIN_BPM
from services.availability import WEEKDAY_LABELS, WEEKDAYS

HOURS_MIN = 1
HOURS_MAX = 40

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")

_WEEKLY_HOURS = re.compile(
    # Never the tail of another number: "7,25" is not 25.
    r"(?<![\d.,])\b(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:h|hrs?|hours?|stunden?|std)\.?\s+"
    r"(?:per|a|each|every|pro|in\s+der|die|jede)\s+(?:week|woche)\b",
    re.IGNORECASE,
)
# "Last week I did 10 hours a week" reports, it does not state what is possible.
_PAST = re.compile(
    r"\b(?:last|letzte[nr]?|did|rode|ran|was|were|had|used\s+to|war|waren|hatte|"
    r"bin\s+\w+\s+gefahren|früher)\b",
    re.IGNORECASE,
)

_WEEKDAY = re.compile(r"\b(" + "|".join(WEEKDAYS) + r")s?\b", re.IGNORECASE)
# A weekday alone is this week ("Thursday I have no time" is an availability
# constraint), and "every Saturday a long ride" names one session, not the
# training days. "My training days" and "I train on" say so with one day.
# "Only", "nur" and "from now on" say so only with two or more days: "On Sunday
# I only have an hour" is about Sunday.
_TRAINING_DAYS = re.compile(
    r"\b(?:my\s+training\s+days|meine\s+trainingstage|"
    r"i\s+(?:can\s+)?train\s+on|ich\s+(?:kann|trainiere)\s+(?:\w+\s+)?(?:am|an))\b",
    re.IGNORECASE,
)
_WEAK_MARKER = re.compile(
    r"\b(?:only|nur|from\s+now\s+on|going\s+forward|ab\s+jetzt|ab\s+sofort|künftig|in\s+zukunft)\b",
    re.IGNORECASE,
)
# "I can only ride on Saturday this week" is a one-off, not the training days.
_ONE_OFF = re.compile(
    r"\b(?:this|next)\s+(?:week|weekend)|\b(?:today|tomorrow|tonight)\b"
    r"|\b(?:diese|nächste|naechste)\s+woche|\b(?:heute|morgen)\b|\b(?:dieses|am)\s+wochenende",
    re.IGNORECASE,
)
_NOT_ON = re.compile(
    r"\b(?:not|no|off|can'?t|cannot|don'?t|nicht|kein|keine|never|nie)\b", re.IGNORECASE
)

_SPORT_WORDS = {
    "cycling": r"(?:cycling|riding|the\s+bike|radfahren|rad\s*fahren|radeln)",
    "running": r"(?:running|run|jogging|laufen|laufe|joggen|jogge)",
    "strength": r"(?:strength(?:\s+training)?|the\s+gym|weights|krafttraining|kraft|gym)",
}
_ADD = r"(?:(?:i'?ve\s+|i\s+have\s+)?started|(?:i'?m|i\s+am)\s+(?:also\s+)?(?:doing|adding)|also\s+do|now\s+also|angefangen\s+(?:mit\s+)?|jetzt\s+auch|auch\s+noch|dazu)"
# "keine Zeit fürs Laufen" is not giving it up: German needs "mehr" or "aufgehört".
_DROP_BEFORE = (
    r"(?:stopped|quit|gave\s+up|no\s+more|no\s+longer|aufgehört\s+mit(?:\s+dem)?)"
)
_DROP_GERMAN = r"(?:kein(?:e|en)?\s+{words}\s+mehr|{words}\s+(?:\w+\s+)?(?:nicht\s+mehr|aufgehört))"


# "I'm a beginner", "ich bin eher Anfängerin", "I would call myself advanced".
# Said about oneself only: "a plan for beginners" or "beginner's luck" is not a
# level. Named on its own word, so "fortgeschrittenen Alter" is no level either.
_LEVEL_WORDS = {
    "beginner": r"(?:beginner|novice|anfänger(?:in)?|einsteiger(?:in)?)",
    "intermediate": r"(?:intermediate|mittleres\s+niveau|mittelstufe)",
    # Not "-en": "fortgeschrittenen" is an age or a stage far more often than a rider.
    "advanced": r"(?:advanced|experienced|fortgeschritten(?:er?)?|erfahren(?:er?)?)",
}
# "I'm" with its apostrophe: without it, German "im" ("im fortgeschrittenen
# Alter") would read as "I'm".
_SELF = (
    r"(?:i'm|i\s+am|i'?d\s+(?:say|call\s+myself)|i\s+would\s+(?:say\s+i'?m|call\s+myself)|"
    r"my\s+level\s+is|ich\s+bin|bin\s+ich|ich\s+würde\s+mich|mein\s+niveau\s+ist)"
)
_LEVEL_FILLER = r"(?:\s+(?:a|an|ein|eine|einen|eher|ziemlich|quite|rather|pretty|more\s+of\s+an?|als|so|wohl|still|noch|really|wirklich)){0,3}"

# "My max heart rate is 186", "Maximalpuls liegt bei 186", "HFmax 186", "max HR: 186".
_MAX_HR = re.compile(
    r"\b(?:max(?:imum|imal)?\.?\s*(?:heart\s*rate|hr|puls|herzfrequenz|hf)|"
    r"maximalpuls|maximale\s+herzfrequenz|hf\s*max|hfmax)\b"
    r"[^\d]{0,25}?(\d{3})\b",
    re.IGNORECASE,
)


def _statements(message: str) -> list[str]:
    message = message.replace("’", "'")
    # "7.5 hours": a decimal point does not end the sentence.
    message = re.sub(r"(?<=\d)\.(?=\d)", ",", message)
    # "7,5 Std. die Woche": an abbreviation's full stop does not end the sentence.
    message = re.sub(r"\b(std|hrs?)\.", r"\1", message, flags=re.IGNORECASE)
    return [
        s.group().strip()
        for s in _SENTENCE.finditer(message)
        if s.group().strip() and not s.group().rstrip().endswith("?")
    ]


def _hours(sentence: str) -> float | None:
    match = _WEEKLY_HOURS.search(sentence)
    if not match or _PAST.search(sentence):
        return None
    hours = float(match.group(1).replace(",", "."))
    return hours if HOURS_MIN <= hours <= HOURS_MAX else None


def _days(sentence: str) -> list[str] | None:
    if _NOT_ON.search(sentence) or _PAST.search(sentence) or _ONE_OFF.search(sentence):
        return None
    named = {
        WEEKDAY_LABELS[WEEKDAYS[m.group(1).lower()]]
        for m in _WEEKDAY.finditer(sentence)
    }
    if not named:
        return None
    if not (
        _TRAINING_DAYS.search(sentence)
        or (_WEAK_MARKER.search(sentence) and len(named) >= 2)
    ):
        return None
    return [day for day in WEEKDAY_LABELS.values() if day in named]


def _sports(sentence: str, current: list[str]) -> list[str] | None:
    sports = list(current)
    for sport, words in _SPORT_WORDS.items():
        if (
            re.search(
                rf"\b{_ADD}\s+(?:\w+\s+){{0,2}}{words}\b|\b{words}\s+(?:\w+\s+)?{_ADD}\b",
                sentence,
                re.IGNORECASE,
            )
            and sport not in sports
        ):
            sports.append(sport)
        elif (
            re.search(
                rf"\b{_DROP_BEFORE}\s+(?:\w+\s+){{0,2}}{words}\b|\b"
                + _DROP_GERMAN.format(words=words),
                sentence,
                re.IGNORECASE,
            )
            and sport in sports
        ):
            sports.remove(sport)
    order = list(_SPORT_WORDS)
    sports.sort(key=order.index)
    # Dropping the last sport is not a plan; that sentence is left alone.
    return sports if sports and sports != sorted(current, key=order.index) else None


def _level(sentence: str) -> str | None:
    if _NOT_ON.search(sentence):
        return None
    for level, words in _LEVEL_WORDS.items():
        if re.search(rf"\b{_SELF}{_LEVEL_FILLER}\s+{words}\b", sentence, re.IGNORECASE):
            return level
    return None


def _max_heart_rate(sentence: str) -> int | None:
    match = _MAX_HR.search(sentence)
    if not match:
        return None
    value = int(match.group(1))
    # The profile's own bounds, so an offer the profile would refuse is never made.
    return value if MAX_HR_MIN_BPM <= value <= MAX_HR_MAX_BPM else None


def propose_profile_change(
    message: str,
    *,
    weekly_hours: float | None,
    training_days: list[str] | None,
    sports: list[str] | None,
    fitness_level: str | None = None,
    max_heart_rate: int | None = None,
    resting_heart_rate: int | None = None,
) -> dict | None:
    """The profile fields ``message`` changes, or ``None`` when it changes none.

    Keys are ``weekly_hours``, ``training_days``, ``sports``, ``fitness_level``
    and ``max_heart_rate``, present only when the stated value differs from the
    profile.
    """
    proposal: dict = {}
    current_sports = list(sports or [])
    for sentence in _statements(message):
        hours = _hours(sentence)
        if hours is not None:
            # The last statement wins, even when it is the profile's own value.
            proposal["weekly_hours"] = hours
        days = _days(sentence)
        if days is not None:
            proposal["training_days"] = days
        new_sports = _sports(sentence, proposal.get("sports", current_sports))
        if new_sports is not None:
            proposal["sports"] = new_sports
        level = _level(sentence)
        if level is not None:
            proposal["fitness_level"] = level
        heart_rate = _max_heart_rate(sentence)
        if heart_rate is not None:
            proposal["max_heart_rate"] = heart_rate
    if set(proposal.get("sports", current_sports)) == set(current_sports):
        proposal.pop("sports", None)
    if proposal.get("weekly_hours") == weekly_hours:
        proposal.pop("weekly_hours", None)
    if set(proposal.get("training_days", training_days or [])) == set(
        training_days or []
    ):
        proposal.pop("training_days", None)
    if proposal.get("fitness_level") == fitness_level:
        proposal.pop("fitness_level", None)
    if proposal.get("max_heart_rate") == max_heart_rate:
        proposal.pop("max_heart_rate", None)
    # The profile refuses a max at or below the resting rate (UserUpdate), so
    # "Update profile" would fail; such a number is not offered.
    if (
        resting_heart_rate is not None
        and proposal.get("max_heart_rate") is not None
        and proposal["max_heart_rate"] <= resting_heart_rate
    ):
        proposal.pop("max_heart_rate")
    return proposal or None
