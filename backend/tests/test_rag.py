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

    # Simulate two rows: (title, content, source_type, doi, url, similarity, topics)
    mock_rows = [
        ("Power Zones", "Zone 2 is aerobic endurance.", "seed", None, None, 0.9, None),
        ("Lactate Threshold Study", "FTP is 95% of 20-min power.", "paper", "10.1/test", "https://example.com", 0.85, ["threshold"]),
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
async def test_chunks_below_the_similarity_floor_are_dropped():
    """Off-topic questions must not arrive as 'relevant research' (#528)."""
    mock_db = MagicMock()
    mock_db.bind = MagicMock()
    mock_db.bind.dialect = MagicMock()
    mock_db.bind.dialect.name = "postgresql"

    mock_rows = [
        ("Power Zones", "Zone 2 is aerobic endurance.", "seed", None, None, 0.78, None),
        ("Strength Training", "Heavy lifting improves economy.", "seed", None, None, 0.61, None),
    ]
    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(return_value=mock_rows)
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
        context, sources = await rag.retrieve_cycling_context(mock_db, "power zones")

    assert "Power Zones" in context
    assert "Strength Training" not in context
    assert [s["title"] for s in sources] == ["Power Zones"]


@pytest.mark.asyncio
async def test_an_all_weak_result_set_returns_nothing_at_all():
    """Better no context than five least-bad chunks under an 'evidence' heading."""
    mock_db = MagicMock()
    mock_db.bind = MagicMock()
    mock_db.bind.dialect = MagicMock()
    mock_db.bind.dialect.name = "postgresql"

    # The measured off-topic band, re-taken on the 54-chunk corpus (#629):
    # 0.617 is what "Move my Monday ride to Tuesday" actually scores, the
    # highest any off-topic question reached. The floor has to clear it.
    mock_rows = [
        ("Strength Training", "Heavy lifting.", "seed", None, None, 0.617, None),
        ("Periodization", "Build then peak.", "seed", None, None, 0.596, None),
        ("Hrv Guided Training", "RMSSD trends.", "seed", None, None, 0.589, None),
    ]
    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(return_value=mock_rows)
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
        context, sources = await rag.retrieve_cycling_context(
            mock_db, "Move my Monday ride to Tuesday"
        )

    assert context == ""
    assert sources == []


@pytest.mark.asyncio
async def test_the_floor_can_be_overridden_per_call():
    mock_db = MagicMock()
    mock_db.bind = MagicMock()
    mock_db.bind.dialect = MagicMock()
    mock_db.bind.dialect.name = "postgresql"

    mock_rows = [("Recovery", "Sleep matters.", "seed", None, None, 0.61, None)]
    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(return_value=mock_rows)
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
        context, sources = await rag.retrieve_cycling_context(
            mock_db, "recovery", min_similarity=0.5
        )

    assert "Recovery" in context
    assert len(sources) == 1


def test_the_floor_sits_between_the_measured_populations():
    """The empty band measured on the 54-chunk corpus (#629).

    Off-topic questions topped out at 0.617, the weakest science question
    reached 0.687. A floor outside that band either admits "move my Monday ride
    to Tuesday" as research or drops "should I be doing more intervals?" — which
    is what 0.70 was doing after #640 shrank the corpus.
    """
    assert 0.617 < rag.MIN_SIMILARITY < 0.687


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
# Limiter-aware ranking (#627)
#
# Similarity answers "which passage resembles this question?". These tests cover
# the half it cannot answer: "which passage bears on *this* athlete's problem?"
# ---------------------------------------------------------------------------


def _rag_db(rows: list[tuple]) -> MagicMock:
    """A PostgreSQL session whose vector search returns *rows*, similarity-ordered."""
    db = MagicMock()
    db.bind = MagicMock()
    db.bind.dialect = MagicMock()
    db.bind.dialect.name = "postgresql"
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows)
    db.execute = AsyncMock(return_value=result)
    return db


# The same question ("should I do more intervals?") against the same corpus. The
# VO2max chunk simply reads less like the question, so similarity ranks it last.
_MIXED_ROWS = [
    ("Interval Basics", "Intervals are hard.", "seed", None, None, 0.86, None),
    ("Sweet Spot", "Sustainable power work.", "seed", None, None, 0.82, ["threshold"]),
    ("VO2max Development", "4x4s raise MAP.", "paper", None, None, 0.74, ["vo2max"]),
]


@pytest.mark.asyncio
async def test_the_athletes_limiter_outranks_a_closer_but_generic_chunk():
    db = _rag_db(_MIXED_ROWS)

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
        _, sources = await rag.retrieve_cycling_context(
            db, "Should I do more intervals?", focus_topics=["vo2max"]
        )

    # 0.74 came last on similarity alone; it leads because it is this athlete's
    # limiter. The rest keep their similarity order behind it.
    assert [s["title"] for s in sources] == [
        "VO2max Development",
        "Interval Basics",
        "Sweet Spot",
    ]


@pytest.mark.asyncio
async def test_a_different_limiter_reorders_the_identical_result_set():
    """Two athletes, one question, one corpus — different evidence."""
    db = _rag_db(_MIXED_ROWS)

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
        _, sources = await rag.retrieve_cycling_context(
            db, "Should I do more intervals?", focus_topics=["threshold"]
        )

    assert [s["title"] for s in sources][0] == "Sweet Spot"


@pytest.mark.asyncio
async def test_without_a_limiter_the_ranking_is_untouched():
    """An athlete with no confident diagnosis must get the pre-#627 behaviour."""
    db = _rag_db(_MIXED_ROWS)

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
        _, sources = await rag.retrieve_cycling_context(
            db, "Should I do more intervals?", focus_topics=[]
        )

    assert [s["title"] for s in sources] == [
        "Interval Basics",
        "Sweet Spot",
        "VO2max Development",
    ]


@pytest.mark.asyncio
async def test_a_topic_match_cannot_lift_a_chunk_past_the_similarity_floor():
    """The #528 gate stays absolute: relevance to the limiter is not relevance."""
    db = _rag_db(
        [
            ("Power Zones", "Zone 2.", "seed", None, None, 0.78, None),
            ("Off-topic VO2max", "Unrelated.", "seed", None, None, 0.42, ["vo2max"]),
        ]
    )

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
        context, sources = await rag.retrieve_cycling_context(
            db, "power zones", focus_topics=["vo2max"]
        )

    assert [s["title"] for s in sources] == ["Power Zones"]
    assert "Off-topic VO2max" not in context


@pytest.mark.asyncio
async def test_ranking_never_enlarges_the_prompt():
    """Steering changes which chunks reach the coach, never how many."""
    rows = [
        (f"Chunk {i}", "Body.", "seed", None, None, 0.9 - i * 0.01, None)
        for i in range(12)
    ] + [("Durability", "Late fade.", "paper", None, None, 0.72, ["endurance_durability"])]
    db = _rag_db(rows)

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
        _, sources = await rag.retrieve_cycling_context(
            db, "why do I fade?", k=5, focus_topics=["endurance_durability"]
        )

    assert len(sources) == 5
    assert sources[0]["title"] == "Durability"


@pytest.mark.asyncio
async def test_the_pool_is_only_widened_when_there_is_a_limiter_to_rank_by():
    """A wider LIMIT that nothing reorders is pure waste."""
    db = _rag_db([])

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
        await rag.retrieve_cycling_context(db, "q", k=5)
        await rag.retrieve_cycling_context(db, "q", k=5, focus_topics=["vo2max"])

    plain_limit, focused_limit = (
        call.args[1]["k"] for call in db.execute.await_args_list
    )

    assert plain_limit == 5, "an unfocused search must not pay for a wider pool"
    # Not `== k * MULTIPLIER`, which would pass at a multiplier of 1 — the value
    # that quietly turns limiter-aware retrieval back into plain similarity.
    assert focused_limit > plain_limit, (
        "ranking can only promote a chunk the search actually returned"
    )


@pytest.mark.asyncio
async def test_rows_ingested_before_tagging_are_ranked_not_discarded():
    """The column is nullable; a corpus that predates it must still answer."""
    db = _rag_db(
        [
            ("Old Chunk A", "Body.", "seed", None, None, 0.81, None),
            ("Old Chunk B", "Body.", "seed", None, None, 0.79, None),
        ]
    )

    with patch.object(rag, "_embed", new_callable=AsyncMock, return_value=[0.1] * 768):
        _, sources = await rag.retrieve_cycling_context(
            db, "threshold work", focus_topics=["threshold"]
        )

    assert [s["title"] for s in sources] == ["Old Chunk A", "Old Chunk B"]


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
