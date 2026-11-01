"""What one account can spend on the owner's key (review of 2026-11-01, ai-trainer-ops#338)."""

from __future__ import annotations

import pytest

import crud
from auth import decode_token
from main import app
from routers.dependencies import enforce_ai_rate_limit
from tests.conftest import TestSessionLocal

_URL = "/api/v1/users/me/athlete-context"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"strengths": ["climbing"] * 21},
        {"coachingRisks": ["x" * 201]},
        {"notes": "x" * 2001},
    ],
)
async def test_the_athlete_context_is_bounded_on_the_way_in(client, auth_headers, body):
    """It goes into every coach prompt; thousands of items were a way to spend
    the whole daily budget in one burst."""
    response = await client.put(_URL, headers=auth_headers, json=body)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_context_inside_the_bounds_is_saved(client, auth_headers):
    body = {"strengths": ["climbing"] * 20, "notes": "x" * 2000}

    response = await client.put(_URL, headers=auth_headers, json=body)

    assert response.status_code == 200
    assert len(response.json()["strengths"]) == 20


@pytest.mark.asyncio
async def test_a_larger_context_stored_before_still_loads(client, auth_headers):
    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_athlete_context(db, user_id, strengths=["climbing"] * 30)
        await db.commit()

    response = await client.get(_URL, headers=auth_headers)

    assert response.status_code == 200
    assert len(response.json()["strengths"]) == 30


@pytest.mark.asyncio
async def test_the_key_test_is_rate_limited(client, auth_headers):
    """Unlimited, it checked stolen keys from this server's address."""
    from fastapi import HTTPException

    def refuse():
        raise HTTPException(status_code=429, detail="limited")

    app.dependency_overrides[enforce_ai_rate_limit] = refuse
    try:
        response = await client.post(
            "/api/v1/users/me/ai-key/test",
            headers=auth_headers,
            json={"provider": "gemini", "apiKey": "x"},
        )
    finally:
        app.dependency_overrides.pop(enforce_ai_rate_limit, None)

    assert response.status_code == 429
