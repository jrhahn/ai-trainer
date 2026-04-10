"""Strava OAuth and activity proxy routes."""

import os
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import models
import schemas
from database import get_db

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
STRAVA_OAUTH_BASE = "https://www.strava.com"

router = APIRouter(tags=["strava"])


class RefreshRequest(schemas.CamelModel):
    refresh_token: str


class RefreshResponse(schemas.CamelModel):
    access_token: str
    refresh_token: str
    expires_at: int


@router.get("/auth/strava")
async def strava_auth(request: Request, token: str = "") -> RedirectResponse:
    if not STRAVA_CLIENT_ID or not STRAVA_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Strava credentials are not configured on the server.",
        )

    auth_header = request.headers.get("authorization", "")
    state_token = token
    if not state_token and auth_header.startswith("Bearer "):
        state_token = auth_header.split(" ", 1)[1]
    if not state_token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    callback_uri = f"{BACKEND_URL}/api/v1/auth/strava/callback"
    params = urllib.parse.urlencode(
        {
            "client_id": STRAVA_CLIENT_ID,
            "redirect_uri": callback_uri,
            "response_type": "code",
            "approval_prompt": "force",
            "scope": "read,activity:read_all",
            "state": state_token,
        }
    )
    return RedirectResponse(f"{STRAVA_OAUTH_BASE}/oauth/authorize?{params}")


@router.get("/auth/strava/callback")
async def strava_callback(
    db: AsyncSession = Depends(get_db),
    code: str = "",
    error: str = "",
    state: str = "",
) -> RedirectResponse:
    if error or not code or not state:
        safe_error = urllib.parse.quote(
            "Strava authorisation was denied or failed. Please try again."
        )
        return RedirectResponse(f"{FRONTEND_URL}/strava/callback?error={safe_error}")

    user_id = auth.decode_token(state)
    user = await db.get(models.User, user_id)
    if user is None:
        return RedirectResponse(f"{FRONTEND_URL}/strava/callback?error=User%20not%20found")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRAVA_OAUTH_BASE}/oauth/token",
            json={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
            },
        )

    if not resp.is_success:
        msg = urllib.parse.quote("Token exchange failed. Please try again.")
        return RedirectResponse(f"{FRONTEND_URL}/strava/callback?error={msg}")

    data = resp.json()
    athlete = data.get("athlete", {})
    athlete_name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()

    token_row = await db.get(models.StravaToken, user.id)
    if token_row is None:
        token_row = models.StravaToken(
            user_id=user.id,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            athlete_id=athlete.get("id", 0),
            athlete_name=athlete_name,
        )
        db.add(token_row)
    else:
        token_row.access_token = data["access_token"]
        token_row.refresh_token = data["refresh_token"]
        token_row.expires_at = data["expires_at"]
        token_row.athlete_id = athlete.get("id", 0)
        token_row.athlete_name = athlete_name

    await db.flush()
    return RedirectResponse(f"{FRONTEND_URL}/strava/callback?success=true")


@router.post("/auth/strava/refresh", response_model=RefreshResponse)
async def strava_refresh(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> RefreshResponse:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRAVA_OAUTH_BASE}/oauth/token",
            json={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "refresh_token": body.refresh_token,
                "grant_type": "refresh_token",
            },
        )

    if not resp.is_success:
        raise HTTPException(status_code=400, detail="Failed to refresh Strava token")

    data = resp.json()
    token_row = await db.get(models.StravaToken, current_user.id)
    if token_row is not None:
        token_row.access_token = data["access_token"]
        token_row.refresh_token = data["refresh_token"]
        token_row.expires_at = data["expires_at"]
        await db.flush()

    return RefreshResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=data["expires_at"],
    )


@router.get("/strava/activities")
async def get_strava_activities(
    current_user: models.User = Depends(auth.get_current_user),
) -> list[dict]:
    if current_user.strava_token is None:
        raise HTTPException(status_code=404, detail="Strava not connected")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{STRAVA_OAUTH_BASE}/api/v3/athlete/activities",
            params={"per_page": 10},
            headers={"Authorization": f"Bearer {current_user.strava_token.access_token}"},
        )
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail="Failed to fetch Strava activities")
    return resp.json()


@router.delete("/strava/disconnect")
async def disconnect_strava(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
) -> dict:
    token_row = await db.get(models.StravaToken, current_user.id)
    if token_row is not None:
        await db.delete(token_row)
        await db.flush()
    return {"status": "disconnected"}
