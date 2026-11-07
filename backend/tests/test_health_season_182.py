"""A health goal's weeks: steady, with no season (ai-trainer-ops#182).

The owner's test athlete, there to stay healthy, read "load week 3 of your base
phase" and week goals about a "Belastungsblock". A health goal has no season to
periodise: no base and build, no 3-up-1-down, no autumn break.
"""

from datetime import date

import pytest

import models
from services import plan_context, season_plan, training_phase
from tests.conftest import TestSessionLocal

OCTOBER = date(2026, 10, 12)  # the autumn break's month for everyone else
SPRING = date(2026, 4, 13)


@pytest.mark.parametrize("today", [OCTOBER, SPRING])
def test_a_health_goal_has_no_season(today):
    phase = training_phase.training_phase(today, [], [], health=True)

    assert phase.phase == training_phase.HEALTH
    assert phase.focus is None


@pytest.mark.parametrize("today", [OCTOBER, SPRING])
def test_without_the_goal_the_season_is_as_before(today):
    assert training_phase.training_phase(today, [], []).phase != training_phase.HEALTH


def test_a_race_ahead_still_shapes_the_weeks():
    race = date(2026, 6, 1)

    phase = training_phase.training_phase(SPRING, [race], [], health=True)

    assert phase.phase == training_phase.BUILD


def test_the_phase_block_says_steady_and_names_no_block():
    section = training_phase.training_phase_section(
        training_phase.training_phase(SPRING, [], [], health=True)
    )

    assert "- Phase: health" in section
    assert "no load cycles" in section
    assert "4-week block" not in section


def test_every_week_is_the_same_steady_week():
    weeks = season_plan.season_weeks(SPRING, [], [], health=True)

    assert len(weeks) == season_plan.SEASON_WEEKS
    for week in weeks:
        assert week.phase == training_phase.HEALTH
        assert week.recovery is False
        assert week.load_week is None
        assert week.hard_sessions == (0, 1)
        assert week.focus == season_plan.HEALTH_FOCUS
    assert "load week" not in season_plan.week_label(weeks[1])


def test_with_nothing_stated_or_ridden_the_weeks_hold_the_who_minutes():
    weeks = season_plan.season_weeks(SPRING, [], [], health=True)

    assert weeks[1].hours == (2.5, 5.0)


def test_stated_hours_still_set_the_band():
    weeks = season_plan.season_weeks(SPRING, [], [], weekly_hours=4, health=True)

    assert weeks[1].hours == (3.0, 4.0)


@pytest.mark.asyncio
async def test_the_athletes_goal_reaches_the_weeks_and_the_phase_block():
    async with TestSessionLocal() as db:
        user = models.User(
            email="werner@example.com",
            hashed_password="x",
            training_goal="health",
        )
        db.add(user)
        await db.commit()

        weeks = await plan_context.season_weeks_for_user(db, user.id, SPRING, [])
        section = await plan_context.training_phase_section_for_user(
            db, user.id, SPRING, []
        )

    assert {week.phase for week in weeks} == {training_phase.HEALTH}
    assert "- Phase: health" in section


def test_the_week_goals_are_written_in_plain_words_for_a_health_goal():
    from services.prompts import week_goals_user

    weeks = [{"weekStart": "2026-04-13", "summary": "health. Focus: steady."}]

    assert "speak in plain" in week_goals_user(weeks, {"trainingGoal": "health"})
    assert "speak in plain" not in week_goals_user(weeks, {"trainingGoal": "race"})
