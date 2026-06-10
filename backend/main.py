import asyncio
import logging
import contextlib
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import auth as _auth
from config import settings
from database import Base, async_session_maker, engine
from routers import ai, admin, auth_router, intervals, strava, users
from services.activity_sync import start_activity_sync_scheduler
from services.plan_maintenance import start_daily_plan_maintenance_scheduler

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = settings.allowed_origins
FRONTEND_URL = settings.primary_frontend_url

_REQUEST_ID_HEADER = "X-Request-ID"


@asynccontextmanager
async def lifespan(_: FastAPI):
    _auth.validate_jwt_secret()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maintenance_task = start_daily_plan_maintenance_scheduler(async_session_maker)
    activity_sync_task = start_activity_sync_scheduler(async_session_maker)
    try:
        yield
    finally:
        for task in (maintenance_task, activity_sync_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="AI Trainer backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", _REQUEST_ID_HEADER],
    expose_headers=[_REQUEST_ID_HEADER],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next) -> Response:
    """Attach a correlation ID to every request/response cycle.

    Reads the incoming ``X-Request-ID`` header when present (so the frontend
    can correlate its own log entries with backend logs), otherwise generates a
    new UUID.  The ID is stored on ``request.state`` so route handlers and
    exception handlers can reference it, and is echoed back in the response
    header so the client can confirm correlation.
    """
    request_id = request.headers.get(_REQUEST_ID_HEADER) or str(uuid.uuid4())
    request.state.request_id = request_id
    response: Response = await call_next(request)
    response.headers[_REQUEST_ID_HEADER] = request_id
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Log every HTTP error with correlation metadata, then return the standard response."""
    request_id = getattr(request.state, "request_id", None)
    log_fn = logger.error if exc.status_code >= 500 else logger.warning
    log_fn(
        "HTTP %s on %s %s (request_id=%s): %s",
        exc.status_code,
        request.method,
        request.url.path,
        request_id,
        exc.detail,
    )
    # Preserve any extra headers the raising code attached (e.g. WWW-Authenticate).
    headers = dict(exc.headers or {})
    if request_id:
        headers[_REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers or None,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Log request validation failures without echoing request bodies into logs."""
    request_id = getattr(request.state, "request_id", None)
    summary = [
        {
            "loc": error.get("loc"),
            "type": error.get("type"),
            "msg": error.get("msg"),
        }
        for error in exc.errors()
    ]
    logger.warning(
        "Request validation failed on %s %s (request_id=%s): %s",
        request.method,
        request.url.path,
        request_id,
        summary,
    )
    headers = {_REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": exc.errors()}),
        headers=headers,
    )


api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth_router.router)
api_v1.include_router(users.router)
api_v1.include_router(ai.router)
api_v1.include_router(strava.router)
api_v1.include_router(intervals.router)
api_v1.include_router(admin.router)
app.include_router(api_v1)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    return {
        "status": "ok",
        "api_version": "v1",
        "frontend_url": ALLOWED_ORIGINS,
    }
