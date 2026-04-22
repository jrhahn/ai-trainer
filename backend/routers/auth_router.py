"""Authentication routes."""

import contextlib
import fcntl
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import crud
import schemas
from database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def _create_authelia_user(email: str, display_name: str, password: str) -> None:
    """Append a new user entry to Authelia's file-based users_database.yml."""
    db_path = Path(auth.AUTHELIA_USERS_DB_PATH)
    if not db_path.exists():
        raise RuntimeError(f"Authelia users database not found at {db_path}")

    # Use a separate lock file so the main file is never partially written.
    lock_path = db_path.with_suffix(".lock")
    with open(lock_path, "w", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            with open(db_path, "r", encoding="utf-8") as fh:
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

            # Write atomically: write to a temp file in the same directory, then
            # rename over the target.  This ensures Authelia's file-watcher always
            # sees a complete, valid YAML file and receives a single clean inotify
            # CREATE event rather than a truncate-then-write sequence that can
            # cause Authelia to cache an empty user list.
            tmp_fd, tmp_name = tempfile.mkstemp(
                dir=db_path.parent, suffix=".tmp", prefix="users_database_"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_fh:
                    yaml.dump(data, tmp_fh, default_flow_style=False, allow_unicode=True)
                os.replace(tmp_name, db_path)
            except Exception:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_name)
                raise
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


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

        existing = await crud.get_user_by_email_simple(db, body.email)
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

        try:
            _create_authelia_user(body.email, body.name, body.password)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

        await crud.create_user(
            db,
            email=body.email,
            name=body.name,
            hashed_password=auth.hash_password(secrets.token_urlsafe(32)),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    existing = await crud.get_user_by_email_simple(db, body.email)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = await crud.create_user(
        db,
        email=body.email,
        name=body.name,
        hashed_password=auth.hash_password(body.password),
    )

    # Commit before issuing the token so the user row is visible to subsequent
    # requests that arrive immediately after registration (e.g. in integration
    # tests).  FastAPI's generator dependency (`get_db`) commits after the
    # response is sent, which creates a race window; an explicit commit here
    # closes it.
    await db.commit()
    token = auth.create_access_token(user.id)
    return schemas.TokenResponse(access_token=token)


def _verify_authelia_credentials(email: str, password: str) -> bool:
    """Verify credentials directly against Authelia's file-based users_database.yml.

    This avoids calling Authelia's /api/firstfactor REST endpoint, which is a
    browser-session API (requires an existing session cookie) and will reject
    server-side requests without that context.
    """
    db_path = Path(auth.AUTHELIA_USERS_DB_PATH)
    if not db_path.exists():
        return False

    with open(db_path, "r", encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}

    users: dict[str, Any] = data.get("users") or {}

    # Users are keyed by username (set to email at registration).
    # Also support lookup by the email field for flexibility.
    user_entry: dict[str, Any] | None = None
    for key, entry in users.items():
        if key == email or entry.get("email") == email:
            user_entry = entry
            break

    if user_entry is None or user_entry.get("disabled", False):
        return False

    hashed = user_entry.get("password", "")
    return auth.verify_password(password, hashed)


@router.post("/login", response_model=schemas.TokenResponse)
async def login(
    body: schemas.LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenResponse:
    if auth.AUTHELIA_AUTH_ENABLED:
        if not auth.AUTHELIA_USERS_DB_PATH:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service is not configured.",
            )

        if not _verify_authelia_credentials(body.email, body.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password.",
            )

        # Find or auto-create the app user for this Authelia account
        user = await crud.get_user_by_email_simple(db, body.email)
        if user is None:
            user = await crud.create_user(
                db,
                email=body.email,
                name=body.email.split("@")[0],
                hashed_password=auth.hash_password(secrets.token_urlsafe(32)),
            )

        token = auth.create_access_token(user.id)
        return schemas.TokenResponse(access_token=token)

    user = await crud.get_user_by_email_simple(db, body.email)
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
