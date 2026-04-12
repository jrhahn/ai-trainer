import pytest

import auth


@pytest.mark.asyncio
async def test_register_and_login(client):
    register_response = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Test Rider",
            "email": "rider@example.com",
            "password": "hunter2xx",
        },
    )
    assert register_response.status_code == 200
    assert register_response.json()["token_type"] == "bearer"

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "rider@example.com", "password": "hunter2xx"},
    )
    assert login_response.status_code == 200
    assert "access_token" in login_response.json()


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(client):
    payload = {
        "name": "Test Rider",
        "email": "dup@example.com",
        "password": "hunter2xx",
    }
    await client.post("/api/v1/auth/register", json=payload)
    duplicate_response = await client.post("/api/v1/auth/register", json=payload)
    assert duplicate_response.status_code == 409


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client):
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Test Rider",
            "email": "wrongpass@example.com",
            "password": "hunter2xx",
        },
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "wrongpass@example.com", "password": "bad-password"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_authelia_session_mints_token_and_creates_user(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    response = await client.get(
        "/api/v1/auth/session",
        headers={
            "Remote-User": "authelia-user",
            "Remote-Name": "Authelia User",
            "Remote-Email": "authelia@example.com",
        },
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"

    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {response.json()['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "authelia@example.com"
    assert me.json()["name"] == "Authelia User"


@pytest.mark.asyncio
async def test_authelia_mode_disables_local_login_and_registration(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)

    register_response = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Test Rider",
            "email": "rider@example.com",
            "password": "hunter2xx",
        },
    )
    assert register_response.status_code == 501

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "rider@example.com", "password": "hunter2xx"},
    )
    assert login_response.status_code == 501
