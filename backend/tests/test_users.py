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
async def test_save_ride_feedback_sets_legs(client, auth_headers, mock_ai_service):
    """PATCH ride-feedback stores the quick leg-freshness rating."""
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
        json={"legs": "heavy"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["stravaActivityId"] == 5001
    assert body["ride"]["feelLegs"] == "heavy"
    # The quick tap must never fabricate a free-text note.
    assert body["ride"]["userNote"] is None


@pytest.mark.asyncio
async def test_save_ride_feedback_clears_legs(client, auth_headers, mock_ai_service):
    """Sending legs=null clears a previously set rating."""
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

    set_resp = await client.patch(
        "/api/v1/users/me/ride-feedback/5002",
        headers=auth_headers,
        json={"legs": "fresh"},
    )
    assert set_resp.json()["ride"]["feelLegs"] == "fresh"

    clear_resp = await client.patch(
        "/api/v1/users/me/ride-feedback/5002",
        headers=auth_headers,
        json={"legs": None},
    )
    assert clear_resp.status_code == 200
    assert clear_resp.json()["ride"]["feelLegs"] is None


@pytest.mark.asyncio
async def test_save_ride_feedback_does_not_clobber_user_note(
    client, auth_headers, mock_ai_service
):
    """A quick legs tap must not overwrite a note captured conversationally."""
    import crud
    from tests.conftest import TestSessionLocal

    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 5005,
                    "name": "Coffee Ride",
                    "type": "Ride",
                    "distance": 20000,
                    "movingTime": 1800,
                    "elapsedTime": 1900,
                    "totalElevationGain": 50,
                    "startDate": "2026-04-23T07:00:00Z",
                    "averageWatts": 120,
                }
            ]
        },
    )

    me = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = me.json()["id"]
    async with TestSessionLocal() as db:
        await crud.update_ride_metric_notes(
            db, user_id, 5005, user_note="Felt great, chatted with coach"
        )
        await db.commit()

    response = await client.patch(
        "/api/v1/users/me/ride-feedback/5005",
        headers=auth_headers,
        json={"legs": "fresh"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ride"]["feelLegs"] == "fresh"
    assert body["ride"]["userNote"] == "Felt great, chatted with coach"


@pytest.mark.asyncio
async def test_save_ride_feedback_not_found(client, auth_headers):
    """PATCH ride-feedback returns 404 when the Strava activity does not exist."""
    response = await client.patch(
        "/api/v1/users/me/ride-feedback/999999",
        headers=auth_headers,
        json={"legs": "normal"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_save_ride_feedback_invalid_legs(client, auth_headers):
    """PATCH ride-feedback rejects leg values outside the allowed set."""
    response = await client.patch(
        "/api/v1/users/me/ride-feedback/12345",
        headers=auth_headers,
        json={"legs": "exhausted"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_save_ride_feedback_requires_auth(client):
    """PATCH ride-feedback requires authentication."""
    response = await client.patch(
        "/api/v1/users/me/ride-feedback/5001",
        json={"legs": "normal"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_save_ride_feedback_persists_in_history(
    client, auth_headers, mock_ai_service
):
    """After saving legs feedback, ride-metrics-history reflects it."""
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
        json={"legs": "normal"},
    )

    history = await client.get(
        "/api/v1/users/me/ride-metrics-history", headers=auth_headers
    )
    rides = history.json()["rides"]
    target = next((r for r in rides if r["stravaActivityId"] == 5003), None)
    assert target is not None
    assert target["feelLegs"] == "normal"


# ---------------------------------------------------------------------------
# Athlete-facing plan-day history / analytics (#357)
# ---------------------------------------------------------------------------


async def _seed_plan_history(user_id: str, changes: list[dict], source: str) -> None:
    import crud
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as db:
        await crud.record_plan_day_changes(db, user_id, changes, source)
        await db.commit()


async def _current_user_id(client, headers: dict[str, str]) -> str:
    resp = await client.get("/api/v1/users/me", headers=headers)
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_plan_history_requires_auth(client):
    resp = await client.get("/api/v1/users/me/plan-history")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_plan_history_returns_only_current_user_rows(client, auth_headers):
    me_id = await _current_user_id(client, auth_headers)

    # A second registered user with their own history must never leak through.
    other = await client.post(
        "/api/v1/auth/register",
        json={"name": "Other", "email": "other@example.com", "password": "Str0ng!Pass"},
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    other_id = await _current_user_id(client, other_headers)

    await _seed_plan_history(
        me_id,
        [{"date": "2026-05-01", "old_day": None, "new_day": {"title": "Mine"}}],
        "coach_chat",
    )
    await _seed_plan_history(
        other_id,
        [{"date": "2026-05-01", "old_day": None, "new_day": {"title": "Theirs"}}],
        "coach_chat",
    )

    resp = await client.get("/api/v1/users/me/plan-history", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["entries"][0]["newDay"]["title"] == "Mine"
    assert data["entries"][0]["source"] == "coach_chat"


@pytest.mark.asyncio
async def test_plan_history_date_filter_and_blocked_flag(client, auth_headers):
    me_id = await _current_user_id(client, auth_headers)
    await _seed_plan_history(
        me_id,
        [{"date": "2026-05-01", "old_day": None, "new_day": {"title": "A"}}],
        "user_edit",
    )
    # A blocked automated attempt (a user pin kept the day) — applied=False.
    await _seed_plan_history(
        me_id,
        [
            {
                "date": "2026-05-02",
                "old_day": {"title": "A"},
                "new_day": {"title": "B"},
                "applied": False,
            }
        ],
        "ride_review",
    )

    all_rows = await client.get("/api/v1/users/me/plan-history", headers=auth_headers)
    assert all_rows.json()["total"] == 2

    blocked = next(
        e for e in all_rows.json()["entries"] if e["source"] == "ride_review"
    )
    assert blocked["applied"] is False

    filtered = await client.get(
        "/api/v1/users/me/plan-history?date=2026-05-02", headers=auth_headers
    )
    assert filtered.json()["total"] == 1
    assert filtered.json()["entries"][0]["date"] == "2026-05-02"


@pytest.mark.asyncio
async def test_plan_history_stats_aggregates(client, auth_headers):
    me_id = await _current_user_id(client, auth_headers)
    await _seed_plan_history(
        me_id,
        [
            {"date": "2026-05-01", "old_day": None, "new_day": {"t": 1}},
            {"date": "2026-05-01", "old_day": {"t": 1}, "new_day": {"t": 2}},
        ],
        "coach_chat",
    )
    await _seed_plan_history(
        me_id,
        [
            {
                "date": "2026-05-02",
                "old_day": {"t": 2},
                "new_day": {"t": 3},
                "applied": False,
            }
        ],
        "auto_adapt",
    )

    resp = await client.get("/api/v1/users/me/plan-history/stats", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["bySource"] == {"coach_chat": 2, "auto_adapt": 1}
    assert data["appliedCount"] == 2
    assert data["blockedCount"] == 1
    # 2026-05-01 has the most changes, so it leads the ranking.
    assert data["mostChangedDates"][0] == {"date": "2026-05-01", "count": 2}


@pytest.mark.asyncio
async def test_save_plan_strips_client_set_server_owned_fields(client, auth_headers):
    """A manual edit must not let the client forge server-owned day fields.

    ``completed`` and ``feedback`` (and ``source`` pinning) are set by the
    server; a client marking a future day completed would corrupt the plan.
    """
    plan = [
        {
            "date": "2026-12-01",
            "workoutType": "endurance",
            "title": "Z2 Ride",
            "description": "Easy aerobic ride",
            "durationMinutes": 90,
            "completed": True,  # forged
            "feedback": {  # forged (valid shape so it isn't rejected, just stripped)
                "actualDurationMinutes": 60,
                "perceivedEffort": 5,
                "completedAt": "2026-12-01T10:00:00Z",
            },
        }
    ]
    save = await client.put(
        "/api/v1/users/me/plan", headers=auth_headers, json={"plan": plan}
    )
    assert save.status_code == 200

    resp = await client.get("/api/v1/users/me/plan", headers=auth_headers)
    day = next(d for d in resp.json()["plan"] if d["date"] == "2026-12-01")
    assert not day.get("completed")  # forged completion stripped
    assert "feedback" not in day  # forged feedback stripped
    assert day["durationMinutes"] == 90  # legitimate content kept


@pytest.mark.asyncio
async def test_plan_write_read_roundtrip_is_canonical_and_lossless(client, auth_headers):
    """Full write→read path yields a coherent day and preserves unknown keys.

    Exercises the PlanDay persist gate end-to-end: a duration window is
    reconciled to a coherent midpoint scalar, and an unmodelled field survives
    (extra="allow") so typing never silently drops stored data.
    """
    plan = [
        {
            "date": "2026-12-02",
            "workoutType": "endurance",
            "title": "Long",
            "description": "3-4h endurance",
            "durationMinMinutes": 180,
            "durationMaxMinutes": 240,
            "someFutureField": "keep-me",
        }
    ]
    save = await client.put(
        "/api/v1/users/me/plan", headers=auth_headers, json={"plan": plan}
    )
    assert save.status_code == 200

    resp = await client.get("/api/v1/users/me/plan", headers=auth_headers)
    day = next(d for d in resp.json()["plan"] if d["date"] == "2026-12-02")
    assert day["durationMinMinutes"] == 180
    assert day["durationMaxMinutes"] == 240
    assert day["durationMinutes"] == round((180 + 240) / 2)  # 210 — coherent
    assert day["someFutureField"] == "keep-me"  # unknown key preserved end-to-end
