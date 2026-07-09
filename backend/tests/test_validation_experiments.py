"""Router tests for validation experiment management endpoints (#382)."""

from __future__ import annotations

import pytest

import crud
from tests.conftest import TestSessionLocal


async def _current_user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


async def _seed_experiment(user_id: str, **kwargs) -> str:
    kwargs.setdefault("question", "Which bike is faster?")
    kwargs.setdefault(
        "protocol", "Compare both bikes over the same climb using identical pedals."
    )
    async with TestSessionLocal() as db:
        experiment = await crud.suggest_athlete_experiment(db, user_id, **kwargs)
        await db.commit()
        return experiment.id


@pytest.mark.asyncio
async def test_list_returns_open_experiments(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _seed_experiment(
        user_id,
        question="Is threshold higher than assumed?",
        protocol="Perform a 30-minute threshold test.",
        rationale="A higher average power raises FTP.",
        category="general",
    )

    response = await client.get(
        "/api/v1/users/me/validation-experiments", headers=auth_headers
    )
    assert response.status_code == 200
    experiments = response.json()["experiments"]
    assert len(experiments) == 1
    assert experiments[0]["protocol"] == "Perform a 30-minute threshold test."
    assert experiments[0]["status"] == "suggested"


@pytest.mark.asyncio
async def test_complete_experiment_drops_from_open_list(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    experiment_id = await _seed_experiment(user_id)

    response = await client.patch(
        f"/api/v1/users/me/validation-experiments/{experiment_id}",
        headers=auth_headers,
        json={"status": "completed"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    open_list = await client.get(
        "/api/v1/users/me/validation-experiments", headers=auth_headers
    )
    assert open_list.json()["experiments"] == []

    with_resolved = await client.get(
        "/api/v1/users/me/validation-experiments?includeResolved=true",
        headers=auth_headers,
    )
    assert len(with_resolved.json()["experiments"]) == 1


@pytest.mark.asyncio
async def test_dismiss_experiment(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    experiment_id = await _seed_experiment(user_id)

    await client.patch(
        f"/api/v1/users/me/validation-experiments/{experiment_id}",
        headers=auth_headers,
        json={"status": "dismissed"},
    )

    open_only = await client.get(
        "/api/v1/users/me/validation-experiments", headers=auth_headers
    )
    assert open_only.json()["experiments"] == []


@pytest.mark.asyncio
async def test_delete_experiment(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    experiment_id = await _seed_experiment(user_id)

    response = await client.delete(
        f"/api/v1/users/me/validation-experiments/{experiment_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204

    missing = await client.delete(
        f"/api/v1/users/me/validation-experiments/{experiment_id}",
        headers=auth_headers,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_update_missing_experiment_is_404(client, auth_headers):
    response = await client.patch(
        "/api/v1/users/me/validation-experiments/does-not-exist",
        headers=auth_headers,
        json={"status": "completed"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_export_includes_experiments(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _seed_experiment(
        user_id,
        question="Does heat cut power?",
        protocol="Repeat the session under cooler conditions.",
    )

    response = await client.get(
        "/api/v1/users/me/memory-export", headers=auth_headers
    )
    assert response.status_code == 200
    protocols = [e["protocol"] for e in response.json()["experiments"]]
    assert "Repeat the session under cooler conditions." in protocols
