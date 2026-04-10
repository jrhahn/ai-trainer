"""Authentication routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import models
import schemas
from database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.TokenResponse)
async def register(
    body: schemas.RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenResponse:
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
    db: AsyncSession = Depends(get_db),
) -> schemas.TokenResponse:
    user = await db.scalar(select(models.User).where(models.User.email == body.email))
    if user is None or not auth.verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = auth.create_access_token(user.id)
    return schemas.TokenResponse(access_token=token)
