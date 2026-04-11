import pytest


PROFILE = {
    "name": "Test Rider",
    "email": "rider@example.com",
    "bikeType": "road",
    "trainingGoal": "ftp_improvement",
    "weeklyHours": 8,
    "followsTrainingPlan": True,
    "fitnessLevel": "intermediate",
}


@pytest.mark.asyncio
async def test_ai_endpoints(client, auth_headers, mock_ai_service):
    analyse_response = await client.post(
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
    assert analyse_response.status_code == 200
    assert analyse_response.json()["estimatedFTP"] == 280

    generate_response = await client.post(
        "/api/v1/ai/generate-plan",
        headers=auth_headers,
        json={"profile": PROFILE},
    )
    assert generate_response.status_code == 200
    assert generate_response.json()[0]["title"] == "Endurance Ride"

    adapt_response = await client.post(
        "/api/v1/ai/adapt-plan",
        headers=auth_headers,
        json={
            "plan": generate_response.json(),
            "recentFeedback": [
                {
                    "actualDurationMinutes": 60,
                    "perceivedEffort": 4,
                    "notes": "Hard",
                    "completedAt": "2026-04-10T10:00:00Z",
                }
            ],
            "profile": PROFILE,
        },
    )
    assert adapt_response.status_code == 200
    assert adapt_response.json()[0]["workoutType"] == "recovery"

    ask_response = await client.post(
        "/api/v1/ai/ask-trainer",
        headers=auth_headers,
        json={
            "question": "Should I swap tomorrow to a rest day?",
            "plan": generate_response.json(),
            "profile": PROFILE,
            "coachMemory": "Feels fatigued on Fridays.",
            "conversationHistory": [{"role": "user", "content": "I am tired."}],
        },
    )
    assert ask_response.status_code == 200
    assert ask_response.json()["response"] == "Take it easy tomorrow."
    assert ask_response.json()["planUpdates"][0]["workoutType"] == "rest"

    memory_response = await client.post(
        "/api/v1/ai/update-coach-memory",
        headers=auth_headers,
        json={
            "currentMemory": "",
            "userMessage": "I train best in the morning.",
            "coachResponse": "We will bias harder sessions earlier.",
        },
    )
    assert memory_response.status_code == 200
    assert memory_response.json()["memory"] == "Prefers morning workouts."

    rate_response = await client.post(
        "/api/v1/ai/rate-workout",
        headers=auth_headers,
        json={
            "day": {
                "date": "2026-04-10",
                "workoutType": "intervals",
                "title": "VO2 Max",
                "description": "Hard reps",
                "durationMinutes": 60,
                "feedback": {
                    "actualDurationMinutes": 58,
                    "perceivedEffort": 4,
                    "notes": "Tough but good",
                    "completedAt": "2026-04-10T10:00:00Z",
                },
            },
            "profile": PROFILE,
        },
    )
    assert rate_response.status_code == 200
    assert rate_response.json()["feedback"] == "Strong execution overall."
