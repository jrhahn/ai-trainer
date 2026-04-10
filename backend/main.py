"""
Strava OAuth backend for AI Trainer.

Holds the app-level Strava Client ID and Client Secret so users never need
to register their own Strava application.

Endpoints
---------
GET  /auth/strava            – Kick off OAuth; redirects user to Strava.
GET  /auth/strava/callback   – Strava redirects here with a code; exchanges it
                               for tokens and forwards them to the frontend.
POST /auth/strava/refresh    – Refresh an expired Strava access token.
GET  /healthz                – Simple health-check.

Configuration (environment variables)
--------------------------------------
STRAVA_CLIENT_ID      – Required. Your registered Strava app client ID.
STRAVA_CLIENT_SECRET  – Required. Your registered Strava app client secret.
FRONTEND_URL          – Where the React app is served (default: http://localhost:5173).
BACKEND_URL           – Public URL of this server (default: http://localhost:8000).
"""

import os
import urllib.parse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

load_dotenv()

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")

STRAVA_OAUTH_BASE = "https://www.strava.com"

app = FastAPI(title="AI Trainer – Strava OAuth backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    configured = bool(STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET)
    return {"status": "ok", "strava_configured": configured}


# ---------------------------------------------------------------------------
# Step 1 – Initiate OAuth
# ---------------------------------------------------------------------------


@app.get("/auth/strava", tags=["strava"])
def strava_auth() -> RedirectResponse:
    """Redirect the user to Strava's authorisation page."""
    if not STRAVA_CLIENT_ID or not STRAVA_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Strava credentials are not configured on the server. "
            "Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET environment variables.",
        )

    callback_uri = f"{BACKEND_URL}/auth/strava/callback"
    params = urllib.parse.urlencode(
        {
            "client_id": STRAVA_CLIENT_ID,
            "redirect_uri": callback_uri,
            "response_type": "code",
            "approval_prompt": "force",
            "scope": "read,activity:read_all",
        }
    )
    return RedirectResponse(f"{STRAVA_OAUTH_BASE}/oauth/authorize?{params}")


# ---------------------------------------------------------------------------
# Step 2 – Handle Strava's callback
# ---------------------------------------------------------------------------


@app.get("/auth/strava/callback", tags=["strava"])
async def strava_callback(code: str = "", error: str = "") -> RedirectResponse:
    """Exchange the authorisation code for tokens and forward them to the frontend."""
    if error or not code:
        # Use a fixed safe message – never reflect the raw user-supplied error value
        # in the redirect URL to avoid an open-redirect / injection risk.
        safe_error = urllib.parse.quote(
            "Strava authorisation was denied or failed. Please try again."
        )
        return RedirectResponse(f"{FRONTEND_URL}/strava/callback?error={safe_error}")

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

    params = urllib.parse.urlencode(
        {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": data["expires_at"],
            "athlete_id": athlete.get("id", ""),
            "athlete_name": athlete_name,
        }
    )
    return RedirectResponse(f"{FRONTEND_URL}/strava/callback?{params}")


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: int


@app.post("/auth/strava/refresh", response_model=RefreshResponse, tags=["strava"])
async def strava_refresh(body: RefreshRequest) -> RefreshResponse:
    """Refresh an expired Strava access token using the server's client secret."""
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
        raise HTTPException(status_code=400, detail="Failed to refresh Strava token.")

    data = resp.json()
    return RefreshResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=data["expires_at"],
    )
