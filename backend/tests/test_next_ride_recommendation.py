"""Tests for the next-ride recommendation feature (Task 6).

Covers:
- ``recommend_next_session`` service function (mocked LLM)
- ``POST /ai/next-ride-recommendation`` router endpoint (plan updates persisted)
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import services.ai_service as ai_service


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

PROFILE = {
    "name": "Test Rider",
    "email": "rider@example.com",
    "bikeType": "road",
    "trainingGoal": "ftp_improvement",
    "weeklyHours": 8,
    "followsTrainingPlan": True,
    "fitnessLevel": "intermediate",
}

TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()

PLAN_WITH_INTERVALS = [
    {
        "date": TOMORROW,
        "workoutType": "intervals",
        "title": "VO2 Intervals",
        "description": "5×4 min at 110% FTP",
        "durationMinutes": 60,
        "completed": False,
    }
]


class FakeRideMetric:
    """Duck-typed ride metric for testing (mirrors the ORM object interface)."""

    def __init__(self, **kwargs):
        self.activity_date = kwargs.get("activity_date", TODAY)
        self.sport_type = kwargs.get("sport_type", "cycling")
        self.ride_purpose = kwargs.get("ride_purpose", None)
        self.duration_seconds = kwargs.get("duration_seconds", 3600)
        self.tss = kwargs.get("tss", 80.0)
        self.normalized_power_w = kwargs.get("normalized_power_w", 240)
        self.ctl_after = kwargs.get("ctl_after", 55.0)
        self.atl_after = kwargs.get("atl_after", 68.0)
        self.tsb_after = kwargs.get("tsb_after", -13.0)
        self.coach_note = kwargs.get("coach_note", None)
        self.user_note = kwargs.get("user_note", "Legs felt heavy. RPE 8/10.")
        self.classification_confidence = kwargs.get("classification_confidence", "high")
        self.classification_reason = kwargs.get("classification_reason", None)
        self.strava_activity_id = kwargs.get("strava_activity_id", 12345)


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommend_next_session_keep_as_planned():
    """Service returns 'keep_as_planned' recommendation correctly."""
    llm_output = json.dumps({
        "response": "You're in good shape. Go ahead with the planned intervals tomorrow.",
        "next_session_recommendation": "Complete the planned VO2 intervals as scheduled — your TSB is positive.",
        "recommendation_type": "keep_as_planned",
        "planUpdates": None,
    })

    with patch.object(ai_service, "_chat", new=AsyncMock(return_value=llm_output)):
        result = await ai_service.recommend_next_session(
            rides=[FakeRideMetric(tsb_after=5.0, user_note="Felt great! RPE 6/10.")],
            plan=PLAN_WITH_INTERVALS,
            profile=PROFILE,
            ctl=58.0,
            atl=53.0,
            tsb=5.0,
        )

    assert result["response"] == "You're in good shape. Go ahead with the planned intervals tomorrow."
    assert "VO2 intervals" in result["next_session_recommendation"]
    assert result["recommendation_type"] == "keep_as_planned"
    assert result["plan_updates"] is None


@pytest.mark.asyncio
async def test_recommend_next_session_recovery():
    """Service returns 'recovery' recommendation with plan update."""
    llm_output = json.dumps({
        "response": "Your legs are heavy and your TSB is deeply negative. Take tomorrow as a recovery ride.",
        "next_session_recommendation": "Replace tomorrow's intervals with a 45-min easy recovery spin.",
        "recommendation_type": "recovery",
        "planUpdates": [
            {
                "date": TOMORROW,
                "workoutType": "recovery",
                "title": "Recovery Spin",
                "description": "Easy aerobic spin at 55-65% FTP",
                "durationMinutes": 45,
            }
        ],
    })

    with patch.object(ai_service, "_chat", new=AsyncMock(return_value=llm_output)):
        result = await ai_service.recommend_next_session(
            rides=[FakeRideMetric(tsb_after=-18.0, user_note="Legs felt very heavy. RPE 9/10.")],
            plan=PLAN_WITH_INTERVALS,
            profile=PROFILE,
            ctl=60.0,
            atl=78.0,
            tsb=-18.0,
        )

    assert result["recommendation_type"] == "recovery"
    assert result["plan_updates"] is not None
    assert len(result["plan_updates"]) == 1
    assert result["plan_updates"][0]["workoutType"] == "recovery"
    assert result["plan_updates"][0]["date"] == TOMORROW


@pytest.mark.asyncio
async def test_recommend_next_session_easier():
    """Service returns 'easier' recommendation."""
    llm_output = json.dumps({
        "response": "You pushed hard yesterday. Do the intervals but drop to 3 reps instead of 5.",
        "next_session_recommendation": "Do the intervals but reduce to 3×4 min to manage fatigue.",
        "recommendation_type": "easier",
        "planUpdates": [
            {
                "date": TOMORROW,
                "workoutType": "intervals",
                "title": "VO2 Intervals (reduced)",
                "description": "3×4 min at 110% FTP",
                "durationMinutes": 45,
            }
        ],
    })

    with patch.object(ai_service, "_chat", new=AsyncMock(return_value=llm_output)):
        result = await ai_service.recommend_next_session(
            rides=[FakeRideMetric(tsb_after=-10.0)],
            plan=PLAN_WITH_INTERVALS,
            profile=PROFILE,
            ctl=55.0,
            atl=65.0,
            tsb=-10.0,
        )

    assert result["recommendation_type"] == "easier"
    assert result["plan_updates"][0]["durationMinutes"] == 45


@pytest.mark.asyncio
async def test_recommend_next_session_move_intensity():
    """Service returns 'move_intensity' recommendation."""
    llm_output = json.dumps({
        "response": "Given your fatigue, it is better to push the intensity to Thursday.",
        "next_session_recommendation": "Move the intervals to Thursday; do an easy endurance ride tomorrow.",
        "recommendation_type": "move_intensity",
        "planUpdates": [
            {
                "date": TOMORROW,
                "workoutType": "endurance",
                "title": "Easy Endurance",
                "description": "Steady aerobic ride at 65-75% FTP",
                "durationMinutes": 60,
            }
        ],
    })

    with patch.object(ai_service, "_chat", new=AsyncMock(return_value=llm_output)):
        result = await ai_service.recommend_next_session(
            rides=[FakeRideMetric(tsb_after=-15.0)],
            plan=PLAN_WITH_INTERVALS,
            profile=PROFILE,
            ctl=55.0,
            atl=70.0,
            tsb=-15.0,
        )

    assert result["recommendation_type"] == "move_intensity"
    assert result["plan_updates"][0]["workoutType"] == "endurance"


@pytest.mark.asyncio
async def test_recommend_next_session_no_rides():
    """Service handles empty rides list gracefully."""
    llm_output = json.dumps({
        "response": "No recent rides — go ahead with tomorrow's session as planned.",
        "next_session_recommendation": "No recent ride data. Proceed with the plan.",
        "recommendation_type": "keep_as_planned",
        "planUpdates": None,
    })

    with patch.object(ai_service, "_chat", new=AsyncMock(return_value=llm_output)):
        result = await ai_service.recommend_next_session(
            rides=[],
            plan=PLAN_WITH_INTERVALS,
            profile=PROFILE,
        )

    assert result["recommendation_type"] == "keep_as_planned"
    assert result["plan_updates"] is None


@pytest.mark.asyncio
async def test_recommend_next_session_llm_omits_plan_updates():
    """Service handles LLM response with missing planUpdates key."""
    llm_output = json.dumps({
        "response": "Looks good. Keep the plan.",
        "next_session_recommendation": "Proceed with tomorrow's session as planned.",
        "recommendation_type": "keep_as_planned",
    })

    with patch.object(ai_service, "_chat", new=AsyncMock(return_value=llm_output)):
        result = await ai_service.recommend_next_session(
            rides=[FakeRideMetric()],
            plan=PLAN_WITH_INTERVALS,
            profile=PROFILE,
        )

    assert result["plan_updates"] is None


# ---------------------------------------------------------------------------
# Router endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_ride_recommendation_no_rides(client, auth_headers, monkeypatch):
    """Endpoint returns recommendation even when user has no ride metrics yet."""
    monkeypatch.setattr(
        ai_service,
        "recommend_next_session",
        AsyncMock(
            return_value={
                "response": "No rides yet. Start easy.",
                "next_session_recommendation": "No prior ride data — start with an easy endurance ride.",
                "recommendation_type": "keep_as_planned",
                "plan_updates": None,
            }
        ),
    )

    response = await client.post(
        "/api/v1/ai/next-ride-recommendation",
        headers=auth_headers,
        json={},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "No rides yet. Start easy."
    assert body["recommendationType"] == "keep_as_planned"
    assert body["planUpdates"] is None


@pytest.mark.asyncio
async def test_next_ride_recommendation_plan_updates_persisted(client, auth_headers, monkeypatch, mock_ai_service):
    """Plan updates returned by the LLM are persisted to the database."""
    # Create a training plan with a future date that we can update
    await client.put(
        "/api/v1/users/me/plan",
        headers=auth_headers,
        json={
            "plan": [
                {
                    "date": TOMORROW,
                    "workoutType": "intervals",
                    "title": "VO2 Intervals",
                    "description": "5×4 min at 110% FTP",
                    "durationMinutes": 60,
                    "completed": False,
                }
            ]
        },
    )

    # Create a ride metric so the endpoint has something to work with
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 7001,
                    "name": "Morning Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 300,
                    "startDate": f"{TODAY}T07:00:00Z",
                    "averageWatts": 210,
                }
            ]
        },
    )

    update_date = TOMORROW

    monkeypatch.setattr(
        ai_service,
        "recommend_next_session",
        AsyncMock(
            return_value={
                "response": "Take it easy — swap to recovery.",
                "next_session_recommendation": "Replace tomorrow's session with a 45-min recovery spin.",
                "recommendation_type": "recovery",
                "plan_updates": [
                    {
                        "date": update_date,
                        "workoutType": "recovery",
                        "title": "Recovery Spin",
                        "description": "Easy aerobic recovery",
                        "durationMinutes": 45,
                    }
                ],
            }
        ),
    )

    response = await client.post(
        "/api/v1/ai/next-ride-recommendation",
        headers=auth_headers,
        json={},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recommendationType"] == "recovery"
    assert body["planUpdates"] is not None
    assert len(body["planUpdates"]) == 1

    # Verify the plan was actually updated in the database
    plan_after = await client.get("/api/v1/users/me/plan", headers=auth_headers)
    assert plan_after.status_code == 200
    updated_day = next(
        (d for d in plan_after.json()["plan"] if d["date"] == update_date),
        None,
    )
    assert updated_day is not None
    assert updated_day["workoutType"] == "recovery"
    assert updated_day["durationMinutes"] == 45


@pytest.mark.asyncio
async def test_next_ride_recommendation_with_strava_activity_id(client, auth_headers, monkeypatch, mock_ai_service):
    """Endpoint accepts strava_activity_id and returns recommendation."""
    # Create a ride metric for activity 8001
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 8001,
                    "name": "Hard Ride",
                    "type": "Ride",
                    "distance": 60000,
                    "movingTime": 5400,
                    "elapsedTime": 5500,
                    "totalElevationGain": 800,
                    "startDate": f"{TODAY}T07:00:00Z",
                    "averageWatts": 250,
                }
            ]
        },
    )

    monkeypatch.setattr(
        ai_service,
        "recommend_next_session",
        AsyncMock(
            return_value={
                "response": "Tough ride! Rest up tomorrow.",
                "next_session_recommendation": "Full rest day tomorrow after this hard effort.",
                "recommendation_type": "recovery",
                "plan_updates": None,
            }
        ),
    )

    response = await client.post(
        "/api/v1/ai/next-ride-recommendation",
        headers=auth_headers,
        json={"stravaActivityId": 8001},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recommendationType"] == "recovery"
    assert body["nextSessionRecommendation"] == "Full rest day tomorrow after this hard effort."


@pytest.mark.asyncio
async def test_next_ride_recommendation_rate_limit(client, auth_headers, monkeypatch):
    """Endpoint returns 503 when the AI service is rate-limited."""
    from services.ai_service import AIRateLimitError

    monkeypatch.setattr(
        ai_service,
        "recommend_next_session",
        AsyncMock(side_effect=AIRateLimitError("rate limited")),
    )

    response = await client.post(
        "/api/v1/ai/next-ride-recommendation",
        headers=auth_headers,
        json={},
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_next_ride_recommendation_no_plan_updates_when_keeping(client, auth_headers, monkeypatch):
    """When recommendation is keep_as_planned with no updates, plan_updates is null."""
    monkeypatch.setattr(
        ai_service,
        "recommend_next_session",
        AsyncMock(
            return_value={
                "response": "All good — proceed with the plan.",
                "next_session_recommendation": "Keep tomorrow's session as planned.",
                "recommendation_type": "keep_as_planned",
                "plan_updates": None,
            }
        ),
    )

    response = await client.post(
        "/api/v1/ai/next-ride-recommendation",
        headers=auth_headers,
        json={},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["planUpdates"] is None
    assert body["recommendationType"] == "keep_as_planned"
