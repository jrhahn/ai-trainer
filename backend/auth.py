"""Password hashing, JWT creation/verification, and FastAPI auth dependency."""

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

import crud
import models
from config import settings
from database import get_db

_JWT_SECRET_DEFAULT = "change-me-in-production"
_DEV_ENVS = {"development", "dev", "local", "test", "testing"}

JWT_SECRET = settings.jwt_secret
JWT_ALGORITHM = settings.jwt_algorithm
JWT_EXPIRE_MINUTES = settings.jwt_expire_minutes
APP_ENV = settings.app_env
AUTHELIA_AUTH_ENABLED = settings.authelia_auth_enabled
AUTHELIA_REMOTE_USER_HEADER = settings.authelia_remote_user_header
AUTHELIA_REMOTE_EMAIL_HEADER = settings.authelia_remote_email_header
AUTHELIA_REMOTE_NAME_HEADER = settings.authelia_remote_name_header
AUTHELIA_INTERNAL_URL = settings.authelia_internal_url.rstrip("/")
AUTHELIA_USERS_DB_PATH = settings.authelia_users_db_path

_bearer_scheme = HTTPBearer(auto_error=False)
_argon2_hasher = PasswordHasher()


def validate_jwt_secret() -> None:
    """Raise RuntimeError if JWT_SECRET is the insecure default in a non-dev environment."""
    if JWT_SECRET == _JWT_SECRET_DEFAULT and APP_ENV.lower() not in _DEV_ENVS:
        raise RuntimeError(
            f"JWT_SECRET is set to the insecure default value '{_JWT_SECRET_DEFAULT}'. "
            "Set a strong, random JWT_SECRET environment variable before starting the app. "
            f"(APP_ENV={APP_ENV!r})"
        )


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if hashed.startswith("$argon2"):
        try:
            return _argon2_hasher.verify(hashed, plain)
        except Argon2Error:
            return False

    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_admin_token() -> str:
    """Return a short-lived JWT that grants admin panel access."""
    expire = datetime.now(timezone.utc) + timedelta(hours=2)
    payload = {"sub": "admin", "role": "admin", "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> str:
    """Return user_id or raise HTTP 401."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> models.User:
    if AUTHELIA_AUTH_ENABLED:
        authelia_user = await _get_or_create_authelia_user(request, db)
        if authelia_user is not None:
            return authelia_user

    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    user_id = decode_token(credentials.credentials)
    user = await crud.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """FastAPI dependency — raises 401/403 unless the request carries a valid admin JWT."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


async def get_authelia_user(
    request: Request,
    db: AsyncSession,
) -> models.User | None:
    if not AUTHELIA_AUTH_ENABLED:
        return None
    return await _get_or_create_authelia_user(request, db)


async def _get_or_create_authelia_user(
    request: Request,
    db: AsyncSession,
) -> models.User | None:
    email = request.headers.get(AUTHELIA_REMOTE_EMAIL_HEADER)
    if not email:
        return None

    user = await crud.get_user_by_email(db, email)
    if user is not None:
        return user

    remote_name = request.headers.get(AUTHELIA_REMOTE_NAME_HEADER)
    remote_user = request.headers.get(AUTHELIA_REMOTE_USER_HEADER)
    return await crud.create_user(
        db,
        email=email,
        name=remote_name or remote_user,
        # Authelia-managed users do not authenticate via local password login.
        # A random one-way hash ensures no reusable local password exists.
        hashed_password=hash_password(secrets.token_urlsafe(32)),
    )
