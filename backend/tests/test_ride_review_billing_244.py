"""The per-ride coach review is billed to the athlete on every path (ai-trainer-ops#244).

``POST /ai/analyse-activities`` closes its ``api:`` scope before it reviews the
rides it auto-matched, so the two LLM calls per ride were logged as "billed to
nobody": invisible to the allowance, the budget cap and the cost report. The
scheduler path got its scope in #516. ``review_matched_ride_and_adapt`` now
opens ``step:ride-review`` itself, so every caller is covered.
"""

import logging

import pytest
from sqlalchemy import select

PLAN = [
    {
        "date": "2026-05-04",
        "workoutType": "endurance",
        "title": "Endurance Ride",
        "description": "Steady Z2",
        "durationMinutes": 90,
    }
]

ACTIVITY = {
    "id": 61001,
    "name": "Morning endurance",
    "type": "Ride",
    "distance": 40000,
    "movingTime": 3600,
    "elapsedTime": 3600,
    "totalElevationGain": 300,
    "startDate": "2026-05-04T08:00:00Z",
}

REVIEW = {
    "feedback": "Strong execution overall.",
    "flag_for_adaptation": False,
    "needs_athlete_feedback": False,
    "follow_up_question": None,
    "suggested_feedback_tags": [],
}


def _provider_call(*args, **kwargs):
    """What the provider layer records for the review's model call."""
    from services import token_accounting

    token_accounting.record_call(
        task="feedback",
        provider="gemini",
        model="gemini-3.5-flash-lite",
        system_prompt="rate this ride",
        json_mode=True,
        latency_ms=10,
        input_tokens=4_000,
        output_tokens=300,
        cached_tokens=1_000,
        total_tokens=4_300,
    )
    return REVIEW


async def _plan_and_user(auth_headers):
    import crud
    from auth import decode_token
    from tests.conftest import TestSessionLocal

    user_id = decode_token(auth_headers["Authorization"].split(" ", 1)[1])
    async with TestSessionLocal() as db:
        await crud.upsert_training_plan(db, user_id, PLAN)
        await db.commit()
        user = await crud.get_user_by_id(db, user_id)
        return user_id, user.consumed_tokens or 0


@pytest.mark.asyncio
async def test_analyse_activities_bills_the_ride_review_to_the_athlete(
    client, auth_headers, mock_ai_service, caplog
):
    import crud
    import models
    from tests.conftest import TestSessionLocal

    user_id, consumed_before = await _plan_and_user(auth_headers)
    mock_ai_service["rate_completed_workout"].side_effect = _provider_call

    with caplog.at_level(logging.WARNING, logger="services.token_accounting"):
        response = await client.post(
            "/api/v1/ai/analyse-activities",
            headers=auth_headers,
            json={"activities": [ACTIVITY]},
        )

    assert response.status_code == 200
    mock_ai_service["rate_completed_workout"].assert_called_once()
    assert "billed to nobody" not in caplog.text
    async with TestSessionLocal() as db:
        rows = (
            await db.execute(select(models.LlmCall).where(models.LlmCall.user_id == user_id))
        ).scalars().all()
        user = await crud.get_user_by_id(db, user_id)
    assert [(row.source, row.task, row.total_tokens) for row in rows] == [
        ("step:ride-review", "feedback", 4_300)
    ]
    assert user.consumed_tokens == consumed_before + 4_300


@pytest.mark.asyncio
async def test_ride_review_over_the_budget_is_skipped_not_raised(
    client, auth_headers, mock_ai_service, monkeypatch
):
    """The budget gate now applies to the review too. The review stays best-effort:
    the import goes through, and the ride just gets no coach note."""
    from services import token_accounting

    await _plan_and_user(auth_headers)

    async def over_budget(db, user, *, source):
        if source == "step:ride-review":
            raise token_accounting.TokenBudgetExceededError(spent=2, budget=1, window_days=30)

    monkeypatch.setattr(token_accounting, "enforce_token_budget", over_budget)

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={"activities": [ACTIVITY]},
    )

    assert response.status_code == 200
    mock_ai_service["rate_completed_workout"].assert_not_called()
    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    ride = next(r for r in history.json()["rides"] if r["stravaActivityId"] == 61001)
    assert ride["planMatchStatus"] == "auto_matched"
    assert not ride.get("coachNote")
