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


@pytest.mark.asyncio
async def test_estimate_ftp_saves_threshold_hr(client, auth_headers):
    """estimate-ftp should persist threshold_heart_rate on the user profile when provided."""
    response = await client.post(
        "/api/v1/users/me/estimate-ftp",
        headers=auth_headers,
        json={"maxHeartRate": 185, "restingHeartRate": 55, "thresholdHeartRate": 162},
    )
    assert response.status_code == 200

    me = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me.json()["maxHeartRate"] == 185
    assert me.json()["restingHeartRate"] == 55
    assert me.json()["thresholdHeartRate"] == 162


@pytest.mark.asyncio
async def test_recalculate_metrics_creates_per_ride_snapshots(client, auth_headers, mock_ai_service):
    """recalculate-metrics should create one AthleteMetricSnapshot per ride, not just one final snapshot."""
    # Create two rides via analyse-activities.
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 3001,
                    "name": "Ride A",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 200,
                    "startDate": "2026-03-01T09:00:00Z",
                    "averageWatts": 200,
                },
                {
                    "id": 3002,
                    "name": "Ride B",
                    "type": "Ride",
                    "distance": 50000,
                    "movingTime": 5400,
                    "elapsedTime": 5500,
                    "totalElevationGain": 400,
                    "startDate": "2026-03-10T09:00:00Z",
                    "averageWatts": 220,
                },
            ]
        },
    )

    # Trigger a manual recalculation with an FTP override.
    recalc_response = await client.post(
        "/api/v1/users/me/recalculate-metrics",
        headers=auth_headers,
        json={"ftpOverride": 260},
    )
    assert recalc_response.status_code == 200
    assert recalc_response.json()["updated"] == 2

    # Metrics history should now contain one snapshot per ride, not just one.
    history_response = await client.get(
        "/api/v1/users/me/metrics-history", headers=auth_headers
    )
    assert history_response.status_code == 200
    snapshots = history_response.json()["snapshots"]
    assert len(snapshots) == 2, (
        f"Expected 2 per-ride snapshots after recalculate, got {len(snapshots)}"
    )
    # All snapshots should use the override FTP.
    for snap in snapshots:
        assert snap["ftp"] == 260
        assert snap["source"] == "manual_recalculate"
        # CTL value should be present (may be 0.0 when no power data)
        assert snap["ctl"] is not None


@pytest.mark.asyncio
async def test_ride_metrics_history_empty(client, auth_headers):
    """GET /ride-metrics-history returns an empty list for a new user."""
    response = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "rides" in body
    assert body["rides"] == []


@pytest.mark.asyncio
async def test_ride_metrics_history_after_analyse_activities(client, auth_headers, mock_ai_service):
    """After analyse-activities, ride-metrics-history should include per-ride CTL/ATL/TSB."""
    analyse_response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 2001,
                    "name": "Morning Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 300,
                    "startDate": "2026-04-15T08:00:00Z",
                    "averageWatts": 200,
                }
            ]
        },
    )
    assert analyse_response.status_code == 200

    response = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "rides" in body
    assert len(body["rides"]) == 1
    ride = body["rides"][0]
    assert ride["activityDate"] == "2026-04-15"
    assert ride["ctlAfter"] is not None
    assert ride["atlAfter"] is not None
    assert ride["tsbAfter"] is not None


@pytest.mark.asyncio
async def test_ride_metrics_history_requires_auth(client):
    response = await client.get("/api/v1/users/me/ride-metrics-history")
    assert response.status_code == 401
