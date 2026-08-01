"""Router tests for athlete hypothesis management endpoints (#381)."""

from __future__ import annotations

import pytest

import crud
from tests.conftest import TestSessionLocal


async def _current_user_id(client, auth_headers) -> str:
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    return response.json()["id"]


async def _seed_hypothesis(user_id: str, **kwargs) -> str:
    async with TestSessionLocal() as db:
        hypothesis = await crud.propose_athlete_hypothesis(db, user_id, **kwargs)
        await db.commit()
        return hypothesis.id


@pytest.mark.asyncio
async def test_list_returns_open_hypotheses(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _seed_hypothesis(
        user_id,
        statement="Upper-body strength suppresses next-day HR response",
        category="fatigue_response",
        rationale="HR ~8 bpm low the day after gym.",
        confidence=0.38,
    )

    response = await client.get(
        "/api/v1/users/me/athlete-hypotheses", headers=auth_headers
    )
    assert response.status_code == 200
    hypotheses = response.json()["hypotheses"]
    assert len(hypotheses) == 1
    assert hypotheses[0]["statement"].startswith("Upper-body strength")
    assert hypotheses[0]["status"] == "proposed"
    assert hypotheses[0]["evidenceCount"] == 1
    assert hypotheses[0]["confidence"] == pytest.approx(0.38)
    # Structured fields default to empty lists when not populated (#479).
    assert hypotheses[0]["evidence"] == []
    assert hypotheses[0]["alternativeExplanations"] == []


@pytest.mark.asyncio
async def test_list_surfaces_structured_evidence_and_alternatives(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _seed_hypothesis(
        user_id,
        statement="Current limiter is likely threshold utilization",
        category="performance_model",
        confidence=0.6,
        evidence=["FTP ~250 W vs MAP ~360 W (69% of the ceiling)."],
        alternative_explanations=["The MAP estimate may be inflated by one effort."],
    )

    response = await client.get(
        "/api/v1/users/me/athlete-hypotheses", headers=auth_headers
    )
    assert response.status_code == 200
    hypothesis = response.json()["hypotheses"][0]
    assert hypothesis["evidence"] == ["FTP ~250 W vs MAP ~360 W (69% of the ceiling)."]
    assert hypothesis["alternativeExplanations"] == [
        "The MAP estimate may be inflated by one effort."
    ]


@pytest.mark.asyncio
async def test_confirm_hypothesis_promotes_to_trait(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    hypothesis_id = await _seed_hypothesis(
        user_id,
        statement="Performs best with two recovery days before a race",
        category="fatigue_response",
    )

    response = await client.patch(
        f"/api/v1/users/me/athlete-hypotheses/{hypothesis_id}",
        headers=auth_headers,
        json={"status": "confirmed"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"

    # Confirming drops it from the open list and adds a learned trait.
    open_list = await client.get(
        "/api/v1/users/me/athlete-hypotheses", headers=auth_headers
    )
    assert open_list.json()["hypotheses"] == []

    facts = await client.get(
        "/api/v1/users/me/athlete-memory-facts", headers=auth_headers
    )
    assert any(
        fact["fact"] == "Performs best with two recovery days before a race"
        for fact in facts.json()["facts"]
    )


@pytest.mark.asyncio
async def test_refute_and_include_resolved(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    hypothesis_id = await _seed_hypothesis(
        user_id, statement="Struggles on back-to-back hard days"
    )

    await client.patch(
        f"/api/v1/users/me/athlete-hypotheses/{hypothesis_id}",
        headers=auth_headers,
        json={"status": "refuted"},
    )

    open_only = await client.get(
        "/api/v1/users/me/athlete-hypotheses", headers=auth_headers
    )
    assert open_only.json()["hypotheses"] == []

    with_resolved = await client.get(
        "/api/v1/users/me/athlete-hypotheses?includeResolved=true",
        headers=auth_headers,
    )
    assert len(with_resolved.json()["hypotheses"]) == 1


@pytest.mark.asyncio
async def test_delete_hypothesis(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    hypothesis_id = await _seed_hypothesis(
        user_id, statement="Fuels poorly on long rides"
    )

    response = await client.delete(
        f"/api/v1/users/me/athlete-hypotheses/{hypothesis_id}", headers=auth_headers
    )
    assert response.status_code == 204

    # Deleting again is a 404.
    missing = await client.delete(
        f"/api/v1/users/me/athlete-hypotheses/{hypothesis_id}", headers=auth_headers
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_update_missing_hypothesis_is_404(client, auth_headers):
    response = await client.patch(
        "/api/v1/users/me/athlete-hypotheses/does-not-exist",
        headers=auth_headers,
        json={"status": "confirmed"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_export_includes_hypotheses(client, auth_headers):
    user_id = await _current_user_id(client, auth_headers)
    await _seed_hypothesis(user_id, statement="Prefers hilly terrain")

    response = await client.get(
        "/api/v1/users/me/memory-export", headers=auth_headers
    )
    assert response.status_code == 200
    statements = [h["statement"] for h in response.json()["hypotheses"]]
    assert "Prefers hilly terrain" in statements
