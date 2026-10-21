"""A race said in chat is offered for the calendar, and rebuilds are limited (ai-trainer-ops#175)."""

from datetime import date, timedelta
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

import models
from services import plan_inputs
from services.dates import app_today
from services.prompts import ask_trainer_plan_updates_rule
from services.race_intent import stated_race_date
from tests.conftest import TestSessionLocal

TODAY = date(2026, 10, 21)  # a Wednesday
ASK = "/api/v1/ai/ask-trainer"
CHANGED = "/api/v1/ai/plan-inputs-changed"
GENERATE = "/api/v1/ai/generate-plan"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # The owner's message, from the self-test.
        ("please update the training plan accordingly i have a race in 3 weeks. my ftp is 440w", "2026-11-11"),
        ("Ich habe in drei Wochen ein Rennen.", "2026-11-11"),
        ("Wettkampf in einem Monat", "2026-11-21"),
        ("I have a race in 10 days, how should I taper?", "2026-10-31"),
        ("race in 2 months", "2026-12-21"),
        ("Mein Rennen ist am 15.11.", "2026-11-15"),
        ("Rennen am 3. Mai 2027", "2027-05-03"),
        ("Radmarathon am 1.6.27", "2027-06-01"),
        ("race on 2027-05-01", "2027-05-01"),
        ("I have a gran fondo on 12 June", "2027-06-12"),
        ("the race is March 8th, 2027", "2027-03-08"),
        ("race on Nov. 12", "2026-11-12"),
        # A date already gone this year is next year's.
        ("my race is on 1 October", "2027-10-01"),
        ("Last week was hard. Race in 2 weeks!", "2026-11-04"),
    ],
)
def test_a_race_ahead_is_read_with_its_date(message, expected):
    assert stated_race_date(message, TODAY) == date.fromisoformat(expected)


@pytest.mark.parametrize(
    "message",
    [
        "I have no race in 3 weeks",
        "race pace in 3 weeks",
        "ride 7.5 h in 2 weeks",
        "My race on 12 October went well",
        "rennen vor 3 wochen",
        "I had a race 2 weeks ago",
        "a race",
        "in 3 weeks I ride with friends",
        "race in 0 days",
        "race on 2029-01-01",  # beyond the horizon
        "race on 31.2.",  # no such day
    ],
)
def test_no_race_ahead_no_date(message):
    assert stated_race_date(message, TODAY) is None


def test_a_month_ahead_keeps_to_the_months_last_day():
    assert stated_race_date("race in 1 month", date(2027, 1, 31)) == date(2027, 2, 28)
    assert stated_race_date("race in 13 months", date(2026, 12, 15)) == date(2028, 1, 15)


async def _user() -> models.User:
    async with TestSessionLocal() as db:
        return await db.scalar(select(models.User))


@pytest.mark.asyncio
async def test_the_chat_offers_the_race_with_its_date_and_saves_nothing(client, auth_headers, mock_ai_service):
    response = await client.post(ASK, headers=auth_headers, json={"question": "I have a race in 3 weeks."})

    expected = (app_today() + timedelta(weeks=3)).isoformat()
    assert response.json()["raceOffer"] == {"date": expected}
    async with TestSessionLocal() as db:
        assert (await db.scalars(select(models.RaceEvent))).all() == []


@pytest.mark.asyncio
async def test_a_race_already_in_the_calendar_or_profile_is_not_offered_again(client, auth_headers, mock_ai_service):
    day = (app_today() + timedelta(weeks=3)).isoformat()
    user = await _user()
    async with TestSessionLocal() as db:
        db.add(models.RaceEvent(user_id=user.id, date=day, distance_km=100, elevation_m=1500))
        await db.commit()
    response = await client.post(ASK, headers=auth_headers, json={"question": "I have a race in 3 weeks."})
    assert response.json()["raceOffer"] is None

    later = (app_today() + timedelta(weeks=5)).isoformat()
    async with TestSessionLocal() as db:
        row = await db.get(models.User, user.id)
        row.race_date = later
        await db.commit()
    response = await client.post(ASK, headers=auth_headers, json={"question": "I have a race in 5 weeks."})
    assert response.json()["raceOffer"] is None


@pytest.mark.asyncio
async def test_no_race_and_a_new_race_are_never_offered_together(client, auth_headers, mock_ai_service):
    async with TestSessionLocal() as db:
        row = await db.scalar(select(models.User))
        row.training_goal, row.race_date = "race", "2099-05-01"
        await db.commit()

    response = await client.post(ASK, headers=auth_headers, json={"question": "I have no race planned. Race in 3 weeks."})

    assert response.json()["raceProposal"] is not None
    assert response.json()["raceOffer"] is None


@pytest.mark.asyncio
async def test_a_race_added_to_the_calendar_brings_up_the_rebuild(client, auth_headers, mock_ai_service):
    assert (await client.post(GENERATE, headers=auth_headers, json={})).status_code == 200
    assert (await client.get(CHANGED, headers=auth_headers)).json()["changed"] is False

    added = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={"date": "2099-05-01", "distanceKm": 120, "elevationM": 2000},
    )
    assert added.status_code == 200
    assert (await client.get(CHANGED, headers=auth_headers)).json()["reasons"] == ["race"]

    # A changed course is news as well, after the next rebuild.
    assert (await client.post(GENERATE, headers=auth_headers, json={})).status_code == 200
    assert (await client.get(CHANGED, headers=auth_headers)).json()["changed"] is False
    event_id = added.json()["id"]
    await client.put(
        f"/api/v1/users/me/race-events/{event_id}",
        headers=auth_headers,
        json={"date": "2099-05-01", "distanceKm": 160, "elevationM": 2000},
    )
    assert (await client.get(CHANGED, headers=auth_headers)).json()["reasons"] == ["race"]


def test_a_record_from_before_calendar_races_compares_with_none():
    before = {"trainingDays": [], "ftp": 250, "activities": 0, "race": None, "assumptions": [], "hours": None}
    now = {**before, "raceEvents": []}
    assert plan_inputs.reasons(before, now) == []
    assert plan_inputs.reasons(before, {**now, "raceEvents": [["2099-05-01", 120.0, 2000]]}) == ["race"]


@pytest.mark.asyncio
async def test_six_rebuilds_a_day_then_the_server_refuses(client, auth_headers, mock_ai_service):
    assert (await client.get(CHANGED, headers=auth_headers)).json()["rebuildsLeft"] == 6
    for left in (5, 4, 3, 2, 1, 0):
        assert (await client.post(GENERATE, headers=auth_headers, json={})).status_code == 200
        assert (await client.get(CHANGED, headers=auth_headers)).json()["rebuildsLeft"] == left
    calls = mock_ai_service["generate_training_plan"].await_count

    refused = await client.post(GENERATE, headers=auth_headers, json={})

    assert refused.status_code == 429
    assert refused.json()["detail"] == (
        "You have rebuilt your plan 6 times today. You can rebuild it again tomorrow."
    )
    # Refused before the model is asked.
    assert mock_ai_service["generate_training_plan"].await_count == calls


@pytest.mark.asyncio
async def test_the_count_starts_again_on_the_athletes_next_day(client, auth_headers, mock_ai_service):
    for _ in range(6):
        await client.post(GENERATE, headers=auth_headers, json={})
    user = await _user()
    async with TestSessionLocal() as db:
        plan = await db.scalar(select(models.TrainingPlan).where(models.TrainingPlan.user_id == user.id))
        plan.rebuild_day = (app_today() - timedelta(days=1)).isoformat()
        await db.commit()

    assert (await client.get(CHANGED, headers=auth_headers)).json()["rebuildsLeft"] == 6
    assert (await client.post(GENERATE, headers=auth_headers, json={})).status_code == 200
    assert (await client.get(CHANGED, headers=auth_headers)).json()["rebuildsLeft"] == 5


@pytest.mark.asyncio
async def test_the_day_is_the_athletes_stored_zone_not_the_requests(client, auth_headers, mock_ai_service):
    user = await _user()
    async with TestSessionLocal() as db:
        row = await db.get(models.User, user.id)
        row.timezone_name = "Pacific/Kiritimati"  # UTC+14
        await db.commit()
    await client.post(GENERATE, headers=auth_headers, json={})
    async with TestSessionLocal() as db:
        plan = await db.scalar(select(models.TrainingPlan).where(models.TrainingPlan.user_id == user.id))
    assert plan.rebuild_day == app_today(timezone_name="Pacific/Kiritimati").isoformat()
    assert plan.rebuild_count == 1


def test_rebuilds_left_counts_only_today():
    plan = models.TrainingPlan(rebuild_day="2026-10-21", rebuild_count=4)
    assert plan_inputs.rebuilds_left(None, "2026-10-21") == 6
    assert plan_inputs.rebuilds_left(plan, "2026-10-21") == 2
    assert plan_inputs.rebuilds_left(plan, "2026-10-22") == 6
    plan_inputs.count_rebuild(plan, "2026-10-22")
    assert (plan.rebuild_day, plan.rebuild_count) == ("2026-10-22", 1)
    plan.rebuild_count = 9
    assert plan_inputs.rebuilds_left(plan, "2026-10-22") == 0


@pytest.mark.parametrize("context_workout", [None, {"date": "2026-10-21"}])
def test_the_coach_says_what_it_left_alone_and_calls_a_stated_value_stated(context_workout):
    rule = ask_trainer_plan_updates_rule(context_workout)
    assert "which days you changed, which you left alone" in rule
    assert "never consistent" in rule
    assert "say which FTP your targets use" in rule


def test_the_migration_adds_the_columns_once(tmp_path):
    path = Path(__file__).parent.parent / "alembic/versions/20261021_000002_plan_daily_rebuilds.py"
    spec = importlib.util.spec_from_file_location("_plan_rebuilds_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'plans.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE training_plans (id VARCHAR(36) PRIMARY KEY)"))

    def run(direction: str) -> set[str]:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, direction)()
        return {c["name"] for c in sa.inspect(engine).get_columns("training_plans")}

    assert {"rebuild_day", "rebuild_count"} <= run("upgrade")
    assert {"rebuild_day", "rebuild_count"} <= run("upgrade")
    assert not {"rebuild_day", "rebuild_count"} & run("downgrade")
    assert not {"rebuild_day", "rebuild_count"} & run("downgrade")
