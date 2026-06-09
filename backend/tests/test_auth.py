import pytest

import auth


@pytest.mark.asyncio
async def test_register_and_login(client):
    register_response = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Test Rider",
            "email": "rider@example.com",
            "password": "Str0ng!Pass",
        },
    )
    assert register_response.status_code == 200
    assert register_response.json()["token_type"] == "bearer"

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "rider@example.com", "password": "Str0ng!Pass"},
    )
    assert login_response.status_code == 200
    assert "access_token" in login_response.json()


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(client):
    payload = {
        "name": "Test Rider",
        "email": "dup@example.com",
        "password": "Str0ng!Pass",
    }
    await client.post("/api/v1/auth/register", json=payload)
    duplicate_response = await client.post("/api/v1/auth/register", json=payload)
    assert duplicate_response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("password", "expected"),
    [
        ("short1!", "at least 8 characters"),
        ("NOLOWERCASE1!", "lowercase"),
        ("nouppercase1!", "uppercase"),
        ("NoNumber!", "number"),
        ("NoSpecial1", "special"),
        ("Password1!", "common"),
        ("Rider2026!", "name or email"),
    ],
)
async def test_register_rejects_weak_passwords(client, password, expected):
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Test Rider",
            "email": "rider@example.com",
            "password": password,
        },
    )
    assert response.status_code == 422
    assert expected in response.text


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client):
    await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Test Rider",
            "email": "wrongpass@example.com",
            "password": "Str0ng!Pass",
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
async def test_authelia_mode_returns_503_when_not_configured(client, monkeypatch):
    """When Authelia is enabled but the internal URL / users-DB path are not set,
    the endpoints return 503 rather than silently falling back to local auth."""
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_USERS_DB_PATH", "")
    monkeypatch.setattr(auth, "AUTHELIA_INTERNAL_URL", "")

    register_response = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Test Rider",
            "email": "rider@example.com",
            "password": "Str0ng!Pass",
        },
    )
    assert register_response.status_code == 503

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "rider@example.com", "password": "Str0ng!Pass"},
    )
    assert login_response.status_code == 503


# ---------------------------------------------------------------------------
# validate_jwt_secret
# ---------------------------------------------------------------------------


def test_validate_jwt_secret_raises_in_production_with_default(monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET", auth._JWT_SECRET_DEFAULT)
    monkeypatch.setattr(auth, "APP_ENV", "production")
    with pytest.raises(RuntimeError, match="insecure default"):
        auth.validate_jwt_secret()


def test_validate_jwt_secret_raises_in_staging_with_default(monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET", auth._JWT_SECRET_DEFAULT)
    monkeypatch.setattr(auth, "APP_ENV", "staging")
    with pytest.raises(RuntimeError, match="insecure default"):
        auth.validate_jwt_secret()


def test_validate_jwt_secret_allowed_in_development_with_default(monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET", auth._JWT_SECRET_DEFAULT)
    monkeypatch.setattr(auth, "APP_ENV", "development")
    auth.validate_jwt_secret()  # should not raise


def test_validate_jwt_secret_allowed_in_test_env_with_default(monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET", auth._JWT_SECRET_DEFAULT)
    monkeypatch.setattr(auth, "APP_ENV", "test")
    auth.validate_jwt_secret()  # should not raise


def test_validate_jwt_secret_allowed_in_production_with_custom_secret(monkeypatch):
    monkeypatch.setattr(
        auth, "JWT_SECRET", "a-strong-custom-secret-that-is-not-the-default"
    )
    monkeypatch.setattr(auth, "APP_ENV", "production")
    auth.validate_jwt_secret()  # should not raise


def test_validate_jwt_secret_raises_in_production_with_short_hmac_secret(monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET", "short-custom-secret")
    monkeypatch.setattr(auth, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(auth, "APP_ENV", "production")
    with pytest.raises(RuntimeError, match="below the 32-byte minimum"):
        auth.validate_jwt_secret()


def test_validate_jwt_secret_suppresses_repeated_dev_short_secret_warning(
    monkeypatch, caplog
):
    monkeypatch.setattr(auth, "JWT_SECRET", "short-dev-secret")
    monkeypatch.setattr(auth, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(auth, "APP_ENV", "development")

    with caplog.at_level("WARNING", logger="auth"):
        auth.validate_jwt_secret()

    assert "Suppressing PyJWT's repeated InsecureKeyLengthWarning" in caplog.text
