"""Goals with a number on them, and how far the athlete is from each (ai-trainer-ops#65).

The owner set a goal of 4000 m of climbing. The coach remembered it as text and
never looked at how far today's riding was from it, because nothing could: the
goal had no number and the rides kept no elevation.

Two halves, the same shape as the other chat captures (#495, #563):

* :func:`extract_goal` reads a stated goal off a coach-chat message,
  deterministically. It needs a number with a unit *and* a statement of intent,
  so "I did 1200 m yesterday" and "how many metres was that?" are not goals.
* :func:`goal_progress` measures a goal against the stored rides: the best
  single ride (or the best rolling week) in the last 4 and 12 weeks, the gap to
  the target, and a next step that grows the biggest day gradually instead of
  jumping to the target.

Progress is computed every time and never stored, so it cannot go stale.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

ELEVATION = "elevation_gain_m"
DISTANCE = "distance_km"
RIDE = "ride"
WEEK = "week"

RECENT_DAYS = 28
BASELINE_DAYS = 84
# How much bigger the next big day should be than the biggest recent one. Long
# days grow in steps the body can absorb, the same reasoning as the 10 % rule.
NEXT_STEP_GROWTH = 0.15

_NUMBER = r"(\d{1,3}(?:[.,']\d{3})+|\d+(?:[.,]\d+)?)"
_ELEVATION_UNIT = (
    r"(?:hm\b|höhenmeter|hoehenmeter|vertical metres|vertical meters"
    r"|m\s+(?:of\s+)?(?:climbing|elevation|ascent|vertical)"
    r"|metres?\s+(?:of\s+)?(?:climbing|elevation)|meters?\s+(?:of\s+)?(?:climbing|elevation))"
)
_DISTANCE_UNIT = r"(?:km\b|kilomet(?:er|re)s?\b)"
# A goal word is enough on its own. A wish ("I want to", "ich will") also says
# "I will ride 80 km tomorrow", which is a plan for one day and must not replace
# a real goal, so a wish counts only in the first person and without a near date.
_GOAL_WORD = re.compile(
    r"\b(?:goals?|targets?|aim|dream|ziele?|traum|vorgenommen|schaffen|achieve)\b",
    re.IGNORECASE,
)
# "my goal" / "mein Ziel" is the athlete's own; "my friend's goal" is not.
_OWN_GOAL = re.compile(
    r"\b(?:my|mein(?:e|em|en)?)\s+(?:goals?|targets?|aim|dream|ziele?|traum)\b",
    re.IGNORECASE,
)
# Someone else's goal: "my friend's goal", "sein Ziel", "the club's target".
_OTHERS_GOAL = re.compile(
    r"(?:\b\w+'s|\b(?:his|her|their|sein(?:e|em|en)?|ihr(?:e|em|en)?|des|der))\s+"
    r"(?:goals?|targets?|aim|dream|ziele?|traum)\b",
    re.IGNORECASE,
)
# A report of something done ("I did 3000 hm, which was my goal") is a result.
_PAST = re.compile(
    r"\b(?:did|was|rode|have done|geschafft|war|bin\b.*\bgefahren|habe\b.*\bgefahren)\b",
    re.IGNORECASE,
)
_WISH = re.compile(
    r"\b(?:want to|wanna|i'?d like to|plan to|möchte|moechte|will|würde gern|wuerde gern)\b",
    re.IGNORECASE,
)
_FIRST_PERSON = re.compile(r"\b(?:i|i'?d|i'?m|ich)\b", re.IGNORECASE)
_NEAR_DATE = re.compile(
    r"\b(?:today|tonight|tomorrow|this (?:morning|afternoon|evening|week|weekend)|"
    r"on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"heute|morgen|übermorgen|uebermorgen|am wochenende|diese woche|"
    r"am (?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag))\b",
    re.IGNORECASE,
)
_RIDE = re.compile(
    r"\b(?:in one ride|in a single ride|single ride|in one day|in a day|in one go|"
    r"an einem tag|in einer fahrt|pro fahrt|am stück|am stueck)\b",
    re.IGNORECASE,
)
# How far either side of a number its scope words are looked for. "4000 hm in
# one ride, I train twice a week" must not become a weekly goal.
_SCOPE_REACH = 30
_WEEK = re.compile(r"\b(?:per week|a week|each week|weekly|pro woche|in der woche|wöchentlich|woechentlich)\b", re.IGNORECASE)
_QUANTITY = re.compile(
    rf"{_NUMBER}\s*(?P<unit>{_ELEVATION_UNIT}|{_DISTANCE_UNIT})", re.IGNORECASE
)

# Sanity bounds. A number outside them is a typo or a different unit.
_BOUNDS = {
    (ELEVATION, RIDE): (300, 12000),
    (ELEVATION, WEEK): (500, 40000),
    (DISTANCE, RIDE): (20, 1500),
    (DISTANCE, WEEK): (30, 3000),
}


@dataclass(frozen=True)
class StatedGoal:
    metric: str
    scope: str
    target: float
    statement: str


def _parse_number(raw: str) -> float | None:
    cleaned = raw.replace("'", "")
    # "4.000" and "4,000" are thousands, "4,5" is a decimal.
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", cleaned):
        cleaned = cleaned.replace(".", "").replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _states_intent(text: str) -> bool:
    if _PAST.search(text):
        return False
    own = bool(_OWN_GOAL.search(text) or _FIRST_PERSON.search(text))
    if _GOAL_WORD.search(text):
        return own and not _OTHERS_GOAL.search(text)
    return bool(_WISH.search(text) and own and not _NEAR_DATE.search(text))


def extract_goals(message: str) -> list[StatedGoal]:
    """Every goal stated in *message*, one per measure.

    "150 km and 3000 hm in one ride" is two goals. Questions are not goals:
    "could I do 4000 hm?" asks, it does not commit.
    """
    text = (message or "").strip()
    if not text or text.endswith("?") or not _states_intent(text):
        return []
    matches = list(_QUANTITY.finditer(text))
    goals: dict[str, StatedGoal] = {}
    for index, match in enumerate(matches):
        # The scope is read from the words next to this number only, never
        # across another number: "100 km a week and 3000 hm in one ride" is one
        # weekly and one single-ride goal, and "pro Woche 10000 hm" is weekly.
        low_edge = matches[index - 1].end() if index else 0
        high_edge = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        near = text[
            max(low_edge, match.start() - _SCOPE_REACH) : min(high_edge, match.end() + _SCOPE_REACH)
        ]
        scope = WEEK if _WEEK.search(near) and not _RIDE.search(near) else RIDE
        value = _parse_number(match.group(1))
        if value is None:
            continue
        unit = match.group("unit").lower()
        metric = DISTANCE if re.match(_DISTANCE_UNIT, unit, re.IGNORECASE) else ELEVATION
        low, high = _BOUNDS[(metric, scope)]
        if low <= value <= high and metric not in goals:
            goals[metric] = StatedGoal(metric, scope, value, text[:500])
    return list(goals.values())


def extract_goal(message: str) -> StatedGoal | None:
    """The first goal stated in *message*, or ``None``."""
    goals = extract_goals(message)
    return goals[0] if goals else None


@dataclass(frozen=True)
class GoalProgress:
    metric: str
    scope: str
    target: float
    best_recent: float | None
    best_baseline: float | None
    rides_with_data: int

    @property
    def gap(self) -> float | None:
        if self.best_baseline is None:
            return None
        return max(self.target - self.best_baseline, 0.0)

    @property
    def reached(self) -> bool:
        return self.best_baseline is not None and self.best_baseline >= self.target

    @property
    def next_step(self) -> float | None:
        if self.best_baseline is None or self.reached:
            return None
        return min(self.target, self.best_baseline * (1 + NEXT_STEP_GROWTH))


def _as_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def goal_progress(
    metric: str, scope: str, target: float, rows: Iterable[object], today: date
) -> GoalProgress:
    """Best ride, or best rolling week, in the last 4 and 12 weeks."""
    per_day: dict[date, float] = {}
    best_ride: dict[date, float] = {}
    count = 0
    for row in rows or []:
        day = _as_date(getattr(row, "activity_date", None))
        value = getattr(row, metric, None)
        if day is None or value is None or not (today - day).days < BASELINE_DAYS + 7:
            continue
        count += 1
        per_day[day] = per_day.get(day, 0.0) + float(value)
        best_ride[day] = max(best_ride.get(day, 0.0), float(value))

    def best(days: int) -> float | None:
        start = today - timedelta(days=days)
        if scope == RIDE:
            values = [v for d, v in best_ride.items() if start <= d <= today]
            return max(values) if values else None
        if not per_day:
            return None
        totals = []
        current = start + timedelta(days=6)
        while current <= today:
            totals.append(
                sum(per_day.get(current - timedelta(days=k), 0.0) for k in range(7))
            )
            current += timedelta(days=1)
        return max(totals) if totals else None

    return GoalProgress(
        metric, scope, target, best(RECENT_DAYS), best(BASELINE_DAYS), count
    )


def _amount(metric: str, value: float) -> str:
    return f"{value:,.0f} m" if metric == ELEVATION else f"{value:,.0f} km"


def _goal_name(metric: str, scope: str, target: float) -> str:
    what = "of climbing" if metric == ELEVATION else ""
    where = "in one ride" if scope == RIDE else "in one week"
    return " ".join(part for part in (_amount(metric, target), what, where) if part)


def goal_progress_section(goals: list[tuple[object, GoalProgress]]) -> str:
    """The athlete's measurable goals and the distance to each, for the coach."""
    if not goals:
        return ""
    lines = [
        "Athlete goals with a number on them (progress computed from synced rides; "
        "build toward them step by step, and mention progress when it is relevant, "
        "not in every message):"
    ]
    for goal, progress in goals:
        name = _goal_name(progress.metric, progress.scope, progress.target)
        deadline = getattr(goal, "deadline", None)
        line = f"- Goal: {name}" + (f", by {deadline}" if deadline else "") + "."
        if progress.best_baseline is None:
            line += (
                " No synced ride in the last 12 weeks reports this measure yet, so "
                "progress is unknown; do not guess it."
            )
        elif progress.reached:
            line += (
                f" Reached: best {_amount(progress.metric, progress.best_baseline)} "
                "in the last 12 weeks. Acknowledge it and ask what is next."
            )
        else:
            recent = (
                _amount(progress.metric, progress.best_recent)
                if progress.best_recent is not None
                else "none"
            )
            line += (
                f" Best in the last 4 weeks: {recent}; in the last 12 weeks: "
                f"{_amount(progress.metric, progress.best_baseline)} "
                f"({progress.best_baseline / progress.target:.0%} of the target). "
                f"Gap: {_amount(progress.metric, progress.gap)}. A sensible next "
                f"{'big day' if progress.scope == RIDE else 'big week'} is about "
                f"{_amount(progress.metric, progress.next_step)}, when the training "
                "phase allows a long session."
            )
        lines.append(line)
    return "\n".join(lines)
