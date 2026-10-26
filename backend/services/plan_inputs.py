"""Has the coach learned something the plan does not know yet? (ai-trainer-ops#158)

A full plan is generated at onboarding and at the first sync, and never again
on its own. What the athlete tells the coach afterwards, the history they
import, the assumptions they answer: none of it reshaped the plan unless the
chat itself proposed a change. So the plan remembers what it was generated
from, and this module says whether that has moved enough to rebuild.

Since ai-trainer-ops#206 the app rebuilds by itself, on the owner's word ("der
coach sollte das einfach selbst machen"), instead of offering a button. A change
the athlete made (``ATHLETE_REASONS``) rebuilds right away; new rides and
answered questions wait for the nightly run, which batches them into one
rebuild. Never silently: the plan keeps why it was last rebuilt, and the
dashboard says so in one sentence.

No model call: a handful of reads, cheap enough for every dashboard load.

The record is written by a full generation only. A change the coach chat or
the nightly job makes to some days does not refresh it: the comparison is with
what the whole plan was last built from.
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

# Full rebuilds a day, on the athlete's own day (ai-trainer-ops#175). A rebuild
# costs about a third of one chat message, so this is not about money: it keeps
# the plan steady and stops a loop of rebuilds. A day the chat changes is part of
# a chat message, which ``enforce_ai_rate_limit`` limits, and does not count.
DAILY_REBUILDS = 6

# The reasons, in the order the notice names them.
REASONS = (
    "training_days",
    "sports",
    "level",
    "ftp",
    "history",
    "race",
    "assumptions",
    "hours",
)
# Changes the athlete made by hand, in Settings or by accepting a chat offer:
# they expect the plan to follow at once (ai-trainer-ops#206). The others (new
# rides, an answered question) wait for the nightly run.
ATHLETE_REASONS = frozenset(
    {"training_days", "sports", "level", "ftp", "race", "hours"}
)


async def current_inputs(db: AsyncSession, user: models.User) -> dict:
    """What a plan generated now would be built from, as plain JSON."""
    activities = await db.scalar(
        select(func.count())
        .select_from(models.RideMetric)
        .where(models.RideMetric.user_id == user.id)
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
    # A race added in the calendar, or one whose date or course changed, is the
    # same news as a new race on the profile (ai-trainer-ops#175).
    events = await crud.get_race_events(db, user.id)
    return {
        "trainingDays": sorted(user.training_days or []),
        "ftp": user.current_ftp,
        "activities": int(activities or 0),
        "race": user.race_date
        if user.training_goal == "race" or user.race_date
        else None,
        "assumptions": list(settled),
        "hours": user.weekly_hours,
        "raceEvents": sorted([e.date, e.distance_km, e.elevation_m] for e in events),
        # Both can be changed in Settings and in the chat (ai-trainer-ops#202).
        "sports": sorted(user.sports or []),
        "level": user.fitness_level,
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
    if (
        old_count < COLD_START_ACTIVITIES <= new_count
    ) or new_count - old_count >= HISTORY_STEP:
        found.add("history")
    # A record from before calendar races were compared has none: compared with
    # no races, so a race added since is still news.
    if (
        before.get("race") != now["race"]
        or (before.get("raceEvents") or []) != now["raceEvents"]
    ):
        found.add("race")
    if set(now["assumptions"]) - set(before.get("assumptions") or []):
        found.add("assumptions")
    if before.get("hours") != now["hours"]:
        found.add("hours")
    # Recorded since ai-trainer-ops#202. A record from before has neither, and
    # is not offered a rebuild for a change it cannot see.
    if "sports" in before and sorted(before["sports"] or []) != now["sports"]:
        found.add("sports")
    if "level" in before and before["level"] != now["level"]:
        found.add("level")
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


def mark_rebuilt(row: models.TrainingPlan, reasons: list[str], at: str) -> None:
    """Keep why the plan was just rebuilt by itself, for the dashboard's one
    sentence (ai-trainer-ops#206). Kept beside the inputs it was built from, so
    the next generation replaces both."""
    row.generated_inputs = {
        **(row.generated_inputs or {}),
        "rebuiltFor": list(reasons),
        "rebuiltAt": at,
    }


def last_rebuild(row: models.TrainingPlan | None) -> dict | None:
    """``{"at", "reasons"}`` of the last rebuild the app made by itself."""
    inputs = (row.generated_inputs if row is not None else None) or {}
    if not inputs.get("rebuiltFor") or not inputs.get("rebuiltAt"):
        return None
    return {"at": inputs["rebuiltAt"], "reasons": list(inputs["rebuiltFor"])}


def rebuilds_left(row: models.TrainingPlan | None, today: str) -> int:
    """Full rebuilds still allowed on ``today``, the athlete's ISO date."""
    if row is None or row.rebuild_day != today:
        return DAILY_REBUILDS
    return max(0, DAILY_REBUILDS - (row.rebuild_count or 0))


def count_rebuild(row: models.TrainingPlan, today: str) -> None:
    """Count one full rebuild on ``today``."""
    if row.rebuild_day != today:
        row.rebuild_day, row.rebuild_count = today, 0
    row.rebuild_count = (row.rebuild_count or 0) + 1
