"""
Main entry point for the Scientific Home Cluster Backend API.
"""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.core.config import settings
from backend.api.v1 import auth, nodes, syncthing
from backend.routers import jobs
from backend.models.error_response import ErrorResponse
from backend.store.memory import get_store
from backend.services.syncthing_service import SyncthingService

# Configure logging
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    # Startup
    logger.info("Starting Scientific Home Cluster API")

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

    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log incoming requests with method, path, status, and duration."""
    start_time = time.perf_counter()

    # Process request
    response = await call_next(request)

    # Calculate duration
    duration_ms = (time.perf_counter() - start_time) * 1000

    # Log request details
    logger.info(
        f"{request.method} {request.url.path} - "
        f"Status: {response.status_code} - "
        f"Duration: {duration_ms:.2f}ms"
    )

    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Convert HTTPException to ErrorResponse format."""
    # Determine title based on status code
    if exc.status_code == 401:
        title = "Unauthorized"
    elif exc.status_code == 404:
        title = "Not Found"
    else:
        title = "Error"

    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            status=exc.status_code,
            title=title,
            detail=exc.detail,
            instance=str(request.url),
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
app.include_router(syncthing.router, prefix=f"{settings.API_V1_STR}/syncthing", tags=["syncthing"])


@app.get("/")
async def root():
    return {"message": "Welcome to Scientific Home Cluster API"}


@app.get(f"{settings.API_V1_STR}/health")
async def health_check():
    """Enhanced health check with store, config, and syncthing status."""
    store = get_store()

    # Check store status
    nodes_count = len(await store.list_nodes())
    jobs_count, _ = await store.list_jobs()

    store_status = "healthy" if nodes_count > 0 else "degraded"

    # Check config status
    config_status = "healthy"
    if not settings.SYNCTHING_ROOT:
        config_status = "degraded"

    # Check Syncthing status (placeholder for Task 3)
    syncthing_status = "not_configured"
    syncthing_path = Path(settings.SYNCTHING_ROOT)
    if syncthing_path.exists():
        syncthing_status = "available"

    # Overall status: healthy if store is healthy (API is functional)
    # Syncthing is a separate service, not required for API health
    overall_status = "healthy" if store_status == "healthy" else "degraded"

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
    }
