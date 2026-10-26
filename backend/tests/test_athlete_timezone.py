"""The nightly job counts the athlete's own day (ai-trainer-ops#128)."""

from datetime import datetime, timezone

import pytest

import crud
import models
from auth import decode_token, hash_password
from services import plan_maintenance
from services.dates import known_timezone
from tests.conftest import TestSessionLocal


@pytest.mark.parametrize(
    ("name", "known"),
    [
        ("America/New_York", "America/New_York"),
        ("Europe/Berlin", "Europe/Berlin"),
        ("Mars/Olympus", None),
        ("../etc/passwd", None),
        ("", None),
        (None, None),
        # Longer than the column: refused as an unknown zone, as every such
        # name is (review of #933).
        ("Europe/" + "x" * 60, None),
    ],
)
def test_only_a_zone_the_server_knows_is_kept(name, known):
    assert known_timezone(name) == known


async def _stored_zone(auth_headers) -> str | None:
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        return (await crud.get_user_by_id(db, user_id)).timezone_name


@pytest.mark.asyncio
async def test_opening_the_app_stores_the_browsers_zone(client, auth_headers):
    assert await _stored_zone(auth_headers) is None

    response = await client.get(
        "/api/v1/users/me",
        headers={**auth_headers, "X-App-Timezone": "America/New_York"},
    )

    assert response.status_code == 200
    assert await _stored_zone(auth_headers) == "America/New_York"


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [{"X-App-Timezone": "Mars/Olympus"}, {}])
async def test_an_unknown_or_missing_zone_keeps_the_stored_one(
    client, auth_headers, header
):
    await client.get(
        "/api/v1/users/me",
        headers={**auth_headers, "X-App-Timezone": "America/New_York"},
    )

    await client.get("/api/v1/users/me", headers={**auth_headers, **header})

    assert await _stored_zone(auth_headers) == "America/New_York"


async def _athlete(email: str, zone: str | None, title: str) -> str:
    """An athlete with an evening session on 17 October and the window ahead full."""
    plan = [
        {
            "date": f"2026-10-{day}",
            "workoutType": "endurance",
            "title": title,
            "description": "Endurance ride",
            "durationMinutes": 60,
        }
        for day in range(17, 29)
    ]
    async with TestSessionLocal() as db:
        user = models.User(
            email=email,
            name="Test Rider",
            hashed_password=hash_password("Str0ng!Pass"),
            is_onboarded=True,
            bike_type="road",
            training_goal="general_fitness",
            fitness_level="intermediate",
            current_ftp=250,
            ai_provider="gemini",
            timezone_name=zone,
        )
        db.add(user)
        await db.flush()
        await crud.upsert_training_plan(db, user.id, plan)
        await db.commit()
        return user.id


@pytest.mark.asyncio
async def test_the_nightly_job_leaves_an_evening_west_of_the_app_alone(monkeypatch):
    """02:30 in Berlin is 20:30 in New York: the session that evening is not
    missed yet, while a Berlin athlete's day is over."""
    await _athlete("new-york@example.com", "America/New_York", "Ride in New York")
    await _athlete("berlin@example.com", None, "Ride in Berlin")
    adapted: list[str] = []

    async def adapt_training_plan(plan, *args, **kwargs):
        adapted.append(plan[0]["title"])
        return []

    async def no_weather(*args, **kwargs):
        return ""

    monkeypatch.setattr(
        plan_maintenance.ai_service, "adapt_training_plan", adapt_training_plan
    )
    monkeypatch.setattr(
        plan_maintenance, "training_weather_context_for_user", no_weather
    )

    result = await plan_maintenance.run_daily_plan_maintenance(
        TestSessionLocal,
        now=datetime(2026, 10, 18, 0, 30, tzinfo=timezone.utc),
        timezone_name="Europe/Berlin",
    )

    assert result.checked == 2
    assert adapted == ["Ride in Berlin"]
