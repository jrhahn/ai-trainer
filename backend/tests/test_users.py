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
            "trainingGoal": "general_fitness",
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
async def test_general_fitness_update_clears_profile_race_context(client, auth_headers):
    race_response = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={
            "trainingGoal": "race",
            "raceDate": "2026-09-15",
            "raceDescription": "Local gran fondo",
        },
    )
    assert race_response.status_code == 200
    assert race_response.json()["raceDate"] == "2026-09-15"

    general_response = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"trainingGoal": "general_fitness"},
    )
    assert general_response.status_code == 200
    assert general_response.json()["trainingGoal"] == "general_fitness"
    assert general_response.json()["raceDate"] is None
    assert general_response.json()["raceDescription"] is None


@pytest.mark.asyncio
async def test_update_me_rejects_retired_training_goals(client, auth_headers):
    response = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"trainingGoal": "ftp_improvement"},
    )
    assert response.status_code == 422


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
    """analyse-activities should create a per-ride metric visible in /ride-metrics-history."""
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
        "/api/v1/users/me/ride-metrics-history", headers=auth_headers
    )
    assert history_response.status_code == 200
    body = history_response.json()
    assert len(body["rides"]) == 1
    ride = body["rides"][0]
    assert ride["activityDate"] == "2026-04-18"
    assert ride["sportType"] == "cycling"


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
async def test_estimate_ftp_returns_profile_ftp(client, auth_headers, mock_ai_service):
    """estimate-ftp returns FTP from the user profile (current_ftp)."""
    # Set an FTP on the profile first.
    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"currentFTP": 280},
    )

    response = await client.post(
        "/api/v1/users/me/estimate-ftp",
        headers=auth_headers,
        json={"maxHeartRate": 185},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["estimatedFTP"] == 280  # from user profile
    assert body["source"] == "profile"


@pytest.mark.asyncio
async def test_estimate_ftp_requires_auth(client):
    response = await client.post(
        "/api/v1/users/me/estimate-ftp",
        json={"maxHeartRate": 185},
    )
    assert response.status_code == 401


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
async def test_recalculate_metrics_refreshes_last_ride_feedback(client, auth_headers, mock_ai_service):
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 4001,
                    "name": "Ride A",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3600,
                    "totalElevationGain": 200,
                    "startDate": "2026-03-01T09:00:00Z",
                    "averageWatts": 200,
                },
                {
                    "id": 4002,
                    "name": "Ride B",
                    "type": "Ride",
                    "distance": 50000,
                    "movingTime": 5400,
                    "elapsedTime": 5400,
                    "totalElevationGain": 400,
                    "startDate": "2026-03-10T09:00:00Z",
                    "averageWatts": 220,
                },
            ]
        },
    )

    response = await client.post(
        "/api/v1/users/me/recalculate-metrics",
        headers=auth_headers,
        json={"ftpOverride": 260},
    )
    assert response.status_code == 200

    me = await client.get("/api/v1/users/me", headers=auth_headers)
    assert me.status_code == 200
    last_ride_feedback = me.json()["riderAssessment"]["lastRideFeedback"]
    assert "Recalculated with FTP 260 W" in last_ride_feedback
    assert "Post-ride load is" in last_ride_feedback


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


# ---------------------------------------------------------------------------
# PATCH /users/me/ride-feedback/{strava_activity_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_ride_feedback_success(client, auth_headers, mock_ai_service):
    """PATCH ride-feedback saves structured feedback as a formatted user_note."""
    # First create a ride metric via analyse-activities.
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 5001,
                    "name": "Morning Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 300,
                    "startDate": "2026-04-20T08:00:00Z",
                    "averageWatts": 200,
                }
            ]
        },
    )

    response = await client.patch(
        "/api/v1/users/me/ride-feedback/5001",
        headers=auth_headers,
        json={
            "rpe": 7,
            "legs": "heavy",
            "intent": "planned workout",
            "note": "Felt tired but pushed through",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["stravaActivityId"] == 5001
    assert "RPE 7/10" in body["userNote"]
    assert "legs: heavy" in body["userNote"]
    assert "intent: planned workout" in body["userNote"]
    assert "Felt tired but pushed through" in body["userNote"]


@pytest.mark.asyncio
async def test_save_ride_feedback_without_optional_note(client, auth_headers, mock_ai_service):
    """PATCH ride-feedback works without the optional note field."""
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 5002,
                    "name": "Easy Spin",
                    "type": "Ride",
                    "distance": 30000,
                    "movingTime": 2700,
                    "elapsedTime": 2800,
                    "totalElevationGain": 100,
                    "startDate": "2026-04-21T07:00:00Z",
                    "averageWatts": 150,
                }
            ]
        },
    )

    response = await client.patch(
        "/api/v1/users/me/ride-feedback/5002",
        headers=auth_headers,
        json={"rpe": 4, "legs": "fresh", "intent": "recovery"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "RPE 4/10" in body["userNote"]
    assert "legs: fresh" in body["userNote"]
    assert "intent: recovery" in body["userNote"]


@pytest.mark.asyncio
async def test_save_ride_feedback_not_found(client, auth_headers):
    """PATCH ride-feedback returns 404 when the Strava activity does not exist."""
    response = await client.patch(
        "/api/v1/users/me/ride-feedback/999999",
        headers=auth_headers,
        json={"rpe": 5, "legs": "normal", "intent": "free ride"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_save_ride_feedback_invalid_rpe(client, auth_headers):
    """PATCH ride-feedback rejects RPE values outside 1–10."""
    response = await client.patch(
        "/api/v1/users/me/ride-feedback/12345",
        headers=auth_headers,
        json={"rpe": 11, "legs": "normal", "intent": "recovery"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_save_ride_feedback_requires_auth(client):
    """PATCH ride-feedback requires authentication."""
    response = await client.patch(
        "/api/v1/users/me/ride-feedback/5001",
        json={"rpe": 6, "legs": "normal", "intent": "planned workout"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_save_ride_feedback_persists_in_history(client, auth_headers, mock_ai_service):
    """After saving feedback, ride-metrics-history reflects the user_note."""
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 5003,
                    "name": "Tempo Ride",
                    "type": "Ride",
                    "distance": 50000,
                    "movingTime": 5400,
                    "elapsedTime": 5500,
                    "totalElevationGain": 400,
                    "startDate": "2026-04-22T08:00:00Z",
                    "averageWatts": 240,
                }
            ]
        },
    )

    await client.patch(
        "/api/v1/users/me/ride-feedback/5003",
        headers=auth_headers,
        json={"rpe": 8, "legs": "normal", "intent": "planned workout", "note": "Great session"},
    )

    history = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    rides = history.json()["rides"]
    # Find the specific ride we submitted feedback for
    target = next((r for r in rides if r["stravaActivityId"] == 5003), None)
    assert target is not None
    assert "RPE 8/10" in target["userNote"]
