"""The readiness score asks the model once while nothing changed, and bills it
(ai-trainer-ops#255).

GET /ai/readiness-score matched the coach's observations to the
recommendations with a model call on every dashboard load. GETs are exempt from
the AI rate limit, and no usage scope was open, so the calls counted toward
nobody's budget.
"""

from __future__ import annotations

import pytest

import routers.ai as ai_router
from services import token_accounting
from tests.test_ai_router_coverage import _seed_observation


def _counting_matcher(calls: list[str]):
    async def matcher(recommendations, observations, provider="openai"):
        calls.append(token_accounting.current_source())
        return {len(recommendations) - 1: list(observations)}

    return matcher


@pytest.fixture(autouse=True)
def _llm_matching(monkeypatch):
    monkeypatch.setattr(ai_router.settings, "readiness_observation_matching", "llm")
    monkeypatch.setattr(ai_router, "_readiness_matches", {}, raising=False)


@pytest.mark.asyncio
async def test_two_loads_with_nothing_changed_ask_the_model_once(
    client, auth_headers, monkeypatch
):
    calls: list[str] = []
    monkeypatch.setattr(
        ai_router.ai_service,
        "match_observations_to_recommendations",
        _counting_matcher(calls),
    )
    await _seed_observation(
        client, auth_headers, "Prefers riding solo in the mornings."
    )

    first = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)
    second = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)

    assert first.status_code == second.status_code == 200
    assert len(calls) == 1
    # The cached answer still reaches the athlete.
    assert second.json()["recommendations"][-1]["reasoning"][0]["text"] == (
        "Prefers riding solo in the mornings."
    )


@pytest.mark.asyncio
async def test_a_new_observation_asks_the_model_again(
    client, auth_headers, monkeypatch
):
    calls: list[str] = []
    monkeypatch.setattr(
        ai_router.ai_service,
        "match_observations_to_recommendations",
        _counting_matcher(calls),
    )
    await _seed_observation(
        client, auth_headers, "Prefers riding solo in the mornings."
    )
    await client.get("/api/v1/ai/readiness-score", headers=auth_headers)

    await _seed_observation(client, auth_headers, "Sleeps badly before races.")
    await client.get("/api/v1/ai/readiness-score", headers=auth_headers)

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_the_model_call_is_billed_to_the_athlete(
    client, auth_headers, monkeypatch
):
    calls: list[str] = []
    monkeypatch.setattr(
        ai_router.ai_service,
        "match_observations_to_recommendations",
        _counting_matcher(calls),
    )
    await _seed_observation(
        client, auth_headers, "Prefers riding solo in the mornings."
    )

    await client.get("/api/v1/ai/readiness-score", headers=auth_headers)

    assert calls == ["api:readiness-score"]


@pytest.mark.asyncio
async def test_a_day_later_the_model_is_asked_again(client, auth_headers, monkeypatch):
    calls: list[str] = []
    now = [1000.0]
    monkeypatch.setattr(ai_router, "_readiness_clock", lambda: now[0])
    monkeypatch.setattr(
        ai_router.ai_service,
        "match_observations_to_recommendations",
        _counting_matcher(calls),
    )
    await _seed_observation(
        client, auth_headers, "Prefers riding solo in the mornings."
    )

    await client.get("/api/v1/ai/readiness-score", headers=auth_headers)
    now[0] += ai_router._READINESS_MATCH_TTL_SECONDS - 1
    await client.get("/api/v1/ai/readiness-score", headers=auth_headers)
    now[0] += 2
    await client.get("/api/v1/ai/readiness-score", headers=auth_headers)

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_failed_call_is_not_kept(client, auth_headers, monkeypatch):
    calls: list[str] = []
    answer = _counting_matcher(calls)

    async def fails_once(recommendations, observations, provider="openai"):
        if not calls:
            calls.append("failed")
            raise RuntimeError("model down")
        return await answer(recommendations, observations, provider)

    monkeypatch.setattr(
        ai_router.ai_service, "match_observations_to_recommendations", fails_once
    )
    await _seed_observation(
        client, auth_headers, "Prefers riding solo in the mornings."
    )

    first = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)
    second = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)

    assert first.status_code == second.status_code == 200
    assert calls == ["failed", "api:readiness-score"]
    assert second.json()["recommendations"][-1]["reasoning"][0]["text"] == (
        "Prefers riding solo in the mornings."
    )
