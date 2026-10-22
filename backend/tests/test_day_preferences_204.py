"""A weekday the athlete prefers over another (ai-trainer-ops#204)."""

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select

import crud
import models
import schemas
from routers.ai import _offers_for_prompt
from services import day_preference
from services.dates import app_today
from services.prompts import ask_trainer_plan_updates_rule
from tests.conftest import TestSessionLocal

ALL_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
OWNER = ["tuesday", "thursday", "saturday", "sunday"]
MONDAY_OVER_TUESDAY = {"prefer": "monday", "over": "tuesday"}


# --- reading the sentence ------------------------------------------------------


@pytest.mark.parametrize(
    "message, prefer, over, standing",
    [
        ("Montag passt mir meistens besser als Dienstag.", "monday", "tuesday", True),
        ("Monday generally works better for me than Tuesday.", "monday", "tuesday", True),
        # The owner's sentence on the test account, 2026-10-22.
        ("Also, Monday is generally better than tuesday", "monday", "tuesday", True),
        ("I prefer Mondays over Tuesdays.", "monday", "tuesday", False),
        ("Ich würde lieber am Samstag als am Sonntag fahren.", "saturday", "sunday", False),
        ("Montag geht mir besser als Dienstag.", "monday", "tuesday", False),
        ("Sonntag ist mir normalerweise lieber als Samstag.", "sunday", "saturday", True),
        ("I'd rather ride on Friday than Thursday.", "friday", "thursday", False),
    ],
)
def test_a_day_preference_is_read(message, prefer, over, standing):
    assert day_preference.extract(message) == [{"prefer": prefer, "over": over, "standing": standing}]


@pytest.mark.parametrize(
    "message",
    [
        # This week only: the chat moves the session, nothing is kept.
        "This week Monday works better than Tuesday.",
        "Diese Woche passt mir Montag besser als Dienstag.",
        "Tomorrow works better than Tuesday.",
        # A question.
        "Does Monday work better than Tuesday?",
        # A day with itself, and no comparison at all.
        "Monday is better than Monday.",
        "Monday works for me.",
        "I prefer long rides on Saturday.",
    ],
)
def test_no_preference_is_read_into_other_sentences(message):
    assert day_preference.extract(message) == []


def test_a_pair_said_again_moves_to_the_end_and_its_opposite_goes():
    kept = day_preference.merged(
        [{"prefer": "tuesday", "over": "monday"}, {"prefer": "saturday", "over": "sunday"}],
        [{"prefer": "monday", "over": "tuesday", "standing": True}],
    )
    assert kept == [{"prefer": "saturday", "over": "sunday"}, MONDAY_OVER_TUESDAY]


def test_only_the_newest_preferences_are_kept():
    pairs = [{"prefer": a, "over": b} for a, b in zip(ALL_DAYS, ALL_DAYS[1:])]
    assert day_preference.merged([], pairs) == pairs[-day_preference.MAX_PREFERENCES:]


def test_a_stored_value_that_is_not_a_pair_is_dropped():
    assert day_preference.merged([{"prefer": "someday", "over": "monday"}, "x", None], []) == []


def test_a_general_preference_for_a_free_day_is_a_swap_of_the_training_days():
    item = {"prefer": "monday", "over": "tuesday", "standing": True}
    assert day_preference.swapped_training_days(OWNER, item) == ["monday", "thursday", "saturday", "sunday"]
    # Not general, both training days already, or the other day free: no swap.
    assert day_preference.swapped_training_days(OWNER, {**item, "standing": False}) is None
    assert day_preference.swapped_training_days(ALL_DAYS, item) is None
    assert day_preference.swapped_training_days(["thursday"], item) is None


# --- the plan --------------------------------------------------------------------


TODAY = "2026-10-21"  # a Wednesday
MON, TUE = "2026-10-26", "2026-10-27"


def _day(iso: str, kind: str = "endurance", minutes: int = 60, **extra) -> dict:
    return {"date": iso, "slot": 0, "workoutType": kind, "title": kind, "durationMinutes": minutes, **extra}


def _apply(plan, *, preferences=(MONDAY_OVER_TUESDAY,), days=WEEKDAYS, protected=frozenset()):
    return day_preference.apply(
        plan, preferences=list(preferences), training_days=days, today=TODAY, protected=set(protected)
    )


def test_the_key_session_goes_to_the_preferred_day():
    plan, reasons = _apply([_day(MON, "endurance", 45), _day(TUE, "threshold", 60)])

    assert [(d["date"], d["workoutType"]) for d in plan] == [(MON, "threshold"), (TUE, "endurance")]
    assert reasons == {(MON, 0): day_preference.REASON.format(day="Monday")}


def test_a_long_easy_session_goes_there_too():
    plan, _ = _apply([_day(MON, "recovery", 30), _day(TUE, "endurance", 120)])
    assert plan[0]["durationMinutes"] == 120


def test_intensity_counts_before_a_somewhat_longer_easy_session():
    plan, reasons = _apply([_day(MON, "threshold", 50), _day(TUE, "endurance", 90)])
    assert reasons == {}
    assert plan[0]["workoutType"] == "threshold"


def test_nothing_moves_when_the_preferred_day_already_has_the_heavier_session():
    plan = [_day(MON, "vo2max", 60), _day(TUE, "endurance", 60)]
    assert _apply(plan) == (plan, {})


def test_a_rest_day_on_the_preferred_day_takes_the_session():
    plan, _ = _apply([_day(MON, "rest", 0), _day(TUE, "endurance", 60)])
    assert [d["workoutType"] for d in plan] == ["endurance", "rest"]


@pytest.mark.parametrize(
    "plan, protected",
    [
        ([_day(MON, "endurance", 45), _day(TUE, "threshold", 60)], {MON}),
        ([_day(MON, "endurance", 45), _day(TUE, "threshold", 60)], {TUE}),
        ([_day(MON, "endurance", 45), _day(TUE, "threshold", 60, completed=True)], set()),
        # Two sessions on a day are not traded as one.
        ([_day(MON, "endurance", 45), _day(TUE, "threshold", 60), _day(TUE, "strength", 30, slot=1)], set()),
    ],
)
def test_a_protected_day_a_done_day_and_a_two_a_day_stay(plan, protected):
    assert _apply(plan, protected=protected) == (plan, {})


def test_a_hard_session_is_not_moved_next_to_another_hard_one():
    # Sunday's VO2max session sits beside Monday; Tuesday's threshold stays put.
    plan = [_day("2026-10-25", "vo2max", 60), _day(MON, "endurance", 45), _day(TUE, "threshold", 60)]
    assert _apply(plan) == (plan, {})


def test_a_hard_session_may_move_when_its_old_day_was_the_neighbour():
    # Monday's neighbour on the other side is Tuesday itself, which it leaves.
    plan, reasons = _apply([_day(MON, "endurance", 45), _day(TUE, "threshold", 60), _day("2026-10-28", "endurance", 60)])
    assert plan[0]["workoutType"] == "threshold"
    assert reasons


def test_the_lighter_session_is_not_moved_next_to_a_hard_one_either():
    # Monday's 40-min threshold weighs 80, Tuesday's 90-min ride 90: the swap
    # would put the threshold on Tuesday, beside Wednesday's VO2max (review of #1015).
    plan = [_day(MON, "threshold", 40), _day(TUE, "endurance", 90), _day("2026-10-28", "vo2max", 60)]
    assert _apply(plan) == (plan, {})


def test_a_long_easy_session_moves_beside_a_hard_one():
    plan, _ = _apply([_day("2026-10-25", "vo2max", 60), _day(MON, "recovery", 30), _day(TUE, "endurance", 120)])
    assert plan[1]["durationMinutes"] == 120


def test_the_past_and_today_stay():
    plan = [_day("2026-10-19", "endurance", 45), _day("2026-10-20", "threshold", 60)]
    assert _apply(plan) == (plan, {})


def test_a_preference_for_a_day_that_is_not_a_training_day_is_not_acted_on():
    plan = [_day(MON, "endurance", 45), _day(TUE, "threshold", 60)]
    assert _apply(plan, days=OWNER) == (plan, {})


def test_every_week_of_the_plan_follows_it():
    plan = [
        _day(MON, "endurance", 45),
        _day(TUE, "threshold", 60),
        _day("2026-11-02", "recovery", 30),
        _day("2026-11-03", "vo2max", 60),
    ]
    out, reasons = _apply(plan)
    assert [d["workoutType"] for d in out] == ["threshold", "endurance", "vo2max", "recovery"]
    assert set(reasons) == {(MON, 0), ("2026-11-02", 0)}


def test_the_coach_sees_only_preferences_it_can_act_on():
    user = models.User(
        email="a@b.c",
        follows_training_plan=False,
        training_days=OWNER,
        day_preferences=[MONDAY_OVER_TUESDAY, {"prefer": "saturday", "over": "sunday"}],
    )
    profile = schemas.UserProfileSchema.from_user(user)
    assert profile.day_preferences == ["Saturday rather than Sunday"]
    user.day_preferences = [MONDAY_OVER_TUESDAY]
    assert schemas.UserProfileSchema.from_user(user).day_preferences is None


def test_the_chat_prompt_and_the_offer_say_it():
    assert "dayPreferences" in ask_trainer_plan_updates_rule(None)
    [offer] = _offers_for_prompt(
        None, None, {"training_days": ["monday", "thursday"], "standing_day_swap": True}
    )
    assert "holds from now on" in offer
    [plain] = _offers_for_prompt(None, None, {"training_days": ["monday", "thursday"]})
    assert "from now on" not in plain


# --- through the app -------------------------------------------------------------


async def _user(**fields) -> None:
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        for name, value in fields.items():
            setattr(user, name, value)
        await db.commit()


async def _stored_user() -> models.User:
    async with TestSessionLocal() as db:
        return await db.scalar(select(models.User))


@pytest.mark.asyncio
async def test_the_chat_keeps_a_preference_shows_it_and_takes_it_back(client, auth_headers, mock_ai_service):
    await _user(training_days=WEEKDAYS)

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Montag passt mir meistens besser als Dienstag."},
    )

    body = response.json()
    assert body["capturedDayPreferences"] == [MONDAY_OVER_TUESDAY]
    # Both are training days: a preference, not a change of the training days.
    assert body["profileProposal"] is None
    assert (await _stored_user()).day_preferences == [MONDAY_OVER_TUESDAY]
    me = (await client.get("/api/v1/users/me", headers=auth_headers)).json()
    assert me["dayPreferences"] == [MONDAY_OVER_TUESDAY]
    # The coach saw it in this very turn.
    profile = mock_ai_service["ask_trainer"].call_args.kwargs.get("profile") or mock_ai_service[
        "ask_trainer"
    ].call_args.args[2]
    assert profile["dayPreferences"] == ["Monday rather than Tuesday"]

    gone = await client.delete("/api/v1/users/me/day-preferences/monday/tuesday", headers=auth_headers)
    assert gone.status_code == 204
    assert (await _stored_user()).day_preferences == []
    # Removing one that is not there is no error.
    again = await client.delete("/api/v1/users/me/day-preferences/monday/tuesday", headers=auth_headers)
    assert again.status_code == 204


@pytest.mark.asyncio
async def test_the_owners_case_offers_a_lasting_swap_of_the_training_days(client, auth_headers, mock_ai_service):
    # Training days Tue, Thu, Sat, Sun: Monday is not one of them (comment 5345).
    await _user(training_days=OWNER)

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Also, Monday is generally better than tuesday"},
    )

    body = response.json()
    assert body["profileProposal"]["trainingDays"] == ["monday", "thursday", "saturday", "sunday"]
    assert body["profileProposal"]["standingDaySwap"] is True
    assert body["capturedDayPreferences"] == []
    stored = await _stored_user()
    assert stored.training_days == OWNER  # offered, not applied
    assert not stored.day_preferences


@pytest.mark.asyncio
async def test_a_one_week_wish_for_a_free_day_is_neither_kept_nor_offered(client, auth_headers, mock_ai_service):
    await _user(training_days=OWNER)

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Monday works better than Tuesday."},
    )

    body = response.json()
    assert body["profileProposal"] is None
    assert body["capturedDayPreferences"] == []


@pytest.mark.asyncio
async def test_a_regenerated_plan_puts_the_key_session_on_the_preferred_day(client, auth_headers, mock_ai_service):
    today = app_today()
    monday = today + timedelta(days=7 - today.weekday())  # next Monday, after today
    tuesday = monday + timedelta(days=1)
    await _user(training_days=WEEKDAYS, day_preferences=[MONDAY_OVER_TUESDAY])
    mock_ai_service["generate_training_plan"].return_value = [
        _day(monday.isoformat(), "endurance", 45),
        _day(tuesday.isoformat(), "threshold", 60),
    ]

    assert (await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})).status_code == 200

    async with TestSessionLocal() as db:
        stored = (await db.scalar(select(models.TrainingPlan))).plan
        reasons = (await db.scalars(select(models.PlanDayHistory.reason))).all()
    by_date = {d["date"]: d["workoutType"] for d in stored}
    assert by_date[monday.isoformat()] == "threshold"
    assert by_date[tuesday.isoformat()] == "endurance"
    assert day_preference.REASON.format(day="Monday") in reasons


@pytest.mark.asyncio
async def test_a_chat_change_keeps_the_day_the_athlete_asked_for(client, auth_headers, mock_ai_service):
    today = app_today()
    monday = today + timedelta(days=7 - today.weekday())
    tuesday = monday + timedelta(days=1)
    await _user(training_days=WEEKDAYS, day_preferences=[MONDAY_OVER_TUESDAY])
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        await crud.upsert_training_plan(
            db, user.id, [_day(monday.isoformat(), "threshold", 60), _day(tuesday.isoformat(), "endurance", 45)]
        )
        await db.commit()
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Intervals on Tuesday, as you asked.",
        "plan_updates": [_day(monday.isoformat(), "endurance", 45), _day(tuesday.isoformat(), "threshold", 60)],
        "sources": [],
    }

    await client.post(
        "/api/v1/ai/ask-trainer", headers=auth_headers, json={"question": "Put the intervals on Tuesday please."}
    )

    async with TestSessionLocal() as db:
        stored = (await db.scalar(select(models.TrainingPlan))).plan
    assert {d["date"]: d["workoutType"] for d in stored}[tuesday.isoformat()] == "threshold"


def test_the_migration_adds_the_column_once(tmp_path):
    path = Path(__file__).parent.parent / "alembic/versions/20261022_000002_day_preferences.py"
    spec = importlib.util.spec_from_file_location("_day_preferences_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'users.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))

    def run(direction: str) -> set[str]:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, direction)()
        return {c["name"] for c in sa.inspect(engine).get_columns("users")}

    assert "day_preferences" in run("upgrade")
    assert "day_preferences" in run("upgrade")
    assert "day_preferences" not in run("downgrade")
    assert "day_preferences" not in run("downgrade")
