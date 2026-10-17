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
    r"\bkann\s+(?:ich\s+)?(?:\w+\s+){0,2}?nicht\s+(?:\w+\s+)?"
    r"(?:trainieren|fahren|radeln|laufen|rennen)\b",
    r"\bkann\s+ich\s+(?:\w+\s+){0,2}?nicht\s*"
    r"(?=$|[,;:.!-]|\s+(?:weil|da|denn|wegen|bin|habe|hab|muss|ich)\b)",
    r"\b(?:fahre|trainiere|laufe|radle)\s+ich\s+(?:\w+\s+){0,2}?nicht\b",
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
    r"|\bkann\s+ich\s+(?:" + _NOT_NEGATION_WORD + r"){0,3}?(?:fahren|trainieren|radeln)\b"
)
_NEGATION_BEFORE = re.compile(
    r"(?:\b(?:no|not|keine?n?|nicht)|n'?t)\s*(?:\w+\s+)?$"
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
_JOINER_ONLY = re.compile(r"^[\s,&+/]*(?:and|und|or|oder|sowie)?[\s,&+/]*$")


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


def _target_dates(normalized: str, today: date) -> list[tuple[str, str]]:
    """Return [(date_iso, weekday_label)] for every day mentioned in the text."""
    targets: list[tuple[str, str]] = []
    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\b{name}s?\b", normalized):
            day = _next_weekday(today, weekday)
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
    or a complaint (ai-trainer-ops#110)."""
    return not (
        _NEGATED_LONG.search(segment)
        or _PAST.search(segment)
        or _COMPLAINT.search(segment)
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
        re.search(pattern, segment) for pattern in UNAVAILABLE_TRAINING_PATTERNS
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
    normalized = " ".join(text.casefold().split())
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
    for clause, is_question in _split_clauses(normalized):
        if is_question:
            continue
        for segment in _day_segments(clause):
            constraints.extend(_segment_constraints(segment, today, source))

    # Keep the first occurrence per (type, date).
    by_key: dict[tuple[str, str], dict] = {}
    for item in constraints:
        by_key.setdefault((item["constraint_type"], item["constraint_date"]), item)
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
    normalized = " ".join(text.casefold().split())
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
