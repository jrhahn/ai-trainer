"""A medication that blunts the heart rate turns heart-rate targets off (ai-trainer-ops#184)."""

from datetime import timedelta
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

import models
from services.dates import app_today
from services.heart_rate_caution import (
    prompt_profile,
    says_blunted,
    says_stopped,
    sets_heart_rate_target,
    without_heart_rate_targets,
)
from tests.conftest import TestSessionLocal

ASK = "/api/v1/ai/ask-trainer"
WERNER = "Ich nehme Betablocker gegen Bluthochdruck. Mein Puls geht kaum noch über 140."


@pytest.mark.parametrize(
    "message",
    [
        WERNER,
        "I take bisoprolol for my blood pressure.",
        "Seit März nehme ich Metoprolol.",
        "My heart rate won't go above 130 anymore.",
        "Mein Puls kommt nicht mehr über 135.",
    ],
)
def test_a_blunted_heart_rate_is_heard(message):
    assert says_blunted(message)
    assert not says_stopped(message)


@pytest.mark.parametrize(
    "message",
    [
        "Does a beta blocker affect training?",
        "My father takes beta blockers.",
        "Ich nehme keine Betablocker.",
        "I don't take beta blockers.",
        "My HR was 150 today.",
        "Ich nehme keine Betablocker mehr.",
    ],
)
def test_no_blunted_heart_rate(message):
    assert not says_blunted(message)


@pytest.mark.parametrize(
    "message", ["Ich nehme keine Betablocker mehr.", "I stopped taking metoprolol.", "Den Betablocker habe ich abgesetzt."]
)
def test_stopping_is_heard(message):
    assert says_stopped(message)


BPM_DAY = {
    "date": "2099-01-01",
    "workoutType": "endurance",
    "title": "Endurance",
    "description": "Easy spin. Keep your heart rate under 132 bpm. Stay relaxed.",
    "targetHeartRate": {"low": 100, "high": 132},
    "keyFocusPoints": ["Hold 133–145 bpm on the climb", "Cadence around 90"],
    "intervals": [{"duration": 60, "heartRate": 140, "rest": 60}],
    "durationMinutes": 60,
}


def test_every_heart_rate_target_goes_and_the_rest_stays():
    cleaned = without_heart_rate_targets(BPM_DAY)

    assert "targetHeartRate" not in cleaned
    assert cleaned["description"] == "Easy spin. Stay relaxed."
    assert cleaned["keyFocusPoints"] == ["Cadence around 90"]
    assert cleaned["intervals"] == [{"duration": 60, "rest": 60}]
    assert cleaned["durationMinutes"] == 60
    assert sets_heart_rate_target(BPM_DAY) and not sets_heart_rate_target(cleaned)


def test_the_prompts_get_no_max_hr_while_it_holds():
    profile = {"maxHeartRate": 165, "currentFTP": 200}
    assert prompt_profile(profile, False) == profile
    held = prompt_profile(profile, True)
    assert "maxHeartRate" not in held
    assert "no heart-rate targets" in held["heartRateTargets"]


async def _user() -> models.User:
    async with TestSessionLocal() as db:
        return await db.scalar(select(models.User))


async def _seed_plan(days: list[dict]) -> None:
    user = await _user()
    async with TestSessionLocal() as db:
        db.add(models.TrainingPlan(user_id=user.id, plan=days))
        await db.commit()


def _ahead(offset: int, **fields) -> dict:
    return {**BPM_DAY, "date": (app_today() + timedelta(days=offset)).isoformat(), **fields}


@pytest.mark.asyncio
async def test_werners_message_removes_the_targets_from_his_plan_at_once(client, auth_headers, mock_ai_service):
    await _seed_plan([_ahead(1), _ahead(3, title="Intervals")])

    response = await client.post(ASK, headers=auth_headers, json={"question": WERNER})

    chips = response.json()["capturedStates"]
    assert {"id": "heart-rate-caution", "label": "Heart-rate targets are off: your plan steers by effort and power", "removable": True} in chips
    user = await _user()
    assert user.heart_rate_targets_off is True
    async with TestSessionLocal() as db:
        plan = (await db.scalar(select(models.TrainingPlan))).plan
        history = (await db.scalars(select(models.PlanDayHistory.source))).all()
    assert plan and not any(sets_heart_rate_target(day) for day in plan)
    assert "heart_rate_caution" in history
    # The coach of this very turn already saw the plan without them.
    seen_plan = mock_ai_service["ask_trainer"].await_args.args[1]
    assert not any(sets_heart_rate_target(day) for day in seen_plan)


@pytest.mark.asyncio
async def test_a_plan_written_while_it_holds_sets_no_heart_rate_target(client, auth_headers, mock_ai_service):
    async with TestSessionLocal() as db:
        row = await db.scalar(select(models.User))
        row.heart_rate_targets_off = True
        await db.commit()
    mock_ai_service["generate_training_plan"].return_value = [_ahead(1), _ahead(2)]

    assert (await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})).status_code == 200

    async with TestSessionLocal() as db:
        plan = (await db.scalar(select(models.TrainingPlan))).plan
    assert plan and not any(sets_heart_rate_target(day) for day in plan)
    profile = mock_ai_service["generate_training_plan"].await_args.args[0]
    assert "maxHeartRate" not in profile and "heartRateTargets" in profile


@pytest.mark.asyncio
async def test_remove_and_stopping_bring_heart_rate_back_for_new_sessions(client, auth_headers, mock_ai_service):
    await client.post(ASK, headers=auth_headers, json={"question": WERNER})
    assert (await client.delete("/api/v1/users/me/states/heart-rate-caution", headers=auth_headers)).status_code == 200
    assert (await _user()).heart_rate_targets_off is False

    await client.post(ASK, headers=auth_headers, json={"question": WERNER})
    response = await client.post(ASK, headers=auth_headers, json={"question": "Ich nehme keine Betablocker mehr."})
    assert {"id": "heart-rate-caution", "label": "Heart-rate targets can come back in new sessions", "removable": False} in response.json()["capturedStates"]
    assert (await _user()).heart_rate_targets_off is False


@pytest.mark.asyncio
async def test_without_the_flag_a_plan_keeps_its_heart_rate_targets(client, auth_headers, mock_ai_service):
    mock_ai_service["generate_training_plan"].return_value = [_ahead(1)]
    await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})
    async with TestSessionLocal() as db:
        plan = (await db.scalar(select(models.TrainingPlan))).plan
    assert any(sets_heart_rate_target(day) for day in plan)


def test_the_migration_adds_the_flag_once(tmp_path):
    path = Path(__file__).parent.parent / "alembic/versions/20261021_000003_heart_rate_targets_off.py"
    spec = importlib.util.spec_from_file_location("_hr_caution_migration", path)
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

    assert "heart_rate_targets_off" in run("upgrade")
    assert "heart_rate_targets_off" in run("upgrade")
    assert "heart_rate_targets_off" not in run("downgrade")
    assert "heart_rate_targets_off" not in run("downgrade")
