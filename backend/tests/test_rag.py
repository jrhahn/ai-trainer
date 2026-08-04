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

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
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

    embedding = [0.1] * 768
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

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.0] * 768):
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

    # Override classify_question to request RAG so retrieve_cycling_context is called
    mock_ai_service["classify_question"].return_value = {
        "category": "science_question",
        "needs_science_rag": True,
    }

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

    # Override classify_question to request RAG
    mock_ai_service["classify_question"].return_value = {
        "category": "science_question",
        "needs_science_rag": True,
    }

    with patch("routers.ai.retrieve_cycling_context", new_callable=AsyncMock) as mock_rag:
        mock_rag.return_value = (science_ctx, [])

        await client.post(
            "/api/v1/ai/ask-trainer",
            headers=auth_headers,
            json={"question": "Should I do polarized training?"},
        )

    call_kwargs = mock_ai_service["ask_trainer"].call_args.kwargs
    assert call_kwargs.get("science_context") == science_ctx


# ---------------------------------------------------------------------------
# POST /ai/refresh-knowledge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_knowledge_queues_background_task(client, auth_headers):
    """Authenticated request must queue the ingestion background task and return 200."""
    with patch("routers.ai._run_knowledge_refresh", new_callable=AsyncMock):
        with patch("routers.ai.embedding_provider_is_configured", return_value=True):
            response = await client.post(
                "/api/v1/ai/refresh-knowledge",
                headers=auth_headers,
            )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "started"
    assert "queued" in body["message"].lower()


@pytest.mark.asyncio
async def test_refresh_knowledge_runs_with_only_a_gemini_key(client, auth_headers):
    """Prod is Gemini-only; demanding an OpenAI key made this endpoint dead there (#515)."""
    with patch("routers.ai._run_knowledge_refresh", new_callable=AsyncMock):
        with patch("services.embeddings.settings") as mock_settings:
            mock_settings.gemini_api_key = "gemini-key"
            mock_settings.openai_api_key = ""
            response = await client.post(
                "/api/v1/ai/refresh-knowledge",
                headers=auth_headers,
            )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_refresh_knowledge_requires_authentication(client):
    """Unauthenticated request must be rejected with HTTP 401."""
    response = await client.post("/api/v1/ai/refresh-knowledge")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_knowledge_returns_503_without_any_embedding_key(
    client, auth_headers
):
    """With no embedding provider configured the endpoint must return HTTP 503."""
    with patch("routers.ai.embedding_provider_is_configured", return_value=False):
        response = await client.post(
            "/api/v1/ai/refresh-knowledge",
            headers=auth_headers,
        )

    assert response.status_code == 503
    assert "embedding provider" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Corpus-presence gate (#515)
# ---------------------------------------------------------------------------


def _pg_db(scalar_result: object) -> MagicMock:
    db = MagicMock()
    db.bind = MagicMock()
    db.bind.dialect = MagicMock()
    db.bind.dialect.name = "postgresql"
    result = MagicMock()
    result.scalar = MagicMock(return_value=scalar_result)
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture(autouse=True)
def _clear_corpus_cache():
    """The corpus answer is cached per process; each test needs a clean slate."""
    rag.reset_corpus_cache()
    yield
    rag.reset_corpus_cache()


@pytest.mark.asyncio
async def test_corpus_is_reported_empty_on_sqlite():
    db = MagicMock()
    db.bind = MagicMock()
    db.bind.dialect = MagicMock()
    db.bind.dialect.name = "sqlite"

    assert await rag.knowledge_corpus_is_populated(db) is False


@pytest.mark.asyncio
async def test_corpus_is_reported_populated_when_rows_exist():
    assert await rag.knowledge_corpus_is_populated(_pg_db(True)) is True


@pytest.mark.asyncio
async def test_a_missing_table_counts_as_an_empty_corpus():
    """Conservative answer: disable retrieval rather than pretend it works."""
    db = _pg_db(True)
    db.execute = AsyncMock(side_effect=RuntimeError("relation does not exist"))

    assert await rag.knowledge_corpus_is_populated(db) is False


@pytest.mark.asyncio
async def test_the_corpus_answer_is_queried_once_per_process():
    """Otherwise the guard would add a query to every chat message it saves a call on."""
    db = _pg_db(True)

    await rag.knowledge_corpus_is_populated(db)
    await rag.knowledge_corpus_is_populated(db)
    await rag.knowledge_corpus_is_populated(db)

    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_classification_is_skipped_when_there_is_no_corpus(
    client, auth_headers, mock_ai_service
):
    """No LLM call may be spent gating a retrieval that cannot return anything (#515)."""
    with patch(
        "routers.ai.knowledge_corpus_is_populated",
        new_callable=AsyncMock,
        return_value=False,
    ):
        response = await client.post(
            "/api/v1/ai/ask-trainer",
            headers=auth_headers,
            json={"question": "Is polarized training better?"},
        )

    assert response.status_code == 200
    mock_ai_service["classify_question"].assert_not_called()
    # The prompt must not carry a half-filled classification line either.
    assert mock_ai_service["ask_trainer"].call_args.kwargs.get("classification") == {}


@pytest.mark.asyncio
async def test_classification_still_runs_once_the_corpus_exists(
    client, auth_headers, mock_ai_service
):
    mock_ai_service["classify_question"].return_value = {
        "category": "science_question",
        "needs_science_rag": True,
    }

    with patch(
        "routers.ai.knowledge_corpus_is_populated",
        new_callable=AsyncMock,
        return_value=True,
    ):
        with patch(
            "routers.ai.retrieve_cycling_context",
            new_callable=AsyncMock,
            return_value=("Zone 2 builds aerobic base.", []),
        ) as mock_rag:
            response = await client.post(
                "/api/v1/ai/ask-trainer",
                headers=auth_headers,
                json={"question": "Is polarized training better?"},
            )

    assert response.status_code == 200
    mock_ai_service["classify_question"].assert_called_once()
    mock_rag.assert_awaited_once()
