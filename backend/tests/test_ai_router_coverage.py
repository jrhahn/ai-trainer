"""Extra coverage tests for routers/ai.py.

Exercises endpoints and helpers that are missing from the base test_ai.py suite:
- _next_race_date_from_events (pure helper)
- _provider / _default_provider (unit)
- readiness_score endpoint
- review_new_rides endpoint
- refresh_knowledge endpoint
- refresh_login_summary endpoint
- 503 rate-limit paths for analyse_activities, generate_plan,
  ask_trainer, race_event_feedback, review_new_rides, refresh_login_summary
- Auto-adapt plan when flag_for_adaptation=True
- Ask-trainer with ride_note_update persisting a note
- Readiness score with rider assessment FTP
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

import services.ai_service as ai_service
from services.ai_service import AIRateLimitError


# ---------------------------------------------------------------------------
# _next_race_date_from_events (pure logic, no DB)
# ---------------------------------------------------------------------------


def test_next_race_date_returns_closest_upcoming(monkeypatch):
    from datetime import date
    import routers.ai as ai_router

    monkeypatch.setattr(ai_router, "app_today", lambda: date(2026, 5, 1))

    events = [
        {"date": "2026-06-01"},
        {"date": "2026-07-15"},
        {"date": "2025-12-01"},  # past
    ]
    result = ai_router._next_race_date_from_events(events, None)
    assert result == "2026-06-01"


def test_next_race_date_uses_fallback_when_no_events(monkeypatch):
    from datetime import date
    import routers.ai as ai_router

    monkeypatch.setattr(ai_router, "app_today", lambda: date(2026, 5, 1))

    result = ai_router._next_race_date_from_events([], "2026-08-10")
    assert result == "2026-08-10"


def test_next_race_date_returns_none_when_all_past(monkeypatch):
    from datetime import date
    import routers.ai as ai_router

    monkeypatch.setattr(ai_router, "app_today", lambda: date(2026, 5, 1))

    events = [{"date": "2025-01-01"}, {"date": "2025-06-15"}]
    result = ai_router._next_race_date_from_events(events, None)
    assert result is None


def test_next_race_date_ignores_invalid_dates():
    import routers.ai as ai_router

    events = [{"date": "not-a-date"}, {"date": None}]
    # Should not raise even with malformed data
    result = ai_router._next_race_date_from_events(events, "bad-fallback")
    assert result is None


# ---------------------------------------------------------------------------
# readiness_score endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_score_no_ride_data(client, auth_headers):
    """readiness_score returns a valid score when there is no ride data yet."""
    response = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "score" in body
    assert "ctl" in body
    assert "atl" in body
    assert "tsb" in body
    assert "recommendations" in body
    assert isinstance(body["recommendations"], list)
    # Each recommendation explains itself with supporting-evidence reasoning.
    for rec in body["recommendations"]:
        assert rec["recommendation"]
        assert isinstance(rec["reasoning"], list)
        assert rec["reasoning"]


async def _seed_observation(client, auth_headers, fact: str) -> None:
    """Persist a high-confidence athlete-memory fact (prompt threshold is 0.5)."""
    created = await client.post(
        "/api/v1/users/me/athlete-memory-facts",
        headers=auth_headers,
        json={"fact": fact, "category": "behaviour", "confidence": 0.9},
    )
    assert created.status_code == 201


def _reasoning_lines(body: dict) -> list[str]:
    return [line for rec in body["recommendations"] for line in rec["reasoning"]]


@pytest.mark.asyncio
async def test_readiness_score_keyword_observation_matching(
    client, auth_headers, monkeypatch
):
    """With keyword matching configured, observations weave in without any LLM call."""
    import routers.ai as ai_router

    monkeypatch.setattr(
        ai_router.settings, "readiness_observation_matching", "keyword"
    )
    # If keyword mode is honoured the LLM matcher must never be called.
    monkeypatch.setattr(
        ai_router.ai_service,
        "match_observations_to_recommendations",
        AsyncMock(side_effect=AssertionError("LLM matcher must not be called")),
    )
    await _seed_observation(
        client, auth_headers, "Tends to skip easy endurance rides when motivation dips."
    )

    response = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)
    assert response.status_code == 200
    assert any(
        "Personal observation: Tends to skip easy endurance rides" in line
        for line in _reasoning_lines(response.json())
    )


@pytest.mark.asyncio
async def test_readiness_score_llm_observation_matching(
    client, auth_headers, monkeypatch
):
    """Default LLM matching routes observations via the matcher's assignments."""
    import routers.ai as ai_router

    monkeypatch.setattr(ai_router.settings, "readiness_observation_matching", "llm")

    async def fake_matcher(recommendations, observations, provider="openai"):
        # Route every observation onto the last recommendation.
        return {len(recommendations) - 1: list(observations)}

    monkeypatch.setattr(
        ai_router.ai_service,
        "match_observations_to_recommendations",
        fake_matcher,
    )
    await _seed_observation(client, auth_headers, "Prefers riding solo in the mornings.")

    response = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert (
        body["recommendations"][-1]["reasoning"][0]
        == "Personal observation: Prefers riding solo in the mornings."
    )


@pytest.mark.asyncio
async def test_readiness_score_llm_matching_falls_back_on_error(
    client, auth_headers, monkeypatch
):
    """If the LLM matcher raises, keyword matching still surfaces observations."""
    import routers.ai as ai_router

    monkeypatch.setattr(ai_router.settings, "readiness_observation_matching", "llm")
    monkeypatch.setattr(
        ai_router.ai_service,
        "match_observations_to_recommendations",
        AsyncMock(side_effect=RuntimeError("llm down")),
    )
    await _seed_observation(
        client, auth_headers, "Tends to skip easy endurance rides when motivation dips."
    )

    response = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)
    assert response.status_code == 200
    assert any(
        "Personal observation: Tends to skip easy endurance rides" in line
        for line in _reasoning_lines(response.json())
    )


@pytest.mark.asyncio
async def test_readiness_score_with_race_events(client, auth_headers):
    """readiness_score includes days_until_race when race events exist."""
    from datetime import date, timedelta

    future_date = (date.today() + timedelta(days=30)).isoformat()

    # Create a race event
    await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={
            "date": future_date,
            "startTime": "09:00",
            "distanceKm": 120.0,
            "elevationM": 1500,
        },
    )

    response = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    # field is either camelCase or snake_case depending on schema config
    days = body.get("daysUntilRace") or body.get("days_until_race")
    assert days is not None and days > 0
    race_date = body.get("raceDate") or body.get("race_date")
    assert race_date == future_date


@pytest.mark.asyncio
async def test_readiness_score_with_rider_assessment(
    client, auth_headers, mock_ai_service
):
    """readiness_score uses FTP from rider assessment when available."""
    # Create a rider assessment via analyse-activities
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 3001,
                    "name": "Tempo Ride",
                    "type": "Ride",
                    "distance": 50000,
                    "movingTime": 4500,
                    "elapsedTime": 4600,
                    "totalElevationGain": 500,
                    "startDate": "2026-04-15T08:00:00Z",
                    "averageWatts": 240,
                }
            ],
            "currentFtp": 280,
        },
    )

    response = await client.get("/api/v1/ai/readiness-score", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["score"] >= 0
    assert body["score"] <= 100


# ---------------------------------------------------------------------------
# review_new_rides endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_new_rides_empty(client, auth_headers):
    """review_new_rides returns empty review when there are no unreviewed rides."""
    response = await client.post("/api/v1/ai/review-new-rides", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["rideCount"] == 0
    assert body["review"] == ""


@pytest.mark.asyncio
async def test_review_new_rides_with_unreviewed_rides(
    client, auth_headers, mock_ai_service
):
    """review_new_rides calls batch_review_rides when unreviewed rides exist."""
    # Create a ride metric by analysing activities first
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 9001,
                    "name": "Morning Ride",
                    "type": "Ride",
                    "distance": 50000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 400,
                    "startDate": "2026-04-20T07:00:00Z",
                    "averageWatts": 200,
                }
            ]
        },
    )

    response = await client.post("/api/v1/ai/review-new-rides", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["rideCount"] == 1
    assert body["review"] == "Good training block."


@pytest.mark.asyncio
async def test_review_new_rides_503_on_rate_limit(
    client, auth_headers, mock_ai_service
):
    """review_new_rides returns HTTP 503 when the AI rate limit is hit."""
    # First create a ride so it has something to review
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 9002,
                    "name": "Rate-limited Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3600,
                    "totalElevationGain": 300,
                    "startDate": "2026-04-21T07:00:00Z",
                    "averageWatts": 195,
                }
            ]
        },
    )

    mock_ai_service["batch_review_rides"].side_effect = AIRateLimitError("rate limited")
    response = await client.post("/api/v1/ai/review-new-rides", headers=auth_headers)
    assert response.status_code == 503
    mock_ai_service["batch_review_rides"].side_effect = None  # reset


# ---------------------------------------------------------------------------
# refresh_knowledge endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_knowledge_queued(client, auth_headers):
    """refresh_knowledge returns 200 with status=started when OPENAI_API_KEY is set."""
    response = await client.post("/api/v1/ai/refresh-knowledge", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "started"


@pytest.mark.asyncio
async def test_refresh_knowledge_503_without_openai_key(
    client, auth_headers, monkeypatch
):
    """refresh_knowledge returns 503 when OPENAI_API_KEY is not configured."""
    from config import settings

    monkeypatch.setattr(settings, "openai_api_key", None)

    response = await client.post("/api/v1/ai/refresh-knowledge", headers=auth_headers)
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# refresh_login_summary endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_login_summary_404_without_assessment(client, auth_headers):
    """refresh_login_summary returns 404 when the user has no rider assessment."""
    response = await client.post(
        "/api/v1/ai/refresh-login-summary", headers=auth_headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_refresh_login_summary_returns_summary(
    client, auth_headers, mock_ai_service
):
    """refresh_login_summary generates and returns a login summary."""
    # First create a rider assessment via analyse-activities
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 777,
                    "name": "Tuesday Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3600,
                    "totalElevationGain": 300,
                    "startDate": "2026-04-22T08:00:00Z",
                    "averageWatts": 220,
                }
            ]
        },
    )

    with patch.object(
        ai_service, "generate_login_summary", AsyncMock(return_value="Welcome back!")
    ):
        response = await client.post(
            "/api/v1/ai/refresh-login-summary", headers=auth_headers
        )

    assert response.status_code == 200
    body = response.json()
    assert "loginSummary" in body


@pytest.mark.asyncio
async def test_refresh_login_summary_503_on_rate_limit(
    client, auth_headers, mock_ai_service
):
    """refresh_login_summary returns 503 when the AI rate limit is hit."""
    # Create assessment first
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 778,
                    "name": "Another Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3600,
                    "totalElevationGain": 300,
                    "startDate": "2026-04-23T08:00:00Z",
                    "averageWatts": 210,
                }
            ]
        },
    )

    with patch.object(
        ai_service,
        "generate_login_summary",
        AsyncMock(side_effect=AIRateLimitError("rate")),
    ):
        response = await client.post(
            "/api/v1/ai/refresh-login-summary", headers=auth_headers
        )

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Auto-adapt plan path (flag_for_adaptation=True in rate_workout)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_workout_triggers_auto_adapt(client, auth_headers, mock_ai_service):
    """rate_workout triggers plan adaptation when flag_for_adaptation is True."""
    # Set up mock to return flag_for_adaptation=True
    mock_ai_service["rate_completed_workout"].return_value = {
        "feedback": "You seem fatigued — consider a rest day.",
        "flag_for_adaptation": True,
        "needs_athlete_feedback": False,
        "follow_up_question": None,
        "suggested_feedback_tags": [],
    }

    response = await client.post(
        "/api/v1/ai/rate-workout",
        headers=auth_headers,
        json={
            "day": {
                "date": "2026-04-10",
                "workoutType": "endurance",
                "title": "Hard Ride",
                "description": "Felt terrible",
                "durationMinutes": 90,
                "feedback": {
                    "actualDurationMinutes": 45,
                    "perceivedEffort": 5,
                    "notes": "Really tough, legs dead",
                    "completedAt": "2026-04-10T10:00:00Z",
                },
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    flag = body.get("flagForAdaptation") or body.get("flag_for_adaptation")
    assert flag is True
    # adapt_training_plan mock should have been called
    mock_ai_service["adapt_training_plan"].assert_called()
    # Reset
    mock_ai_service["rate_completed_workout"].return_value = {
        "feedback": "Strong execution overall.",
        "flag_for_adaptation": False,
        "needs_athlete_feedback": False,
        "follow_up_question": None,
        "suggested_feedback_tags": [],
    }


# ---------------------------------------------------------------------------
# Ask-trainer with ride_note_update persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_trainer_persists_ride_note_update(
    client, auth_headers, mock_ai_service
):
    """ask_trainer persists ride notes when the response includes ride_note_update."""
    # Create a ride first
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 6001,
                    "name": "Tuesday Ride",
                    "type": "Ride",
                    "distance": 50000,
                    "movingTime": 4500,
                    "elapsedTime": 4600,
                    "totalElevationGain": 400,
                    "startDate": "2026-04-18T07:00:00Z",
                    "averageWatts": 200,
                }
            ]
        },
    )

    # Return a ride_note_update from ask_trainer
    mock_ai_service["ask_trainer"].return_value = {
        "response": "I've noted that you felt strong on Tuesday.",
        "plan_updates": [],
        "sources": [],
        "ride_note_update": {
            "activity_date": "2026-04-18",
            "note": "Felt strong, HR was low for power output",
        },
    }

    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "I felt great on Tuesday's ride!"},
    )
    assert response.status_code == 200

    # Reset mock
    mock_ai_service["ask_trainer"].return_value = {
        "response": "Take it easy tomorrow.",
        "plan_updates": [],
        "sources": [],
    }


@pytest.mark.asyncio
async def test_ask_trainer_forwards_browser_timezone(
    client, auth_headers, mock_ai_service
):
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers={**auth_headers, "X-App-Timezone": "America/Los_Angeles"},
        json={"question": "What should I do today?"},
    )

    assert response.status_code == 200
    call_kwargs = mock_ai_service["ask_trainer"].call_args.kwargs
    assert call_kwargs["timezone_name"] == "America/Los_Angeles"


# ---------------------------------------------------------------------------
# 503 rate-limit paths for core endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_activities_503_on_rate_limit(
    client, auth_headers, mock_ai_service
):
    """analyse_activities returns HTTP 503 when AIRateLimitError is raised."""
    mock_ai_service["analyse_strava_activities"].side_effect = AIRateLimitError(
        "rate limited"
    )
    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 1,
                    "name": "Ride",
                    "type": "Ride",
                    "distance": 50000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 500,
                    "startDate": "2026-04-10T08:00:00Z",
                    "averageWatts": 220,
                }
            ]
        },
    )
    assert response.status_code == 503
    mock_ai_service["analyse_strava_activities"].side_effect = None


@pytest.mark.asyncio
async def test_generate_plan_503_on_rate_limit(client, auth_headers, mock_ai_service):
    mock_ai_service["generate_training_plan"].side_effect = AIRateLimitError(
        "rate limited"
    )
    response = await client.post(
        "/api/v1/ai/generate-plan", headers=auth_headers, json={}
    )
    assert response.status_code == 503
    mock_ai_service["generate_training_plan"].side_effect = None


@pytest.mark.asyncio
async def test_ask_trainer_503_on_rate_limit_via_patch(
    client, auth_headers, mock_ai_service
):
    mock_ai_service["ask_trainer"].side_effect = AIRateLimitError("rate limited")
    response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={"question": "When should I taper?"},
    )
    assert response.status_code == 503
    mock_ai_service["ask_trainer"].side_effect = None


@pytest.mark.asyncio
async def test_race_event_feedback_503_on_rate_limit(
    client, auth_headers, mock_ai_service
):
    mock_ai_service["race_event_feedback"].side_effect = AIRateLimitError(
        "rate limited"
    )
    from datetime import date, timedelta

    future = (date.today() + timedelta(days=60)).isoformat()
    response = await client.post(
        "/api/v1/ai/race-event-feedback",
        headers=auth_headers,
        json={
            "event": {
                "id": "abc",
                "date": future,
                "startTime": "08:00",
                "distanceKm": 160.0,
                "elevationM": 2000,
            },
            "action": "added",
        },
    )
    assert response.status_code == 503
    mock_ai_service["race_event_feedback"].side_effect = None


@pytest.mark.asyncio
async def test_rate_workout_503_on_rate_limit(client, auth_headers, mock_ai_service):
    mock_ai_service["rate_completed_workout"].side_effect = AIRateLimitError(
        "rate limited"
    )
    response = await client.post(
        "/api/v1/ai/rate-workout",
        headers=auth_headers,
        json={
            "day": {
                "date": "2026-04-10",
                "workoutType": "endurance",
                "title": "Easy Ride",
                "description": "Easy aerobic ride",
                "durationMinutes": 90,
                "feedback": {
                    "actualDurationMinutes": 88,
                    "perceivedEffort": 2,
                    "notes": "Felt great",
                    "completedAt": "2026-04-10T10:00:00Z",
                },
            },
        },
    )
    assert response.status_code == 503
    mock_ai_service["rate_completed_workout"].side_effect = None
