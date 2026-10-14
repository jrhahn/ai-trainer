"""The goal sentences of the season plan, and when the days must follow a week (ai-trainer-ops#98).

The weeks are computed (``season_plan``). The model adds one thing, a goal
sentence per week, and is asked only for the weeks whose stored goal is missing
or was written for a week that has since changed. One call covers all of them.
"""

from __future__ import annotations

from datetime import date

import crud
import models
import schemas
from services import ai_service, plan_context, season_plan
from services.llm import resolve_user_provider
from services.token_accounting import track_llm_usage


async def ensure_week_goals(
    db,
    user: models.User,
    weeks: list[season_plan.SeasonWeek],
    today: date,
    *,
    source: str,
) -> dict[date, str]:
    """The goals of ``weeks``, writing the ones that are missing or stale."""
    stored = await crud.get_week_goals(db, user.id, [w.start.isoformat() for w in weeks])
    goals = season_plan.current_goals(weeks, stored)
    missing = [week for week in weeks if week.start not in goals]
    if not missing:
        return goals
    profile = schemas.UserProfileSchema.from_user(user).model_dump(by_alias=True)
    race_events = [
        {"date": e.date, "distanceKm": e.distance_km, "elevationM": e.elevation_m}
        for e in await crud.get_race_events(db, user.id)
    ]
    goals_section = await plan_context.goal_section_for_user(db, user.id, today)
    # Nobody asked for these sentences, so there is no athlete turn whose
    # language they could mirror (#658).
    language_samples = await crud.get_recent_athlete_messages(db, user.id)
    async with track_llm_usage(db, user, source=source):
        written = await ai_service.write_week_goals(
            [
                {
                    "weekStart": week.start.isoformat(),
                    "summary": season_plan.week_summary(week),
                    "nextRace": week.next_race.isoformat() if week.next_race else None,
                }
                for week in missing
            ],
            profile,
            race_events=race_events,
            goals_section=goals_section,
            provider=resolve_user_provider(user),
            language_samples=language_samples,
        )
    for week in missing:
        if goal := written.get(week.start.isoformat()):
            await crud.save_week_goal(
                db,
                user.id,
                week.start.isoformat(),
                fingerprint=season_plan.fingerprint(week),
                goal=goal,
            )
            goals[week.start] = goal
    return goals


async def moved_weeks(
    db, user_id: str, weeks: list[season_plan.SeasonWeek]
) -> list[tuple[models.WeekGoal, str]]:
    """The weeks whose targets moved since the nightly run last fitted the days
    to them, each with the week's fingerprint as it is now."""
    stored = await crud.get_week_goals(db, user_id, [w.start.isoformat() for w in weeks])
    return [
        (row, current)
        for week in weeks
        if (row := stored.get(week.start.isoformat())) is not None
        and row.fitted_fingerprint != (current := season_plan.fingerprint(week))
    ]


def mark_fitted(moved: list[tuple[models.WeekGoal, str]]) -> None:
    """The days were fitted to these weeks as they are now. Once per move: a
    writer may keep a departure on purpose, and asking again every night is the
    churn of #666."""
    for row, current in moved:
        row.fitted_fingerprint = current
