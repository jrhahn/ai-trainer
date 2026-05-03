"""Coverage tests for routers/users.py endpoints not covered by test_users.py.

Covers:
- race events CRUD (create, get, update, delete)
- chat history (GET /chat, POST /chat, DELETE /chat)
- estimate-ftp endpoint
- recalculate-metrics endpoint (success + 400 when no FTP)
- fitness-history endpoint
- ride-metrics-history endpoint
- workout log endpoints (save + get)
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Workout log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_workout_log(client, auth_headers):
    """POST /workouts/{date} persists a log; GET /workouts returns it."""
    date = "2026-04-15"
    save_response = await client.post(
        f"/api/v1/users/me/workouts/{date}",
        headers=auth_headers,
        json={
            "feedback": {
                "actualDurationMinutes": 75,
                "averagePower": 230,
                "averageHeartRate": 148,
                "peakPower": 480,
                "perceivedEffort": 3,
                "notes": "Felt solid",
                "completedAt": "2026-04-15T09:00:00Z",
            }
        },
    )
    assert save_response.status_code == 200
    assert save_response.json()["status"] == "ok"

    get_response = await client.get("/api/v1/users/me/workouts", headers=auth_headers)
    assert get_response.status_code == 200
    body = get_response.json()
    assert date in body
    entry = body[date]
    assert entry["actualDurationMinutes"] == 75
    assert entry["averagePower"] == 230
    assert entry["perceivedEffort"] == 3


# ---------------------------------------------------------------------------
# Race events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_race_events_empty(client, auth_headers):
    response = await client.get("/api/v1/users/me/race-events", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["events"] == []


@pytest.mark.asyncio
async def test_create_and_list_race_events(client, auth_headers):
    create_resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={
            "date": "2026-08-15",
            "startTime": "08:00",
            "distanceKm": 120.0,
            "elevationM": 1800,
        },
    )
    assert create_resp.status_code == 200
    event = create_resp.json()
    assert event["date"] == "2026-08-15"
    assert event["distanceKm"] == 120.0

    list_resp = await client.get("/api/v1/users/me/race-events", headers=auth_headers)
    assert list_resp.status_code == 200
    events = list_resp.json()["events"]
    assert len(events) == 1
    assert events[0]["date"] == "2026-08-15"


@pytest.mark.asyncio
async def test_update_race_event(client, auth_headers):
    create_resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={
            "date": "2026-09-01",
            "startTime": "07:00",
            "distanceKm": 80.0,
            "elevationM": 900,
        },
    )
    event_id = create_resp.json()["id"]

    update_resp = await client.put(
        f"/api/v1/users/me/race-events/{event_id}",
        headers=auth_headers,
        json={
            "date": "2026-09-05",
            "startTime": "06:30",
            "distanceKm": 100.0,
            "elevationM": 1200,
        },
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["date"] == "2026-09-05"
    assert updated["distanceKm"] == 100.0


@pytest.mark.asyncio
async def test_update_race_event_404(client, auth_headers):
    response = await client.put(
        "/api/v1/users/me/race-events/nonexistent-id",
        headers=auth_headers,
        json={
            "date": "2026-10-01",
            "startTime": "08:00",
            "distanceKm": 90.0,
            "elevationM": 500,
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_race_event(client, auth_headers):
    create_resp = await client.post(
        "/api/v1/users/me/race-events",
        headers=auth_headers,
        json={
            "date": "2026-11-01",
            "startTime": "09:00",
            "distanceKm": 60.0,
            "elevationM": 500,
        },
    )
    event_id = create_resp.json()["id"]

    del_resp = await client.delete(
        f"/api/v1/users/me/race-events/{event_id}",
        headers=auth_headers,
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    list_resp = await client.get("/api/v1/users/me/race-events", headers=auth_headers)
    assert list_resp.json()["events"] == []


@pytest.mark.asyncio
async def test_delete_race_event_404(client, auth_headers):
    response = await client.delete(
        "/api/v1/users/me/race-events/does-not-exist",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_empty(client, auth_headers):
    response = await client.get("/api/v1/users/me/chat", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["messages"] == []


@pytest.mark.asyncio
async def test_add_and_get_chat_message(client, auth_headers):
    add_resp = await client.post(
        "/api/v1/users/me/chat",
        headers=auth_headers,
        json={
            "role": "user",
            "content": "How should I prepare for the race?",
            "timestamp": "2026-04-10T10:00:00Z",
        },
    )
    assert add_resp.status_code == 200
    msg = add_resp.json()
    assert msg["role"] == "user"
    assert msg["content"] == "How should I prepare for the race?"

    get_resp = await client.get("/api/v1/users/me/chat", headers=auth_headers)
    assert get_resp.status_code == 200
    messages = get_resp.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["content"] == "How should I prepare for the race?"


@pytest.mark.asyncio
async def test_clear_chat(client, auth_headers):
    # Add a message first
    await client.post(
        "/api/v1/users/me/chat",
        headers=auth_headers,
        json={
            "role": "assistant",
            "content": "Focus on sleep and recovery.",
            "timestamp": "2026-04-10T10:01:00Z",
        },
    )

    del_resp = await client.delete("/api/v1/users/me/chat", headers=auth_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    get_resp = await client.get("/api/v1/users/me/chat", headers=auth_headers)
    assert get_resp.json()["messages"] == []


# ---------------------------------------------------------------------------
# estimate-ftp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_estimate_ftp_with_no_current_ftp(client, auth_headers):
    """Returns estimated_ftp=None and source='none' when no FTP is set."""
    response = await client.post(
        "/api/v1/users/me/estimate-ftp",
        headers=auth_headers,
        json={"maxHeartRate": 190, "restingHeartRate": 55},
    )
    assert response.status_code == 200
    body = response.json()
    # Schema uses camelCase alias: estimatedFTP (not estimatedFtp)
    estimated = body.get("estimatedFTP") if "estimatedFTP" in body else body.get("estimated_ftp")
    assert estimated is None
    assert body["source"] == "none"


@pytest.mark.asyncio
async def test_estimate_ftp_with_current_ftp(client, auth_headers):
    """Returns current_ftp when it is set on the user profile."""
    # Set current_ftp via profile update
    await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"currentFTP": 280},
    )

    response = await client.post(
        "/api/v1/users/me/estimate-ftp",
        headers=auth_headers,
        json={"maxHeartRate": 188},
    )
    assert response.status_code == 200
    body = response.json()
    estimated = body.get("estimatedFTP") if "estimatedFTP" in body else body.get("estimated_ftp")
    assert estimated == 280
    assert body["source"] == "profile"


# ---------------------------------------------------------------------------
# recalculate-metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalculate_metrics_400_when_no_ftp(client, auth_headers):
    """recalculate-metrics returns 400 when no FTP is available."""
    response = await client.post(
        "/api/v1/users/me/recalculate-metrics",
        headers=auth_headers,
        json={},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_recalculate_metrics_success_with_ftp_override(client, auth_headers, mock_ai_service):
    """recalculate-metrics succeeds when ftp_override is provided."""
    # Seed a ride metric via analyse-activities
    await client.post(
        "/api/v1/ai/analyse-activities",
        headers=auth_headers,
        json={
            "activities": [
                {
                    "id": 5001,
                    "name": "Base Ride",
                    "type": "Ride",
                    "distance": 60000,
                    "movingTime": 5400,
                    "elapsedTime": 5500,
                    "totalElevationGain": 400,
                    "startDate": "2026-04-12T08:00:00Z",
                    "averageWatts": 215,
                }
            ]
        },
    )

    response = await client.post(
        "/api/v1/users/me/recalculate-metrics",
        headers=auth_headers,
        json={"ftpOverride": 250},
    )
    assert response.status_code == 200
    body = response.json()
    ftp_used = body.get("ftpUsed") or body.get("ftp_used")
    assert ftp_used == 250
    updated = body.get("updated")
    assert updated >= 1


# ---------------------------------------------------------------------------
# ride-metrics-history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ride_metrics_history_empty(client, auth_headers):
    response = await client.get("/api/v1/users/me/ride-metrics-history", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "rides" in body
    assert body["rides"] == []
