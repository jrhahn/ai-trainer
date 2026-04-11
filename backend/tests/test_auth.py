import pytest


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
