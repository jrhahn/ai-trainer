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
    r"\bkann\s+(?:ich\s+)?nicht\b",
    r"\bgeht\s+nicht\b",
    r"\bunavailable\b",
    r"\bno\s+time\b",
    r"\bcan(?:not|'t)\s+(?:train|ride|work\s*out)\b",
    r"\bnot\s+available\b",
)

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


def _split_clauses(normalized: str) -> list[str]:
    return [part.strip() for part in _CLAUSE_BOUNDARY.split(normalized) if part.strip()]


def _next_weekday(today: date, weekday: int) -> date:
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta)


def _parse_duration_minutes(normalized: str) -> int | None:
    """Best-effort minutes from phrasing like '3 hours', '2,5 std', '90 min'."""
    hours = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:h\b|hours?|stunden?|std\b)", normalized)
    if hours:
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

    # Scan each clause independently so an intent only applies to the days named
    # alongside it, not to every day mentioned anywhere in the message (#412).
    for clause in _split_clauses(normalized):
        targets = _target_dates(clause, today)
        if not targets:
            continue

        has_unavailable = any(
            re.search(pattern, clause) for pattern in UNAVAILABLE_PATTERNS
        )
        has_training_context = any(
            re.search(pattern, clause) for pattern in TRAINING_CONTEXT_PATTERNS
        )
        if has_unavailable and has_training_context:
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
            re.search(pattern, clause) for pattern in LONG_SESSION_PATTERNS
        )
        if has_long_session:
            min_minutes = (
                _parse_duration_minutes(clause) or DEFAULT_LONG_SESSION_MINUTES
            )
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

    # Keep the first occurrence per (type, date).
    by_key: dict[tuple[str, str], dict] = {}
    for item in constraints:
        by_key.setdefault(
            (item["constraint_type"], item["constraint_date"]), item
        )
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
    for clause in _split_clauses(normalized):
        if not any(re.search(pattern, clause) for pattern in LIFT_PATTERNS):
            continue
        lift = True
        for date_iso, _weekday in _target_dates(clause, today):
            if date_iso not in dates:
                dates.append(date_iso)
    return {"lift": lift, "dates": dates}
