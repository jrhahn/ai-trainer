"""Password hashing, JWT creation/verification, and FastAPI auth dependency."""

import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models
from database import get_db

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "10080"))  # 7 days
AUTHELIA_AUTH_ENABLED = os.environ.get("AUTHELIA_AUTH_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
AUTHELIA_REMOTE_USER_HEADER = os.environ.get("AUTHELIA_REMOTE_USER_HEADER", "Remote-User")
AUTHELIA_REMOTE_EMAIL_HEADER = os.environ.get("AUTHELIA_REMOTE_EMAIL_HEADER", "Remote-Email")
AUTHELIA_REMOTE_NAME_HEADER = os.environ.get("AUTHELIA_REMOTE_NAME_HEADER", "Remote-Name")

_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire}
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
    user = await db.scalar(
        select(models.User)
        .options(
            selectinload(models.User.training_plan),
            selectinload(models.User.chat_messages),
            selectinload(models.User.coach_memory),
            selectinload(models.User.strava_token),
            selectinload(models.User.rider_assessment),
        )
        .where(models.User.id == user_id)
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


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

    user = await db.scalar(
        select(models.User)
        .options(
            selectinload(models.User.training_plan),
            selectinload(models.User.chat_messages),
            selectinload(models.User.coach_memory),
            selectinload(models.User.strava_token),
            selectinload(models.User.rider_assessment),
        )
        .where(models.User.email == email)
    )
    if user is not None:
        return user

    remote_name = request.headers.get(AUTHELIA_REMOTE_NAME_HEADER)
    remote_user = request.headers.get(AUTHELIA_REMOTE_USER_HEADER)
    user = models.User(
        email=email,
        name=remote_name or remote_user,
        hashed_password=hash_password(secrets.token_urlsafe(32)),
    )
    db.add(user)
    await db.flush()
    return user
