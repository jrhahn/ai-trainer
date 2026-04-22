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


@pytest.mark.asyncio
async def test_estimate_ftp_no_data(client, auth_headers):
    """estimate-ftp on a brand-new account with no rides returns null FTP."""
    response = await client.post(
        "/api/v1/users/me/estimate-ftp",
        headers=auth_headers,
        json={"maxHeartRate": 185, "restingHeartRate": 55},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["estimatedFTP"] is None
    assert body["source"] == "none"

    # Verify HR values were persisted on the user profile.
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me.json()["maxHeartRate"] == 185
    assert me.json()["restingHeartRate"] == 55


@pytest.mark.asyncio
async def test_estimate_ftp_defaults_resting_hr(client, auth_headers):
    """estimate-ftp should default resting HR to 60 when it was never set."""
    # Ensure the user profile has no resting HR initially.
    me_before = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me_before.json()["restingHeartRate"] is None

    await client.post(
        "/api/v1/users/me/estimate-ftp",
        headers=auth_headers,
        json={"maxHeartRate": 190},
    )

    me_after = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me_after.json()["maxHeartRate"] == 190
    assert me_after.json()["restingHeartRate"] == 60


@pytest.mark.asyncio
async def test_estimate_ftp_returns_snapshot_ftp(client, auth_headers, mock_ai_service):
    """estimate-ftp returns FTP from the most recent AthleteMetricSnapshot."""
    # Create a snapshot via analyse-activities (uses mock FTP = 280 W).
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 1001,
                    "name": "Hard Ride",
                    "type": "Ride",
                    "distance": 50000,
                    "movingTime": 4500,
                    "elapsedTime": 4600,
                    "totalElevationGain": 500,
                    "startDate": "2026-04-20T09:00:00Z",
                    "averageWatts": 240,
                }
            ]
        },
    )

    response = await client.post(
        "/api/v1/users/me/estimate-ftp",
        headers=auth_headers,
        json={"maxHeartRate": 185},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["estimatedFTP"] == 280  # from mock_ai_service
    assert body["source"] == "strava_analysis"


@pytest.mark.asyncio
async def test_estimate_ftp_requires_auth(client):
    response = await client.post(
        "/api/v1/users/me/estimate-ftp",
        json={"maxHeartRate": 185},
    )
    assert response.status_code == 401
