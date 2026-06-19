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


def _next_weekday(today: date, weekday: int) -> date:
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta)


def extract_availability_constraints(
    text: str,
    *,
    today: date,
) -> list[dict[str, str]]:
    """Extract simple no-training constraints from athlete text.

    The extractor intentionally handles only high-confidence phrasing. Ambiguous
    schedule preferences remain in free-form coach memory until confirmed.
    """
    normalized = " ".join(text.casefold().split())
    if not normalized:
        return []

    has_unavailable = any(
        re.search(pattern, normalized) for pattern in UNAVAILABLE_PATTERNS
    )
    has_training_context = any(
        re.search(pattern, normalized) for pattern in TRAINING_CONTEXT_PATTERNS
    )
    if not has_unavailable or not has_training_context:
        return []

    constraints: list[dict[str, str]] = []
    for name, weekday in WEEKDAYS.items():
        if not re.search(rf"\b{name}s?\b", normalized):
            continue
        constraint_date = _next_weekday(today, weekday)
        constraints.append(
            {
                "constraint_type": "no_training",
                "constraint_date": constraint_date.isoformat(),
                "weekday": WEEKDAY_LABELS[weekday],
                "reason": "Athlete said they are unavailable for training.",
                "source": text.strip()[:500],
                "expires_on": constraint_date.isoformat(),
            }
        )

    if re.search(r"\b(morgen|tomorrow)\b", normalized):
        constraint_date = today + timedelta(days=1)
        constraints.append(
            {
                "constraint_type": "no_training",
                "constraint_date": constraint_date.isoformat(),
                "weekday": WEEKDAY_LABELS[constraint_date.weekday()],
                "reason": "Athlete said they are unavailable for training tomorrow.",
                "source": text.strip()[:500],
                "expires_on": constraint_date.isoformat(),
            }
        )

    if re.search(r"\b(heute|today)\b", normalized):
        constraints.append(
            {
                "constraint_type": "no_training",
                "constraint_date": today.isoformat(),
                "weekday": WEEKDAY_LABELS[today.weekday()],
                "reason": "Athlete said they are unavailable for training today.",
                "source": text.strip()[:500],
                "expires_on": today.isoformat(),
            }
        )

    # Keep first occurrence per concrete date.
    by_date = {item["constraint_date"]: item for item in constraints}
    return list(by_date.values())
