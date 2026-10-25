"""
Main entry point for the Scientific Home Cluster Backend API.
"""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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
from backend.core.frontend import register_frontend, serving_frontend  # noqa: E402
from backend.core.telemetry import configure_telemetry  # noqa: E402
from backend.core.metrics import record_http_request  # noqa: E402
from backend.core.rate_limit import init_rate_limiter, shutdown_rate_limiter  # noqa: E402

# basicConfig above already put a handler on root, so this sets the level only:
# it is a setLevel, not a second configuration, and it is where the operator's
# LOG_LEVEL actually takes effect. The generation INFO above is deliberately
# emitted before it is applied, so a first-time key generation is reported even
# at LOG_LEVEL=WARNING.
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

    # Initialize rate limiter
    await init_rate_limiter()
    logger.info("Rate limiter initialized")

    # Background metrics updater for gauges that cannot be derived synchronously.
    async def _metrics_loop() -> None:
        while True:
            try:
                store = get_store()
                nodes = await store.list_nodes()
                online = sum(1 for n in nodes if n.status == "ONLINE")
                from backend.core.metrics import (
                    set_nodes_online,
                    set_node_heartbeat_age,
                    set_syncthing_available,
                )

                set_nodes_online(online)
                now = datetime.now(timezone.utc)
                for node in nodes:
                    heartbeat = node.last_heartbeat
                    if heartbeat.tzinfo is None:
                        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                    set_node_heartbeat_age(
                        node.node_id, (now - heartbeat).total_seconds()
                    )
                set_syncthing_available(Path(settings.SYNCTHING_ROOT).exists())
            except Exception:
                pass
            await asyncio.sleep(30)

    app.state.metrics_task = asyncio.create_task(_metrics_loop())

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

    # Stop metrics updater
    metrics_task = getattr(app.state, "metrics_task", None)
    if metrics_task is not None:
        metrics_task.cancel()
        try:
            await metrics_task
        except asyncio.CancelledError:
            pass

    # Stop Syncthing watcher
    if hasattr(app.state, "syncthing_service"):
        await app.state.syncthing_service.stop()

    # Shutdown rate limiter
    await shutdown_rate_limiter()
    logger.info("Rate limiter shut down")

    # Close database connections
    await close_database()

    logger.info("Shutdown complete")


# Compute API docs enablement before FastAPI instantiation.
# Priority: explicit ENABLE_API_DOCS > ENVIRONMENT in (development, test)
enable_docs = settings.ENABLE_API_DOCS or settings.ENVIRONMENT in (
    "development",
    "test",
)
docs_url = "/docs" if enable_docs else None
redoc_url = "/redoc" if enable_docs else None
openapi_url = f"{settings.API_V1_STR}/openapi.json" if enable_docs else None

# Log the docs state at startup (INFO level)
if enable_docs:
    if settings.ENABLE_API_DOCS:
        logger.info("API docs ENABLED (ENABLE_API_DOCS=true)")
    else:
        logger.info(f"API docs ENABLED (ENVIRONMENT={settings.ENVIRONMENT})")
else:
    logger.info("API docs DISABLED (production mode)")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=openapi_url,
    lifespan=lifespan,
    docs_url=docs_url,
    redoc_url=redoc_url,
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
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start_time) * 1000
        record_http_request(request.method, request.url.path, 500, duration_ms / 1000.0)
        raise

    # Calculate duration
    duration_ms = (time.perf_counter() - start_time) * 1000

    record_http_request(
        request.method, request.url.path, response.status_code, duration_ms / 1000.0
    )

    # Log request details
    logger.info(
        f"{request.method} {request.url.path} - "
        f"Status: {response.status_code} - "
        f"Duration: {duration_ms:.2f}ms - "
        f"Request-ID: {request_id}"
    )

    return response


@app.middleware("http")
async def csp_middleware(request: Request, call_next):
    """Add Content-Security-Policy header to all responses."""
    response = await call_next(request)

    csp_header = "Content-Security-Policy"
    if settings.CSP_REPORT_ONLY:
        csp_header = "Content-Security-Policy-Report-Only"

    # Only add if not already set (e.g., by frontend serving)
    if csp_header not in response.headers:
        response.headers[csp_header] = settings.CSP_POLICY

    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Convert HTTPException to ErrorResponse format.

    Registered for starlette's HTTPException, not fastapi's subclass, and the
    distinction is load-bearing. Starlette's router raises the BASE class itself
    when nothing matched (404) and when the path exists under another method
    (405) - `starlette/routing.py: Route.handle` and `Router.not_found` both do
    `raise HTTPException(...)` with `starlette.exceptions.HTTPException`.
    Registering only fastapi's subclass therefore covers what the application
    raises and misses everything the framework raises, so a routing-level 404
    answered `{"detail": "Not Found"}` while every application 404 carried the
    ErrorResponse envelope. fastapi.HTTPException IS a subclass of this one, so
    the base registration covers both and nothing is lost (issue #140).
    """
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

    # Include rate limit headers if present
    headers = getattr(exc, "headers", None)
    rate_limit_headers = getattr(request.state, "rate_limit_headers", None)
    if rate_limit_headers:
        if headers is None:
            headers = {}
        headers.update(rate_limit_headers)

    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            status=exc.status_code,
            title=title,
            detail=exc.detail,
            instance=str(request.url),
            error_code=error_code,
        ).model_dump(),
        # Carried through rather than dropped. A 405 raised by Starlette sets
        # `Allow`, and a 405 without it is a 405 a client cannot act on - the
        # one header that tells it which methods to try.
        headers=headers,
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
if settings.effective_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.effective_cors_origins,
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


# Registered only when the SPA is not being served. Both cannot answer for "/",
# and FastAPI resolves in registration order, so the API's own root has to stand
# aside: in a container the page at "/" is the app, not this JSON. See
# backend/core/frontend.py.
if not serving_frontend():

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

    store_status = "healthy"

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


# Last, deliberately. It claims "/{full_path:path}", so every route above -
# the API routers, /docs, /redoc, /api/v1/health and the JSON root - has to be
# registered first or this catch-all would take them instead. A no-op unless
# SERVE_FRONTEND is on.
register_frontend(app)

# OpenTelemetry instrumentation. Configured last so the FastAPI app is fully
# assembled (routers, middleware, exception handlers) before it is instrumented.
if settings.OTEL_ENABLED and not settings.OTEL_SDK_DISABLED:
    configure_telemetry(app)
