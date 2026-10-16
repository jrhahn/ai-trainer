"""Short-lived athlete states said in chat: ill, tired for days, or a day with less to give (ai-trainer-ops#18).

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
KIND_TIRED = "tired"

# An illness with no stated end is assumed to be over this long after the
# athlete last said it. Without a cap, one "I have a cold" would mark the athlete
# ill for good; counted from the last mention, saying it again keeps it.
OPEN_ILLNESS_DAYS = 10
# Days after an illness ends in which hard work is still a departure. Return-to-
# sport consensus (IOC 2016 consensus on load and illness; the "neck check")
# asks for a graded return, with no fixed number of days. Three is the prior.
RETURN_EASY_DAYS = 3
# Easy days that tiredness lasting for days asks for, counted from the day the
# athlete says it (ai-trainer-ops#103 Q34, the owner's (a)). The consensus on
# overreaching (Meeusen et al. 2013, ECSS and ACSM) asks for less load first when
# fatigue and poor performance persist, with no fixed number of days. Three, as
# for the return from an illness, is the prior. Saying it again extends it.
TIRED_EASY_DAYS = 3

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
    r"|fieberfrei|symptomfrei|fever[-\s]?free|symptom[-\s]free"
    r"|no\s+longer\s+(?:sick|ill)|not\s+(?:sick|ill)\s+any\s*more"
    r"|(?:i'?m|i\s+am|i\s+feel|feeling)\s+(?:\w+\s+)?(?:better|healthy|recovered)(?!\s+(?:at|than)\b)"
    r"|i'?ve\s+recovered|i\s+have\s+recovered"
    r")\b",
    re.IGNORECASE,
)
# A past illness the message ends recently: "Ich war eine Woche krank und bin
# seit gestern fieberfrei", "I was ill until yesterday". "Was ill" alone stays
# not-now; with an end this recent, the return prior has a day to count from.
_PAST_ILL = re.compile(
    rf"\b(?:was|were|war|waren)\s+(?:\w+\s+){{0,3}}{_ILL_WORD}\b", re.IGNORECASE
)
_ENDED_YESTERDAY = re.compile(r"\b(?:bis\s+gestern|until\s+yesterday|till\s+yesterday)\b", re.IGNORECASE)
_SINCE_RECENTLY = re.compile(
    r"\b(?:seit\s+(?:gestern|heute)|since\s+(?:yesterday|today|this\s+morning))\b", re.IGNORECASE
)
_TIRED_WORD = (
    r"(?:müde|kaputt|platt|erschöpft|erschoepft|ausgelaugt|schlapp|tired|exhausted|"
    r"fatigued|drained|knackered|wiped(?:\s+out)?|worn\s+out|run\s+down)"
)
_SINCE_DAYS = r"seit\s+(?:(?:einer|einigen|mehreren|ein\s+paar|\w+)\s+)?(?:woche|wochen|tagen)"
# Tiredness that has lasted, not one bad day: "Seit einer Woche bin ich ständig
# müde", "ständig platt", "tired all week", "always tired". "Ich bin müde" alone
# is today's state, which the coach hears in the message itself.
_TIRED = re.compile(
    r"\b(?:"
    rf"{_SINCE_DAYS}\b[^.!?\n]{{0,40}}?\b{_TIRED_WORD}"
    rf"|{_TIRED_WORD}\b[^.!?\n]{{0,30}}?\b{_SINCE_DAYS}"
    rf"|(?:ständig|staendig|dauernd|permanent|die\s+ganze\s+(?:zeit|woche))\s+(?:so\s+)?{_TIRED_WORD}"
    rf"|müdigkeit\b[^.!?\n]{{0,40}}?\b{_SINCE_DAYS}"
    rf"|{_TIRED_WORD}\b[^.!?\n]{{0,20}}?\b(?:for|all)\s+(?:a\s+|the\s+|this\s+)?(?:whole\s+)?"
    r"(?:week|weeks|days|few\s+days|several\s+days)"
    rf"|(?:always|constantly|permanently)\s+(?:so\s+)?{_TIRED_WORD}"
    r")\b",
    re.IGNORECASE,
)
# "Nicht mehr müde" is not tired, and a flat tyre or a broken derailleur is not
# the athlete ("Das Schaltwerk ist seit einer Woche kaputt"): the sentence has to
# be about themselves.
_NOT_TIRED = re.compile(
    rf"\b(?:not|no\s+longer|never|nicht|nie|kein\w*)\s+(?:mehr\s+|so\s+|more\s+)?{_TIRED_WORD}"
    r"|\b(?:\w*reifen|schlauch|tires?|tyres?|tubes?)\b",
    re.IGNORECASE,
)
# The athlete has to be the one who is tired, in the same clause: "Ich bin seit
# Tagen platt", "Meine Beine sind seit Tagen kaputt", "I've been tired all week".
# An "ich" elsewhere is not enough: "Mein Rad ist seit Tagen kaputt und ich weiß
# nicht weiter", "Mein Sohn ist seit Tagen müde und ich mache mir Sorgen".
_SELF_SUBJECT = re.compile(
    # The apostrophe is required: a bare "im" is German ("Mein Rad im Keller …").
    # ``_statements`` has already turned a curly one into "'".
    r"(?:\b(?:ich\s+bin|bin\s+ich|fühle?\s+(?:ich\s+)?mich|meine\s+beine|i'm|i\s+am"
    r"|i've\s+been|i\s+have\s+been|i\s+feel|my\s+legs)|^bin)\b"
    rf"[^.!?,;\n]{{0,40}}?\b{_TIRED_WORD}"
    r"|\bmeine\s+müdigkeit\b|\bmüdigkeit\b[^.!?,;\n]{0,30}?\bbei\s+mir\b",
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
# The prefix is required: "I ride 5 days a week" is not an illness end.
_FOR_DAYS = re.compile(
    r"\b(?:for\s+(?:the\s+next\s+)?|next\s+|für\s+(?:die\s+nächsten\s+)?"
    r"|die\s+nächsten\s+|noch\s+)(\d{1,2})\s+(?:days|tage)\b",
    re.IGNORECASE,
)
# "If I get a fever", "I hope I don't get the flu": not ill now.
_HYPOTHETICAL = re.compile(
    r"\b(?:if|in\s+case|hope|hoping|might|may|could|falls|wenn|hoffe|hoffentlich|vielleicht)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass(frozen=True, slots=True)
class StatedState:
    kind: str
    starts_on: date
    ends_on: date | None
    # When the athlete last said it; an open illness expires from here.
    said_on: date | None = None


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
    """The states the athlete states in ``message``: at most one illness, at most
    one tiredness that has lasted days, any reduced days."""
    found: list[StatedState] = []
    ill = False
    tired = False
    for sentence in _statements(message):
        match = _ILL.search(sentence)
        if (
            not ill
            and match
            and not _NOT_ILL.search(sentence)
            and not _HYPOTHETICAL.search(sentence, 0, match.start())
        ):
            ill = True
            found.append(StatedState(KIND_ILL, today, _illness_end(sentence, today)))
            continue
        match = _TIRED.search(sentence)
        if (
            not tired
            and match
            and _SELF_SUBJECT.search(sentence)
            and not _NOT_TIRED.search(sentence)
            and not _HYPOTHETICAL.search(sentence, 0, match.start())
        ):
            # Its easy days cover what a bad night in the same sentence costs.
            tired = True
            end = today + timedelta(days=TIRED_EASY_DAYS - 1)
            found.append(StatedState(KIND_TIRED, today, end))
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
    if not ill and _past_illness_ended_yesterday(message):
        yesterday = today - timedelta(days=1)
        found.insert(0, StatedState(KIND_ILL, yesterday, yesterday))
    return found


def _past_illness_ended_yesterday(message: str) -> bool:
    """A past illness whose end the message puts at yesterday.

    "Bis gestern" has to sit in the illness's own sentence: in "Ich war letzte
    Woche krank. Bis gestern war ich im Urlaub." it ends the holiday. A recovery
    word with "seit gestern" names the end of an illness by itself, so the
    illness may be told in another sentence ("Ich war krank. Seit gestern bin ich
    fieberfrei.").
    """
    sentences = _statements(message)
    if any(_PAST_ILL.search(s) and _ENDED_YESTERDAY.search(s) for s in sentences):
        return True
    return any(_PAST_ILL.search(s) for s in sentences) and any(
        _RECOVERED.search(s) and _SINCE_RECENTLY.search(s) for s in sentences
    )


def says_recovered(message: str) -> bool:
    """A statement that the athlete is well again."""
    return any(_RECOVERED.search(sentence) for sentence in _statements(message))


def effective_end(state: StatedState) -> date:
    """The last day a state holds; an open illness lasts ``OPEN_ILLNESS_DAYS`` from its last mention."""
    if state.ends_on is not None:
        return state.ends_on
    if state.kind != KIND_ILL:
        return state.starts_on
    return (state.said_on or state.starts_on) + timedelta(days=OPEN_ILLNESS_DAYS - 1)


def still_relevant(state: StatedState, today: date) -> bool:
    """Whether a state still says anything about the plan from ``today``."""
    tail = RETURN_EASY_DAYS if state.kind == KIND_ILL else 0
    return effective_end(state) + timedelta(days=tail) >= today


def label(kind: str, starts_on: date, ends_on: date | None) -> str:
    """How the reply names a captured state."""
    if kind == KIND_ILL:
        if ends_on is None:
            return f"Ill since {starts_on.isoformat()}"
        return f"Ill until {ends_on.isoformat()}"
    if kind == KIND_TIRED:
        return f"Tired for days: easy until {(ends_on or starts_on).isoformat()}"
    return f"Less to give on {starts_on.isoformat()}"


def recovered_label(ends_on: date) -> str:
    """How the reply names an illness the athlete just said is over."""
    return (
        f"Well again: easy days until "
        f"{(ends_on + timedelta(days=RETURN_EASY_DAYS)).isoformat()}"
    )
