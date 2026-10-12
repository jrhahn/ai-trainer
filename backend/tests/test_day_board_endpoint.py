"""The day board the athlete can read is the one the planner got (ai-trainer-ops#17, #39 Q9)."""

from pathlib import Path

import crud
import models
import pytest
from services import motivation_model as mm
from services import weather_preference, weather_service
from services.weather_preference import DIRECTION_SENSITIVE
from sqlalchemy import select

from tests.conftest import TestSessionLocal

REPO_ROOT = Path(__file__).resolve().parents[2]


async def _athlete_with_an_objective(*, memory: bool = True) -> None:
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.memory_updates_enabled = memory
        await crud.upsert_athlete_motivation_model(
            db,
            user.id,
            updates={
                "primary_objective": "Ride technical trails",
                "utility_weights": {
                    "enjoyment": 0.40,
                    "adaptation": 0.20,
                    "consistency": 0.20,
                    "health": 0.20,
                    "race_performance": 0.0,
                },
                "modality_affinity": {"mtb": 0.9, "road": 0.4},
            },
            source=mm.SOURCE_USER_SET,
        )
        await db.commit()


@pytest.mark.asyncio
async def test_the_board_comes_with_its_deductions_and_its_source(client, auth_headers):
    await _athlete_with_an_objective()

    response = await client.get("/api/v1/users/me/day-board", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    options = body["options"]
    assert len(options) == 5
    for option in options:
        assert option["score"] == pytest.approx(
            option["base"] - option["heatCost"] - option["freshnessCost"], abs=0.02
        )
        # A freshness charge always says what it competes with, in words.
        assert bool(option["freshnessCost"]) == bool(option["competesWithLabel"])
    assert [o["score"] for o in options] == sorted(
        (o["score"] for o in options), reverse=True
    )

    # The link lands on the function that computed it, not near it.
    source = body["source"]
    lines = (REPO_ROOT / source["path"]).read_text().splitlines()
    assert lines[source["line"] - 1].startswith(f"def {source['symbol']}(")


@pytest.mark.asyncio
async def test_nothing_known_means_no_board(client, auth_headers):
    response = await client.get("/api/v1/users/me/day-board", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["options"] == []


@pytest.mark.asyncio
async def test_memory_off_means_no_board_even_with_an_objective(client, auth_headers):
    """The planner gets no board then, so the athlete is not shown one either."""
    await _athlete_with_an_objective(memory=False)

    response = await client.get("/api/v1/users/me/day-board", headers=auth_headers)

    assert response.json()["options"] == []


@pytest.mark.asyncio
async def test_a_hot_day_for_a_heat_sensitive_athlete_shows_the_heat_cost(
    client, auth_headers, monkeypatch
):
    await _athlete_with_an_objective()

    async def hot_forecast(db, user_id, horizon_days):
        return None, [{"temperature_max_c": 34.0}]

    async def sensitive(db, user_id):
        return DIRECTION_SENSITIVE

    monkeypatch.setattr(weather_service, "daily_forecast_for_user", hot_forecast)
    monkeypatch.setattr(weather_preference, "heat_tolerance_for_user", sensitive)

    body = (await client.get("/api/v1/users/me/day-board", headers=auth_headers)).json()

    assert body["hot"] is True
    assert body["heatTolerance"] == DIRECTION_SENSITIVE
    assert any(option["heatCost"] > 0 for option in body["options"])


@pytest.mark.asyncio
async def test_the_board_needs_a_signed_in_athlete(client):
    response = await client.get("/api/v1/users/me/day-board")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_the_link_is_pinned_to_the_deployed_commit(
    client, auth_headers, monkeypatch
):
    """A line number into ``develop`` drifts with every merge; the SHA does not."""
    from config import settings

    response = await client.get("/api/v1/users/me/day-board", headers=auth_headers)
    assert response.json()["source"]["ref"] == "develop"

    monkeypatch.setattr(settings, "git_sha", "0a1b2c3d")
    response = await client.get("/api/v1/users/me/day-board", headers=auth_headers)
    assert response.json()["source"]["ref"] == "0a1b2c3d"
