"""A session that names a power zone gets that zone's watts (ai-trainer-ops#75)."""

from datetime import date, timedelta

import pytest

from services.zone_label import align_target_to_named_zone

FTP = 320  # Z2 = 176–240 W at this FTP


def _day(description: str, low: int, high: int, **extra) -> dict:
    return {
        "date": "2026-10-11",
        "title": "Easy ride",
        "description": description,
        "targetPower": {"low": low, "high": high},
        **extra,
    }


def test_the_session_from_the_owners_chat_gets_its_z2_watts():
    # Described as Z2, targeted at recovery watts.
    day = _day("Steady Z2 endurance, keep it smooth.", 110, 145)

    aligned = align_target_to_named_zone(day, FTP)

    assert aligned["targetPower"] == {"low": 176, "high": 240}
    assert aligned["description"] == day["description"]


@pytest.mark.parametrize(
    "day",
    [
        _day("Steady Zone 2 endurance.", 190, 230),  # agrees
        _day("Steady Z2 endurance.", 160, 200),  # overlaps, close enough
        _day("Recovery spin, stay below Z2.", 110, 145),  # Z2 as a ceiling
        _day("Bleib unter Z2, ganz locker.", 110, 145),
        _day("Z2 with two blocks of Z4.", 110, 145),  # two zones named
        _day("Recovery spin in Z1.", 200, 230),  # Z1 is open below
        _day("Easy endurance.", 110, 145),  # no zone named
        _day("Steady Z2.", 110, 145, intervals=[{"durationMinutes": 5}]),
    ],
)
def test_anything_else_is_left_alone(day):
    assert align_target_to_named_zone(day, FTP) is day


def test_without_an_ftp_or_a_target_nothing_moves():
    day = _day("Steady Z2.", 110, 145)

    assert align_target_to_named_zone(day, None) is day
    assert align_target_to_named_zone({"description": "Steady Z2."}, FTP) == {
        "description": "Steady Z2."
    }


@pytest.mark.asyncio
async def test_the_plan_gate_aligns_a_written_day(client, auth_headers):
    from sqlalchemy import select

    import crud
    import models
    from services import plan_pipeline
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as db:
        user = await db.scalar(select(models.User))
        user.current_ftp = FTP
        day = {
            **_day(
                "Steady Z2 endurance.",
                110,
                145,
                workoutType="endurance",
                durationMinutes=75,
            ),
            "date": (date.today() + timedelta(days=2)).isoformat(),
        }
        result = await plan_pipeline.commit_plan(
            db, user, [day], base_plan=[], source="coach_chat"
        )
        await db.commit()
        stored = (await crud.get_training_plan(db, user.id)).plan

    assert result.plan[0]["targetPower"]["low"] == 176
    assert stored[0]["targetPower"] == {"low": 176, "high": 240}
