"""The provider's spending cap is no short throttle (review of 2026-11-01, ai-trainer-ops#338).

Gemini answers a project over its monthly spending cap with a 429, like any
rate limit. The athlete read "try again in a few minutes" for days, and nobody
told the owner.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import routers.ai as ai_router
from services import llm

ASK = "/api/v1/ai/ask-trainer"
CAP = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'Your project has "
    "exceeded its monthly spending cap.'}}"
)


def test_a_spending_cap_is_told_from_a_throttle():
    assert isinstance(llm.rate_limit_error(RuntimeError(CAP)), llm.AISpendingCapError)
    assert isinstance(
        llm.rate_limit_error(RuntimeError("insufficient_quota")), llm.AISpendingCapError
    )
    throttle = llm.rate_limit_error(RuntimeError("429 Quota exceeded for metric rpm"))
    assert type(throttle) is llm.AIRateLimitError


@pytest.mark.asyncio
async def test_the_athlete_reads_that_the_coach_is_paused_and_the_owner_is_told(
    client, auth_headers, mock_ai_service, monkeypatch
):
    pushed = AsyncMock(return_value=True)
    monkeypatch.setattr(ai_router.owner_alerts, "notify_once", pushed)
    mock_ai_service["ask_trainer"].side_effect = llm.AISpendingCapError(CAP)

    response = await client.post(ASK, headers=auth_headers, json={"question": "Hi"})
    await asyncio.gather(*ai_router._ALERT_TASKS)

    assert response.status_code == 503
    assert response.json()["detail"] == ai_router._SPENDING_CAP_DETAIL
    pushed.assert_awaited_once()
    assert pushed.await_args.args[0] == "ai-spending-cap"


@pytest.mark.asyncio
async def test_a_throttle_still_says_try_again_and_pushes_nothing(
    client, auth_headers, mock_ai_service, monkeypatch
):
    pushed = AsyncMock(return_value=True)
    monkeypatch.setattr(ai_router.owner_alerts, "notify_once", pushed)
    mock_ai_service["ask_trainer"].side_effect = llm.AIRateLimitError("429")

    response = await client.post(ASK, headers=auth_headers, json={"question": "Hi"})

    assert response.status_code == 503
    assert response.json()["detail"] == ai_router._RATE_LIMIT_DETAIL
    pushed.assert_not_called()


def test_without_a_running_loop_the_detail_still_says_paused():
    """A handler outside an event loop cannot push; it still tells the athlete."""
    assert (
        ai_router._rate_limit_detail(llm.AISpendingCapError(CAP))
        == ai_router._SPENDING_CAP_DETAIL
    )


def test_the_shared_handler_turns_the_cap_into_the_paused_detail():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        with ai_router._ai_errors_as_http():
            raise llm.AISpendingCapError(CAP)

    assert raised.value.status_code == 503
    assert raised.value.detail == ai_router._SPENDING_CAP_DETAIL


@pytest.mark.asyncio
async def test_extracting_facts_under_the_cap_says_paused(
    client, auth_headers, mock_ai_service, monkeypatch
):
    monkeypatch.setattr(ai_router.owner_alerts, "notify_once", AsyncMock())
    mock_ai_service["extract_athlete_facts"].side_effect = llm.AISpendingCapError(CAP)

    response = await client.post(
        "/api/v1/ai/extract-athlete-facts",
        headers=auth_headers,
        json={"transcript": "Athlete: I hate resting."},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == ai_router._SPENDING_CAP_DETAIL


@pytest.mark.asyncio
async def test_writing_week_goals_under_the_cap_says_paused(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(ai_router.owner_alerts, "notify_once", AsyncMock())
    monkeypatch.setattr(
        ai_router.week_goals,
        "ensure_week_goals",
        AsyncMock(side_effect=llm.AISpendingCapError(CAP)),
    )

    response = await client.post("/api/v1/ai/season-plan/goals", headers=auth_headers)

    assert response.status_code == 503
    assert response.json()["detail"] == ai_router._SPENDING_CAP_DETAIL
