import pytest
from unittest.mock import AsyncMock


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
    assert update_response.json()["stravaAutoSyncEnabled"] is True
    assert update_response.json()["intervalsAutoSyncEnabled"] is True

    delete_response = await client.delete("/api/v1/users/me", headers=auth_headers)
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_update_me_persists_strava_auto_sync_preference(client, auth_headers):
    default_response = await client.get("/api/v1/users/me", headers=auth_headers)
    assert default_response.status_code == 200
    assert default_response.json()["stravaAutoSyncEnabled"] is True
    assert default_response.json()["intervalsAutoSyncEnabled"] is True

    disabled_response = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"stravaAutoSyncEnabled": False, "intervalsAutoSyncEnabled": False},
    )
    assert disabled_response.status_code == 200
    assert disabled_response.json()["stravaAutoSyncEnabled"] is False
    assert disabled_response.json()["intervalsAutoSyncEnabled"] is False

    enabled_response = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"stravaAutoSyncEnabled": True, "intervalsAutoSyncEnabled": True},
    )
    assert enabled_response.status_code == 200
    assert enabled_response.json()["stravaAutoSyncEnabled"] is True
    assert enabled_response.json()["intervalsAutoSyncEnabled"] is True


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
    response = await client.get(
        "/api/v1/users/me/metrics-history", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert "snapshots" in body
    assert body["snapshots"] == []


@pytest.mark.asyncio
async def test_metrics_history_after_analyse_activities(
    client, auth_headers, mock_ai_service
):
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
async def test_recalculate_metrics_creates_per_ride_snapshots(
    client, auth_headers, mock_ai_service
):
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
    assert (
        len(snapshots) == 2
    ), f"Expected 2 per-ride snapshots after recalculate, got {len(snapshots)}"
    # All snapshots should use the override FTP.
    for snap in snapshots:
        assert snap["ftp"] == 260
        assert snap["source"] == "manual_recalculate"
        # CTL value should be present (may be 0.0 when no power data)
        assert snap["ctl"] is not None


@pytest.mark.asyncio
async def test_recalculate_metrics_refreshes_last_ride_feedback(
    client, auth_headers, mock_ai_service
):
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
    response = await client.get(
        "/api/v1/users/me/ride-metrics-history", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert "rides" in body
    assert body["rides"] == []


@pytest.mark.asyncio
async def test_ride_metrics_history_after_analyse_activities(
    client, auth_headers, mock_ai_service
):
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

    response = await client.get(
        "/api/v1/users/me/ride-metrics-history", headers=auth_headers
    )
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
async def test_analyse_activities_persists_weather_on_ride_metrics(
    client, auth_headers, mock_ai_service, monkeypatch
):
    import routers.ai as ai_router

    monkeypatch.setattr(
        ai_router,
        "enrich_activity_weather",
        AsyncMock(
            return_value={
                "start_lat": 52.52,
                "start_lng": 13.405,
                "weather_temperature_c": 31.2,
                "weather_condition": "clear",
                "weather_code": 0,
                "weather_source": "open_meteo",
            }
        ),
    )

    response = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 2002,
                    "name": "Hot Ride",
                    "type": "Ride",
                    "distance": 40000,
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 300,
                    "startDate": "2026-04-15T08:00:00Z",
                    "startLatlng": [52.52, 13.405],
                }
            ]
        },
    )
    assert response.status_code == 200

    history = await client.get(
        "/api/v1/users/me/ride-metrics-history", headers=auth_headers
    )
    ride = history.json()["rides"][0]
    assert ride["weatherTemperatureC"] == 31.2
    assert ride["weatherCondition"] == "clear"
    assert ride["startLat"] == 52.52

    analyse_call = mock_ai_service["analyse_strava_activities"].await_args
    assert analyse_call.args[0][0]["weather_temperature_c"] == 31.2


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
            "planMatchFeedback": "matched",
            "note": "Felt tired but pushed through",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["stravaActivityId"] == 5001
    assert "RPE 7/10" in body["userNote"]
    assert "legs: heavy" in body["userNote"]
    assert "intent: planned workout" in body["userNote"]
    assert "plan match: matched plan" in body["userNote"]
    assert "Felt tired but pushed through" in body["userNote"]
    assert body["ride"]["labelOverride"] == "Solid"


@pytest.mark.asyncio
async def test_save_ride_feedback_without_optional_note(
    client, auth_headers, mock_ai_service
):
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
async def test_save_ride_feedback_persists_in_history(
    client, auth_headers, mock_ai_service
):
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
        json={
            "rpe": 8,
            "legs": "normal",
            "intent": "planned workout",
            "note": "Great session",
        },
    )

    history = await client.get(
        "/api/v1/users/me/ride-metrics-history", headers=auth_headers
    )
    rides = history.json()["rides"]
    # Find the specific ride we submitted feedback for
    target = next((r for r in rides if r["stravaActivityId"] == 5003), None)
    assert target is not None
    assert "RPE 8/10" in target["userNote"]


@pytest.mark.asyncio
async def test_save_ride_feedback_persists_plan_match_override(
    client, auth_headers, mock_ai_service
):
    """Plan-match feedback updates the persistent ride badge override."""
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 5004,
                    "name": "Long VO2 Ride",
                    "type": "Ride",
                    "distance": 65000,
                    "movingTime": 6900,
                    "elapsedTime": 7000,
                    "totalElevationGain": 600,
                    "startDate": "2026-06-18T08:00:00Z",
                    "averageWatts": 230,
                    "weightedAverageWatts": 268,
                }
            ]
        },
    )

    response = await client.patch(
        "/api/v1/users/me/ride-feedback/5004",
        headers=auth_headers,
        json={
            "rpe": 8,
            "legs": "normal",
            "intent": "planned workout",
            "planMatchFeedback": "mostly_matched",
            "note": "The VO2 intervals were good, the ride just ran long.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "plan match: partly matched plan" in body["userNote"]
    assert body["ride"]["labelOverride"] == "Close"

    history = await client.get(
        "/api/v1/users/me/ride-metrics-history", headers=auth_headers
    )
    target = next(
        (r for r in history.json()["rides"] if r["stravaActivityId"] == 5004),
        None,
    )
    assert target is not None
    assert target["labelOverride"] == "Close"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"currentFTP": 1500},  # typo: above any human threshold power
        {"currentFTP": 5},
        {"maxHeartRate": 40},
        {"maxHeartRate": 400},
        {"restingHeartRate": 5},
        # Relative check: resting HR cannot sit at or above max HR.
        {"restingHeartRate": 180, "maxHeartRate": 175},
    ],
)
async def test_update_me_rejects_implausible_metrics(client, auth_headers, payload):
    response = await client.put(
        "/api/v1/users/me", headers=auth_headers, json=payload
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_me_accepts_plausible_metrics(client, auth_headers):
    response = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"currentFTP": 280, "maxHeartRate": 190, "restingHeartRate": 48},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["currentFTP"] == 280
    # No MAP recorded yet, so there is nothing to check FTP against.
    assert body["ftpPlausibilityWarning"] is None


@pytest.mark.asyncio
async def test_ftp_plausibility_warning_uses_recorded_map(client, auth_headers):
    import crud
    from tests.conftest import TestSessionLocal

    await client.put(
        "/api/v1/users/me", headers=auth_headers, json={"currentFTP": 300}
    )

    me = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = me.json()["id"]

    # Best 5-min power of 310 W puts FTP at 97 % of MAP — impossible.
    async with TestSessionLocal() as db:
        await crud.create_athlete_metric_snapshot(
            db, user_id, ftp=300, map_5min=310, source="ftp_estimation"
        )
        await db.commit()

    warned = await client.get("/api/v1/users/me", headers=auth_headers)
    assert warned.status_code == 200
    warning = warned.json()["ftpPlausibilityWarning"]
    assert warning is not None
    assert "too high" in warning

    # A MAP that puts FTP at 78 % clears the check.
    async with TestSessionLocal() as db:
        await crud.create_athlete_metric_snapshot(
            db, user_id, ftp=300, map_5min=385, source="ftp_estimation"
        )
        await db.commit()

    cleared = await client.get("/api/v1/users/me", headers=auth_headers)
    assert cleared.json()["ftpPlausibilityWarning"] is None
