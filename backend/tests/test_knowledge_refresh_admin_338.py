"""The knowledge refresh is the operator's (review of 2026-11-01, ai-trainer-ops#338).

It re-embeds the whole corpus on the owner's key, outside any athlete's budget,
and rewrites the shared corpus tables. Under /ai any signed-in tester could
start it, as often as the rate limit allowed and several at once.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import routers.admin as admin_router

_URL = "/api/v1/admin/refresh-knowledge"


@pytest.fixture
async def admin_headers(client, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "admin_password", "super-secret")
    resp = await client.post("/api/v1/admin/login", json={"password": "super-secret"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_the_operator_queues_a_refresh(client, admin_headers):
    with (
        patch("routers.admin._run_knowledge_refresh", new_callable=AsyncMock) as run,
        patch("routers.admin.embedding_provider_is_configured", return_value=True),
    ):
        response = await client.post(_URL, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "started"
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_athlete_cannot_start_one_and_the_old_route_is_gone(
    client, auth_headers
):
    with patch("routers.admin._run_knowledge_refresh", new_callable=AsyncMock) as run:
        athlete = await client.post(_URL, headers=auth_headers)
        old = await client.post("/api/v1/ai/refresh-knowledge", headers=auth_headers)
        nobody = await client.post(_URL)

    assert athlete.status_code == 403
    assert old.status_code in (404, 405)
    assert nobody.status_code == 401
    run.assert_not_called()


@pytest.mark.asyncio
async def test_a_second_refresh_is_not_queued_while_one_runs(client, admin_headers):
    with (
        patch("routers.admin._run_knowledge_refresh", new_callable=AsyncMock) as run,
        patch("routers.admin.embedding_provider_is_configured", return_value=True),
    ):
        await admin_router._KNOWLEDGE_REFRESH.acquire()
        try:
            response = await client.post(_URL, headers=admin_headers)
        finally:
            admin_router._KNOWLEDGE_REFRESH.release()

    assert response.json()["status"] == "running"
    run.assert_not_called()


@pytest.mark.asyncio
async def test_without_an_embedding_key_it_says_so(client, admin_headers):
    """Gemini alone is enough (#515); with neither key it is 503."""
    with patch("routers.admin.embedding_provider_is_configured", return_value=False):
        response = await client.post(_URL, headers=admin_headers)

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_the_refresh_runs_one_at_a_time_and_resets_the_corpus_cache():
    with (
        patch("scripts.ingest_cycling_science.main", new_callable=AsyncMock) as ingest,
        patch("routers.admin.reset_corpus_cache") as reset,
    ):
        await admin_router._run_knowledge_refresh()

    ingest.assert_awaited_once()
    reset.assert_called_once()
    assert not admin_router._KNOWLEDGE_REFRESH.locked()


@pytest.mark.asyncio
async def test_a_failed_refresh_is_logged_not_raised():
    with patch(
        "scripts.ingest_cycling_science.main", new=AsyncMock(side_effect=RuntimeError)
    ):
        await admin_router._run_knowledge_refresh()

    assert not admin_router._KNOWLEDGE_REFRESH.locked()
