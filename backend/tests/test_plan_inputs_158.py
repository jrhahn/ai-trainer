"""Offer a rebuild once the coach has learned something material (ai-trainer-ops#158)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select

import models
from services import plan_inputs
from tests.conftest import TestSessionLocal

CHANGED = "/api/v1/ai/plan-inputs-changed"
BASE = {
    "trainingDays": ["saturday", "wednesday"],
    "ftp": 250,
    "activities": 3,
    "race": None,
    "assumptions": [],
    "hours": None,
}


def _now(**changes) -> dict:
    return {**BASE, **changes}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, []),
        ({"trainingDays": ["saturday", "sunday", "wednesday"]}, ["training_days"]),
        ({"ftp": 262}, []),  # 4.8 %: noise between two estimates
        ({"ftp": 263}, ["ftp"]),  # 5.2 %
        ({"ftp": None}, ["ftp"]),
        ({"activities": 7}, []),
        ({"activities": 8}, ["history"]),  # the cold start ends
        ({"race": "2027-05-12"}, ["race"]),
        ({"assumptions": ["h1"]}, ["assumptions"]),
        ({"hours": 6}, ["hours"]),
        ({"race": "2027-05-12", "trainingDays": ["monday"], "activities": 30}, ["training_days", "history", "race"]),
    ],
)
def test_the_reasons_name_what_moved_enough(changes, expected):
    assert plan_inputs.reasons(BASE, _now(**changes)) == expected


def test_ten_more_activities_count_after_the_cold_start_too():
    before = {**BASE, "activities": 20}

    assert plan_inputs.reasons(before, _now(activities=29)) == []
    assert plan_inputs.reasons(before, _now(activities=30)) == ["history"]


def test_a_plan_without_a_record_is_not_offered_a_rebuild():
    assert plan_inputs.reasons(None, _now(activities=50)) == []
    assert plan_inputs.reasons({}, _now(activities=50)) == []


def test_an_assumption_settled_before_the_plan_is_no_reason():
    assert plan_inputs.reasons({**BASE, "assumptions": ["h1"]}, _now(assumptions=["h1"])) == []
    assert plan_inputs.reasons({**BASE, "assumptions": ["h1"]}, _now(assumptions=["h1", "h2"])) == ["assumptions"]


async def _user_id() -> str:
    async with TestSessionLocal() as db:
        return (await db.scalar(select(models.User))).id


async def _settle(category: str, status: str) -> None:
    user_id = await _user_id()
    async with TestSessionLocal() as db:
        db.add(models.AthleteHypothesis(user_id=user_id, statement=f"{category} {status}", statement_key=f"{category}-{status}", category=category, status=status))
        await db.commit()


@pytest.mark.asyncio
async def test_the_dashboard_hears_of_a_change_and_a_rebuild_clears_it(client, auth_headers, mock_ai_service):
    assert (await client.get(CHANGED, headers=auth_headers)).json() == {"changed": False, "reasons": []}
    assert (await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})).status_code == 200
    assert (await client.get(CHANGED, headers=auth_headers)).json() == {"changed": False, "reasons": []}

    await client.put("/api/v1/users/me", json={"trainingDays": ["monday", "thursday"]}, headers=auth_headers)
    await _settle("starting_weekly_volume", "confirmed")
    # Only a starting assumption the athlete settled counts, not any hypothesis.
    await _settle("performance_model", "confirmed")
    await _settle("starting_recovery", "proposed")

    assert (await client.get(CHANGED, headers=auth_headers)).json() == {
        "changed": True,
        "reasons": ["training_days", "assumptions"],
    }

    assert (await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})).status_code == 200
    assert (await client.get(CHANGED, headers=auth_headers)).json()["changed"] is False


@pytest.mark.asyncio
async def test_rides_count_towards_the_history(client, auth_headers, mock_ai_service):
    await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})
    user_id = await _user_id()
    async with TestSessionLocal() as db:
        for day in range(8):
            db.add(models.RideMetric(user_id=user_id, strava_activity_id=1000 + day, activity_date=f"2026-10-{day + 1:02d}", sport_type="cycling", duration_seconds=3600))
        await db.commit()

    assert (await client.get(CHANGED, headers=auth_headers)).json()["reasons"] == ["history"]


@pytest.mark.asyncio
async def test_recording_without_a_plan_does_nothing(client, auth_headers):
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        await plan_inputs.record(db, user)
        assert await plan_inputs.changes_since_generation(db, user) == []


def test_the_migration_adds_the_column_once(tmp_path):
    path = Path(__file__).parent.parent / "alembic/versions/20261020_000001_plan_generated_inputs.py"
    spec = importlib.util.spec_from_file_location("_plan_inputs_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'plans.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE training_plans (id VARCHAR(36) PRIMARY KEY)"))

    def run(direction: str) -> None:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(migration, direction)()

    run("upgrade")
    run("upgrade")
    assert "generated_inputs" in {c["name"] for c in sa.inspect(engine).get_columns("training_plans")}
    run("downgrade")
    run("downgrade")
    assert "generated_inputs" not in {c["name"] for c in sa.inspect(engine).get_columns("training_plans")}
