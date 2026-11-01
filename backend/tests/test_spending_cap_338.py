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
