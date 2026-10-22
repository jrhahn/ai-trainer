"""The plan rebuilds itself when what it was built from moved (ai-trainer-ops#206)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

import crud
import models
from services import plan_inputs, plan_maintenance
from services.dates import app_today, app_today_iso
from tests.conftest import TestSessionLocal

REBUILD = "/api/v1/ai/plan-rebuild"
CHANGED = "/api/v1/ai/plan-inputs-changed"


def _plan(title: str = "Endurance") -> list[dict]:
    start = app_today() + timedelta(days=1)
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "workoutType": "endurance",
            "title": title,
            "durationMinutes": 60,
        }
        for i in range(3)
    ]


async def _first_plan(client, auth_headers, mock_ai_service) -> None:
    """Onboarding's generation, which records what the plan was built from."""
    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"trainingDays": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
              "isOnboarded": True},
    )
    mock_ai_service["generate_training_plan"].return_value = _plan()
    assert (await client.post("/api/v1/ai/generate-plan", headers=auth_headers, json={})).status_code == 200
    mock_ai_service["generate_training_plan"].reset_mock()
    mock_ai_service["generate_training_plan"].return_value = _plan("Rebuilt")


async def _row() -> models.TrainingPlan:
    async with TestSessionLocal() as db:
        return await db.scalar(select(models.TrainingPlan))


# --- the record ------------------------------------------------------------------


def test_the_last_rebuild_is_kept_beside_the_inputs_and_goes_with_the_next_generation():
    row = models.TrainingPlan(plan=[], generated_inputs={"ftp": 250})
    assert plan_inputs.last_rebuild(row) is None

    plan_inputs.mark_rebuilt(row, ["training_days", "race"], "2026-10-22T10:00:00+00:00")

    assert plan_inputs.last_rebuild(row) == {"at": "2026-10-22T10:00:00+00:00", "reasons": ["training_days", "race"]}
    assert row.generated_inputs["ftp"] == 250
    # A rebuild record is no input: the comparison ignores it.
    inputs = {"trainingDays": [], "ftp": 250, "activities": 0, "race": None, "assumptions": [], "hours": None, "raceEvents": []}
    assert plan_inputs.reasons({**inputs, "rebuiltFor": ["ftp"], "rebuiltAt": "x"}, inputs) == []
    assert plan_inputs.last_rebuild(None) is None


def test_only_the_athletes_own_changes_rebuild_at_once():
    assert plan_inputs.ATHLETE_REASONS == {"training_days", "sports", "level", "ftp", "race", "hours"}
    assert not {"history", "assumptions"} & plan_inputs.ATHLETE_REASONS
    assert plan_inputs.ATHLETE_REASONS <= set(plan_inputs.REASONS)


# --- right after the athlete's change ----------------------------------------------


@pytest.mark.asyncio
async def test_a_change_the_athlete_made_rebuilds_the_plan_and_says_why(client, auth_headers, mock_ai_service):
    await _first_plan(client, auth_headers, mock_ai_service)
    await client.put("/api/v1/users/me", headers=auth_headers, json={"currentFTP": 280})

    response = await client.post(REBUILD, headers=auth_headers, json={})

    body = response.json()
    assert response.status_code == 200
    assert body["rebuilt"] is True
    assert body["reasons"] == ["ftp"]
    # The generation went through the pipeline like any other.
    assert "Rebuilt" in {d["title"] for d in body["plan"]}
    assert mock_ai_service["generate_training_plan"].call_count == 1
    changed = (await client.get(CHANGED, headers=auth_headers)).json()
    assert changed["changed"] is False
    assert changed["lastRebuild"]["reasons"] == ["ftp"]
    assert changed["rebuildsLeft"] == plan_inputs.DAILY_REBUILDS - 2

    # Asked again with nothing new: no second rebuild.
    again = (await client.post(REBUILD, headers=auth_headers, json={})).json()
    assert again["rebuilt"] is False
    assert mock_ai_service["generate_training_plan"].call_count == 1


@pytest.mark.asyncio
async def test_a_done_day_stays_through_the_rebuild(client, auth_headers, mock_ai_service):
    await _first_plan(client, auth_headers, mock_ai_service)
    async with TestSessionLocal() as db:
        row = await db.scalar(select(models.TrainingPlan))
        first = dict(row.plan[0])
        row.plan = [{**first, "completed": True}, *row.plan[1:]]
        await db.commit()
    await client.put("/api/v1/users/me", headers=auth_headers, json={"currentFTP": 300})

    body = (await client.post(REBUILD, headers=auth_headers, json={})).json()

    assert body["rebuilt"] is True
    by_date = {d["date"]: d for d in (await _row()).plan}
    assert by_date[first["date"]]["title"] == "Endurance"


@pytest.mark.asyncio
async def test_new_rides_alone_wait_for_the_nightly_run(client, auth_headers, mock_ai_service, monkeypatch):
    await _first_plan(client, auth_headers, mock_ai_service)

    async def more_rides(db, user):
        return {**(await real(db, user)), "activities": 40}

    real = plan_inputs.current_inputs
    monkeypatch.setattr(plan_inputs, "current_inputs", more_rides)

    body = (await client.post(REBUILD, headers=auth_headers, json={})).json()

    assert body == {"rebuilt": False, "reasons": ["history"], "plan": None, "limited": False}
    mock_ai_service["generate_training_plan"].assert_not_called()


@pytest.mark.asyncio
async def test_no_plan_no_rebuild(client, auth_headers, mock_ai_service):
    body = (await client.post(REBUILD, headers=auth_headers, json={})).json()

    assert body["rebuilt"] is False
    mock_ai_service["generate_training_plan"].assert_not_called()


@pytest.mark.asyncio
async def test_the_daily_cap_holds_and_the_change_waits(client, auth_headers, mock_ai_service):
    await _first_plan(client, auth_headers, mock_ai_service)
    async with TestSessionLocal() as db:
        row = await db.scalar(select(models.TrainingPlan))
        row.rebuild_count = plan_inputs.DAILY_REBUILDS
        await db.commit()
    await client.put("/api/v1/users/me", headers=auth_headers, json={"sports": ["cycling", "running"]})

    body = (await client.post(REBUILD, headers=auth_headers, json={})).json()

    assert body["rebuilt"] is False
    assert body["limited"] is True
    assert body["reasons"] == ["sports"]
    mock_ai_service["generate_training_plan"].assert_not_called()


# --- the nightly run --------------------------------------------------------------


async def _user(db) -> models.User:
    # As the nightly run loads it: every relationship eager (crud.get_user_by_id).
    return await crud.get_user_by_id(db, (await db.scalar(select(models.User))).id)


async def _nightly() -> bool:
    async with TestSessionLocal() as db:
        user = await _user(db)
        done = await plan_maintenance.rebuild_if_inputs_changed(db, user, timezone_name="UTC")
        await db.commit()
        return done


@pytest.mark.asyncio
async def test_new_history_rebuilds_once_in_the_nightly_run(client, auth_headers, mock_ai_service, monkeypatch):
    await _first_plan(client, auth_headers, mock_ai_service)
    real = plan_inputs.current_inputs

    async def more_rides(db, user):
        return {**(await real(db, user)), "activities": 40}

    monkeypatch.setattr(plan_inputs, "current_inputs", more_rides)

    assert await _nightly() is True
    # The rebuild recorded the new count, so the next night has nothing to do:
    # one rebuild for the whole history, not one per ride.
    assert await _nightly() is False
    assert mock_ai_service["generate_training_plan"].call_count == 1
    assert plan_inputs.last_rebuild(await _row())["reasons"] == ["history"]


@pytest.mark.asyncio
async def test_the_nightly_run_respects_the_cap(client, auth_headers, mock_ai_service, monkeypatch):
    await _first_plan(client, auth_headers, mock_ai_service)
    async with TestSessionLocal() as db:
        row = await db.scalar(select(models.TrainingPlan))
        # The nightly run counts on the athlete's day, here in UTC.
        row.rebuild_day = app_today_iso(timezone_name="UTC")
        row.rebuild_count = plan_inputs.DAILY_REBUILDS
        await db.commit()
    await client.put("/api/v1/users/me", headers=auth_headers, json={"fitnessLevel": "advanced"})

    assert await _nightly() is False
    mock_ai_service["generate_training_plan"].assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_nightly_generation_is_skipped_not_raised(client, auth_headers, mock_ai_service):
    await _first_plan(client, auth_headers, mock_ai_service)
    await client.put("/api/v1/users/me", headers=auth_headers, json={"fitnessLevel": "beginner"})
    mock_ai_service["generate_training_plan"].return_value = []  # an empty plan is a 502

    assert await _nightly() is False
    assert plan_inputs.last_rebuild(await _row()) is None


@pytest.mark.asyncio
async def test_the_nightly_maintenance_rebuilds_instead_of_adapting(client, auth_headers, mock_ai_service, monkeypatch):
    await _first_plan(client, auth_headers, mock_ai_service)
    await client.put("/api/v1/users/me", headers=auth_headers, json={"fitnessLevel": "beginner"})
    adapted = []

    async def fake_adapt(*args, **kwargs):
        adapted.append(1)
        return []

    monkeypatch.setattr(plan_maintenance.ai_service, "adapt_training_plan", fake_adapt)

    async with TestSessionLocal() as db:
        user = await _user(db)
        updated = await plan_maintenance.maintain_user_training_plan(db, user, timezone_name="UTC")
        await db.commit()

    assert updated is True
    assert adapted == []
    assert mock_ai_service["generate_training_plan"].call_count == 1
