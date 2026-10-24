"""The sessions an illness took come back when the athlete is well again (ai-trainer-ops#232).

Mia said she was ill, and the coach turned Sunday, Tuesday and Thursday into
"Rest Day - Illness". Two days later she was well again: Sunday became a
30-minute spin, the easy days ended on the 26th, and Tuesday and Thursday stayed
illness rest days, with the next real session a week away.

The owner's (a), 2026-10-24: every day the illness turned into rest gets its
session back from the plan history. Inside the easy days the session comes back
easy and no longer than before; the reply names the days. Deterministic, not
left to the model.

"Turned into rest by the illness" is read from ``plan_day_history``: a write
after the illness was recorded that made a session a rest day. Not the rest days
the rest-day guard writes for its own reasons, and not a day that is no training
day: those were never the illness's.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models
import schemas
from services import plan_priors, rest_days

# An intense session inside the easy days comes back as one of these, by sport.
EASY_TITLES = {"cycling": "Easy ride", "running": "Easy run"}
EASY_TITLE = "Easy session"
EASY_DESCRIPTION = (
    "Easy, as you come back from the illness: an effort you could talk through."
)
# Fixed texts, so their German is fixed too (``plan_translations.FIXED``).
GERMAN = {
    "Easy ride": "Lockere Ausfahrt",
    "Easy run": "Lockerer Lauf",
    EASY_TITLE: "Lockere Einheit",
    EASY_DESCRIPTION: (
        "Locker, weil du gerade von der Krankheit zurückkommst: so, dass du dich "
        "dabei unterhalten könntest."
    ),
}

_GUARD_REASONS = frozenset({rest_days.OFF_DAY_REASON, rest_days.IN_A_ROW_REASON})


# What an easy session leaves out of an intense one: its targets, and the words
# that describe the hard work.
_INTENSE_KEYS = frozenset(
    {"intervals", "targetPower", "targetHeartRate", "workoutPurpose", "keyFocusPoints"}
)


def _easy(day: dict) -> dict:
    """``day`` as an easy session, no longer than it was. A session already easy
    stays as it was."""
    if plan_priors._workout_type(day) not in plan_priors.INTENSITY_TYPES and not day.get(
        "intervals"
    ):
        return day
    return {
        **{key: value for key, value in day.items() if key not in _INTENSE_KEYS},
        "workoutType": "recovery",
        "title": EASY_TITLES.get(schemas.day_sport(day), EASY_TITLE),
        "description": EASY_DESCRIPTION,
    }


async def restored_sessions(
    db: AsyncSession,
    user: models.User,
    illness: models.AthleteState,
    plan: list[dict],
    *,
    today: date,
    easy_until: date,
) -> dict[tuple[str, int], dict]:
    """The sessions that give back what ``illness`` took, as of ``today``, by
    ``(date, slot)``: whole days, to replace the rest days with.

    One per session a write after the illness was recorded turned into rest
    and that is still rest, not done, today or later, on a training day. Inside
    the easy days it comes back easy (:func:`_easy`); after them as it was.
    """
    rows = (
        await db.scalars(
            select(models.PlanDayHistory)
            .where(
                models.PlanDayHistory.user_id == user.id,
                models.PlanDayHistory.applied.is_(True),
                models.PlanDayHistory.recorded_at >= illness.created_at,
                models.PlanDayHistory.date >= today.isoformat(),
            )
            .order_by(models.PlanDayHistory.recorded_at)
        )
    ).all()
    current = {schemas.session_key(day): day for day in plan if isinstance(day, dict)}
    training_days = {d.lower() for d in user.training_days or [] if isinstance(d, str)}
    taken: dict[tuple, dict] = {}
    for row in rows:
        old, new = row.old_day, row.new_day
        if not isinstance(old, dict) or not isinstance(new, dict):
            continue
        if rest_days.is_rest(old) or not rest_days.is_rest(new):
            continue
        if new.get("description") in _GUARD_REASONS:
            continue
        key = (row.date, row.slot or 0)
        # The first such write holds the session as it was before the illness.
        taken.setdefault(key, old)
    restored: dict[tuple[str, int], dict] = {}
    for key, session in sorted(taken.items()):
        day = current.get(key)
        if day is None or not rest_days.is_rest(day) or day.get("completed"):
            continue
        when = date.fromisoformat(key[0])
        if training_days and rest_days._WEEKDAY_NAMES[when.weekday()] not in training_days:
            continue
        restored[key] = _easy(session) if when <= easy_until else session
    return restored
