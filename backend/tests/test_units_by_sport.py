"""A plan's numbers are ones the athlete can ride or run to (ai-trainer-ops#117).

Every plan prompt asked for watts from the athlete's FTP, with no word for an
athlete who has none or for a run, the interval schema required watts, and no
plan prompt named a sport, so a running-only athlete's runs were stored as rides.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import crud
import models
import schemas
from auth import hash_password
from services import plan_pipeline, prompts
from tests.conftest import TestSessionLocal

RUN = {
    "date": "2026-10-20",
    "workoutType": "endurance",
    "title": "Easy run",
    "durationMinutes": 45,
}


# ---------------------------------------------------------------------------
# The interval schema
# ---------------------------------------------------------------------------


def test_an_interval_needs_no_watts():
    by_heart = schemas.PlanInterval.model_validate(
        {"duration": 240, "heartRate": 168, "rest": 120}
    )
    by_pace = schemas.PlanInterval.model_validate(
        {"duration": 240, "pace": "4:10/km", "rest": 120}
    )

    assert (by_heart.power, by_heart.heart_rate) == (None, 168)
    assert (by_pace.power, by_pace.pace) == (None, "4:10/km")


# ---------------------------------------------------------------------------
# The athlete's only sport
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sports", "day", "expected"),
    [
        (["running"], RUN, "running"),
        (["running", "strength"], RUN, "running"),
        (["cycling", "running"], RUN, None),
        (["cycling"], RUN, None),
        (None, RUN, None),
        (["running"], {**RUN, "sport": "cycling"}, "cycling"),
        (["running"], {**RUN, "workoutType": "strength"}, None),
        (["running"], {**RUN, "workoutType": "rest"}, None),
    ],
)
def test_a_day_without_a_sport_gets_the_athletes_only_one(sports, day, expected):
    assert plan_pipeline._with_the_athletes_sport(day, sports).get("sport") == expected


# ---------------------------------------------------------------------------
# Watts nobody can ride to
# ---------------------------------------------------------------------------

WATTS = {
    "targetPower": {"low": 200, "high": 220},
    "intervals": [{"duration": 240, "power": 300, "rest": 120}],
}


@pytest.mark.parametrize(
    ("ftp", "sport", "kept"),
    [
        (None, "cycling", False),
        (0, "cycling", False),
        (250, "running", False),
        (250, "cycling", True),
    ],
)
def test_watts_are_kept_only_for_a_ride_at_a_known_ftp(ftp, sport, kept):
    day = plan_pipeline._without_unearned_watts({**RUN, "sport": sport, **WATTS}, ftp)

    assert ("targetPower" in day) is kept
    assert ("power" in day["intervals"][0]) is kept
    assert day["intervals"][0]["duration"] == 240


def test_heart_rate_and_pace_stay_where_the_watts_go():
    """Without an FTP only the watts go; the rep keeps its other target (ai-trainer-ops#260)."""
    day = plan_pipeline._without_unearned_watts(
        {
            **RUN,
            "sport": "cycling",
            "intervals": [
                {"duration": 240, "power": 300, "heartRate": 168, "rest": 120},
                {"duration": 240, "pace": "4:30/km", "rest": 120},
            ],
        },
        None,
    )

    assert day["intervals"] == [
        {"duration": 240, "heartRate": 168, "rest": 120},
        {"duration": 240, "pace": "4:30/km", "rest": 120},
    ]


async def _athlete(email: str, *, sports, ftp) -> str:
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Runner",
            hashed_password=hash_password("Str0ng!Pass"),
            current_ftp=ftp,
            sports=sports,
        )
        db.add(user)
        await db.flush()
        await db.commit()
        return user.id


@pytest.mark.asyncio
async def test_a_running_only_athlete_without_ftp_gets_runs_without_watts():
    user_id = await _athlete("runner@example.com", sports=["running"], ftp=None)
    first = date.today() + timedelta(days=1)
    proposed = [
        {
            "date": first.isoformat(),
            "workoutType": "intervals",
            "title": "Hill repeats",
            "durationMinutes": 50,
            **WATTS,
        }
    ]

    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await plan_pipeline.commit_plan(
            db, user, proposed, base_plan=[], source="generate"
        )
        await db.commit()

    (day,) = result.plan
    assert day["sport"] == "running"
    assert "targetPower" not in day
    assert day["intervals"] == [{"duration": 240, "rest": 120}]


@pytest.mark.asyncio
async def test_a_heart_rate_interval_from_the_chat_is_applied_not_dropped():
    """'make Thursday 5x4 min at 165-172 bpm' was dropped for missing watts."""
    user_id = await _athlete("hr-only@example.com", sports=["cycling"], ftp=None)
    day = (date.today() + timedelta(days=2)).isoformat()
    base = [
        {
            "date": day,
            "workoutType": "endurance",
            "title": "Easy",
            "durationMinutes": 60,
        }
    ]
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, base)
        await db.commit()

    update = {
        "date": day,
        "workoutType": "intervals",
        "intervals": [{"duration": 240, "heartRate": 168, "rest": 120}] * 5,
    }
    async with TestSessionLocal() as db:
        user = await crud.get_user_by_id(db, user_id)
        result = await plan_pipeline.commit_plan_updates(
            db, user, [update], base_plan=base, source="coach_chat"
        )
        await db.commit()

    (stored,) = result.plan
    assert stored["workoutType"] == "intervals"
    assert len(stored["intervals"]) == 5
    assert stored["intervals"][0]["heartRate"] == 168
    assert not any("power" in interval for interval in stored["intervals"])


# ---------------------------------------------------------------------------
# What every plan prompt says
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "system", [prompts.generate_plan_system, prompts.adapt_plan_system]
)
def test_every_plan_writer_is_told_the_units_and_the_sport(system):
    text = system()

    assert prompts.UNITS_BY_SPORT_RULE in text
    assert '"sport"' in text


def test_the_chat_intervals_rule_allows_heart_rate_or_pace():
    rule = prompts.ask_trainer_plan_updates_rule(None)

    assert '"heartRate": <bpm>' in rule
    assert '"pace"' in rule
