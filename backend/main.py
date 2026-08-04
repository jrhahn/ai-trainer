import logging
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
from services.activity_sync import activity_sync_job
from services.duration_refresh import duration_refresh_job
from services.contradiction_detection import athlete_contradiction_detection_job
from services.experiment_suggestion import validation_experiment_suggestion_job
from services.hypothesis_generation import athlete_hypothesis_generation_job
from services.insight_generation import athlete_insight_generation_job
from services.llm import AIKeyNotConfiguredError
from services.open_question_generation import athlete_open_question_generation_job
from services.pipeline_graph import graph as pipeline_graph
from services.plan_maintenance import daily_plan_maintenance_job
from services.prediction_evaluation import prediction_evaluation_job
from services.scheduler import InProcessScheduler

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = settings.allowed_origins
FRONTEND_URL = settings.primary_frontend_url

_REQUEST_ID_HEADER = "X-Request-ID"


async def _create_dev_schema() -> None:
    """Create tables directly from the models — dev/test convenience only.

    Alembic is the single source of truth for the schema in real deployments
    (``entrypoint.sh`` runs ``alembic upgrade head`` before the app boots). There,
    ``create_all`` would only create missing *tables* (never missing columns), so
    it silently masks a forgotten migration and drifts from the migrated schema.
    It is therefore skipped outside development/test, where migrations aren't run
    and creating tables from the models is the convenient bootstrap (#327).
    """
    if settings.app_env not in ("development", "test"):
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _auth.validate_jwt_secret()
    _auth.warn_if_authelia_proxy_unprotected()
    # Fail fast if the pipeline dependency graph is not a DAG.
    pipeline_graph.validate()
    await _create_dev_schema()
    scheduler = InProcessScheduler()
    scheduler.register(daily_plan_maintenance_job(async_session_maker))
    scheduler.register(activity_sync_job(async_session_maker))
    scheduler.register(duration_refresh_job(async_session_maker))
    scheduler.register(athlete_insight_generation_job(async_session_maker))
    scheduler.register(athlete_contradiction_detection_job(async_session_maker))
    scheduler.register(athlete_hypothesis_generation_job(async_session_maker))
    scheduler.register(athlete_open_question_generation_job(async_session_maker))
    scheduler.register(validation_experiment_suggestion_job(async_session_maker))
    scheduler.register(prediction_evaluation_job(async_session_maker))
    scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


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


@app.exception_handler(AIKeyNotConfiguredError)
async def ai_key_not_configured_handler(
    request: Request, exc: AIKeyNotConfiguredError
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.warning(
        "AI key not configured on %s %s (request_id=%s): %s",
        request.method,
        request.url.path,
        request_id,
        str(exc),
    )
    headers: dict[str, str] = {}
    if request_id:
        headers[_REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=402,
        content={"detail": str(exc)},
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
        "allowed_origins": ALLOWED_ORIGINS,
    }
