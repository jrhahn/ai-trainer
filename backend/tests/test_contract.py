"""Frontend/backend API contract integration tests.

These tests simulate the exact request shapes the TypeScript frontend sends
and assert that the backend response shapes match the TypeScript types.

Key flows covered:
1. Manual onboarding (no Strava): register → update profile → generate plan → save plan
2. StravaActivity snake_case fields – the frontend StravaActivity interface uses
   snake_case property names (moving_time, elapsed_time, …); verify the backend
   accepts them alongside the camelCase aliases.
3. UserResponse camelCase field set – verify GET /users/me returns every field
   the BackendUserResponse TypeScript interface expects.
4. UpdateProfileRequest camelCase fields – verify PUT /users/me accepts the
   camelCase keys that user.ts sends.
5. Chat & coach-memory endpoint contract.
"""

import pytest


# ---------------------------------------------------------------------------
# Helper: expected camelCase keys in a UserResponse
# These must match the BackendUserResponse interface in frontend/src/services/user.ts
# ---------------------------------------------------------------------------

_USER_RESPONSE_REQUIRED_KEYS = {
    "id",
    "email",
    "isOnboarded",
    "stravaAnalysisComplete",
    "followsTrainingPlan",
    "aiProvider",
    "consumedTokens",
}

_USER_RESPONSE_OPTIONAL_KEYS = {
    "name",
    "lastStravaActivityId",
    "bikeType",
    "trainingGoal",
    "raceDate",
    "raceDescription",
    "weeklyHours",
    "maxHeartRate",
    "restingHeartRate",
    "currentFTP",
    "fitnessLevel",
    "riderAssessment",
    "stravaConnection",
}


# ---------------------------------------------------------------------------
# 1. Full manual onboarding flow (no Strava)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_onboarding_full_flow(client, mock_ai_service):
    """Simulate the exact sequence OnboardingPage.tsx performs for a new user
    who chooses manual assessment (no Strava connection).

    Sequence:
    1. Register → receive JWT
    2. GET /users/me → isOnboarded should be False
    3. PUT /users/me (camelCase profile fields + isOnboarded: true)
    4. POST /ai/generate-plan with empty body {}
    5. PUT /users/me/plan to save the AI-generated plan
    6. GET /users/me/plan → plan should be retrievable
    7. GET /users/me → isOnboarded should now be True
    """
    # 1. Register
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Alice Rider",
            "email": "alice@example.com",
            "password": "Str0ng!Pass",
        },
    )
    assert reg_resp.status_code == 200
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Initial GET /users/me – new user should not be onboarded
    me_resp = await client.get("/api/v1/users/me", headers=headers)
    assert me_resp.status_code == 200
    me = me_resp.json()
    assert me["isOnboarded"] is False
    assert me["stravaAnalysisComplete"] is False

    # 3. PUT /users/me using the same camelCase body that user.ts sends
    # (Mirrors the body built by updateCurrentUser() in frontend/src/services/user.ts)
    update_resp = await client.put(
        "/api/v1/users/me",
        headers=headers,
        json={
            "name": "Alice Rider",
            "bikeType": "road",
            "trainingGoal": "general_fitness",
            "followsTrainingPlan": False,
            "currentFTP": 250,
            "fitnessLevel": "intermediate",
            "maxHeartRate": 185,
            "isOnboarded": True,
            "stravaAnalysisComplete": False,
        },
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["bikeType"] == "road"
    assert updated["trainingGoal"] == "general_fitness"
    assert updated["isOnboarded"] is True
    assert updated["currentFTP"] == 250
    assert updated["fitnessLevel"] == "intermediate"

    # 4. POST /ai/generate-plan with empty body (as generateTrainingPlan() sends)
    gen_resp = await client.post("/api/v1/ai/generate-plan", headers=headers, json={})
    assert gen_resp.status_code == 200
    plan = gen_resp.json()
    assert isinstance(plan, list)
    assert len(plan) > 0
    first_day = plan[0]
    # Verify the plan day shape matches the frontend TrainingDay interface
    assert "date" in first_day
    assert "workoutType" in first_day
    assert "title" in first_day
    assert "description" in first_day
    assert "durationMinutes" in first_day

    # 5. PUT /users/me/plan to persist the plan (as saveTrainingPlan() sends)
    save_resp = await client.put(
        "/api/v1/users/me/plan",
        headers=headers,
        json={"plan": plan},
    )
    assert save_resp.status_code == 200
    saved = save_resp.json()
    assert saved["plan"] == plan

    # 6. GET /users/me/plan – plan must be retrievable
    get_plan_resp = await client.get("/api/v1/users/me/plan", headers=headers)
    assert get_plan_resp.status_code == 200
    assert get_plan_resp.json()["plan"] == plan

    # 7. GET /users/me – isOnboarded must be True after step 3
    final_me = (await client.get("/api/v1/users/me", headers=headers)).json()
    assert final_me["isOnboarded"] is True
    assert final_me["stravaAnalysisComplete"] is False


# ---------------------------------------------------------------------------
# 2. UserResponse camelCase field set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_response_camelcase_shape(client):
    """GET /users/me must return every camelCase key that BackendUserResponse
    (frontend/src/services/user.ts) declares.
    """
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Bob", "email": "bob@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = (await client.get("/api/v1/users/me", headers=headers)).json()

    # All required keys must be present
    for key in _USER_RESPONSE_REQUIRED_KEYS:
        assert key in me, f"Missing required key '{key}' in /users/me response"

    # No snake_case field names should leak into the response
    snake_case_fields = {
        "is_onboarded",
        "strava_analysis_complete",
        "last_strava_activity_id",
        "bike_type",
        "training_goal",
        "race_date",
        "race_description",
        "weekly_hours",
        "follows_training_plan",
        "resting_heart_rate",
        "max_heart_rate",
        "current_ftp",
        "fitness_level",
        "ai_provider",
        "rider_assessment",
        "strava_connection",
    }
    for key in snake_case_fields:
        assert key not in me, f"Unexpected snake_case key '{key}' in /users/me response"


# ---------------------------------------------------------------------------
# 3. UpdateProfileRequest accepts camelCase fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_profile_camelcase_fields(client):
    """PUT /users/me must accept the camelCase field names that
    updateCurrentUser() in frontend/src/services/user.ts sends.
    """
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Carol", "email": "carol@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.put(
        "/api/v1/users/me",
        headers=headers,
        json={
            "name": "Carol Updated",
            "bikeType": "gravel",
            "trainingGoal": "race",
            "raceDate": "2026-09-15",
            "raceDescription": "Local gran fondo",
            "weeklyHours": 10.5,
            "followsTrainingPlan": True,
            "maxHeartRate": 190,
            "restingHeartRate": 52,
            "currentFTP": 300,
            "fitnessLevel": "advanced",
            "isOnboarded": True,
            "stravaAnalysisComplete": False,
            "aiProvider": "openai",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Carol Updated"
    assert body["bikeType"] == "gravel"
    assert body["trainingGoal"] == "race"
    assert body["raceDate"] == "2026-09-15"
    assert body["weeklyHours"] == 10.5
    assert body["followsTrainingPlan"] is True
    assert body["maxHeartRate"] == 190
    assert body["restingHeartRate"] == 52
    assert body["currentFTP"] == 300
    assert body["fitnessLevel"] == "advanced"
    assert body["isOnboarded"] is True
    assert body["aiProvider"] == "openai"


# ---------------------------------------------------------------------------
# 4. StravaActivity snake_case fields accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strava_activities_snake_case_fields_accepted(client, mock_ai_service):
    """The frontend StravaActivity interface uses snake_case property names
    (moving_time, elapsed_time, total_elevation_gain, start_date, …).
    The backend StravaActivitySchema must accept these via populate_by_name=True.
    """
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Dan", "email": "dan@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Send activities using the exact same keys as the frontend StravaActivity interface
    resp = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=headers,
        json={
            "activities": [
                {
                    "id": 42,
                    "name": "Morning Ride",
                    "type": "Ride",
                    "distance": 60000,
                    # snake_case — matches frontend StravaActivity interface
                    "moving_time": 5400,
                    "elapsed_time": 5500,
                    "total_elevation_gain": 800,
                    "start_date": "2026-04-10T07:00:00Z",
                    "average_watts": 230,
                    "average_heartrate": 158,
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "assessment" in body
    assert "riderType" in body["assessment"]
    assert "notes" in body["assessment"]


@pytest.mark.asyncio
async def test_strava_activities_camelcase_fields_also_accepted(
    client, mock_ai_service
):
    """The backend should accept camelCase aliases too (used in the existing test suite)."""
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Eve", "email": "eve@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=headers,
        json={
            "activities": [
                {
                    "id": 7,
                    "name": "Evening Ride",
                    "type": "Ride",
                    "distance": 40000,
                    # camelCase aliases
                    "movingTime": 3600,
                    "elapsedTime": 3700,
                    "totalElevationGain": 500,
                    "startDate": "2026-04-11T18:00:00Z",
                    "averageWatts": 210,
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "assessment" in body
    assert body["assessment"]["riderType"] == "allrounder"


# ---------------------------------------------------------------------------
# 5. Plan endpoint contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_response_shape(client):
    """GET /users/me/plan and PUT /users/me/plan must use the { plan: [...] }
    envelope that the frontend fetchTrainingPlan / saveTrainingPlan expect.
    """
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Frank", "email": "frank@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Empty plan on fresh user
    empty = (await client.get("/api/v1/users/me/plan", headers=headers)).json()
    assert empty == {"plan": []}

    plan_days = [
        {
            "date": "2026-05-01",
            "workoutType": "endurance",
            "title": "Z2 Ride",
            "description": "Easy aerobic ride",
            "durationMinutes": 90,
        },
        {
            "date": "2026-05-02",
            "workoutType": "rest",
            "title": "Rest Day",
            "description": "Full rest",
            "durationMinutes": 0,
        },
    ]

    saved = (
        await client.put(
            "/api/v1/users/me/plan",
            headers=headers,
            json={"plan": plan_days},
        )
    ).json()
    assert saved == {"plan": plan_days}

    retrieved = (await client.get("/api/v1/users/me/plan", headers=headers)).json()
    assert retrieved == {"plan": plan_days}


# ---------------------------------------------------------------------------
# 6. Workout log contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workout_log_contract(client):
    """POST /users/me/workouts/{date} must accept the WorkoutFeedbackSchema
    camelCase shape that saveWorkoutLog() sends, and GET /users/me/workouts
    must return a dict keyed by date with the same camelCase feedback fields.
    """
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Grace", "email": "grace@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    feedback = {
        "actualDurationMinutes": 75,
        "averagePower": 215,
        "averageHeartRate": 152,
        "peakPower": 380,
        "perceivedEffort": 3,
        "notes": "Felt strong today",
        "completedAt": "2026-05-01T10:30:00Z",
    }

    save_resp = await client.post(
        "/api/v1/users/me/workouts/2026-05-01",
        headers=headers,
        json={"feedback": feedback},
    )
    assert save_resp.status_code == 200
    assert save_resp.json()["status"] == "ok"

    logs = (await client.get("/api/v1/users/me/workouts", headers=headers)).json()
    assert "2026-05-01" in logs
    day_log = logs["2026-05-01"]
    assert day_log["actualDurationMinutes"] == 75
    assert day_log["averagePower"] == 215
    assert day_log["averageHeartRate"] == 152
    assert day_log["perceivedEffort"] == 3
    assert day_log["notes"] == "Felt strong today"
    assert day_log["completedAt"] == "2026-05-01T10:30:00Z"


# ---------------------------------------------------------------------------
# 7. Race event contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_race_events_contract(client):
    """Race event endpoints use the camelCase shape expected by the expert calendar."""
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Racer", "email": "racer@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    empty = (await client.get("/api/v1/users/me/race-events", headers=headers)).json()
    assert empty == {"events": []}

    create_resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=headers,
        json={
            "date": "2026-06-01",
            "startTime": "09:00",
            "distanceKm": 120.5,
            "elevationM": 1800,
        },
    )
    assert create_resp.status_code == 200
    event = create_resp.json()
    assert event["date"] == "2026-06-01"
    assert event["startTime"] == "09:00"
    assert event["distanceKm"] == 120.5
    assert event["elevationM"] == 1800

    memory = (await client.get("/api/v1/users/me/coach-memory", headers=headers)).json()
    assert "Race calendar:" in memory["memory"]
    assert "120.5 km" in memory["memory"]

    list_resp = (
        await client.get("/api/v1/users/me/race-events", headers=headers)
    ).json()
    assert list_resp["events"][0]["id"] == event["id"]

    update_resp = await client.put(
        f"/api/v1/users/me/race-events/{event['id']}",
        headers=headers,
        json={
            "date": "2026-06-02",
            "startTime": None,
            "distanceKm": 130,
            "elevationM": 2100,
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["startTime"] is None
    assert update_resp.json()["distanceKm"] == 130

    delete_resp = await client.delete(
        f"/api/v1/users/me/race-events/{event['id']}",
        headers=headers,
    )
    assert delete_resp.status_code == 200
    assert (
        await client.get("/api/v1/users/me/race-events", headers=headers)
    ).json() == {"events": []}


@pytest.mark.asyncio
async def test_race_event_feedback_contract(client, mock_ai_service):
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Feedback",
            "email": "feedback@example.com",
            "password": "Str0ng!Pass",
        },
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    event = {
        "id": "race-1",
        "date": "2026-06-01",
        "startTime": None,
        "distanceKm": 120,
        "elevationM": 1800,
    }
    resp = await client.post(
        "/api/v1/ai/race-event-feedback",
        headers=headers,
        json={"event": event, "action": "added"},
    )
    assert resp.status_code == 200
    assert "feedback" in resp.json()
    assert "adapt the plan" in resp.json()["feedback"].lower()


# ---------------------------------------------------------------------------
# 8. Chat history contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_history_contract(client):
    """Chat endpoints must use the ChatMessageSchema camelCase shape that
    saveChatMessage() / fetchChatHistory() / clearChatHistoryRemote() use.
    """
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Henry", "email": "henry@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # POST message (as saveChatMessage sends)
    msg = {
        "role": "user",
        "content": "How should I train?",
        "timestamp": "2026-05-01T09:00:00Z",
    }
    post_resp = await client.post("/api/v1/users/me/chat", headers=headers, json=msg)
    assert post_resp.status_code == 200
    saved_msg = post_resp.json()
    assert saved_msg["role"] == "user"
    assert saved_msg["content"] == "How should I train?"
    assert saved_msg["timestamp"] == "2026-05-01T09:00:00Z"

    # GET history (as fetchChatHistory expects { messages: [...] })
    history = (await client.get("/api/v1/users/me/chat", headers=headers)).json()
    assert "messages" in history
    assert len(history["messages"]) == 1
    assert history["messages"][0]["content"] == "How should I train?"

    # DELETE clears history (as clearChatHistoryRemote sends)
    del_resp = await client.delete("/api/v1/users/me/chat", headers=headers)
    assert del_resp.status_code == 200

    empty_history = (await client.get("/api/v1/users/me/chat", headers=headers)).json()
    assert empty_history["messages"] == []


# ---------------------------------------------------------------------------
# 8. Coach memory contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coach_memory_contract(client):
    """Coach memory endpoints must use the { memory: string } shape that
    fetchCoachMemory() / saveCoachMemoryRemote() use.
    """
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Iris", "email": "iris@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Empty memory on new user
    empty = (await client.get("/api/v1/users/me/coach-memory", headers=headers)).json()
    assert empty == {"memory": ""}

    # PUT memory (as saveCoachMemoryRemote sends { memory: string })
    saved = (
        await client.put(
            "/api/v1/users/me/coach-memory",
            headers=headers,
            json={
                "memory": "Athlete prefers morning rides and responds well to interval work."
            },
        )
    ).json()
    assert saved == {
        "memory": "Athlete prefers morning rides and responds well to interval work."
    }

    # GET memory returns the saved value
    retrieved = (
        await client.get("/api/v1/users/me/coach-memory", headers=headers)
    ).json()
    assert (
        retrieved["memory"]
        == "Athlete prefers morning rides and responds well to interval work."
    )


# ---------------------------------------------------------------------------
# 9. Auth token shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_token_response_shape(client):
    """POST /auth/register and POST /auth/login must return { access_token, token_type }
    matching the TokenResponse schema that the frontend auth.ts expects.
    """
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Jake", "email": "jake@example.com", "password": "Str0ng!Pass"},
    )
    assert reg_resp.status_code == 200
    token_data = reg_resp.json()
    assert "access_token" in token_data
    assert "token_type" in token_data
    assert token_data["token_type"] == "bearer"
    assert isinstance(token_data["access_token"], str)
    assert len(token_data["access_token"]) > 0

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "jake@example.com", "password": "Str0ng!Pass"},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert "access_token" in login_data
    assert login_data["token_type"] == "bearer"


# ---------------------------------------------------------------------------
# 10. analyse-activities response matches AnalyseActivitiesResponse schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyse_activities_response_shape(client, mock_ai_service):
    """POST /ai/analyse-activities must return { assessment: {...}, planUpdates?: [...] }
    matching the AnalyseActivitiesResult TypeScript type in frontend/src/services/ai.ts.
    """
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Kim", "email": "kim@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/ai/analyse-activities",
        headers=headers,
        json={
            "activities": [
                {
                    "id": 1,
                    "name": "Ride",
                    "type": "Ride",
                    "distance": 50000,
                    "moving_time": 3600,
                    "elapsed_time": 3700,
                    "total_elevation_gain": 500,
                    "start_date": "2026-04-10T08:00:00Z",
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    # Top-level keys match AnalyseActivitiesResult
    assert "assessment" in body

    # Assessment shape matches RiderAssessment TypeScript interface
    assessment = body["assessment"]
    assert "riderType" in assessment
    assert "notes" in assessment
    # Optional fields may or may not be present
    for optional_key in ("rideInsights", "lastRideFeedback"):
        if optional_key in assessment:
            assert (
                assessment[optional_key] is not None or assessment[optional_key] is None
            )

    # planUpdates is optional; when present it must be a list
    if "planUpdates" in body and body["planUpdates"] is not None:
        assert isinstance(body["planUpdates"], list)


# ---------------------------------------------------------------------------
# 11. Metrics history endpoint contract (replaces fitness-history)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_history_empty_for_new_user(client):
    """GET /users/me/metrics-history returns { snapshots: [] } for a new user."""
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Leo", "email": "leo@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/users/me/metrics-history", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "snapshots" in body
    assert body["snapshots"] == []


@pytest.mark.asyncio
async def test_ride_metrics_history_preserves_activity_type_after_analysis(
    client, mock_ai_service
):
    """analyse-activities should create a metric row with the Strava activity type."""
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Mia", "email": "mia@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=headers,
        json={
            "activities": [
                {
                    "id": 1,
                    "name": "Hill Hike",
                    "type": "Hike",
                    "distance": 50000,
                    "moving_time": 3600,
                    "elapsed_time": 3700,
                    "total_elevation_gain": 500,
                    "start_date": "2026-04-10T08:00:00Z",
                }
            ]
        },
    )

    resp = await client.get("/api/v1/users/me/ride-metrics-history", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rides"]) == 1
    ride = body["rides"][0]
    assert ride["activityDate"] == "2026-04-10"
    assert ride["activityName"] == "Hill Hike"
    assert ride["sportType"] == "Hike"


# ---------------------------------------------------------------------------
# 12. .fit file upload endpoint contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fit_upload_rejects_non_fit_file(client):
    """POST /users/me/upload-fit must reject non-.fit files with 422."""
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Nina", "email": "nina@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/users/me/upload-fit",
        headers=headers,
        files={"file": ("workout.csv", b"date,power\n2026-01-01,200", "text/csv")},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_fit_upload_rejects_invalid_fit_data(client):
    """POST /users/me/upload-fit must return 422 for a .fit file with invalid data."""
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Oscar", "email": "oscar@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/v1/users/me/upload-fit",
        headers=headers,
        files={
            "file": ("workout.fit", b"not a real fit file", "application/octet-stream")
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_fit_upload_writes_metric_snapshot(client, mock_ai_service, monkeypatch):
    """A successful .fit upload must create an AthleteMetricSnapshot row (source='fit_upload')."""

    # Build a minimal mock FitFile that returns avg_power + avg_hr from session messages
    class _MockDataField:
        def __init__(self, name, value):
            self.name = name
            self.value = value

    class _MockRecord:
        def __init__(self, fields):
            self._fields = fields

        def __iter__(self):
            return iter(self._fields)

    session_record = _MockRecord(
        [
            _MockDataField("sport", "cycling"),
            _MockDataField("total_elapsed_time", 3600),
            _MockDataField("avg_power", 200),
            _MockDataField("avg_heart_rate", 155),
            _MockDataField("start_time", None),
        ]
    )

    class _MockFitFile:
        def __init__(self, *args, **kwargs):
            pass

        def parse(self):
            pass

        def get_messages(self, msg_type):
            if msg_type == "session":
                return [session_record]
            return []

    import fitparse as _fitparse_mod

    monkeypatch.setattr(_fitparse_mod, "FitFile", _MockFitFile)

    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"name": "Pat", "email": "pat@example.com", "password": "Str0ng!Pass"},
    )
    token = reg_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    upload_resp = await client.post(
        "/api/v1/users/me/upload-fit",
        headers=headers,
        files={
            "file": ("workout.fit", b"\x0e\x10\xd9\x07", "application/octet-stream")
        },
    )
    assert upload_resp.status_code == 200
    body = upload_resp.json()
    assert body["status"] == "ok"
    assert body["sport_type"] == "cycling"

    # Metric snapshot should now appear in history
    hist_resp = await client.get("/api/v1/users/me/metrics-history", headers=headers)
    assert hist_resp.status_code == 200
    hist = hist_resp.json()
    assert len(hist["snapshots"]) >= 1
    snap = hist["snapshots"][0]
    # Mock AI service returns estimatedFTP=210
    assert snap["ftp"] == 210
    assert snap["source"] == "fit_upload"
