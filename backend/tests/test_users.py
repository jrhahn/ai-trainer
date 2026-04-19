import pytest


@pytest.mark.asyncio
async def test_get_update_and_delete_me(client, auth_headers):
    get_response = await client.get("/api/v1/users/me", headers=auth_headers)
    assert get_response.status_code == 200
    assert get_response.json()["email"] == "rider@example.com"

    update_response = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={
            "bikeType": "road",
            "trainingGoal": "ftp_improvement",
            "weeklyHours": 8,
            "fitnessLevel": "intermediate",
            "isOnboarded": True,
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["bikeType"] == "road"
    assert update_response.json()["isOnboarded"] is True

    delete_response = await client.delete("/api/v1/users/me", headers=auth_headers)
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_users_me_requires_auth(client):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_metrics_history_empty(client, auth_headers):
    """GET /metrics-history returns an empty list for a new user."""
    response = await client.get("/api/v1/users/me/metrics-history", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "snapshots" in body
    assert body["snapshots"] == []


@pytest.mark.asyncio
async def test_metrics_history_after_analyse_activities(client, auth_headers, mock_ai_service):
    """analyse-activities should create a metric snapshot visible in /metrics-history."""
    analyse_response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 999,
                    "name": "Test Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 300,
                    "startDate": "2026-04-18T08:00:00Z",
                    "averageWatts": 200,
                }
            ]
        },
    )
    assert analyse_response.status_code == 200

    history_response = await client.get(
        "/api/v1/users/me/metrics-history", headers=auth_headers
    )
    assert history_response.status_code == 200
    body = history_response.json()
    assert len(body["snapshots"]) == 1
    snap = body["snapshots"][0]
    assert snap["ftp"] == 280  # matches mock_ai_service estimatedFTP
    assert snap["thresholdHR"] == 172  # matches mock_ai_service estimatedThresholdHR
    assert snap["source"] == "strava_analysis"
    assert "recordedAt" in snap


@pytest.mark.asyncio
async def test_metrics_history_requires_auth(client):
    response = await client.get("/api/v1/users/me/metrics-history")
    assert response.status_code == 401
