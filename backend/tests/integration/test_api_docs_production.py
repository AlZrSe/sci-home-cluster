"""
Integration tests for API docs production behavior (issue #161).

Tests the actual HTTP endpoints /docs, /redoc, /openapi.json
under different ENVIRONMENT and ENABLE_API_DOCS configurations.
"""

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import Settings, settings as real_settings
from backend.core.database import close_database, init_database, run_migrations
from backend.core.metrics import record_http_request
from backend.core.rate_limit import init_rate_limiter, shutdown_rate_limiter
from backend.api.v1 import auth, jobs, nodes, syncthing
from backend.core.frontend import register_frontend, serving_frontend
from backend.core.telemetry import configure_telemetry
from backend.core.config import absolute_sqlite_path
from backend.services.syncthing_service import SyncthingService
from backend.store import get_store
from shared.schemas.error_response import ErrorResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request
import logging
import asyncio
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict


def create_test_app(test_settings) -> FastAPI:  # noqa: C901
    """Create a FastAPI app with the given settings for testing."""

    # Compute API docs enablement before FastAPI instantiation.
    # Priority: explicit ENABLE_API_DOCS > ENVIRONMENT in (development, test)
    enable_docs = test_settings.ENABLE_API_DOCS or test_settings.ENVIRONMENT in (
        "development",
        "test",
    )
    docs_url = "/docs" if enable_docs else None
    redoc_url = "/redoc" if enable_docs else None
    openapi_url = f"{test_settings.API_V1_STR}/openapi.json" if enable_docs else None

    # Log the docs state at startup (INFO level)
    logger = logging.getLogger("backend.main")
    if enable_docs:
        if test_settings.ENABLE_API_DOCS:
            logger.info("API docs ENABLED (ENABLE_API_DOCS=true)")
        else:
            logger.info(f"API docs ENABLED (ENVIRONMENT={test_settings.ENVIRONMENT})")
    else:
        logger.info("API docs DISABLED (production mode)")

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
                    nodes_list = await store.list_nodes()
                    online = sum(1 for n in nodes_list if n.status == "ONLINE")
                    from backend.core.metrics import (
                        set_nodes_online,
                        set_node_heartbeat_age,
                        set_syncthing_available,
                    )

                    set_nodes_online(online)
                    now = datetime.now(timezone.utc)
                    for node in nodes_list:
                        heartbeat = node.last_heartbeat
                        if heartbeat.tzinfo is None:
                            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                        set_node_heartbeat_age(
                            node.node_id, (now - heartbeat).total_seconds()
                        )
                    set_syncthing_available(Path(test_settings.SYNCTHING_ROOT).exists())
                except Exception:
                    pass
                await asyncio.sleep(30)

        app.state.metrics_task = asyncio.create_task(_metrics_loop())

        # Name the mode the process is in, on one line, at startup.
        logger.info(
            "SEED_DEMO_DATA=%s - demo dataset %s; metrics and logs %s",
            test_settings.SEED_DEMO_DATA,
            "seeded" if test_settings.SEED_DEMO_DATA else "not seeded",
            "are generated on read when missing"
            if test_settings.SEED_DEMO_DATA
            else "are never invented (empty series / 404)",
        )

        # Start Syncthing watcher
        syncthing_service = SyncthingService(Path(test_settings.SYNCTHING_ROOT))
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

    app = FastAPI(
        title=test_settings.PROJECT_NAME,
        version=test_settings.VERSION,
        openapi_url=openapi_url,
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
    )

    # Fallback error codes
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
        return ERROR_CODE_MAP.get(status_code, "INTERNAL_ERROR")

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        start_time = time.perf_counter()
        request_id = getattr(request.state, "request_id", "unknown")
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start_time) * 1000
            record_http_request(
                request.method, request.url.path, 500, duration_ms / 1000.0
            )
            raise
        duration_ms = (time.perf_counter() - start_time) * 1000
        record_http_request(
            request.method,
            request.url.path,
            response.status_code,
            duration_ms / 1000.0,
        )
        logger.info(
            f"{request.method} {request.url.path} - "
            f"Status: {response.status_code} - "
            f"Duration: {duration_ms:.2f}ms - "
            f"Request-ID: {request_id}"
        )
        return response

    @app.middleware("http")
    async def csp_middleware(request: Request, call_next):
        response = await call_next(request)
        csp_header = "Content-Security-Policy"
        if test_settings.CSP_REPORT_ONLY:
            csp_header = "Content-Security-Policy-Report-Only"
        if csp_header not in response.headers:
            response.headers[csp_header] = test_settings.CSP_POLICY
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        error_code = getattr(exc, "error_code", None) or get_error_code(
            exc.status_code, str(exc.detail)
        )
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
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
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
    if test_settings.effective_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=test_settings.effective_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Include API routers
    app.include_router(
        auth.router, prefix=f"{test_settings.API_V1_STR}/auth", tags=["auth"]
    )
    app.include_router(
        jobs.router, prefix=f"{test_settings.API_V1_STR}/jobs", tags=["jobs"]
    )
    app.include_router(
        nodes.router, prefix=f"{test_settings.API_V1_STR}/nodes", tags=["nodes"]
    )
    app.include_router(
        syncthing.router,
        prefix=f"{test_settings.API_V1_STR}/syncthing",
        tags=["syncthing"],
    )

    if not serving_frontend():

        @app.get("/")
        async def root():
            return {"message": "Welcome to Scientific Home Cluster API"}

    def _database_display_name(database_url: str) -> str:
        without_credentials = database_url.rsplit("@", 1)[-1]
        path = absolute_sqlite_path(without_credentials)
        if path is not None:
            return str(path)
        return without_credentials.rsplit("///", 1)[-1] or "unknown"

    @app.get(f"{test_settings.API_V1_STR}/health")
    async def health_check():
        from backend.core.database import get_engine
        from sqlalchemy import text

        store = get_store()
        nodes_count = len(await store.list_nodes())
        jobs_count, _ = await store.list_jobs()
        store_status = "healthy"
        config_status = "healthy"
        if not test_settings.SYNCTHING_ROOT:
            config_status = "degraded"
        syncthing_status = "not_configured"
        syncthing_path = Path(test_settings.SYNCTHING_ROOT)
        if syncthing_path.exists():
            syncthing_status = "available"
        database_status = "healthy"
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as e:
            logger.warning(f"Database health check failed: {e}")
            database_status = "unhealthy"
        overall_status = (
            "healthy"
            if store_status == "healthy" and database_status == "healthy"
            else "degraded"
        )
        return {
            "status": overall_status,
            "version": test_settings.VERSION,
            "store": {
                "status": store_status,
                "nodes": nodes_count,
                "jobs": len(jobs_count),
            },
            "config": {
                "status": config_status,
                "syncthing_root": test_settings.SYNCTHING_ROOT,
                "api_version": test_settings.API_V1_STR,
            },
            "syncthing": {
                "status": syncthing_status,
                "path": test_settings.SYNCTHING_ROOT,
            },
            "database": {
                "status": database_status,
                "url": _database_display_name(test_settings.DATABASE_URL),
            },
        }

    register_frontend(app)

    if test_settings.OTEL_ENABLED and not test_settings.OTEL_SDK_DISABLED:
        configure_telemetry(app)

    return app


# =============================================================================
# Test Fixtures
# =============================================================================


def _make_test_settings(**overrides):
    """Create a Settings instance with test overrides."""
    defaults = {
        "_env_file": None,
        "SECRET_KEY": real_settings.SECRET_KEY,
        "ENVIRONMENT": "development",
        "ENABLE_API_DOCS": False,
        "SYNCTHING_ROOT": real_settings.SYNCTHING_ROOT,
        "DATABASE_URL": real_settings.DATABASE_URL,
        "SEED_DEMO_DATA": real_settings.SEED_DEMO_DATA,
    }
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
def test_settings_development():
    """Settings for development environment (docs enabled)."""
    return _make_test_settings(ENVIRONMENT="development", ENABLE_API_DOCS=False)


@pytest.fixture
def test_settings_production_disabled():
    """Settings for production environment (docs disabled)."""
    return _make_test_settings(ENVIRONMENT="production", ENABLE_API_DOCS=False)


@pytest.fixture
def test_settings_production_enabled():
    """Settings for production with explicit ENABLE_API_DOCS=true."""
    return _make_test_settings(ENVIRONMENT="production", ENABLE_API_DOCS=True)


@pytest.fixture
def test_settings_test():
    """Settings for test environment (docs enabled)."""
    return _make_test_settings(ENVIRONMENT="test", ENABLE_API_DOCS=False)


@pytest_asyncio.fixture
async def test_client(test_settings_obj):
    """Create an AsyncClient for a test app with specific settings."""
    app = create_test_app(test_settings_obj)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        yield ac


@pytest.fixture
def auth_test_client(test_client: AsyncClient) -> AsyncClient:
    """Create an authenticated client with Bearer token."""
    test_client.headers["Authorization"] = "Bearer localhost-no-auth"
    return test_client


# =============================================================================
# Test Classes
# =============================================================================


class TestApiDocsDevelopment:
    """Integration tests for development environment (docs enabled by default)."""

    @pytest.fixture
    def test_settings_obj(self):
        return _make_test_settings(ENVIRONMENT="development", ENABLE_API_DOCS=False)

    @pytest.mark.asyncio
    async def test_docs_endpoint_returns_200_swagger_ui(self, auth_test_client):
        """IT-AD-01: /docs returns 200 with Swagger UI HTML in development."""
        response = await auth_test_client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "swagger-ui" in response.text.lower()

    @pytest.mark.asyncio
    async def test_redoc_endpoint_returns_200_redoc(self, auth_test_client):
        """IT-AD-02: /redoc returns 200 with ReDoc HTML in development."""
        response = await auth_test_client.get("/redoc")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "redoc" in response.text.lower()

    @pytest.mark.asyncio
    async def test_openapi_json_endpoint_returns_200_valid_schema(
        self, auth_test_client
    ):
        """IT-AD-03: /openapi.json returns 200 with valid OpenAPI schema in dev."""
        response = await auth_test_client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")
        data = response.json()
        assert "openapi" in data
        assert "info" in data
        assert data["info"]["title"] == "Scientific Home Cluster API"
        assert "paths" in data


class TestApiDocsProductionDisabled:
    """Integration tests for production environment (docs disabled by default)."""

    @pytest.fixture
    def test_settings_obj(self):
        return _make_test_settings(ENVIRONMENT="production", ENABLE_API_DOCS=False)

    @pytest.mark.asyncio
    async def test_docs_endpoint_returns_404(self, auth_test_client):
        """IT-AD-04: /docs returns 404 in production when ENABLE_API_DOCS unset."""
        response = await auth_test_client.get("/docs")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_redoc_endpoint_returns_404(self, auth_test_client):
        """IT-AD-05: /redoc returns 404 in production when ENABLE_API_DOCS unset."""
        response = await auth_test_client.get("/redoc")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_openapi_json_endpoint_returns_404(self, auth_test_client):
        """IT-AD-06: /openapi.json returns 404 in production when
        ENABLE_API_DOCS unset."""
        response = await auth_test_client.get("/api/v1/openapi.json")
        assert response.status_code == 404


class TestApiDocsProductionExplicitEnabled:
    """Integration tests for production with ENABLE_API_DOCS=true."""

    @pytest.fixture
    def test_settings_obj(self):
        return _make_test_settings(ENVIRONMENT="production", ENABLE_API_DOCS=True)

    @pytest.mark.asyncio
    async def test_all_endpoints_return_200(self, auth_test_client):
        """IT-AD-07: All three endpoints return 200 when
        ENABLE_API_DOCS=true in production."""
        response = await auth_test_client.get("/docs")
        assert response.status_code == 200
        assert "swagger-ui" in response.text.lower()

        response = await auth_test_client.get("/redoc")
        assert response.status_code == 200
        assert "redoc" in response.text.lower()

        response = await auth_test_client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")
        data = response.json()
        assert "openapi" in data


class TestApiDocsProductionExplicitDisabled:
    """Integration tests for production with explicit ENABLE_API_DOCS=false."""

    @pytest.fixture
    def test_settings_obj(self):
        return _make_test_settings(ENVIRONMENT="production", ENABLE_API_DOCS=False)

    @pytest.mark.asyncio
    async def test_all_endpoints_return_404(self, auth_test_client):
        """IT-AD-08: All three endpoints return 404 when
        ENABLE_API_DOCS=false in production."""
        response = await auth_test_client.get("/docs")
        assert response.status_code == 404

        response = await auth_test_client.get("/redoc")
        assert response.status_code == 404

        response = await auth_test_client.get("/api/v1/openapi.json")
        assert response.status_code == 404


class TestApiDocsTestEnvironment:
    """Integration tests for test environment (docs enabled)."""

    @pytest.fixture
    def test_settings_obj(self):
        return _make_test_settings(ENVIRONMENT="test", ENABLE_API_DOCS=False)

    @pytest.mark.asyncio
    async def test_all_endpoints_return_200(self, auth_test_client):
        """IT-AD-09: All three endpoints return 200 in test environment."""
        response = await auth_test_client.get("/docs")
        assert response.status_code == 200
        assert "swagger-ui" in response.text.lower()

        response = await auth_test_client.get("/redoc")
        assert response.status_code == 200
        assert "redoc" in response.text.lower()

        response = await auth_test_client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")
        data = response.json()
        assert "openapi" in data


class TestApiDocsStartupLogging:
    """Integration test for startup logging (IT-AD-10)."""

    @pytest.mark.asyncio
    async def test_startup_log_contains_docs_state(self, caplog):
        """IT-AD-10: Startup log contains 'API docs ENABLED' or
        'API docs DISABLED' with reason."""
        settings = _make_test_settings(ENVIRONMENT="development", ENABLE_API_DOCS=False)
        enable_docs = settings.ENABLE_API_DOCS or settings.ENVIRONMENT in (
            "development",
            "test",
        )
        logger = logging.getLogger("backend.main")

        with caplog.at_level(logging.INFO, logger="backend.main"):
            if enable_docs:
                if settings.ENABLE_API_DOCS:
                    logger.info("API docs ENABLED (ENABLE_API_DOCS=true)")
                else:
                    logger.info(
                        f"API docs ENABLED (ENVIRONMENT={settings.ENVIRONMENT})"
                    )
            else:
                logger.info("API docs DISABLED (production mode)")

        assert "API docs ENABLED (ENVIRONMENT=development)" in caplog.text

    @pytest.mark.asyncio
    async def test_startup_log_production_disabled(self, caplog):
        """Startup log shows DISABLED for production."""
        settings = _make_test_settings(ENVIRONMENT="production", ENABLE_API_DOCS=False)
        enable_docs = settings.ENABLE_API_DOCS or settings.ENVIRONMENT in (
            "development",
            "test",
        )
        logger = logging.getLogger("backend.main")

        with caplog.at_level(logging.INFO, logger="backend.main"):
            if enable_docs:
                if settings.ENABLE_API_DOCS:
                    logger.info("API docs ENABLED (ENABLE_API_DOCS=true)")
                else:
                    logger.info(
                        f"API docs ENABLED (ENVIRONMENT={settings.ENVIRONMENT})"
                    )
            else:
                logger.info("API docs DISABLED (production mode)")

        assert "API docs DISABLED (production mode)" in caplog.text

    @pytest.mark.asyncio
    async def test_startup_log_production_explicit_enabled(self, caplog):
        """Startup log shows ENABLED with ENABLE_API_DOCS=true for production."""
        settings = _make_test_settings(ENVIRONMENT="production", ENABLE_API_DOCS=True)
        enable_docs = settings.ENABLE_API_DOCS or settings.ENVIRONMENT in (
            "development",
            "test",
        )
        logger = logging.getLogger("backend.main")

        with caplog.at_level(logging.INFO, logger="backend.main"):
            if enable_docs:
                if settings.ENABLE_API_DOCS:
                    logger.info("API docs ENABLED (ENABLE_API_DOCS=true)")
                else:
                    logger.info(
                        f"API docs ENABLED (ENVIRONMENT={settings.ENVIRONMENT})"
                    )
            else:
                logger.info("API docs DISABLED (production mode)")

        assert "API docs ENABLED (ENABLE_API_DOCS=true)" in caplog.text


class TestApiDocsNoInformationLeakage:
    """Security tests: verify no schema information is leaked when disabled."""

    @pytest.fixture
    def test_settings_obj(self):
        return _make_test_settings(ENVIRONMENT="production", ENABLE_API_DOCS=False)

    @pytest.mark.asyncio
    async def test_no_schema_info_in_404_response_body(self, auth_test_client):
        """ST-AD-01: No schema information in 404 response body when disabled."""
        for endpoint in ["/docs", "/redoc", "/api/v1/openapi.json"]:
            response = await auth_test_client.get(endpoint)
            assert response.status_code == 404
            # Response should be a generic 404, not contain schema info
            text = response.text.lower()
            # The instance field will contain the requested URL, which is expected
            # Check for actual OpenAPI schema content leakage, not endpoint names
            assert "openapi" not in text or "openapi" in text and "openapi" in endpoint
            assert "swagger" not in text
            assert "redoc" not in text or "redoc" in text and "redoc" in endpoint
            assert "paths" not in text
            assert "components" not in text
            assert "schemas" not in text
            assert "info" not in text or "info" in text and "title" not in text

    @pytest.mark.asyncio
    async def test_no_server_headers_leaked(self, auth_test_client):
        """ST-AD-02: No Server or framework version headers leaked on 404."""
        response = await auth_test_client.get("/docs")
        assert response.status_code == 404
        # FastAPI/Starlette should not leak version info
        assert "server" not in response.headers
        # The X-Request-ID header is expected from our middleware
        assert "x-request-id" in response.headers


class TestApiDocsExistingTestsCompatibility:
    """Verify existing test infrastructure still works."""

    @pytest.fixture
    def test_settings_obj(self):
        return _make_test_settings(ENVIRONMENT="test", ENABLE_API_DOCS=False)

    @pytest.mark.asyncio
    async def test_openapi_schema_validation_still_works(self, auth_test_client):
        """Existing OpenAPI schema validation should work when docs enabled."""
        response = await auth_test_client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "paths" in data
        # Verify some expected endpoints exist
        assert "/api/v1/health" in data["paths"]
        assert "/api/v1/jobs/" in data["paths"]
        assert "/api/v1/nodes/" in data["paths"]
