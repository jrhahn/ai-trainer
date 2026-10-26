"""A constraint the chat pins is shown under the reply and can be removed (ai-trainer-ops#110)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

import crud
import models
from auth import decode_token
from domain.passwords import hash_password
from domain.availability import constraint_label
from services.dates import app_today
from tests.conftest import TestSessionLocal


@pytest.mark.parametrize(
    ("args", "label"),
    [
        (("no_training", "2026-10-24", None), "No training on Saturday 2026-10-24"),
        (
            (
                "required_workout",
                "2026-10-25",
                {"workoutType": "endurance", "minDurationMinutes": 120},
            ),
            "Long ride on Sunday 2026-10-25, at least 120 min",
        ),
        (("required_workout", "2026-10-25", None), "Long ride on Sunday 2026-10-25"),
        (("no_training", None, None), "No training on that day"),
    ],
)
def test_a_constraint_is_named_in_the_athletes_terms(args, label):
    assert constraint_label(*args) == label


def _user_id(auth_headers) -> str:
    return decode_token(auth_headers["Authorization"].split(" ", 1)[1])


@pytest.mark.asyncio
async def test_a_pinned_day_off_comes_back_with_the_reply_and_can_be_removed(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "Samstag kann ich nicht, bin auf einer Hochzeit."},
    )

    [chip] = response.json()["capturedConstraints"]
    assert chip["label"].startswith("No training on Saturday ")
    assert chip["removable"] is True
    async with TestSessionLocal() as db:
        active = await crud.list_active_availability_constraints(
            db, _user_id(auth_headers), today=app_today().isoformat()
        )
    assert [row.id for row in active] == [chip["id"]]

    removed = await client.delete(
        f"/api/v1/users/me/availability-constraints/{chip['id']}", headers=auth_headers
    )

    assert removed.status_code == 200
    assert removed.json() == {"status": "removed"}
    async with TestSessionLocal() as db:
        assert (
            await crud.list_active_availability_constraints(
                db, _user_id(auth_headers), today=app_today().isoformat()
            )
            == []
        )
    # Removed once: a second press finds nothing to remove.
    again = await client.delete(
        f"/api/v1/users/me/availability-constraints/{chip['id']}", headers=auth_headers
    )
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_a_reply_that_pins_nothing_shows_no_chip(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={
            "question": "Keine lange Ausfahrt am Samstag, ich bin auf einer Hochzeit."
        },
    )

    assert response.json()["capturedConstraints"] == []


@pytest.mark.asyncio
async def test_another_athletes_constraint_cannot_be_removed(client, auth_headers):
    day = (app_today() + timedelta(days=3)).isoformat()
    async with TestSessionLocal() as db:
        other = models.User(
            email="someone-else@example.com",
            name="Other Rider",
            hashed_password=hash_password("Str0ng!Pass"),
        )
        db.add(other)
        await db.flush()
        row = await crud.upsert_availability_constraint(
            db, other.id, constraint_date=day, weekday="monday", expires_on=day
        )
        row_id = row.id
        await db.commit()

    response = await client.delete(
        f"/api/v1/users/me/availability-constraints/{row_id}", headers=auth_headers
    )

    assert response.status_code == 404
    async with TestSessionLocal() as db:
        assert (
            await db.scalar(
                select(models.AthleteAvailabilityConstraint).where(
                    models.AthleteAvailabilityConstraint.id == row_id
                )
            )
        ).active is True
