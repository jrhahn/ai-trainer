"""The day board the athlete can read is the one the planner got (ai-trainer-ops#17, #39 Q9)."""

from pathlib import Path

import pytest
from sqlalchemy import select

import crud
import models
from services import motivation_model as mm
from tests.conftest import TestSessionLocal

REPO_ROOT = Path(__file__).resolve().parents[2]


async def _athlete_with_an_objective() -> None:
    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.memory_updates_enabled = True
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
async def test_the_board_needs_a_signed_in_athlete(client):
    response = await client.get("/api/v1/users/me/day-board")
    assert response.status_code == 401
