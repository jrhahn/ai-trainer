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
