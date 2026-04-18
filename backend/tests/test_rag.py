"""Tests for the cycling science RAG service (services/rag.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.rag as rag


# ---------------------------------------------------------------------------
# retrieve_cycling_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_cycling_context_returns_empty_for_sqlite():
    """SQLite does not support pgvector; retrieval must return empty results."""
    mock_db = MagicMock()
    mock_db.bind = MagicMock()
    mock_db.bind.dialect = MagicMock()
    mock_db.bind.dialect.name = "sqlite"

    context, sources = await rag.retrieve_cycling_context(mock_db, "What is FTP?")

    assert context == ""
    assert sources == []


@pytest.mark.asyncio
async def test_retrieve_cycling_context_returns_empty_on_exception():
    """Any unexpected error during retrieval must be swallowed; returns empty."""
    mock_db = MagicMock()
    mock_db.bind = MagicMock()
    mock_db.bind.dialect = MagicMock()
    mock_db.bind.dialect.name = "postgresql"
    # Make db.execute raise to simulate pgvector not being installed
    mock_db.execute = AsyncMock(side_effect=RuntimeError("relation does not exist"))

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 1536):
        context, sources = await rag.retrieve_cycling_context(mock_db, "intervals")

    assert context == ""
    assert sources == []


@pytest.mark.asyncio
async def test_retrieve_cycling_context_formats_results():
    """When rows are returned, results must be formatted with source attribution."""
    mock_db = MagicMock()
    mock_db.bind = MagicMock()
    mock_db.bind.dialect = MagicMock()
    mock_db.bind.dialect.name = "postgresql"

    # Simulate two rows: (title, content, source_type, doi, url, similarity)
    mock_rows = [
        ("Power Zones", "Zone 2 is aerobic endurance.", "seed", None, None, 0.9),
        ("Lactate Threshold Study", "FTP is 95% of 20-min power.", "paper", "10.1/test", "https://example.com", 0.85),
    ]
    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(return_value=mock_rows)
    mock_db.execute = AsyncMock(return_value=mock_result)

    embedding = [0.1] * 1536
    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=embedding):
        context, sources = await rag.retrieve_cycling_context(mock_db, "power zones")

    assert "Power Zones" in context
    assert "Zone 2 is aerobic endurance." in context
    assert "Lactate Threshold Study" in context
    assert "FTP is 95% of 20-min power." in context
    assert "---" in context  # separator between chunks

    assert len(sources) == 2
    assert sources[0]["title"] == "Power Zones"
    assert sources[0]["sourceType"] == "seed"
    assert "doi" not in sources[0]  # no doi for seed source
    assert sources[1]["title"] == "Lactate Threshold Study"
    assert sources[1]["doi"] == "10.1/test"
    assert sources[1]["url"] == "https://example.com"
    assert sources[1]["similarity"] == 0.85


@pytest.mark.asyncio
async def test_retrieve_cycling_context_returns_empty_when_no_rows():
    """When the table is empty, retrieval must return empty results."""
    mock_db = MagicMock()
    mock_db.bind = MagicMock()
    mock_db.bind.dialect = MagicMock()
    mock_db.bind.dialect.name = "postgresql"

    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(return_value=[])
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.0] * 1536):
        context, sources = await rag.retrieve_cycling_context(mock_db, "nutrition")

    assert context == ""
    assert sources == []


# ---------------------------------------------------------------------------
# ask_trainer HTTP endpoint — sources in response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_trainer_response_includes_sources_when_rag_returns_results(
    client, auth_headers, mock_ai_service
):
    """When RAG retrieval returns sources, the HTTP response must include them."""
    rag_sources = [
        {"title": "Power Zones", "sourceType": "seed", "similarity": 0.92},
    ]

    with patch("routers.ai.retrieve_cycling_context", new_callable=AsyncMock) as mock_rag:
        mock_rag.return_value = ("Zone 2 is endurance.", rag_sources)

        response = await client.post(
            "/api/v1/ai/ask-trainer",
            headers=auth_headers,
            json={"question": "What power zone should I train in?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Take it easy tomorrow."
    assert body["sources"] is not None
    assert len(body["sources"]) == 1
    assert body["sources"][0]["title"] == "Power Zones"


@pytest.mark.asyncio
async def test_ask_trainer_response_has_no_sources_when_rag_returns_empty(
    client, auth_headers, mock_ai_service
):
    """When RAG retrieval returns no sources, the response sources list is empty/null."""
    with patch("routers.ai.retrieve_cycling_context", new_callable=AsyncMock) as mock_rag:
        mock_rag.return_value = ("", [])

        response = await client.post(
            "/api/v1/ai/ask-trainer",
            headers=auth_headers,
            json={"question": "Move my Monday ride to Tuesday"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Take it easy tomorrow."
    # sources should be absent or an empty list when no RAG context
    assert not body.get("sources")


@pytest.mark.asyncio
async def test_ask_trainer_science_context_forwarded_to_ai_service(
    client, auth_headers, mock_ai_service
):
    """science_context returned by RAG must be forwarded to ai_service.ask_trainer."""
    science_ctx = "Polarized training improves VO2max by 11%."

    with patch("routers.ai.retrieve_cycling_context", new_callable=AsyncMock) as mock_rag:
        mock_rag.return_value = (science_ctx, [])

        await client.post(
            "/api/v1/ai/ask-trainer",
            headers=auth_headers,
            json={"question": "Should I do polarized training?"},
        )

    call_kwargs = mock_ai_service["ask_trainer"].call_args.kwargs
    assert call_kwargs.get("science_context") == science_ctx
