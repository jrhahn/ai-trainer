"""HTTP tests for the admin router."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from config import settings


@pytest.fixture
def admin_enabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "super-secret")


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    resp = await client.post("/api/v1/admin/login", json={"password": "super-secret"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_admin_login_success(client, admin_enabled):
    resp = await client.post("/api/v1/admin/login", json={"password": "super-secret"})
    assert resp.status_code == 200
    assert resp.json()["access_token"]


@pytest.mark.asyncio
async def test_admin_login_wrong_password(client, admin_enabled):
    resp = await client.post("/api/v1/admin/login", json={"password": "nope"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_login_disabled_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "")
    resp = await client.post("/api/v1/admin/login", json={"password": "x"})
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_admin_users_requires_admin_token(client, admin_enabled):
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_users_lists_registered_users(client, admin_enabled, auth_headers):
    headers = await _admin_headers(client)
    resp = await client.get("/api/v1/admin/users", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["totalUsers"] >= 1
    emails = [u["email"] for u in data["users"]]
    assert "rider@example.com" in emails
    # aggregated stats default to zero for a brand-new user
    rider = next(u for u in data["users"] if u["email"] == "rider@example.com")
    assert rider["rideCount"] == 0
    assert rider["chatMessageCount"] == 0
    assert rider["stravaConnected"] is False


@pytest.mark.asyncio
async def test_admin_plan_history_requires_admin_token(client, admin_enabled):
    resp = await client.get("/api/v1/admin/users/some-id/plan-history")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_plan_history_unknown_user_404(client, admin_enabled):
    headers = await _admin_headers(client)
    resp = await client.get("/api/v1/admin/users/no-such-user/plan-history", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_plan_history_returns_recorded_changes(client, admin_enabled, auth_headers):
    # A manual plan save records per-day history (source "user_edit").
    plan = [
        {
            "date": "2026-05-01",
            "workoutType": "endurance",
            "title": "Z2 Ride",
            "description": "Easy aerobic ride",
            "durationMinutes": 90,
        }
    ]
    save = await client.put("/api/v1/users/me/plan", headers=auth_headers, json={"plan": plan})
    assert save.status_code == 200

    headers = await _admin_headers(client)
    listing = await client.get("/api/v1/admin/users", headers=headers)
    user_id = next(
        u["id"] for u in listing.json()["users"] if u["email"] == "rider@example.com"
    )

    resp = await client.get(f"/api/v1/admin/users/{user_id}/plan-history", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["userId"] == user_id
    assert data["total"] == 1
    entry = data["entries"][0]
    assert entry["date"] == "2026-05-01"
    assert entry["source"] == "user_edit"
    assert entry["applied"] is True
    assert entry["oldDay"] is None  # first save: no prior version
    assert entry["newDay"]["workoutType"] == "endurance"

    # Date filter narrows the result set.
    empty = await client.get(
        f"/api/v1/admin/users/{user_id}/plan-history?date=2020-01-01", headers=headers
    )
    assert empty.status_code == 200
    assert empty.json()["total"] == 0


@pytest.mark.asyncio
async def test_admin_delete_user(client, admin_enabled, auth_headers):
    headers = await _admin_headers(client)

    listing = await client.get("/api/v1/admin/users", headers=headers)
    user_id = next(
        u["id"] for u in listing.json()["users"] if u["email"] == "rider@example.com"
    )

    deleted = await client.delete(f"/api/v1/admin/users/{user_id}", headers=headers)
    assert deleted.status_code == 204

    # deleting again now 404s
    missing = await client.delete(f"/api/v1/admin/users/{user_id}", headers=headers)
    assert missing.status_code == 404
