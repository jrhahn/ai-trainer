"""Structured athlete availability constraints."""

from __future__ import annotations

import re
from datetime import date, timedelta

WEEKDAYS = {
    "monday": 0,
    "montag": 0,
    "tuesday": 1,
    "dienstag": 1,
    "wednesday": 2,
    "mittwoch": 2,
    "thursday": 3,
    "donnerstag": 3,
    "friday": 4,
    "freitag": 4,
    "saturday": 5,
    "samstag": 5,
    "sonnabend": 5,
    "sunday": 6,
    "sonntag": 6,
}

WEEKDAY_LABELS = {
    0: "monday",
    1: "tuesday",
    2: "wednesday",
    3: "thursday",
    4: "friday",
    5: "saturday",
    6: "sunday",
}

UNAVAILABLE_PATTERNS = (
    r"\bkeine\s+zeit\b",
    r"\bkeinen?\s+zeit\b",
    r"\bgeht\s+nicht\b",
    r"\bunavailable\b",
    r"\bno\s+time\b",
    r"\bnot\s+available\b",
    r"\b(?:don'?t|do\s+not)\s+have\s+(?:the\s+|any\s+)?time\b",
)

# Unavailability that is about training by itself, with no training word
# needed beside it (ai-trainer-ops#110). The German "kann nicht" counts only
# when what cannot happen is the training ("kann ich nicht fahren") or nothing
# at all ("Samstag kann ich nicht, bin auf einer Hochzeit"). "Montag kann ich
# nicht ins Büro, also habe ich Zeit zum Fahren" used to block Monday.
UNAVAILABLE_TRAINING_PATTERNS = (
    r"\bcan(?:not|'t)\s+(?:train|ride|work\s*out)\b",
    r"\b(?:can(?:not|'t)|won'?t\s+be\s+able\s+to)\s+make\s+it\b",
    r"\bi\s+can(?:not|'t)\s*(?=$|[,;:.!-])",
    r"\bkann\s+(?:ich\s+)?(?:\w+\s+){0,2}?nicht\s+"
    r"(?:(?!(?:zur|zum|ins|in|nach)\b)\w+\s+){0,3}?"
    r"(?:trainieren|fahren|radeln|laufen|rennen)\b",
    r"\bkann\s+ich\s+(?:\w+\s+){0,2}?nicht\s*"
    r"(?=$|[,;:.!-]|\s+(?:weil|da|denn|wegen|bin|habe|hab|muss|ich)\b)",
    r"\b(?:fahre|trainiere|laufe|radle)\s+ich\s+(?:\w+\s+){0,2}?nicht\b",
)

# The day itself ruled out, with no training word beside it (ai-trainer-ops#316):
# "Montag und Donnerstag gehen nicht", "Donnerstag fällt aus", "Thursday is
# out". The coach was told twice that Thursday was out, and nothing was stored.
# Only where the phrase closes the statement: "Donnerstag geht nicht so gut",
# "geht es mir nicht gut" and "das Rad ist raus aus der Werkstatt" say
# something else.
_ENDS = (
    r"\s*(?=$|[,;:.!-]|\s+(?:weil|da|denn|wegen|bin|habe|hab|muss|ich|leider"
    r"|sonst|also)\b)"
)
_ENDS_EN = r"\s*(?=$|[,;:.!-]|\s+(?:for\s+me|because|as|since|i'?m|i\s+am|i'?ll|so)\b)"
DAY_RULED_OUT_PATTERNS = (
    r"\b(?:geht|gehen|passt|passen|klappt|klappen)\s+(?:\w+\s+){0,5}?nicht" + _ENDS,
    r"\bschaff(?:e)?\s+ich\s+(?:\w+\s+){0,2}?nicht" + _ENDS,
    r"\b(?:fällt|fallen|faellt)\s+(?:\w+\s+){0,2}?aus" + _ENDS,
    r"\b(?:ist|sind)\s+(?:\w+\s+){0,2}?raus" + _ENDS,
    r"\bkeine?n?\s+zeit" + _ENDS,
    r"\bwird\s+(?:das\s+|es\s+)?nichts\s+(?:mit|aus)\s+(?:dem\s+)?training\b",
    r"\b(?:is|are)\s+out(?:\s+of\s+the\s+question)?" + _ENDS_EN,
    r"\b(?:doesn'?t|don'?t|does\s+not|do\s+not|won'?t)\s+work" + _ENDS_EN,
)

# A limit on how long, not on whether: "Sonntag kann ich nicht so lange
# fahren, nur 1 Stunde" asks for a short ride, and blocking the day is the
# opposite of what was said (ai-trainer-ops#110).
_DURATION_LIMIT = re.compile(
    r"\bnicht\s+(?:so\s+|zu\s+)?(?:lange?|länger|laenger)\b"
    r"|\bnur\s+(?:\w+\s+)?\d"
    r"|\bnot\s+(?:too\s+|that\s+|so\s+|very\s+)?long\b"
    r"|\bonly\s+(?:\w+\s+)?\d"
)

# The athlete saying they have time is the opposite of a block: "also habe ich
# Zeit zum Fahren", "so I can ride all day". Negated forms ("I don't have
# time") are unavailability and are checked for before this is consulted.
# One word that is not a negation, so "habe keine Zeit" never reads as "habe Zeit".
_NOT_NEGATION_WORD = r"(?!(?:no|not|kein\w*|nicht|nie)\b)\w+\s+"
_POSITIVE_AVAILABILITY = re.compile(
    r"\b(?:have|got|habe|hab|hätte|haette)\s+(?:" + _NOT_NEGATION_WORD + r"){0,2}?"
    r"(?:time|zeit)\b"
    r"|\bcan\s+(?:" + _NOT_NEGATION_WORD + r")?(?:ride|train|do)\b"
    r"|\bkann\s+ich\s+(?:"
    + _NOT_NEGATION_WORD
    + r"){0,3}?(?:fahren|trainieren|radeln)\b"
)
_NEGATION_BEFORE = re.compile(
    r"\b(?:no|not|never|keine?n?|nicht|nie"
    r"|(?:do|does|did|can|could|wo|would|is|are|have|has|had)n'?t)\s+(?:\w+\s+)?$"
)

# Away, with no training word and no "can't" beside it: "Nächsten Sonntag bin
# ich weg", "I'm away on Saturday" captured nothing (the gap #63 noted). Only the
# forms that leave no ride: "unterwegs" and "travelling" also describe a ride,
# and a wedding may still leave a morning spin. A filler word is never a
# negation, so "bin ich nicht weg" and "wieder da" stay open.
AWAY_PATTERNS = (
    r"\bbin\s+(?:ich\s+)?(?:" + _NOT_NEGATION_WORD + r"){0,2}?"
    r"(?:weg|verreist|nicht\s+da|nicht\s+zu\s*hause)\b",
    r"\b(?:i'?m|i\s+am|i'?ll\s+be|i\s+will\s+be)\s+"
    r"(?:away|not\s+around|not\s+home|out\s+of\s+town)\b",
    r"\bi\s+won'?t\s+be\s+(?:around|home|here)\b",
)

# A day the athlete says is off (ai-trainer-ops#72). Unlike "no time" these need
# no training word beside them: "Thursday stays free", "tomorrow is a rest day",
# "Friday off" say it on their own, and for weeks the coach kept planning those
# days because nothing captured them. Only statements count: "tomorrow off?" is
# the athlete asking, and the coach may answer.
OFF_DAY_PATTERNS = (
    r"\b(?:day\s+off|off\s+day|rest(?:ing)?\s*day|ruhetag)\b",
    # German "frei" alone is ambiguous: "Samstag habe ich frei" means the
    # athlete is free to ride. Only the forms that keep a day clear count.
    r"\b(?:bleibt|bleiben|muss)\s+(?:\w+\s+)?frei\b",
    r"\bfrei\s+(?:bleiben|halten|lassen)\b",
    r"\b(?:is|are)\s+off\b(?!\s+work)",
    r"\boff\s*(?=$|[,;:-]|\s+(?:and|und)\b)",
)
# Off work is the opposite of a day off training: "I'm off work on Friday so I
# can ride more", "Thursday is my day off, so I have time for a long ride"
# (ai-trainer-ops#110). "I'm off" was dropped from the patterns above for the
# same reason; "Friday is off" still counts.
_OFF_WORK = re.compile(
    r"\boff\s+(?:work|the\s+job)\b"
    r"|\b(?:my|mein(?:en)?)\s+(?:day\s+off|freie[rn]?\s+tag)\b"
    r"|\b(?:have|got|take|taking)\s+(?:the\s+|a\s+)?day\s+off\b"
    r"|\barbeitsfrei\b|\bfrei\s+von\s+der\s+arbeit\b"
)
# "the power meter was off" is not a day off.
_NOT_A_DAY_OFF = re.compile(r"\b(?:was|war|were|waren)\s+off\b")

HOLIDAY_PATTERNS = (
    r"\burlaub\b",
    r"\bferien\b",
    r"\bholiday\b",
    r"\bvacation\b",
    r"\bnicht\s+trainieren\b",
    r"\bcan(?:not|'t)\s+train\b",
)

# A holiday the bike comes along to is not an absence (ai-trainer-ops#110):
# "vom 4. bis 26. November im Urlaub auf Mallorca, mit Rad!" blocked 23 days.
_BIKE_ALONG = re.compile(
    r"\bmit\s+(?:dem\s+|meinem\s+)?(?:rad|fahrrad|rennrad|renner|bike|gravelbike)\b"
    r"|\b(?:rad|fahrrad|bike)\s+(?:ist\s+)?(?:dabei|mit)\b"
    r"|\bnehme\s+(?:das|mein)\s+(?:rad|fahrrad|bike)\s+mit\b"
    r"|\bwith\s+(?:my\s+|the\s+)?bike\b"
    r"|\b(?:take|taking|bring|bringing)\s+(?:my|the)\s+bike\b"
)

MONTHS = {
    "januar": 1,
    "january": 1,
    "jan": 1,
    "februar": 2,
    "february": 2,
    "feb": 2,
    "märz": 3,
    "maerz": 3,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "may": 5,
    "juni": 6,
    "june": 6,
    "jun": 6,
    "juli": 7,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "october": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "december": 12,
    "dez": 12,
    "dec": 12,
}
_MONTH = "(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b"
_DATE_RANGE = re.compile(
    r"\b(?:ab|vom|von|from|between)\s+(?:dem\s+|the\s+)?(\d{1,2})\.?\s*(?:"
    + _MONTH
    + r"\s*)?"
    r"(?:bis|to|until|till|and|-|–)\s+(?:zum\s+|the\s+)?(\d{1,2})\.?\s*" + _MONTH
)
# ponytail: one constraint row per day; a range longer than this is a typo or a
# season, not a trip, and would flood the prompt. A range row would lift the cap.
MAX_RANGE_DAYS = 45

TRAINING_CONTEXT_PATTERNS = (
    r"\btraining\b",
    r"\btrainieren\b",
    r"\bfahren\b",
    r"\bworkout\b",
    r"\bride\b",
    r"\beinheit\b",
)

# High-confidence phrasing for a required long endurance session.
LONG_SESSION_PATTERNS = (
    r"\blange[rns]?\s+(?:einheit|ausfahrt|tour|runde|ride)\b",
    r"\blong\s+(?:ride|session|workout|endurance|run)\b",
    r"\blanger?\s+ride\b",
)

DEFAULT_LONG_SESSION_MINUTES = 120

# A limit on every session, not on one day: "Max 1 hour per session",
# "höchstens 45 Minuten pro Einheit" (ai-trainer-ops#185). Mia's limit was kept
# in the coach memory only, so the chat shortened one Sunday and left 75- and
# 105-minute sessions in the plan. Only with "per session" or the like: "max 2
# hours" alone may be a day's or a week's.
SESSION_CAP = "max_session_minutes"
_CAP_WORD = (
    r"(?:max(?:imum|imal)?\.?|at\s+most|no\s+more\s+than|not\s+more\s+than"
    r"|h(?:ö|oe)chstens|nicht\s+(?:mehr|l(?:ä|ae)nger)\s+als)"
)
_CAP_DURATION = (
    r"(?:\d+(?:[.,]\d+)?\s*(?:h\b|hours?|stunden?|std\b|min(?:uten|utes?)?\b)"
    r"|(?:an|one|a|half\s+an|eine[rn]?|einer\s+halben|eine\s+halbe|anderthalb)\s+(?:hours?|stunden?))"
)
_PER_SESSION = (
    r"(?:per|pro|each|je|jede[nrs]?)\s+"
    r"(?:session|ride|workout|training|trainingseinheit|einheit|fahrt|ausfahrt)\b"
)
_SESSION_CAP = re.compile(
    rf"\b{_CAP_WORD}\s+(?:of\s+)?(?P<before>{_CAP_DURATION})\s+{_PER_SESSION}"
    rf"|\b(?P<after>{_CAP_DURATION})\s+(?:max(?:imum)?|at\s+most|h(?:ö|oe)chstens|maximal)\s+{_PER_SESSION}"
)
_CAP_WORDS = {
    "half an hour": 30,
    "einer halben stunde": 30,
    "eine halbe stunde": 30,
    "anderthalb stunden": 90,
}
# A limit on named weekdays, said as what the athlete has: "Dienstag und
# Donnerstag habe ich nur eine Stunde", "on Tuesdays I only have 45 minutes",
# "Dienstag max. 1 Stunde" (ai-trainer-ops#231). Lena said it, the coach said
# "gut zu wissen", and her Tuesday stayed at 75 minutes. A wish for a session
# ("Dienstag nur eine Stunde locker") names no limit and is left to the coach.
_WEEKDAY_CAP = re.compile(
    rf"(?:\b(?:hab(?:e)?\s+ich|ich\s+hab(?:e)?|kann\s+ich|ich\s+kann)\s+(?:\w+\s+){{0,2}}?nur"
    r"|\bi\s+(?:(?:usually|always|generally|normally|typically)\s+)?"
    r"(?:only\s+(?:ever\s+)?have|have\s+only|can\s+only\s+(?:do|ride|train))"
    rf"|\b{_CAP_WORD})"
    r"\s+(?:(?:time\s+for|zeit\s+f(?:ü|ue)r)\s+)?"
    rf"(?P<before>{_CAP_DURATION})"
)
_WEEKDAY_NAMES = re.compile(r"\b(" + "|".join(WEEKDAYS) + r")(?:s|en)?\b")
# Such a limit holds for the next such day only, unless the athlete says it
# holds for every one (owner's decision of 2026-10-24, ai-trainer-ops#295). Said
# for every one: these words, or the weekday as an adverb or in the plural
# ("samstags", "an Samstagen", "on Saturdays").
_EVERY_SUCH_DAY = re.compile(
    r"\b(?:immer|generell|grunds(?:ä|ae)tzlich|normalerweise|in\s+der\s+regel|meistens"
    r"|regelm(?:ä|ae)(?:ß|ss)ig|always|ever|generally|usually|normally|typically)\b"
    r"|\b(?:jede[nmrs]?|every|each)\s+(?:woche|week|" + "|".join(WEEKDAYS) + r")\b"
)
# Outside these a limit is a typo or a joke, not a schedule.
_CAP_RANGE = (15, 360)

# A long ride the athlete rules out, has already done, or complains about is
# not one they asked for (ai-trainer-ops#110). "Keine lange Ausfahrt am
# Samstag, ich bin auf einer Hochzeit" pinned a two-hour ride on the wedding
# day, and "Am Sonntag war die lange Ausfahrt super" pinned next Sunday.
_NEGATED_LONG = re.compile(
    r"\b(?:kein(?:e|en|em|er|es)?|nicht|no|not|without|ohne|statt|anstatt"
    r"|instead\s+of|skip(?:ping)?|weniger|less)\s+(?:\w+\s+){0,2}?(?:lange[rns]?|long)\b"
)
_PAST_DAY_NAMES = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_PAST = re.compile(
    r"\b(?:war|waren|was|were|did|hatte|hatten|rode|ridden|went|gestern|yesterday"
    r"|vorgestern)\b"
    r"|\b(?:letzte[nmrs]?|last)\s+(?:woche|week|wochenende|weekend|"
    + _PAST_DAY_NAMES
    + r")\b"
    r"|\b(?:have|has|had)\s+(?:\w+\s+){0,2}?(?:done|ridden|finished|completed)\b"
    # The German perfect: "hat gut getan", "bin ... gefahren". "geplant" and
    # "gedacht" state a plan, and "gegen", "genau", "gerne" are no participles.
    r"|\b(?:hat|habe|hab|bin|ist|sind|haben)\b(?:\s+\w+){0,6}?\s+"
    r"(?:getan|ge(?!plant\b|dacht\b|wünscht\b|wuenscht\b|gen\b|nau\b|rne?\b|nug\b"
    r"|ht\b|hen\b|meinsam|mütlich|muetlich|samt|sund)\w+(?:t|en))\b"
)
_COMPLAINT = re.compile(
    r"\bzu\s+(?:viel|lang|lange|hart|anstrengend|heftig)\b"
    r"|\btoo\s+(?:much|long|hard)\b"
    r"|\b(?:exhausting|brutal|anstrengend|kaputt|platt)\b"
)

# Phrasing that asks to lift/remove a previously captured availability
# constraint. The coach's override note explicitly invites this ("Let me know if
# you'd like to lift that constraint"), so the athlete's reply must be honoured
# instead of silently keeping the constraint active (#437). Kept high-confidence
# and paired with a "constraint" reference so ordinary chat ("remove the warmup")
# never trips it.
LIFT_PATTERNS = (
    r"\b(?:lift|remove|cancel|drop|clear|delete)\b[^.?!]*\b(?:constraint|restriction|requirement|pin|block)\b",
    r"\b(?:hebe?|entferne?|streich(?:e|en)?|l(?:ö|oe)sch(?:e|en)?)\b[^.?!]*\b(?:sperre|einschr(?:ä|ae)nkung|vorgabe|constraint|beschr(?:ä|ae)nkung)\b",
    r"\bsperre\b[^.?!]*\bauf\b",  # "hebe die Sperre auf"
)

# Boundaries that separate independent statements within one message. Splitting
# on these keeps each intent tied to the days named in the *same* clause, so a
# message like "tomorrow off? saturday long endurance" no longer pins a required
# endurance session onto the rest day (the whole-message scan used to apply every
# detected intent to every date it found — see #412). Note that "," and "and"
# are deliberately *not* boundaries: they join same-intent phrasing such as
# "monday and today" or "long ride on saturday, 3 hours".
_CLAUSE_BOUNDARY = re.compile(r"[?!.;]+|\s+but\s+|\s+aber\s+|\s+however\s+")


def _split_clauses(normalized: str) -> list[tuple[str, bool]]:
    """Clauses, each with whether it is a question."""
    clauses: list[tuple[str, bool]] = []
    start = 0
    for boundary in _CLAUSE_BOUNDARY.finditer(normalized):
        clauses.append(
            (normalized[start : boundary.start()].strip(), "?" in boundary.group())
        )
        start = boundary.end()
    clauses.append((normalized[start:].strip(), False))
    return [(text, question) for text, question in clauses if text]


_DAY_MENTION = re.compile(
    r"\b(?:" + "|".join(WEEKDAYS) + r")s?\b|\b(?:morgen|tomorrow|heute|today)\b"
)
# "Montag und Donnerstag", and with an article: "am Montag und am Donnerstag"
# lost Monday's intent to the joining "am" (ai-trainer-ops#316).
_JOINER_ONLY = re.compile(
    r"^[\s,&+/]*(?:and|und|or|oder|sowie)?[\s,&+/]*(?:am|an|on|the|den|dem)?[\s,&+/]*$"
)


def _day_segments(clause: str) -> list[str]:
    """Split a clause so each part carries the days it names and what it says.

    "tomorrow gym and Friday off, Saturday the long one" names three days with
    three intents. Scanned as one clause, the long ride pinned all three as
    required sessions, Friday included (ai-trainer-ops#72). A part runs from one
    day mention to the next; text before the first day belongs to the first; a
    day joined to the next by only "and" or "," shares the next one's intent, so
    "Saturday and Sunday a long ride" still pins both.
    """
    mentions = list(_DAY_MENTION.finditer(clause))
    if len(mentions) < 2:
        return [clause]
    ends = [mention.start() for mention in mentions[1:]] + [len(clause)]
    merged: list[str] = []
    for index in range(len(mentions) - 1, -1, -1):
        start = 0 if index == 0 else mentions[index].start()
        segment = clause[start : ends[index]]
        if merged and _JOINER_ONLY.match(clause[mentions[index].end() : ends[index]]):
            merged[-1] = segment + merged[-1]
        else:
            merged.append(segment)
    return list(reversed(merged))


# A sentence ends at "?", "!", ";" or a full stop that does not close a date
# number ("4." in "4. November" is part of the date).
_SENTENCE_END = re.compile(r"(?<!\d)\.|[?!;]")


def _sentence_around(text: str, start: int, end: int) -> tuple[str, bool]:
    """The sentence holding ``text[start:end]`` and whether it is a question."""
    before = [m.end() for m in _SENTENCE_END.finditer(text, 0, start)]
    after = _SENTENCE_END.search(text, end)
    sentence_start = before[-1] if before else 0
    sentence_end = after.end() if after else len(text)
    return text[sentence_start:sentence_end], bool(after and after.group() == "?")


def _date_range(normalized: str, today: date) -> list[date]:
    """Every day of a stated absence such as "4 to 26 November on holiday"."""
    days: list[date] = []
    for match in _DATE_RANGE.finditer(normalized):
        sentence, is_question = _sentence_around(normalized, match.start(), match.end())
        # A question about a range asks; it does not block (same rule as the
        # clauses below).
        if is_question or not any(
            re.search(pattern, sentence)
            for pattern in HOLIDAY_PATTERNS
            + UNAVAILABLE_PATTERNS
            + UNAVAILABLE_TRAINING_PATTERNS
            + AWAY_PATTERNS
        ):
            continue
        if _BIKE_ALONG.search(sentence):
            continue
        first_day, first_month, last_day, last_month = match.groups()
        last_month_number = MONTHS[last_month]
        first_month_number = MONTHS[first_month] if first_month else last_month_number
        # Every date built here can fail: 30 February, or 29 February moved
        # into a year without one. A range that cannot be dated is skipped.
        try:
            first = date(today.year, first_month_number, int(first_day))
            last = date(today.year, last_month_number, int(last_day))
            if last < first:
                first = first.replace(year=first.year - 1)
            if last < today:
                first, last = (
                    first.replace(year=first.year + 1),
                    last.replace(year=last.year + 1),
                )
        except ValueError:
            continue
        if (last - first).days >= MAX_RANGE_DAYS:
            continue
        day = max(first, today)
        while day <= last:
            days.append(day)
            day += timedelta(days=1)
    return days


def _next_weekday(today: date, weekday: int) -> date:
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta)


# "Nächsten Sonntag", said on a Sunday, is a week away: it was stored as today.
_NEXT_BEFORE = re.compile(
    r"\b(?:next|coming|n(?:ä|ae)chste[nmrs]?|kommende[nmrs]?)\s+$"
)


# A number after one of these is a time of day: "Start 8h" read as an
# eight-hour ride (ai-trainer-ops#110).
_TIME_OF_DAY_BEFORE = re.compile(
    r"\b(?:start|starte|startet|um|ab|at|from|gegen|bis|by|until|abfahrt|treffpunkt"
    r"|treffen|los)\s*$"
)


def _parse_duration_minutes(normalized: str) -> int | None:
    """Best-effort minutes from phrasing like '3 hours', '2,5 std', '90 min'."""
    for hours in re.finditer(
        r"(\d+(?:[.,]\d+)?)\s*(?:h\b|hours?|stunden?|std\b)", normalized
    ):
        if _TIME_OF_DAY_BEFORE.search(normalized[: hours.start()]):
            continue
        return int(round(float(hours.group(1).replace(",", ".")) * 60))
    minutes = re.search(r"(\d+)\s*(?:min\b|minutes?|minuten?)", normalized)
    if minutes:
        return int(minutes.group(1))
    return None


def weekday_label_for(date_iso: str) -> str | None:
    """``"thursday"`` for ``"2026-10-29"``, or ``None`` for a non-date."""
    try:
        return WEEKDAY_LABELS[date.fromisoformat(date_iso).weekday()]
    except ValueError:
        return None


def _session_cap_minutes(
    clause: str, pattern: re.Pattern[str] = _SESSION_CAP
) -> int | None:
    """The per-session limit ``clause`` states, in minutes, or ``None`` (#185)."""
    match = pattern.search(clause)
    if not match:
        return None
    phrase = " ".join((match.group("before") or match.groupdict().get("after")).split())
    minutes = _CAP_WORDS.get(phrase)
    if minutes is None and re.fullmatch(
        r"(?:an|one|a|eine[rn]?)\s+(?:hours?|stunden?)", phrase
    ):
        minutes = 60
    if minutes is None:
        minutes = _parse_duration_minutes(phrase)
    if minutes is None or not _CAP_RANGE[0] <= minutes <= _CAP_RANGE[1]:
        return None
    return minutes


def _target_dates(normalized: str, today: date) -> list[tuple[str, str]]:
    """Return [(date_iso, weekday_label)] for every day mentioned in the text."""
    targets: list[tuple[str, str]] = []
    for name, weekday in WEEKDAYS.items():
        for match in re.finditer(rf"\b{name}s?\b", normalized):
            day = _next_weekday(today, weekday)
            if day == today and _NEXT_BEFORE.search(normalized[: match.start()]):
                day += timedelta(days=7)
            targets.append((day.isoformat(), WEEKDAY_LABELS[weekday]))
    if re.search(r"\b(morgen|tomorrow)\b", normalized):
        day = today + timedelta(days=1)
        targets.append((day.isoformat(), WEEKDAY_LABELS[day.weekday()]))
    if re.search(r"\b(heute|today)\b", normalized):
        targets.append((today.isoformat(), WEEKDAY_LABELS[today.weekday()]))
    # Keep the first weekday label seen per concrete date.
    seen: dict[str, str] = {}
    for date_iso, weekday_label in targets:
        seen.setdefault(date_iso, weekday_label)
    return list(seen.items())


def _has_positive_availability(segment: str) -> bool:
    """Whether the athlete says they have time or can ride, not negated."""
    for match in _POSITIVE_AVAILABILITY.finditer(segment):
        if not _NEGATION_BEFORE.search(segment[: match.start()]):
            return True
    return False


def _asks_for_it(segment: str) -> bool:
    """Whether a long-session mention is a request rather than a no, a report
    or a complaint (ai-trainer-ops#110).

    Judged on the comma-separated part that holds the long session, so a past
    tense or a "was" ("what") in another part does not silence the request.
    """
    parts = [
        part
        for part in re.split(r"[,:]", segment)
        if any(re.search(pattern, part) for pattern in LONG_SESSION_PATTERNS)
    ]
    # Every part that mentions a long session has to ask for it: the part after
    # a day's mention can belong to the next day ("Keine lange Ausfahrt am
    # Samstag, lange Ausfahrt am Sonntag" puts both in Saturday's segment).
    return bool(parts) and all(
        not (
            _NEGATED_LONG.search(part) or _PAST.search(part) or _COMPLAINT.search(part)
        )
        for part in parts
    )


def _segment_constraints(segment: str, today: date, source: str) -> list[dict]:
    targets = _target_dates(segment, today)
    if not targets:
        return []
    constraints: list[dict] = []
    has_unavailable = any(
        re.search(pattern, segment) for pattern in UNAVAILABLE_PATTERNS
    )
    has_training_context = any(
        re.search(pattern, segment) for pattern in TRAINING_CONTEXT_PATTERNS
    )
    unavailable_for_training = any(
        re.search(pattern, segment)
        for pattern in UNAVAILABLE_TRAINING_PATTERNS + DAY_RULED_OUT_PATTERNS
    ) or (
        any(re.search(pattern, segment) for pattern in AWAY_PATTERNS)
        and not _BIKE_ALONG.search(segment)
    )
    is_off_day = (
        _NOT_A_DAY_OFF.search(segment) is None
        and _OFF_WORK.search(segment) is None
        and any(re.search(pattern, segment) for pattern in OFF_DAY_PATTERNS)
    )
    blocks = (
        (has_unavailable and has_training_context) or unavailable_for_training
    ) and not _DURATION_LIMIT.search(segment)
    if (blocks or is_off_day) and not _has_positive_availability(segment):
        for date_iso, weekday_label in targets:
            constraints.append(
                {
                    "constraint_type": "no_training",
                    "constraint_date": date_iso,
                    "weekday": weekday_label,
                    "reason": "Athlete said they are unavailable for training.",
                    "source": source,
                    "expires_on": date_iso,
                }
            )

    has_long_session = any(
        re.search(pattern, segment) for pattern in LONG_SESSION_PATTERNS
    )
    if has_long_session and _asks_for_it(segment):
        min_minutes = _parse_duration_minutes(segment) or DEFAULT_LONG_SESSION_MINUTES
        for date_iso, weekday_label in targets:
            constraints.append(
                {
                    "constraint_type": "required_workout",
                    "constraint_date": date_iso,
                    "weekday": weekday_label,
                    "reason": "Athlete asked for a long endurance session on this day.",
                    "source": source,
                    "expires_on": date_iso,
                    "required_workout": {
                        "workoutType": "endurance",
                        "minDurationMinutes": min_minutes,
                    },
                }
            )
    return constraints


def constraint_label(
    constraint_type: str,
    constraint_date: str | None,
    required_workout: dict | None = None,
    weekday: str | None = None,
) -> str:
    """How the reply names a constraint the chat just stored (ai-trainer-ops#110).

    It is enforced on every plan write, so the athlete sees each one under the
    reply and can remove it, like a captured goal or state.
    """
    if constraint_type == SESSION_CAP:
        minutes = (required_workout or {}).get("maxDurationMinutes")
        if weekday and constraint_date:
            # The next such day only (ai-trainer-ops#295).
            return f"At most {minutes} min on {weekday.capitalize()} {constraint_date} only"
        if weekday:
            return f"At most {minutes} min on {weekday.capitalize()}s"
        return f"At most {minutes} min per session"
    try:
        day = date.fromisoformat(constraint_date or "")
        when = f"{WEEKDAY_LABELS[day.weekday()].capitalize()} {day.isoformat()}"
    except ValueError:
        when = "that day"
    if constraint_type == "required_workout":
        minutes = (required_workout or {}).get("minDurationMinutes")
        return f"Long ride on {when}" + (f", at least {minutes} min" if minutes else "")
    return f"No training on {when}"


def _normalise(text: str) -> str:
    """Casefolded, single-spaced, with typographic apostrophes made plain.

    Phone keyboards write "can’t" and "I’m"; every pattern here is written with
    "'", so those read as no negation at all (review of #923).
    """
    plain = text.replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(plain.casefold().split())


def extract_availability_constraints(
    text: str,
    *,
    today: date,
) -> list[dict]:
    """Extract high-confidence availability constraints from athlete text.

    Handles both negative ("no training on day X") and positive ("a long
    endurance session on day Y") phrasing. Ambiguous schedule preferences remain
    in free-form coach memory until confirmed.
    """
    normalized = _normalise(text)
    if not normalized:
        return []

    source = text.strip()[:500]
    constraints: list[dict] = []

    for day in _date_range(normalized, today):
        constraints.append(
            {
                "constraint_type": "no_training",
                "constraint_date": day.isoformat(),
                "weekday": WEEKDAY_LABELS[day.weekday()],
                "reason": "Athlete said they are away and cannot train.",
                "source": source,
                "expires_on": day.isoformat(),
            }
        )

    # Scan each clause independently so an intent only applies to the days named
    # alongside it, not to every day mentioned anywhere in the message (#412), and
    # each day within a clause to its own part of it (ai-trainer-ops#72). A
    # question pins nothing: "why did the plan drop Sunday's long ride?" is a
    # question about the plan, not a request for one.
    # A limit on every session (#185). "max." abbreviates, it ends no sentence;
    # a limit with a day named ("Sunday max 1 hour per session") is that day's,
    # not every session's: it is stored as that weekday's (#231).
    for clause, is_question in _split_clauses(re.sub(r"\bmax\.", "max", normalized)):
        cap = (
            None
            if is_question or _DAY_MENTION.search(clause)
            else _session_cap_minutes(clause)
        )
        if cap:
            constraints.append(
                {
                    "constraint_type": SESSION_CAP,
                    "constraint_date": None,
                    "weekday": None,
                    "reason": f"Athlete said each session can be at most {cap} minutes.",
                    "source": source,
                    "expires_on": None,
                    # From the day it was said: sessions before it are history,
                    # and a completed one keeps the length it was ridden to
                    # (review of PR #1000).
                    "required_workout": {
                        "maxDurationMinutes": cap,
                        "since": today.isoformat(),
                    },
                }
            )

        # The weekdays this clause names keep their own limit (#231): every such
        # day when the athlete says so, otherwise the next one only (#295).
        for segment in [] if is_question else _day_segments(clause):
            cap = _session_cap_minutes(segment, _WEEKDAY_CAP)
            every = bool(_EVERY_SUCH_DAY.search(segment))
            mentions: dict[str, list[re.Match[str]]] = {}
            for match in _WEEKDAY_NAMES.finditer(segment) if cap else ():
                mentions.setdefault(match.group(1), []).append(match)
            for name, named in mentions.items():
                match = named[0]
                index = WEEKDAYS[name]
                weekday = WEEKDAY_LABELS[index]
                if every or any(m.group(0) != name for m in named):
                    constraints.append(
                        {
                            "constraint_type": SESSION_CAP,
                            "constraint_date": None,
                            "weekday": weekday,
                            "reason": f"Athlete said a session on a {weekday} can be at most {cap} minutes.",
                            "source": source,
                            "expires_on": None,
                            "required_workout": {
                                "maxDurationMinutes": cap,
                                "since": today.isoformat(),
                            },
                        }
                    )
                    continue
                day = _next_weekday(today, index)
                if day == today and _NEXT_BEFORE.search(segment[: match.start()]):
                    day += timedelta(days=7)
                constraints.append(
                    {
                        "constraint_type": SESSION_CAP,
                        "constraint_date": day.isoformat(),
                        "weekday": weekday,
                        "reason": (
                            f"Athlete said a session on {weekday} {day.isoformat()} can be at "
                            f"most {cap} minutes, that day only."
                        ),
                        "source": source,
                        "expires_on": day.isoformat(),
                        "required_workout": {
                            "maxDurationMinutes": cap,
                            "since": today.isoformat(),
                        },
                    }
                )

    for clause, is_question in _split_clauses(normalized):
        if is_question:
            continue
        for segment in _day_segments(clause):
            constraints.extend(_segment_constraints(segment, today, source))

    # Keep the first occurrence per (type, date, weekday).
    by_key: dict[tuple[str, str | None, str | None], dict] = {}
    for item in constraints:
        key = (item["constraint_type"], item["constraint_date"], item.get("weekday"))
        by_key.setdefault(key, item)
    return list(by_key.values())


def extract_constraint_lift(text: str, *, today: date) -> dict:
    """Detect a request to lift a previously captured availability constraint.

    The coach's override note invites the athlete to lift a blocking constraint
    (#437). This recognises that reply and reports which constraints to drop:

    - ``lift``: whether the message asks to lift a constraint at all.
    - ``dates``: ISO dates named alongside the lift request. When empty, the
      caller resolves "that constraint" to the constraint(s) the previous
      override note flagged.

    Detection is scoped to the clause carrying the lift phrasing, so an unrelated
    day mentioned elsewhere in the message is not swept in.
    """
    normalized = _normalise(text)
    if not normalized:
        return {"lift": False, "dates": []}

    dates: list[str] = []
    lift = False
    for clause, _question in _split_clauses(normalized):
        if not any(re.search(pattern, clause) for pattern in LIFT_PATTERNS):
            continue
        lift = True
        for date_iso, _weekday in _target_dates(clause, today):
            if date_iso not in dates:
                dates.append(date_iso)
    return {"lift": lift, "dates": dates}
