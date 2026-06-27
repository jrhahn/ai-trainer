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


# ---------------------------------------------------------------------------
# Trusted-proxy shared secret (issue #324 defense-in-depth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authelia_session_accepted_with_proxy_secret(client, monkeypatch):
    """Remote-* headers are honored when the proxy secret header matches."""
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", "s3cret-from-proxy")
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SECRET_HEADER", "X-Authelia-Proxy-Secret")

    response = await client.get(
        "/api/v1/auth/session",
        headers={
            "Remote-Email": "trusted@example.com",
            "Remote-Name": "Trusted",
            "X-Authelia-Proxy-Secret": "s3cret-from-proxy",
        },
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_authelia_session_rejected_without_proxy_secret(client, monkeypatch):
    """When a proxy secret is configured, Remote-* headers without it are ignored."""
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", "s3cret-from-proxy")
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SECRET_HEADER", "X-Authelia-Proxy-Secret")

    response = await client.get(
        "/api/v1/auth/session",
        headers={"Remote-Email": "forged@example.com", "Remote-Name": "Forged"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_authelia_session_rejected_with_wrong_proxy_secret(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", "s3cret-from-proxy")
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SECRET_HEADER", "X-Authelia-Proxy-Secret")

    response = await client.get(
        "/api/v1/auth/session",
        headers={
            "Remote-Email": "forged@example.com",
            "X-Authelia-Proxy-Secret": "wrong-guess",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_authelia_session_trusts_headers_when_no_secret_configured(client, monkeypatch):
    """With no proxy secret set, header trust is unchanged (network-isolation only)."""
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", "")

    response = await client.get(
        "/api/v1/auth/session",
        headers={"Remote-Email": "plain@example.com", "Remote-Name": "Plain"},
    )
    assert response.status_code == 200


def test_warn_if_authelia_proxy_unprotected_logs_when_secret_missing(monkeypatch, caplog):
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", "")

    with caplog.at_level("WARNING", logger="auth"):
        auth.warn_if_authelia_proxy_unprotected()

    assert "AUTHELIA_PROXY_SHARED_SECRET is" in caplog.text


def test_warn_if_authelia_proxy_unprotected_silent_when_secret_set(monkeypatch, caplog):
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", "configured")

    with caplog.at_level("WARNING", logger="auth"):
        auth.warn_if_authelia_proxy_unprotected()

    assert "AUTHELIA_PROXY_SHARED_SECRET" not in caplog.text


# ---------------------------------------------------------------------------
# Lower-level auth helpers (issue #335 coverage)
# ---------------------------------------------------------------------------

import types

import jwt as _jwt
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


def test_verify_password_returns_false_for_malformed_bcrypt_hash():
    assert auth.verify_password("whatever", "not-a-real-bcrypt-hash") is False


def test_decode_token_missing_sub_raises_401():
    token = _jwt.encode({"sub": ""}, auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(token)
    assert exc.value.status_code == 401


def test_decode_token_invalid_raises_401():
    with pytest.raises(HTTPException):
        auth.decode_token("not-a-jwt")


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_require_admin_rejects_missing_credentials():
    with pytest.raises(HTTPException) as exc:
        auth.require_admin(None)
    assert exc.value.status_code == 401


def test_require_admin_rejects_invalid_token():
    with pytest.raises(HTTPException) as exc:
        auth.require_admin(_creds("garbage"))
    assert exc.value.status_code == 401


def test_require_admin_rejects_non_admin_role():
    token = auth.create_access_token("user-1")  # no admin role
    with pytest.raises(HTTPException) as exc:
        auth.require_admin(_creds(token))
    assert exc.value.status_code == 403


def test_require_admin_accepts_admin_token():
    auth.require_admin(_creds(auth.create_admin_token()))  # does not raise


@pytest.mark.asyncio
async def test_get_or_create_authelia_user_returns_existing(monkeypatch):
    existing = object()

    async def fake_get(db, email):
        return existing

    monkeypatch.setattr(auth, "AUTHELIA_PROXY_SHARED_SECRET", "")  # trust the request
    monkeypatch.setattr(auth.crud, "get_user_by_email", fake_get)

    request = types.SimpleNamespace(headers={auth.AUTHELIA_REMOTE_EMAIL_HEADER: "a@b.com"})
    result = await auth._get_or_create_authelia_user(request, db=None)
    assert result is existing


@pytest.mark.asyncio
async def test_get_current_user_falls_back_to_bearer_without_authelia_header(client, monkeypatch, auth_headers):
    """In Authelia mode, a request without Remote-Email still authenticates via bearer."""
    monkeypatch.setattr(auth, "AUTHELIA_AUTH_ENABLED", True)

    resp = await client.get("/api/v1/users/me", headers=auth_headers)
    assert resp.status_code == 200
