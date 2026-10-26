"""A weekday the athlete prefers over another (ai-trainer-ops#204).

The training days are a starting point, and onboarding now says so: tell the
coach if one day generally works better than another. "Montag passt mir
meistens besser als Dienstag" used to leave nothing behind. The coach might move
one session, and every later plan used Tuesday again.

So a sentence like that is read here, kept on the profile as a pair
``(prefer, over)``, shown under the reply, and honoured by the automatic
writers. When both days are training days, a week's key or long session goes to
the preferred day first (``apply``). When the preferred day is not a training
day and the sentence says "generally", the general change is a swap of the
training days, which the chat offers like any other change to them
(``swapped_training_days``).

Pure: no database, no clock.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from services.availability import WEEKDAYS

# As the profile stores training days.
WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
# Kept per athlete; the oldest goes first. A handful is all a week can honour.
MAX_PREFERENCES = 3

_DAY = "(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")s?"
_FILLER = r"(?:\s+(?:mir|me|for\s+me|generally|usually|mostly|normally|always|in\s+general|really|"
_FILLER += r"just|much|a\s+lot|viel|meistens|generell|normalerweise|immer|grundsätzlich|eigentlich|einfach|auch|"
_FILLER += r"definitely|deutlich)){0,3}"

_PATTERNS = (
    # "Monday works better than Tuesday", "Monday is generally better than tuesday"
    rf"\b{_DAY}{_FILLER}\s+(?:works|suits|fits|is|are){_FILLER}\s+better{_FILLER}\s+than\s+(?:on\s+)?{_DAY}\b",
    # "I prefer Monday to Tuesday", "I'd rather train Monday than Tuesday"
    rf"\bprefer\s+(?:to\s+train\s+|training\s+|riding\s+)?(?:on\s+)?{_DAY}\s+(?:to|over|rather\s+than|instead\s+of)\s+(?:on\s+)?{_DAY}\b",
    rf"\brather\s+(?:train|ride|run)?\s*(?:on\s+)?{_DAY}\s+than\s+(?:on\s+)?{_DAY}\b",
    # "Montag passt mir meistens besser als Dienstag", "Montag geht besser als Dienstag"
    rf"\b{_DAY}{_FILLER}\s+(?:passt|geht|ist){_FILLER}\s+(?:besser|lieber){_FILLER}\s+als\s+(?:am\s+)?{_DAY}\b",
    # "lieber Montag als Dienstag", "lieber am Montag als am Dienstag"
    rf"\blieber\s+(?:am\s+)?{_DAY}\s+als\s+(?:am\s+)?{_DAY}\b",
)
_PREFERENCE = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]

# A standing preference, not this week's: "generally", "meistens" …
_STANDING = re.compile(
    r"\b(?:generally|in\s+general|usually|mostly|normally|always|from\s+now\s+on|going\s+forward|"
    r"generell|meistens|normalerweise|immer|grundsätzlich|ab\s+jetzt|künftig|in\s+zukunft)\b",
    re.IGNORECASE,
)
# This week only: the chat moves the session, nothing is kept.
_ONE_OFF = re.compile(
    r"\b(?:this|next)\s+(?:week|weekend)\b|\b(?:today|tomorrow|tonight)\b"
    r"|\b(?:diese|nächste|naechste)\s+woche\b|\b(?:heute|morgen)\b|\bdieses\s+wochenende\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def _name(word: str) -> str:
    return WEEKDAY_NAMES[WEEKDAYS[word.lower()]]


def extract(message: str) -> list[dict]:
    """The day preferences ``message`` states, as ``{"prefer", "over", "standing"}``.

    A question, a one-off ("this week") and a day compared with itself are
    left out.
    """
    found: list[dict] = []
    for match in _SENTENCE.finditer(message.replace("’", "'")):
        sentence = match.group().strip()
        if not sentence or sentence.endswith("?") or _ONE_OFF.search(sentence):
            continue
        for pattern in _PREFERENCE:
            hit = pattern.search(sentence)
            if not hit:
                continue
            prefer, over = _name(hit.group(1)), _name(hit.group(2))
            if prefer != over:
                found.append(
                    {
                        "prefer": prefer,
                        "over": over,
                        "standing": bool(_STANDING.search(sentence)),
                    }
                )
            break
    return found


def merged(existing: list[dict] | None, new: list[dict]) -> list[dict]:
    """``existing`` with ``new`` added: a pair said again moves to the end, the
    opposite pair goes, and only the newest ``MAX_PREFERENCES`` stay."""
    result = [
        {"prefer": p["prefer"], "over": p["over"]}
        for p in existing or []
        if isinstance(p, dict)
        and p.get("prefer") in WEEKDAY_NAMES
        and p.get("over") in WEEKDAY_NAMES
    ]
    for item in new:
        pair = {"prefer": item["prefer"], "over": item["over"]}
        result = [
            p
            for p in result
            if p != pair and p != {"prefer": pair["over"], "over": pair["prefer"]}
        ]
        result.append(pair)
    return result[-MAX_PREFERENCES:]


def swapped_training_days(
    training_days: list[str] | None, item: dict
) -> list[str] | None:
    """The training days with ``over`` traded for ``prefer``, when that is what
    a standing preference means: ``prefer`` is not a training day, ``over`` is.
    ``None`` otherwise."""
    days = [d for d in training_days or [] if d in WEEKDAY_NAMES]
    if not item.get("standing") or item["prefer"] in days or item["over"] not in days:
        return None
    swapped = {d for d in days if d != item["over"]} | {item["prefer"]}
    return [d for d in WEEKDAY_NAMES if d in swapped]


def honoured(
    preferences: list[dict] | None, training_days: list[str] | None
) -> list[dict]:
    """The preferences a plan can act on: both days are training days."""
    days = set(training_days or [])
    return [
        p
        for p in preferences or []
        if p.get("prefer") in days and p.get("over") in days
    ]


def _intense(day: dict) -> bool:
    # Imported here for the cycle explained in _weight below.
    from services.plan_priors import INTENSITY_TYPES

    return str(day.get("workoutType") or "").strip().lower() in INTENSITY_TYPES


def _weight(day: dict) -> float:
    """How much a day's session weighs: its minutes, doubled for intensity, so
    the key session counts before a longer easy one of similar length."""
    # Imported here: schemas imports this module for the profile it hands the
    # coach, and plan_priors imports schemas.
    from services.plan_priors import INTENSITY_TYPES, planned_minutes

    kind = str(day.get("workoutType") or "").strip().lower()
    return planned_minutes(day) * (2 if kind in INTENSITY_TYPES else 1)


REASON = "Moved to {day}, the day you prefer for your key sessions."


def _hard_neighbour(
    result: list[dict], by_date: dict[str, list[int]], iso: str, leaving: str
) -> bool:
    """Whether a day next to ``iso`` holds a hard session, not counting
    ``leaving``, the day the session comes from."""
    day = date.fromisoformat(iso)
    for other in (
        (day - timedelta(days=1)).isoformat(),
        (day + timedelta(days=1)).isoformat(),
    ):
        if other == leaving:
            continue
        if any(_intense(result[i]) for i in by_date.get(other, [])):
            return True
    return False


def apply(
    plan: list[dict],
    *,
    preferences: list[dict] | None,
    training_days: list[str] | None,
    today: str,
    protected: set[str],
) -> tuple[list[dict], dict[tuple[str, int], str]]:
    """``plan`` with, in each week, the heavier of the two days' sessions on the
    preferred day; and why, per ``(date, slot)``.

    Only single-session days after today that nothing protects trade places,
    and only when both days are training days. ``protected`` are dates nothing
    may change here (done, ridden, pinned, or left alone by this write). A hard
    session is not moved next to another hard one: the week-shaped guards ran
    before this, and a preference is no reason to stack intensity (review of
    ai-trainer#1015).
    """
    result = list(plan)
    reasons: dict[tuple[str, int], str] = {}
    by_date: dict[str, list[int]] = {}
    for i, day in enumerate(result):
        if day.get("date"):
            by_date.setdefault(str(day["date"]), []).append(i)

    def single(iso: str) -> int | None:
        indices = by_date.get(iso) or []
        if len(indices) != 1 or iso <= today or iso in protected:
            return None
        return None if result[indices[0]].get("completed") else indices[0]

    for pref in honoured(preferences, training_days):
        prefer_index = WEEKDAY_NAMES.index(pref["prefer"])
        over_index = WEEKDAY_NAMES.index(pref["over"])
        mondays = sorted(
            {
                date.fromisoformat(iso)
                - timedelta(days=date.fromisoformat(iso).weekday())
                for iso in by_date
            }
        )
        for monday in mondays:
            a_iso = (monday + timedelta(days=prefer_index)).isoformat()
            b_iso = (monday + timedelta(days=over_index)).isoformat()
            a, b = single(a_iso), single(b_iso)
            if a is None or b is None or _weight(result[b]) <= _weight(result[a]):
                continue
            # Both sessions move, so both landings are checked: a lighter session
            # can be intense too (a short threshold against a long easy ride).
            if _intense(result[b]) and _hard_neighbour(result, by_date, a_iso, b_iso):
                continue
            if _intense(result[a]) and _hard_neighbour(result, by_date, b_iso, a_iso):
                continue
            day_a, day_b = result[a], result[b]
            result[a] = {**day_b, "date": a_iso, "slot": day_a.get("slot", 0)}
            result[b] = {**day_a, "date": b_iso, "slot": day_b.get("slot", 0)}
            reasons[(a_iso, int(day_a.get("slot") or 0))] = REASON.format(
                day=pref["prefer"].capitalize()
            )
    return result, reasons


def label(pref: dict) -> str:
    """How the preference reads under the reply and in Settings, in English;
    the app words the weekdays in the interface's language."""
    return f"{pref['prefer'].capitalize()} rather than {pref['over'].capitalize()}"
