import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import Base, engine
from routers import ai, auth_router, strava, users
import auth as _auth

load_dotenv()

_frontend_url_raw = os.environ.get("FRONTEND_URL", "http://localhost:5173")
# Support a comma-separated list of origins so that deployments accessible from
# multiple hostnames / IP addresses (e.g. domain + raw IP during initial setup)
# can all be permitted without wildcard CORS.
ALLOWED_ORIGINS = [u.strip().rstrip("/") for u in _frontend_url_raw.split(",") if u.strip()]
FRONTEND_URL = ALLOWED_ORIGINS[0] if ALLOWED_ORIGINS else "http://localhost:5173"


@asynccontextmanager
async def lifespan(_: FastAPI):
    _auth.validate_jwt_secret()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="AI Trainer backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth_router.router)
api_v1.include_router(users.router)
api_v1.include_router(ai.router)
api_v1.include_router(strava.router)
app.include_router(api_v1)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    return {
        "status": "ok",
        "api_version": "v1",
        "frontend_url": ALLOWED_ORIGINS,
    }

