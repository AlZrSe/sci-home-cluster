"""
Main entry point for the Scientific Home Cluster Backend API.
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Configure logging BEFORE anything that logs at import time.
#
# backend.core.config resolves and persists the JWT signing key during this
# module's import of it, and reports a first-use generation at INFO. uvicorn
# 0.25.0's LOGGING_CONFIG configures `uvicorn`, `uvicorn.error` and
# `uvicorn.access` and no `root` handler, so the root logger would otherwise
# have no handler and its default WARNING level would drop the record before it
# was ever created - the line was reachable from pytest and from nowhere else
# (issue #56, defect D-1).
#
# INFO for now, not settings.LOG_LEVEL: that value is not known until
# backend.core.config is imported, which is the very import this has to precede.
# It is applied immediately below, and a second basicConfig would be a no-op
# once root has a handler - which is why the ordering is the fix.
logging.basicConfig(level=logging.INFO)

from backend.core.config import absolute_sqlite_path, settings  # noqa: E402
from backend.core.database import (  # noqa: E402
    init_database,
    close_database,
    run_migrations,
)
from backend.api.v1 import auth, nodes, syncthing, jobs  # noqa: E402
from shared.schemas.error_response import ErrorResponse  # noqa: E402
from backend.store import get_store  # noqa: E402
from backend.services.syncthing_service import SyncthingService  # noqa: E402

# basicConfig above already put a handler on root, so this sets the level only:
# it is a setLevel, not a second configuration, and it is where the operator's
# LOG_LEVEL actually takes effect.
logging.getLogger().setLevel(getattr(logging, settings.LOG_LEVEL))
logger = logging.getLogger(__name__)


# Fallback error codes, used only for HTTPExceptions raised without an
# explicit code. Codes are otherwise attached at the raise site - see
# backend/core/errors.py.
ERROR_CODE_MAP: Dict[int, str] = {
    400: "VALIDATION_FAILED",
    401: "AUTH_TOKEN_INVALID",
    403: "AUTH_FORBIDDEN",
    404: "NOT_FOUND",
    409: "RESOURCE_CONFLICT",
    422: "VALIDATION_FAILED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def get_error_code(status_code: int, detail: str) -> str:
    """
    Fallback error code for an HTTPException that carries none.

    Prefer raising backend.core.errors.APIError, which sets the code
    explicitly. This used to substring-match the detail text, so a code
    could change just because a message was reworded.
    """
    return ERROR_CODE_MAP.get(status_code, "INTERNAL_ERROR")


def _database_display_name(database_url: str) -> str:
    """
    A safe, human-readable identifier for the configured database.

    Strips any credentials so they can never be echoed by /health, and reports
    the ABSOLUTE location. This used to split the URL on "///" and return the
    bare file name, which was identical for every candidate database on disk and
    so could not tell a developer which file the process had actually opened -
    the file name is the same whether it sits in the repo root, in backend/, or
    in data/ (issue #34).

    A relative URL is resolved against the working directory, which is what
    SQLite does with it, so it never degrades into an ambiguous bare name.
    """
    without_credentials = database_url.rsplit("@", 1)[-1]
    path = absolute_sqlite_path(without_credentials)
    if path is not None:
        return str(path)
    return without_credentials.rsplit("///", 1)[-1] or "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    # Startup
    logger.info("Starting Scientific Home Cluster API")

    # Initialize database connection
    await init_database()

    # Run database migrations
    await run_migrations()

    # Initialize the store (singleton)
    get_store()
    logger.info("Database store initialized")

    # Name the mode the process is in, on one line, at startup. This flag
    # no longer only controls demo seeding: with it off the backend also
    # refuses to invent metrics and log lines at read time (issue #35), so
    # an empty dashboard means two very different things depending on it
    # and the operator cannot tell them apart from the outside.
    logger.info(
        "SEED_DEMO_DATA=%s - demo dataset %s; metrics and logs %s",
        settings.SEED_DEMO_DATA,
        "seeded" if settings.SEED_DEMO_DATA else "not seeded",
        "are generated on read when missing"
        if settings.SEED_DEMO_DATA
        else "are never invented (empty series / 404)",
    )

    # Start Syncthing watcher
    syncthing_service = SyncthingService(Path(settings.SYNCTHING_ROOT))
    await syncthing_service.start()
    app.state.syncthing_service = syncthing_service
    logger.info("Syncthing service started")

    yield

    # Shutdown
    logger.info("Shutting down Scientific Home Cluster API")

    # Stop Syncthing watcher
    if hasattr(app.state, "syncthing_service"):
        await app.state.syncthing_service.stop()

    # Close database connections
    await close_database()

    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Add request ID to each request for tracing."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id

    response = await call_next(request)

    # Add request ID to response headers
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log incoming requests with method, path, status, and duration."""
    start_time = time.perf_counter()
    request_id = getattr(request.state, "request_id", "unknown")

    # Process request
    response = await call_next(request)

    # Calculate duration
    duration_ms = (time.perf_counter() - start_time) * 1000

    # Log request details
    logger.info(
        f"{request.method} {request.url.path} - "
        f"Status: {response.status_code} - "
        f"Duration: {duration_ms:.2f}ms - "
        f"Request-ID: {request_id}"
    )

    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Convert HTTPException to ErrorResponse format."""
    # APIError carries its own code; anything else falls back to the
    # status-code map.
    error_code = getattr(exc, "error_code", None) or get_error_code(
        exc.status_code, str(exc.detail)
    )

    # Determine title based on status code
    if exc.status_code == 401:
        title = "Unauthorized"
    elif exc.status_code == 404:
        title = "Not Found"
    elif exc.status_code == 403:
        title = "Forbidden"
    elif exc.status_code == 409:
        title = "Conflict"
    elif exc.status_code == 400:
        title = "Bad Request"
    else:
        title = "Error"

    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            status=exc.status_code,
            title=title,
            detail=exc.detail,
            instance=str(request.url),
            error_code=error_code,
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Convert RequestValidationError to ErrorResponse format."""
    logger.warning(f"Validation error for {request.url}: {exc.errors()}")
    error_code = "VALIDATION_FAILED"
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            status=422,
            title="Unprocessable Entity",
            detail="Validation failed: "
            + ", ".join(
                f"{'.'.join(str(e) for e in err['loc'])}: {err['msg']}"
                for err in exc.errors()
            ),
            instance=str(request.url),
            error_code=error_code,
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Convert any unhandled exception to ErrorResponse format."""
    logger.exception(f"Unhandled exception for {request.url}: {exc}")
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            status=500,
            title="Internal Server Error",
            detail="An unexpected error occurred",
            instance=str(request.url),
            error_code="INTERNAL_ERROR",
        ).model_dump(),
    )


# Set up CORS middleware
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Include API routers
app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["auth"])
app.include_router(jobs.router, prefix=f"{settings.API_V1_STR}/jobs", tags=["jobs"])
app.include_router(nodes.router, prefix=f"{settings.API_V1_STR}/nodes", tags=["nodes"])
app.include_router(
    syncthing.router, prefix=f"{settings.API_V1_STR}/syncthing", tags=["syncthing"]
)


@app.get("/")
async def root():
    return {"message": "Welcome to Scientific Home Cluster API"}


@app.get(f"{settings.API_V1_STR}/health")
async def health_check():
    """Enhanced health check with store, config, syncthing, and database status."""
    from backend.core.database import get_engine

    store = get_store()

    # Check store status
    nodes_count = len(await store.list_nodes())
    jobs_count, _ = await store.list_jobs()

    store_status = "healthy" if nodes_count > 0 else "degraded"

    # Check config status
    config_status = "healthy"
    if not settings.SYNCTHING_ROOT:
        config_status = "degraded"

    # Check Syncthing status
    syncthing_status = "not_configured"
    syncthing_path = Path(settings.SYNCTHING_ROOT)
    if syncthing_path.exists():
        syncthing_status = "available"

    # Check database status
    database_status = "healthy"
    try:
        from sqlalchemy import text

        engine = get_engine()
        # Test database connectivity
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        database_status = "unhealthy"

    # Overall status: healthy if store and database are healthy
    overall_status = (
        "healthy"
        if store_status == "healthy" and database_status == "healthy"
        else "degraded"
    )

    return {
        "status": overall_status,
        "version": settings.VERSION,
        "store": {
            "status": store_status,
            "nodes": nodes_count,
            "jobs": len(jobs_count),
        },
        "config": {
            "status": config_status,
            "syncthing_root": settings.SYNCTHING_ROOT,
            "api_version": settings.API_V1_STR,
        },
        "syncthing": {
            "status": syncthing_status,
            "path": settings.SYNCTHING_ROOT,
        },
        "database": {
            "status": database_status,
            # The absolute database path, never credentials. A bare file name
            # is the same for every candidate file on disk, so reporting one
            # left "which database am I on?" unanswerable (issue #34).
            "url": _database_display_name(settings.DATABASE_URL),
        },
    }
