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

from backend.core.config import settings
from backend.core.database import init_database, close_database, run_migrations
from backend.api.v1 import auth, nodes, syncthing, jobs
from backend.models.error_response import ErrorResponse
from backend.store import get_store
from backend.services.syncthing_service import SyncthingService

# Configure logging
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))
logger = logging.getLogger(__name__)


# Error code mapping for HTTP status codes
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

# Specific error codes for common scenarios
SPECIFIC_ERROR_CODES: Dict[str, str] = {
    "not authenticated": "AUTH_TOKEN_MISSING",
    "could not validate credentials": "AUTH_TOKEN_INVALID",
    "invalid or expired token": "AUTH_TOKEN_EXPIRED",
    "invalid shared token": "AUTH_SHARED_TOKEN_INVALID",
    "shared token not configured": "SHARED_TOKEN_NOT_CONFIGURED",
    "job": "JOB_NOT_FOUND",
    "node": "NODE_NOT_FOUND",
    "metrics for job": "METRICS_NOT_FOUND",
    "logs for job": "LOGS_NOT_FOUND",
    "not in retryable state": "JOB_NOT_RETRYABLE",
    "not cancellable": "JOB_NOT_CANCELLABLE",
    "syncthing not configured": "SYNCTHING_UNAVAILABLE",
    "syncthing": "SYNCTHING_UNAVAILABLE",
    "database": "DATABASE_UNAVAILABLE",
}


def get_error_code(status_code: int, detail: str) -> str:
    """Determine the appropriate error code based on status code and detail message."""
    detail_lower = detail.lower()

    # Check for specific error patterns first
    for pattern, code in SPECIFIC_ERROR_CODES.items():
        if pattern in detail_lower:
            return code

    # Fall back to status code mapping
    return ERROR_CODE_MAP.get(status_code, "INTERNAL_ERROR")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    # Startup
    logger.info("Starting Scientific Home Cluster API")

    # Initialize database connection
    await init_database()

    # Run database migrations
    await run_migrations()

    # Initialize the in-memory store (singleton)
    get_store()
    logger.info("In-memory store initialized")

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
    error_code = get_error_code(exc.status_code, str(exc.detail))

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
            "url": (
                settings.DATABASE_URL.split("///")[-1]
                if "///" in settings.DATABASE_URL
                else "memory"
            ),
        },
    }
