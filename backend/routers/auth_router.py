"""Authentication routes."""

import fcntl
import secrets
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import models
import schemas
from database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def _create_authelia_user(email: str, display_name: str, password: str) -> None:
    """Append a new user entry to Authelia's file-based users_database.yml."""
    db_path = Path(auth.AUTHELIA_USERS_DB_PATH)
    if not db_path.exists():
        raise RuntimeError(f"Authelia users database not found at {db_path}")

    with open(db_path, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            data: dict[str, Any] = yaml.safe_load(fh) or {}
            users: dict[str, Any] = data.get("users") or {}

            # Reject if the email is already taken
            for entry in users.values():
                if entry.get("email") == email:
                    raise ValueError("Email already registered")

            # Use email as the username key; also check the key itself
            if email in users:
                raise ValueError("Email already registered")

            users[email] = {
                "disabled": False,
                "displayname": display_name,
                "email": email,
                "password": auth.hash_password(password),
                "groups": [],
            }
            data["users"] = users

            fh.seek(0)
            fh.truncate()
            yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@router.post("/register", response_model=schemas.TokenResponse)
async def register(
    body: schemas.RegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenResponse | Response:
    if auth.AUTHELIA_AUTH_ENABLED:
        if not auth.AUTHELIA_USERS_DB_PATH:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="User registration is not configured on this server.",
            )

        existing = await db.scalar(select(models.User).where(models.User.email == body.email))
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

        try:
            _create_authelia_user(body.email, body.name, body.password)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

        user = models.User(
            email=body.email,
            name=body.name,
            hashed_password=auth.hash_password(secrets.token_urlsafe(32)),
        )
        db.add(user)
        await db.flush()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    existing = await db.scalar(select(models.User).where(models.User.email == body.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = models.User(
        email=body.email,
        name=body.name,
        hashed_password=auth.hash_password(body.password),
    )
    db.add(user)
    await db.flush()

    token = auth.create_access_token(user.id)
    return schemas.TokenResponse(access_token=token)


@router.post("/login", response_model=schemas.TokenResponse)
async def login(
    body: schemas.LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenResponse:
    if auth.AUTHELIA_AUTH_ENABLED:
        if not auth.AUTHELIA_INTERNAL_URL:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service is not configured.",
            )

        try:
            async with httpx.AsyncClient() as client:
                authelia_resp = await client.post(
                    f"{auth.AUTHELIA_INTERNAL_URL}/api/firstfactor",
                    json={
                        "username": body.email,
                        "password": body.password,
                        "keepMeLoggedIn": False,
                        "requestMethod": "GET",
                        "targetURL": "",
                    },
                    timeout=10.0,
                )
        except httpx.RequestError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service unavailable.",
            )

        if authelia_resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Please try again later.",
            )

        if authelia_resp.status_code != status.HTTP_200_OK:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password.",
            )

        try:
            authelia_data = authelia_resp.json()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Invalid response from authentication service.",
            )

        if authelia_data.get("status") != "OK":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=authelia_data.get("message") or "Incorrect username or password.",
            )

        # Find or auto-create the app user for this Authelia account
        user = await db.scalar(select(models.User).where(models.User.email == body.email))
        if user is None:
            user = models.User(
                email=body.email,
                name=body.email.split("@")[0],
                hashed_password=auth.hash_password(secrets.token_urlsafe(32)),
            )
            db.add(user)
            await db.flush()

        token = auth.create_access_token(user.id)

        # Forward the Authelia session cookie so the browser can use
        # the Authelia portal (password-reset, etc.) without re-authenticating.
        for cookie_header in authelia_resp.headers.get_list("set-cookie"):
            response.headers.append("set-cookie", cookie_header)

        return schemas.TokenResponse(access_token=token)

    user = await db.scalar(select(models.User).where(models.User.email == body.email))
    if user is None or not auth.verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = auth.create_access_token(user.id)
    return schemas.TokenResponse(access_token=token)


@router.get("/session", response_model=schemas.TokenResponse)
async def session_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenResponse:
    user = await auth.get_authelia_user(request, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authelia session not found")
    token = auth.create_access_token(user.id)
    return schemas.TokenResponse(access_token=token)
