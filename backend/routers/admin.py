"""Admin panel routes.

Requires the ``ADMIN_PASSWORD`` env var to be set.  Exposes:

  POST /admin/login   – authenticate with the admin password, receive a JWT
  GET  /admin/users   – per-user stats (protected, admin JWT required)
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import models
import schemas
from config import settings
from database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AdminLoginRequest(BaseModel):
    password: str


class AdminUserStat(schemas.CamelModel):
    id: str
    email: str
    name: str | None
    created_at: datetime
    last_login: datetime | None
    ai_provider: str
    is_onboarded: bool
    strava_connected: bool
    strava_analysis_complete: bool
    consumed_tokens: int
    ride_count: int
    last_activity_date: str | None
    chat_message_count: int


class AdminUsersResponse(schemas.CamelModel):
    users: list[AdminUserStat]
    total_users: int
    total_tokens: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _admin_enabled() -> None:
    if not settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin panel is not enabled (ADMIN_PASSWORD not set)",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login", response_model=schemas.TokenResponse)
async def admin_login(body: AdminLoginRequest) -> schemas.TokenResponse:
    """Validate the admin password and return a short-lived admin JWT."""
    _admin_enabled()
    if not secrets.compare_digest(body.password, settings.admin_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    token = auth.create_admin_token()
    return schemas.TokenResponse(access_token=token, token_type="bearer")


@router.get("/users", response_model=AdminUsersResponse, dependencies=[Depends(auth.require_admin)])
async def admin_users(db: AsyncSession = Depends(get_db)) -> AdminUsersResponse:
    """Return aggregated stats for every registered user."""
    _admin_enabled()

    # Subquery: ride count + last activity date per user
    ride_sq = (
        select(
            models.RideMetric.user_id,
            func.count().label("ride_count"),
            func.max(models.RideMetric.activity_date).label("last_activity_date"),
        )
        .group_by(models.RideMetric.user_id)
        .subquery()
    )

    # Subquery: chat message count per user
    chat_sq = (
        select(
            models.ChatMessage.user_id,
            func.count().label("chat_count"),
        )
        .group_by(models.ChatMessage.user_id)
        .subquery()
    )

    # Subquery: strava token presence
    strava_sq = (
        select(models.StravaToken.user_id)
        .subquery()
    )

    stmt = (
        select(
            models.User,
            func.coalesce(ride_sq.c.ride_count, 0).label("ride_count"),
            ride_sq.c.last_activity_date,
            func.coalesce(chat_sq.c.chat_count, 0).label("chat_count"),
            (strava_sq.c.user_id.isnot(None)).label("strava_connected"),
        )
        .outerjoin(ride_sq, ride_sq.c.user_id == models.User.id)
        .outerjoin(chat_sq, chat_sq.c.user_id == models.User.id)
        .outerjoin(strava_sq, strava_sq.c.user_id == models.User.id)
        .order_by(models.User.created_at.desc())
    )

    rows = (await db.execute(stmt)).all()

    user_stats: list[AdminUserStat] = []
    for row in rows:
        user: models.User = row[0]
        ride_count: int = row[1]
        last_activity_date: str | None = row[2]
        chat_count: int = row[3]
        strava_connected: bool = bool(row[4])

        user_stats.append(
            AdminUserStat(
                id=user.id,
                email=user.email,
                name=user.name,
                created_at=user.created_at,
                last_login=user.last_login,
                ai_provider=user.ai_provider,
                is_onboarded=user.is_onboarded,
                strava_connected=strava_connected,
                strava_analysis_complete=user.strava_analysis_complete,
                consumed_tokens=user.consumed_tokens,
                ride_count=ride_count,
                last_activity_date=last_activity_date,
                chat_message_count=chat_count,
            )
        )

    return AdminUsersResponse(
        users=user_stats,
        total_users=len(user_stats),
        total_tokens=sum(u.consumed_tokens for u in user_stats),
    )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(auth.require_admin)])
async def admin_delete_user(user_id: str, db: AsyncSession = Depends(get_db)) -> None:
    """Permanently delete a user and all their associated data."""
    _admin_enabled()
    user = await db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await db.delete(user)
