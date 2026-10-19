"""Has the coach learned something the plan does not know yet? (ai-trainer-ops#158)

A full plan is generated at onboarding and at the first sync, and never again
on its own. What the athlete tells the coach afterwards, the history they
import, the assumptions they answer: none of it reshaped the plan unless the
chat itself proposed a change. So the plan remembers what it was generated
from, and the dashboard asks this module whether that has moved enough to
offer a rebuild. The athlete decides; nothing is rebuilt silently.

No model call: a handful of reads, cheap enough for every dashboard load.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from services.starting_hypotheses import CATEGORY_PREFIX

# Below this the backend's own models do not read the athlete yet; crossing it
# is the moment the history starts to speak (``COLD_START_ACTIVITIES``).
COLD_START_ACTIVITIES = 8
# A history import or a few weeks of riding: enough new activities to matter.
HISTORY_STEP = 10
# An FTP move smaller than this is noise between two estimates.
FTP_CHANGE = 0.05

# The reasons, in the order the card names them.
REASONS = ("training_days", "ftp", "history", "race", "assumptions", "hours")


async def current_inputs(db: AsyncSession, user: models.User) -> dict:
    """What a plan generated now would be built from, as plain JSON."""
    activities = await db.scalar(
        select(func.count()).select_from(models.RideMetric).where(models.RideMetric.user_id == user.id)
    )
    settled = await db.scalars(
        select(models.AthleteHypothesis.id)
        .where(
            models.AthleteHypothesis.user_id == user.id,
            models.AthleteHypothesis.category.startswith(CATEGORY_PREFIX),
            models.AthleteHypothesis.status.in_(("confirmed", "refuted")),
        )
        .order_by(models.AthleteHypothesis.id)
    )
    return {
        "trainingDays": sorted(user.training_days or []),
        "ftp": user.current_ftp,
        "activities": int(activities or 0),
        "race": user.race_date if user.training_goal == "race" or user.race_date else None,
        "assumptions": list(settled),
        "hours": user.weekly_hours,
    }


def reasons(before: dict | None, now: dict) -> list[str]:
    """Why ``now`` differs enough from ``before`` to rebuild, in ``REASONS`` order.

    No record means no comparison: a plan from before this existed is not
    offered a rebuild until its next generation records what it came from.
    """
    if not before:
        return []
    found: set[str] = set()
    if sorted(before.get("trainingDays") or []) != now["trainingDays"]:
        found.add("training_days")
    old_ftp, new_ftp = before.get("ftp"), now["ftp"]
    if bool(old_ftp) != bool(new_ftp) or (
        old_ftp and new_ftp and abs(new_ftp - old_ftp) / old_ftp > FTP_CHANGE
    ):
        found.add("ftp")
    old_count, new_count = int(before.get("activities") or 0), now["activities"]
    if (old_count < COLD_START_ACTIVITIES <= new_count) or new_count - old_count >= HISTORY_STEP:
        found.add("history")
    if before.get("race") != now["race"]:
        found.add("race")
    if set(now["assumptions"]) - set(before.get("assumptions") or []):
        found.add("assumptions")
    if before.get("hours") != now["hours"]:
        found.add("hours")
    return [reason for reason in REASONS if reason in found]


async def record(db: AsyncSession, user: models.User) -> None:
    """Remember what the plan was just generated from."""
    row = await crud.get_training_plan(db, user.id)
    if row is None:
        return
    row.generated_inputs = await current_inputs(db, user)
    await db.flush()


async def changes_since_generation(db: AsyncSession, user: models.User) -> list[str]:
    row = await crud.get_training_plan(db, user.id)
    if row is None:
        return []
    return reasons(row.generated_inputs, await current_inputs(db, user))
