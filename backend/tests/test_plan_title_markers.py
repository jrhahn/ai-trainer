"""The data markers never reach a stored or shown plan day (2026-10-15).

Plan writers are shown the remaining days with every title marked «…» as data,
and the nightly run copied the markers into the titles it wrote back, so the
week strip read «Aerobic Endurance Ride».
"""

from __future__ import annotations

import pytest

import crud
import models
import schemas
from services import plan_pipeline
from domain import untrusted_text
from services.prompts import adapt_plan_system, generate_plan_system
from tests.conftest import TestSessionLocal


def _day(date: str, title: str) -> dict:
    return {
        "date": date,
        "workoutType": "endurance",
        "title": title,
        "description": "Ride «steady» in zone 2.",
        "durationMinutes": 60,
        "keyFocusPoints": ["Hold «90» rpm"],
    }


def test_the_canonical_day_drops_the_markers_everywhere():
    day = schemas.canonical_plan_day(_day("2026-10-16", "«Aerobic Endurance Ride»"))

    assert day["title"] == "Aerobic Endurance Ride"
    assert day["description"] == "Ride steady in zone 2."
    assert day["keyFocusPoints"] == ["Hold 90 rpm"]


def test_both_plan_writers_are_told_what_the_markers_are():
    for system in (adapt_plan_system(), generate_plan_system()):
        assert untrusted_text.DATA_NOT_INSTRUCTION_RULE in system
    assert "leave « and » out" in untrusted_text.DATA_NOT_INSTRUCTION_RULE


@pytest.mark.asyncio
async def test_cleaning_a_stored_title_is_not_a_change_to_narrate(client, auth_headers):
    me = (await client.get("/api/v1/users/me", headers=auth_headers)).json()
    stored = [_day("2026-10-16", "«Aerobic Endurance Ride»")]
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, me["id"], stored)
        await db.commit()

    shown = (await client.get("/api/v1/users/me/plan", headers=auth_headers)).json()[
        "plan"
    ]
    assert shown[0]["title"] == "Aerobic Endurance Ride"

    async with TestSessionLocal() as db:
        user = await db.get(models.User, me["id"])
        commit = await plan_pipeline.commit_plan(
            db, user, stored, base_plan=stored, source="nightly_maintenance"
        )
        await db.commit()

    assert commit.plan[0]["title"] == "Aerobic Endurance Ride"
    assert commit.applied_changes == []


def test_unmark_walks_dicts_and_lists_and_leaves_the_rest():
    value = {"«key»": ["«a»", {"b": "x«»y"}], "n": 3, "none": None, "flag": True}

    assert untrusted_text.unmark(value) == {
        "«key»": ["a", {"b": "xy"}],
        "n": 3,
        "none": None,
        "flag": True,
    }
