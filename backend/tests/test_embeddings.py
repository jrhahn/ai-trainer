"""Tests for the embedding provider abstraction (services/embeddings.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import services.embeddings as embeddings


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def _keys(monkeypatch, *, gemini: str = "", openai: str = "", provider: str = "gemini"):
    monkeypatch.setattr(embeddings.settings, "gemini_api_key", gemini)
    monkeypatch.setattr(embeddings.settings, "openai_api_key", openai)
    monkeypatch.setattr(embeddings.settings, "embedding_provider", provider)


def test_gemini_is_used_when_configured_and_keyed(monkeypatch):
    _keys(monkeypatch, gemini="g", openai="o", provider="gemini")
    assert isinstance(embeddings.get_embedder(), embeddings.GeminiEmbedder)


def test_openai_is_used_when_it_is_the_configured_provider(monkeypatch):
    _keys(monkeypatch, gemini="g", openai="o", provider="openai")
    assert isinstance(embeddings.get_embedder(), embeddings.OpenAIEmbedder)


def test_the_available_key_wins_over_a_preference_with_no_key(monkeypatch):
    """Prod is Gemini-only: preferring OpenAI must not strand retrieval (#515)."""
    _keys(monkeypatch, gemini="g", openai="", provider="openai")
    assert isinstance(embeddings.get_embedder(), embeddings.GeminiEmbedder)


def test_no_key_at_all_raises_rather_than_returning_a_dead_embedder(monkeypatch):
    """A silent failure here is indistinguishable from an empty corpus."""
    _keys(monkeypatch, gemini="", openai="", provider="gemini")
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.get_embedder()

    assert embeddings.embedding_provider_is_configured() is False


def test_provider_is_reported_configured_with_either_key(monkeypatch):
    _keys(monkeypatch, gemini="", openai="o", provider="gemini")
    assert embeddings.embedding_provider_is_configured() is True


# ---------------------------------------------------------------------------
# Query / document asymmetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queries_and_documents_use_different_task_types():
    """Gemini's retrieval embeddings are asymmetric; using one task type for both
    would quietly degrade every result."""
    fake = AsyncMock(return_value=[[0.1] * embeddings.EMBEDDING_DIM])
    embedder = type("E", (), {"model": "fake", "embed": staticmethod(fake)})()

    with patch.object(embeddings, "get_embedder", return_value=embedder):
        await embeddings.embed_query("what is FTP?")
        assert fake.await_args.kwargs["task_type"] == embeddings.TASK_QUERY

        await embeddings.embed_documents(["FTP is functional threshold power."])
        assert fake.await_args.kwargs["task_type"] == embeddings.TASK_DOCUMENT


@pytest.mark.asyncio
async def test_embedding_no_documents_does_not_call_the_provider():
    fake = AsyncMock(return_value=[])
    embedder = type("E", (), {"model": "fake", "embed": staticmethod(fake)})()

    with patch.object(embeddings, "get_embedder", return_value=embedder):
        assert await embeddings.embed_documents([]) == []

    fake.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_provider_that_returns_nothing_for_a_query_is_an_error():
    fake = AsyncMock(return_value=[])
    embedder = type("E", (), {"model": "fake", "embed": staticmethod(fake)})()

    with patch.object(embeddings, "get_embedder", return_value=embedder):
        with pytest.raises(embeddings.EmbeddingError):
            await embeddings.embed_query("anything")


# ---------------------------------------------------------------------------
# pgvector literal
# ---------------------------------------------------------------------------


def test_pgvector_literal_format():
    assert embeddings.to_pgvector_literal([0.5, -1.0, 2.25]) == "[0.5,-1.0,2.25]"


def test_the_stored_dimension_matches_what_the_migration_created():
    """Corpus and query vectors share one column width; drift here means every
    retrieval fails at the database, not at the model."""
    migration = (
        __import__("pathlib")
        .Path(__file__)
        .parent.parent.joinpath(
            "alembic/versions/20260806_000001_resize_knowledge_embeddings.py"
        )
        .read_text()
    )
    assert f"_NEW_DIM = {embeddings.EMBEDDING_DIM}" in migration
