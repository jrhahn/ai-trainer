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

from domain.availability import WEEKDAYS, _next_weekday, _target_dates

KIND_ILL = "ill"
# Ill with fever or symptoms below the neck: no riding at all while it lasts,
# and a return at least as long as the illness. The "neck check" (Eichner) and
# the IOC 2016 consensus (Schwellnus): above the neck, light exercise may do;
# fever, body aches, chest or gut symptoms mean none, and exercise with a fever
# risks myocarditis (review of 2026-11-01, ai-trainer-ops#338).
KIND_FEVER = "fever"
ILL_KINDS = frozenset({KIND_ILL, KIND_FEVER})
KIND_REDUCED = "reduced"
KIND_TIRED = "tired"
# A warning sign said in chat (chest pain, fainting, …, ``domain.red_flags``):
# no session until the athlete says a doctor has checked it. ESC 2020 and
# AHA/ACC: stop exercising and get a prompt medical evaluation (review of
# 2026-11-01, ai-trainer-ops#338, A1). Only the dates are stored, not the
# symptom (Art. 9 GDPR).
KIND_CLEARANCE = "clearance"
# How long an unanswered warning sign holds the plan, from its last mention.
# The athlete who never says the doctor's word gets their plan back after two
# weeks; the note under each reply until then keeps asking.
OPEN_CLEARANCE_DAYS = 14
# The kinds that hold every session, not only intensity.
NO_SESSION_KINDS = frozenset({KIND_FEVER, KIND_CLEARANCE})


def is_ill(kind: str) -> bool:
    """Either kind of illness: everything that holds for one holds for both."""
    return kind in ILL_KINDS


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

# A shot, a jab or a test is no illness (ai-trainer-ops#115): "I got a flu shot",
# "I have a covid test tomorrow" and "Corona-Test" each opened ten ill days.
_NOT_THE_ILLNESS = r"(?![\s-]*(?:shot|jab|vaccin\w*|booster|test\w*|impf\w*))"
# One word that is no negation, so "Ich habe kein Fieber" is no fever.
_NOT_NEGATED_WORD = r"(?:(?!(?:kein\w*|nicht|nie|no|not)\b)\w+\s+)"
_ILL = re.compile(
    r"\b(?:"
    # "ill-prepared" is no illness.
    r"(?:i'?m|i\s+am|i've\s+been|i\s+have\s+been|i\s+feel|feeling)\s+(?:\w+\s+){0,2}(?:sick|ill(?!-)|unwell)"
    r"|i(?:'ve|\s+have)?\s+(?:got\s+|caught\s+)?(?:a\s+|the\s+)?(?:cold(?!\s+(?:shower|water|weather|ride|start|feet|hands))|flu|fever|covid|virus|stomach\s+bug|infection)"
    + _NOT_THE_ILLNESS
    + r"|(?:ich\s+)?bin\s+(?:\w+\s+){0,3}(?:krank(?:geschrieben)?|erkältet|erkaeltet)"
    # "Seit vorgestern erkältet", said without the verb.
    r"|seit\s+(?:\w+\s+){1,2}(?:krank|erkältet|erkaeltet)"
    r"|(?:ich\s+)?(?:habe?|hab)\s+" + _NOT_NEGATED_WORD + r"{0,2}"
    r"(?:fieber|grippe|erkältung|erkaeltung|corona|covid|einen\s+infekt|magen-darm)"
    + _NOT_THE_ILLNESS
    + r")\b",
    re.IGNORECASE,
)
# "sick of intervals" is not an illness, "not sick" and "was ill" are not now.
# Only a negation or a past tense right before the word counts, so "I'm ill and
# not riding" is still an illness.
_ILL_WORD = r"(?:sick|ill|unwell|krank|erkältet|erkaeltet)"
_NOT_ILL = re.compile(
    r"\bsick\s+of\b|\bsick\s+and\s+tired\b"
    rf"|\b(?:not|never|no\s+longer|nicht|nie|was|were|war|waren)\s+(?:\w+\s+)?{_ILL_WORD}\b"
    # "Ich habe gestern Fieber gehabt, heute geht es": over, not now. Only after
    # a symptom: "Ich bin krank und habe gestern frei gehabt" is ill (review of #927).
    r"|\b(?:fieber|grippe|erkältung|erkaeltung|corona|covid|infekt|magen-darm|husten"
    r"|schnupfen|halsweh)\s+(?:\w+\s+){0,2}?gehabt\b",
    re.IGNORECASE,
)
# The athlete taking an illness back (ai-trainer-ops#115): it never was. Not a
# recovery, which keeps the easy days of a return: "nicht mehr krank" and
# "not sick any more" stay recoveries.
# A false take-back deletes real health data, so it is guarded at least as
# tightly as a capture (review of #927): not "sick of"/"sick and tired"/"ill-",
# not about another time ("I wasn't sick last week"), and see ``says_not_ill``.
_NOT_ABOUT_NOW = (
    r"(?!\s+any\s*more|\s+(?:of|and\s+tired)\b|-"
    r"|\s+(?:\w+\s+)?(?:last|yesterday|before|earlier|then|tomorrow"
    r"|letzte\w*|gestern|vorher|damals|morgen|mehr)\b)"
)
_NOT_ILL_AT_ALL = re.compile(
    r"\b(?:"
    r"ich\s+(?:bin|war)\s+(?:doch\s+|gar\s+|überhaupt\s+|ueberhaupt\s+)?nicht\s+krank"
    + _NOT_ABOUT_NOW
    + r"|i'?m\s+not\s+(?:actually\s+)?(?:sick|ill)"
    + _NOT_ABOUT_NOW
    + r"|i\s+am\s+not\s+(?:actually\s+)?(?:sick|ill)"
    + _NOT_ABOUT_NOW
    + r"|i\s+(?:wasn't|was\s+not|wasnt)\s+(?:actually\s+|really\s+)?(?:sick|ill)"
    + _NOT_ABOUT_NOW
    + r")\b",
    re.IGNORECASE,
)
_RECOVERED = re.compile(
    r"\b(?:"
    r"wieder\s+(?:gesund|fit)|(?:bin|fühle\s+mich)\s+(?:\w+\s+)?gesund|nicht\s+mehr\s+krank"
    r"|fieberfrei|symptomfrei|fever[-\s]?free|symptom[-\s]free"
    r"|no\s+longer\s+(?:sick|ill)|not\s+(?:sick|ill)\s+any\s*more"
    r"|kein(?:e|en)?\s+(?:fieber|erkältung|erkaeltung|grippe|symptome)\s+mehr"
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
_ENDED_YESTERDAY = re.compile(
    r"\b(?:bis\s+gestern|until\s+yesterday|till\s+yesterday)\b", re.IGNORECASE
)
_SINCE_RECENTLY = re.compile(
    r"\b(?:seit\s+(?:gestern|heute)|since\s+(?:yesterday|today|this\s+morning))\b",
    re.IGNORECASE,
)
_TIRED_WORD = (
    r"(?:müde|kaputt|platt|erschöpft|erschoepft|ausgelaugt|schlapp|tired|exhausted|"
    r"fatigued|drained|knackered|wiped(?:\s+out)?|worn\s+out|run\s+down)"
)
_SINCE_DAYS = (
    r"seit\s+(?:(?:einer|einigen|mehreren|ein\s+paar|\w+)\s+)?(?:woche|wochen|tagen)"
)
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
_YESTERDAY = re.compile(
    r"\b(?:yesterday|last\s+night|gestern|letzte\s+nacht)\b", re.IGNORECASE
)
_UNTIL = re.compile(
    r"\b(?:until|till|bis)\s+(?:(?:on|zum|am)\s+)?("
    + "|".join(WEEKDAYS)
    + r"|tomorrow|morgen)\b",
    re.IGNORECASE,
)
# The prefix is required: "I ride 5 days a week" is not an illness end. Each
# language's count words go with its own units (review of #927).
_FOR_DAYS = re.compile(
    r"\b(?:for\s+(?:the\s+next\s+)?|next\s+)(\d{1,2}|one|two|three|four)\s+(days?|weeks?)\b"
    r"|\b(?:für\s+(?:die\s+nächsten\s+)?|die\s+nächsten\s+|noch\s+)"
    r"(\d{1,2}|einen|eine|zwei|drei|vier)\s+(tage?|wochen?)\b",
    re.IGNORECASE,
)
# What makes a duration no time left ill (review of #927). With four weeks
# allowed, each misread held intensity back for up to a month.
# A countdown: "noch 3 Wochen bis Weihnachten". "Bis ich wieder fit bin" is the
# illness's own end. "To" is left out: "rest for 3 days to recover" is one too.
_COUNTDOWN_AFTER = re.compile(
    r"\s+(?:bis|until|till|before|vor)\b(?!\s+(?:ich|i)\b)", re.IGNORECASE
)
# An event in the duration's clause: "my race is in the next 2 weeks".
_EVENT_WORD = re.compile(
    r"\b(?:race|rennen|wettkampf|competition|event|marathon|triathlon|gran\s*fondo"
    r"|urlaub|holiday|vacation|trip|reise)\b",
    re.IGNORECASE,
)
# Time already past, just before it: "I've been training for 3 weeks", "I've
# had it for 3 days", "Ich war für 2 Wochen weg".
_PAST_BEFORE = re.compile(
    r"\b(?:been|had|was|were|war|waren|hatte|hatten|gewesen)\s+(?:[\w']+\s+){0,3}$",
    re.IGNORECASE,
)
# A stated illness may run longer than the cap on an open one: "ill for 14
# days" became ten (ai-trainer-ops#115). Four weeks is where it stops being a
# training question.
MAX_STATED_ILLNESS_DAYS = 28
_NUMBER_WORDS = {
    "one": 1,
    "einen": 1,
    "eine": 1,
    "einem": 1,
    "einer": 1,
    "two": 2,
    "zwei": 2,
    "three": 3,
    "drei": 3,
    "four": 4,
    "vier": 4,
}
# Time already ill, not time left: "I've been sick for 3 days", "seit 2 Wochen
# krank". It has to be the illness's time: "I've been training for 3 days, now
# I'm sick" started today (review of #927).
_ILL_NOW_WORD = r"(?:sick|ill(?!-)|unwell|krank|erkältet|erkaeltet)"
_ELAPSED = re.compile(
    rf"\bbeen\s+(?:\w+\s+){{0,2}}?{_ILL_NOW_WORD}\s+(?:\w+\s+){{0,2}}?"
    r"for\s+(\d{1,2}|one|two|three|four)\s+(days?|weeks?)\b"
    r"|\bseit\s+(\d{1,2}|einem|einer|zwei|drei|vier)\s+(tag(?:en)?|wochen?)\s+(?:\w+\s+){0,3}?"
    rf"{_ILL_NOW_WORD}"
    rf"|{_ILL_NOW_WORD}\s+(?:\w+\s+){{0,2}}?seit\s+(\d{{1,2}}|einem|einer|zwei|drei|vier)"
    r"\s+(tag(?:en)?|wochen?)\b",
    re.IGNORECASE,
)
_SINCE_DAY_BEFORE = re.compile(
    r"\b(?:seit\s+vorgestern|since\s+the\s+day\s+before\s+yesterday)\b", re.IGNORECASE
)
_SINCE_YESTERDAY = re.compile(
    r"\b(?:seit\s+gestern|since\s+(?:yesterday|last\s+night))\b", re.IGNORECASE
)
# A clause that is the illness's "since": "Since yesterday, I'm sick", "Ich bin
# krank, schon seit gestern".
_LEADS_WITH_SINCE = re.compile(
    r"\s*(?:(?:schon|erst|already)\s+)?(?:seit|since)\b", re.IGNORECASE
)
_CLAUSE_MARK = re.compile(r"[,;]")
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
    message = message.replace("’", "'")
    return [
        s.group().strip()
        for s in _SENTENCE.finditer(message)
        if s.group().strip() and not s.group().rstrip().endswith("?")
    ]


def _count(word: str) -> int | None:
    """A number the patterns allow, or None for a word the list does not know."""
    return int(word) if word.isdigit() else _NUMBER_WORDS.get(word.lower())


def _days(count: str, unit: str) -> int | None:
    """``count`` days or weeks, in days."""
    number = _count(count)
    if number is not None and unit.lower().startswith(("week", "woche")):
        return number * 7
    return number


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Where the comma- or semicolon-delimited clause around ``text[start:end]`` lies."""
    left = max(text.rfind(",", 0, start), text.rfind(";", 0, start)) + 1
    ends = [i for i in (text.find(",", end), text.find(";", end)) if i >= 0]
    return left, min(ends, default=len(text))


def _stated_days(text: str) -> int | None:
    """The days left ill that ``text`` states: no countdown, no time past, and
    no more than ``MAX_STATED_ILLNESS_DAYS``. A duration that fails one of these
    leaves the next one in the sentence to be read (review of #927)."""
    for match in _FOR_DAYS.finditer(text):
        left, right = _clause_bounds(text, match.start(), match.end())
        if (
            _COUNTDOWN_AFTER.match(text, match.end())
            or _EVENT_WORD.search(text, left, right)
            or _PAST_BEFORE.search(text, left, match.start())
        ):
            continue
        days = _days(*(group for group in match.groups() if group))
        if days is not None and 0 < days <= MAX_STATED_ILLNESS_DAYS:
            return days
    return None


def _illness_end(sentence: str, today: date) -> date | None:
    until = _UNTIL.search(sentence)
    if until:
        word = until.group(1).lower()
        if word in ("tomorrow", "morgen"):
            return today + timedelta(days=1)
        return _next_weekday(today, WEEKDAYS[word])
    # Time already ill is no time left; a remainder said beside it still is:
    # "Ich bin seit 3 Tagen krank, noch 2 Tage".
    days = _stated_days(_ELAPSED.sub(" ", sentence))
    return None if days is None else today + timedelta(days=days - 1)


def _illness_context(sentence: str, illness: re.Match[str]) -> str:
    """The clause that names the illness, with a "since …" clause beside it.

    In "I'm sick, I haven't run since yesterday" the date is the running's
    (review of #927).
    """
    clauses = _CLAUSE_MARK.split(sentence)
    at = len(_CLAUSE_MARK.findall(sentence, 0, illness.start()))
    beside = [
        clauses[i]
        for i in (at - 1, at + 1)
        if 0 <= i < len(clauses) and _LEADS_WITH_SINCE.match(clauses[i])
    ]
    return ", ".join([clauses[at], *beside])


def _illness_start(sentence: str, today: date, illness: re.Match[str]) -> date:
    """The day an illness began, when the sentence says (ai-trainer-ops#115).

    "Ich bin seit gestern krank" was stored as starting today. An open illness
    expires from the day it was last said (``said_on``), so an early start
    shortens nothing: "sick for 14 days" starts 14 days back (review of #927).
    """
    context = _illness_context(sentence, illness)
    if _SINCE_DAY_BEFORE.search(context):
        return today - timedelta(days=2)
    if _SINCE_YESTERDAY.search(context):
        return today - timedelta(days=1)
    elapsed = _ELAPSED.search(sentence)
    if elapsed:
        days = _days(*(group for group in elapsed.groups() if group))
        if days:
            return today - timedelta(days=days)
    return today


# Below the neck: fever, flu, a stomach bug, body aches, the chest. Not when
# denied ("kein Fieber", "no fever") in the words just before.
_SYSTEMIC = re.compile(
    r"\b(?:fieber(?!frei)\w*|fever(?!-?free)\w*|febrile|grippe|influenza|flu|covid\w*|corona"
    r"|magen-?darm\w*|gliederschmerz\w*|body\s*aches?|bronchitis"
    r"|lungenentzündung|pneumonia|3[89](?:[.,]\d)?\s*(?:°|grad\b))",
    re.IGNORECASE,
)
# Within the clause only: in "Not great, fever since Monday" the "not" is
# about something else (review of #1248).
_DENIED = re.compile(
    r"\b(?:kein\w*|ohne|no|not|without)\b(?:[^\S\n]+\w+){0,2}[^\S\n]*$",
    re.IGNORECASE,
)
# A fever word about someone or something else: "Meine Frau hat die Grippe",
# "I skipped my flu shot" (review of #1248).
_NOT_MINE = re.compile(
    r"\b(?:frau|mann|partner\w*|freund\w*|kind\w*|sohn|tochter|mutter|vater"
    r"|kolleg\w*|wife|husband|kids?|children|son|daughter|mother|father|colleague\w*"
    r"|impf\w*|shot|jab|vaccin\w*)\b",
    re.IGNORECASE,
)
# …unless the athlete says it is theirs: "Mein Mann hat mich angesteckt, ich
# habe Fieber" (re-review of #1248).
_MY_FEVER = re.compile(
    r"\b(?:ich\s+(?:habe|hab|hatte)\s+(?:\w+\s+){0,2}(?:fieber|grippe)"
    r"|i(?:\s+(?:have|had|got)|'ve\s+got)\s+(?:\w+\s+){0,2}(?:fever|flu|temperature)"
    r"(?!\s+(?:shot|jab|vaccin\w*)))",
    re.IGNORECASE,
)


def _systemic(sentence: str) -> bool:
    for match in _SYSTEMIC.finditer(sentence):
        if not _DENIED.search(sentence[max(0, match.start() - 30) : match.start()]):
            return True
    return False


def _with_fever(illness: str, message: str) -> bool:
    """The illness sentence names fever, or another sentence does that is about
    the athlete: "Ich bin krank. Fieber seit gestern." but not "Ich bin
    erkältet. Meine Frau hat die Grippe."."""
    if _systemic(illness):
        return True
    return any(
        _systemic(sentence)
        and (not _NOT_MINE.search(sentence) or _MY_FEVER.search(sentence))
        for sentence in _statements(message)
        if sentence != illness
    )


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
            found.append(
                StatedState(
                    KIND_FEVER if _with_fever(sentence, message) else KIND_ILL,
                    _illness_start(sentence, today, match),
                    _illness_end(sentence, today),
                )
            )
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
        if not (
            _REDUCED_NEXT_DAY.search(sentence) or _REDUCED_SAME_DAY.search(sentence)
        ):
            continue
        named = [
            date.fromisoformat(iso) for iso, _ in _target_dates(sentence.lower(), today)
        ]
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


def _states_an_illness(sentence: str) -> bool:
    """Whether any clause of ``sentence`` says the athlete is ill now.

    Per clause, because "Ich bin nicht krank, aber ich habe Fieber" negates one
    and states the other.
    """
    for clause in re.split(r",|;|\s+(?:aber|but|doch|however)\s+", sentence):
        match = _ILL.search(clause)
        if (
            match
            and not _NOT_ILL.search(clause)
            and not _HYPOTHETICAL.search(clause, 0, match.start())
        ):
            return True
    return False


def says_not_ill(message: str) -> bool:
    """The athlete taking an illness back: it never was (ai-trainer-ops#115).

    Not in a hypothetical sentence ("I hope I'm not sick tomorrow"), and not in
    a message that states an illness too: a take-back deletes the illness.
    """
    sentences = _statements(message)
    if any(_states_an_illness(sentence) for sentence in sentences):
        return False
    return any(
        _NOT_ILL_AT_ALL.search(sentence) and not _HYPOTHETICAL.search(sentence)
        for sentence in sentences
    )


# "Mein Arzt hat grünes Licht gegeben", "der Kardiologe sagt, alles in
# Ordnung", "the doctor cleared me". A doctor's word in the same sentence as an
# all-clear; not a plan to go ("ich gehe morgen zum Arzt") or a question.
_DOCTOR = re.compile(
    r"\b(?:arzt|ärztin|ärzte?|hausarzt|hausärztin|kardiolog\w*|internist\w*"
    # People, not places: "In der Klinik war es voll, aber alles gut" is no
    # all-clear (review of #1253).
    r"|sportmediziner\w*|doc|doctor|gp|cardiologist|physician)\b",
    re.IGNORECASE,
)
_ALL_CLEAR = re.compile(
    r"\b(?:grünes\s+licht|freigegeben|freigabe|entwarnung|alles\s+(?:in\s+ordnung|ok|okay|gut)"
    r"|nichts\s+gefunden|unauffällig|darf\s+wieder|kann\s+wieder|ohne\s+befund"
    r"|cleared|green\s+light|all\s+clear|nothing\s+(?:wrong|found)|(?:is|was|all)\s+fine"
    r"|i'?m\s+fine\s+to|ok(?:ay)?\s+to\s+(?:ride|train)|can\s+(?:ride|train)\s+again)",
    re.IGNORECASE,
)
_NOT_YET = re.compile(
    r"\b(?:noch\s+nicht|nicht|kein\w*|not|no|never|\w+n't|cannot|cant|hoffe\w*|hope\w*"
    r"|wenn|if|ob|whether"
    r"|morgen|tomorrow|nächste\w*|next|termin|appointment|will|werde?)\b",
    re.IGNORECASE,
)


def says_cleared(message: str) -> bool:
    """The athlete says a doctor has checked the warning sign and cleared them."""
    return any(
        _DOCTOR.search(sentence)
        and (clear := _ALL_CLEAR.search(sentence))
        and not _NOT_YET.search(sentence[: clear.start()])
        for sentence in _statements(message)
    )


def says_recovered(message: str) -> bool:
    """A statement that the athlete is well again."""
    return any(_RECOVERED.search(sentence) for sentence in _statements(message))


def effective_end(state: StatedState) -> date:
    """The last day a state holds; an open illness lasts ``OPEN_ILLNESS_DAYS`` from its last mention."""
    if state.ends_on is not None:
        return state.ends_on
    said = state.said_on or state.starts_on
    if state.kind == KIND_CLEARANCE:
        return said + timedelta(days=OPEN_CLEARANCE_DAYS - 1)
    if not is_ill(state.kind):
        return state.starts_on
    return said + timedelta(days=OPEN_ILLNESS_DAYS - 1)


def return_days(kind: str, starts_on: date, ends_on: date | None) -> int:
    """Easy days after an illness ends. After a fever, at least as many as the
    illness lasted: a week of flu is no 3-day cold (ai-trainer-ops#338). An
    illness with no stated end has no known length: the usual days (review of
    #1248)."""
    if kind == KIND_FEVER and ends_on is not None:
        return max(RETURN_EASY_DAYS, (ends_on - starts_on).days + 1)
    return RETURN_EASY_DAYS if is_ill(kind) else 0


def still_relevant(state: StatedState, today: date) -> bool:
    """Whether a state still says anything about the plan from ``today``."""
    end = effective_end(state)
    tail = return_days(state.kind, state.starts_on, state.ends_on)
    return end + timedelta(days=tail) >= today


def label(kind: str, starts_on: date, ends_on: date | None) -> str:
    """How the reply names a captured state."""
    if kind == KIND_CLEARANCE:
        return (
            f"Warning sign on {starts_on.isoformat()}: no training until a doctor "
            "has checked it"
        )
    if kind == KIND_FEVER:
        if ends_on is None:
            return f"Ill with fever since {starts_on.isoformat()}: no training"
        return f"Ill with fever until {ends_on.isoformat()}: no training"
    if kind == KIND_ILL:
        if ends_on is None:
            return f"Ill since {starts_on.isoformat()}"
        return f"Ill until {ends_on.isoformat()}"
    if kind == KIND_TIRED:
        return f"Tired for days: easy until {(ends_on or starts_on).isoformat()}"
    return f"Less to give on {starts_on.isoformat()}"


def retracted_label() -> str:
    """How the reply names an illness the athlete took back."""
    return "Not ill: the illness is removed"


def cleared_label() -> str:
    """How the reply names a warning sign a doctor has now checked."""
    return "Cleared by a doctor: the plan goes on"


def recovered_label(ends_on: date, easy_days: int = RETURN_EASY_DAYS) -> str:
    """How the reply names an illness the athlete just said is over."""
    return (
        f"Well again: easy days until "
        f"{(ends_on + timedelta(days=easy_days)).isoformat()}"
    )
