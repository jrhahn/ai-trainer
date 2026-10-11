"""Goals with a number on them, and how far away they are (ai-trainer-ops#65)."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import crud
import models
from auth import hash_password
from services import goal_progress as gp
from services import plan_context
from services.activity_imports import (
    ImportedActivity,
    reported_distance_km,
    reported_elevation_m,
)
from tests.conftest import TestSessionLocal

TODAY = date(2026, 10, 11)


def _ride(days_ago: int, elevation: float | None = None, distance: float | None = None):
    return SimpleNamespace(
        activity_date=(TODAY - timedelta(days=days_ago)).isoformat(),
        elevation_gain_m=elevation,
        distance_km=distance,
    )


# ---------------------------------------------------------------------------
# What counts as a stated goal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message, metric, scope, target",
    [
        ("Mein Ziel: 4000 hm an einem Tag", gp.ELEVATION, gp.RIDE, 4000),
        ("Ich will dieses Jahr 4.000 Höhenmeter fahren", gp.ELEVATION, gp.RIDE, 4000),
        ("My goal is 3000 m of climbing in one ride", gp.ELEVATION, gp.RIDE, 3000),
        ("I want to do a 200 km ride", gp.DISTANCE, gp.RIDE, 200),
        ("Ich möchte 10000 hm pro Woche schaffen", gp.ELEVATION, gp.WEEK, 10000),
    ],
)
def test_a_stated_goal_is_captured(message, metric, scope, target):
    goal = gp.extract_goal(message)

    assert goal is not None
    assert (goal.metric, goal.scope, goal.target) == (metric, scope, target)


@pytest.mark.parametrize(
    "message",
    [
        "Yesterday I did 1200 hm, legs are cooked",  # a report, not a goal
        "Could I do 4000 hm in one day?",  # a question
        "I want to ride 4000 m",  # metres of what?
        "Mein Ziel sind 40000000 hm",  # not a real number for one ride
        "I want to feel fresher on Sunday",  # no number
        "I will ride 80 km tomorrow",  # a plan for one day
        "Morgen will ich 1500 hm fahren",  # the same, in German
        "Mein Kumpel will 3000 hm fahren",  # someone else's wish
        "My friend's goal is 3000 hm",  # someone else's goal
        "I did 3000 hm, which was my goal",  # a result, not a goal
        "My friend's goal is 3000 hm, I think that's too much",  # still theirs
    ],
)
def test_other_sentences_are_not_goals(message):
    assert gp.extract_goal(message) is None


def test_each_measure_takes_its_own_scope():
    goals = gp.extract_goals("My goal: 100 km a week and 3000 hm in one ride")

    assert {(g.metric, g.scope) for g in goals} == {
        (gp.DISTANCE, gp.WEEK),
        (gp.ELEVATION, gp.RIDE),
    }
    assert gp.extract_goal("Mein Ziel: pro Woche 10000 hm").scope == gp.WEEK
    assert (
        gp.extract_goal("My goal is 4000 hm in one ride, I train twice a week").scope
        == gp.RIDE
    )
    assert gp.extract_goal("My goal is 4000 hm, I ride twice a week").scope == gp.RIDE
    assert gp.extract_goal("Ich möchte 10000 hm pro Woche schaffen").scope == gp.WEEK


def test_a_past_ride_in_the_same_message_keeps_the_goal_out():
    """Deliberately the safe direction: a report anywhere means no goal is stored,
    and the athlete can say the goal again on its own."""
    assert gp.extract_goal("I did 3000 hm last week and now I want 4000 hm") is None


def test_numbers_are_read_the_way_people_write_them():
    assert gp.extract_goal("Mein Ziel: 4'000 hm").target == 4000
    assert gp.extract_goal("My goal is 4,000 m of climbing").target == 4000
    assert gp.extract_goal("Mein Ziel: 2,5 km") is None  # a decimal, below any real ride


def test_two_measures_in_one_sentence_are_two_goals():
    goals = gp.extract_goals("My goal is 150 km and 3000 hm in one ride")

    assert {(g.metric, g.target) for g in goals} == {
        (gp.DISTANCE, 150),
        (gp.ELEVATION, 3000),
    }


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def test_progress_is_measured_against_the_biggest_recent_ride():
    rows = [_ride(3, 1500), _ride(20, 1800), _ride(60, 2100), _ride(120, 3900)]
    progress = gp.goal_progress(gp.ELEVATION, gp.RIDE, 4000, rows, TODAY)

    assert progress.best_recent == 1800
    assert progress.best_baseline == 2100  # the 120-day-old ride is too old
    assert progress.gap == 1900
    assert progress.next_step == pytest.approx(2415)
    section = gp.goal_progress_section([(SimpleNamespace(deadline=None), progress)])
    assert "4,000 m of climbing in one ride" in section
    assert "Gap: 1,900 m" in section
    assert "2,415 m" in section


def test_a_bigger_ride_moves_the_gap():
    rows = [_ride(10, 2100)]
    before = gp.goal_progress(gp.ELEVATION, gp.RIDE, 4000, rows, TODAY)
    after = gp.goal_progress(gp.ELEVATION, gp.RIDE, 4000, rows + [_ride(1, 2800)], TODAY)

    assert after.gap < before.gap


def test_weekly_goals_sum_a_rolling_week():
    rows = [_ride(1, 1000), _ride(3, 1500), _ride(5, 1200), _ride(30, 2000)]
    progress = gp.goal_progress(gp.ELEVATION, gp.WEEK, 5000, rows, TODAY)

    assert progress.best_recent == 3700


def test_two_rides_on_one_day_both_count_toward_a_week():
    rows = [_ride(2, 800), _ride(2, 700), _ride(4, 1000)]
    progress = gp.goal_progress(gp.ELEVATION, gp.WEEK, 5000, rows, TODAY)

    assert progress.best_recent == 2500


def test_no_data_is_said_rather_than_guessed():
    progress = gp.goal_progress(gp.ELEVATION, gp.RIDE, 4000, [_ride(2, None)], TODAY)

    assert progress.best_baseline is None
    assert "progress is unknown" in gp.goal_progress_section([(object(), progress)])


def test_a_reached_goal_is_acknowledged():
    progress = gp.goal_progress(gp.ELEVATION, gp.RIDE, 4000, [_ride(5, 4100)], TODAY)

    assert progress.reached
    assert "Reached" in gp.goal_progress_section([(object(), progress)])


# ---------------------------------------------------------------------------
# Import keeps the measures the goal needs
# ---------------------------------------------------------------------------


def test_provider_figures_are_kept_and_absence_stays_absent():
    assert reported_distance_km(84213.4) == 84.21
    assert reported_distance_km(0) is None
    assert reported_elevation_m(2134.6) == 2134.6
    assert reported_elevation_m(None) is None

    ride = ImportedActivity(
        source="strava",
        external_activity_id="1",
        name="Climb",
        start_datetime=None,
        activity_date="2026-10-10",
        distance_km=84.2,
        elevation_gain_m=2134.6,
    ).to_ride_input()
    assert (ride["distance_km"], ride["elevation_gain_m"]) == (84.2, 2134.6)


@pytest.mark.asyncio
async def test_a_later_sync_without_the_figure_does_not_erase_it():
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email="goal-import@example.com", name="R", hashed_password="x"
        )
        common = dict(strava_activity_id=42, activity_date="2026-10-10")
        await crud.upsert_ride_metric(db, user.id, elevation_gain_m=2100.0, **common)
        row = await crud.upsert_ride_metric(db, user.id, **common)
        await db.commit()

    assert row.elevation_gain_m == 2100.0


# ---------------------------------------------------------------------------
# Wiring: chat capture and every plan writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_goal_said_in_chat_reaches_the_plan_writers(
    client, auth_headers, mock_ai_service
):
    await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Mein Ziel ist 4000 hm an einem Tag."},
    )

    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        goals = await crud.list_active_goals(db, user.id)
        context = await plan_context.plan_writer_context(db, user.id, [])

    assert [(g.metric, g.scope, g.target) for g in goals] == [
        (gp.ELEVATION, gp.RIDE, 4000)
    ]
    assert "4,000 m of climbing in one ride" in context.coherence

    response = await client.post(
        "/api/v1/ai/generate-plan", headers=auth_headers, json={}
    )
    assert response.status_code == 200
    section = mock_ai_service["generate_training_plan"].call_args.kwargs[
        "athlete_model_section"
    ]
    assert "4,000 m of climbing in one ride" in section


@pytest.mark.asyncio
async def test_a_new_target_replaces_the_old_one():
    async with TestSessionLocal() as db:
        user = models.User(
            email="goal-replace@example.com",
            name="R",
            hashed_password=hash_password("Str0ng!Pass"),
        )
        db.add(user)
        await db.flush()
        for target in (3000, 4000):
            await crud.upsert_active_goal(
                db, user.id, metric=gp.ELEVATION, scope=gp.RIDE, target=target
            )
        goals = await crud.list_active_goals(db, user.id)
        replaced = await db.scalars(
            select(models.AthleteGoal).where(models.AthleteGoal.status == "replaced")
        )
        await db.commit()

    assert [g.target for g in goals] == [4000]
    assert [g.target for g in replaced] == [3000]


@pytest.mark.asyncio
async def test_going_back_to_an_old_target_starts_a_new_row():
    async with TestSessionLocal() as db:
        user = await crud.create_user(
            db, email="goal-aba@example.com", name="R", hashed_password="x"
        )
        for target in (3000, 4000, 3000):
            await crud.upsert_active_goal(
                db, user.id, metric=gp.ELEVATION, scope=gp.RIDE, target=target
            )
        rows = (
            await db.scalars(
                select(models.AthleteGoal).order_by(models.AthleteGoal.created_at)
            )
        ).all()
        await db.commit()

    assert [(g.target, g.status) for g in rows] == [
        (3000, "replaced"),
        (4000, "replaced"),
        (3000, "active"),
    ]
