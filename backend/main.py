from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import auth as _auth
from config import settings
from database import Base, engine
from routers import ai, auth_router, strava, users

ALLOWED_ORIGINS = settings.allowed_origins
FRONTEND_URL = settings.primary_frontend_url


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

