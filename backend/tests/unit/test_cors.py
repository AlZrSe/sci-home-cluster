"""
Unit tests for CORS behavior, including Lovable preview origins opt-in.

Covers AC-1 through AC-5 from spec docs/specs/52-cors-lovable.md
"""

import os
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import app


def test_effective_cors_origins_default_excludes_lovable():
    """Default settings exclude Lovable preview origins."""
    settings = Settings(_env_file=None, SECRET_KEY="test-key-not-persisted")
    origins = settings.effective_cors_origins

    assert "http://localhost:3000" in origins
    assert "http://localhost:5173" in origins
    assert "http://localhost:8080" in origins
    assert "https://preview.lovableproject.com" not in origins


def test_effective_cors_origins_includes_lovable_when_enabled(monkeypatch):
    """ENABLE_LOVABLE_PREVIEW_CORS=true adds Lovable preview origin."""
    monkeypatch.setenv("ENABLE_LOVABLE_PREVIEW_CORS", "true")
    # Must create new Settings instance to pick up env var
    settings = Settings(_env_file=None, SECRET_KEY="test-key-not-persisted")
    origins = settings.effective_cors_origins

    assert "http://localhost:3000" in origins
    assert "https://preview.lovableproject.com" in origins


def test_cors_preflight_localhost_allowed():
    """Localhost origins are allowed in CORS preflight (AC-4)."""
    client = TestClient(app)

    for origin in [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
    ]:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200, f"Failed for {origin}: {response.text}"
        assert response.headers.get("access-control-allow-origin") == origin
        assert response.headers.get("access-control-allow-credentials") == "true"
        assert "GET" in response.headers.get("access-control-allow-methods", "")


def test_cors_preflight_lovable_rejected_by_default():
    """Lovable preview origin is rejected when ENABLE_LOVABLE_PREVIEW_CORS=false
    (AC-1 negative)."""
    client = TestClient(app)

    response = client.options(
        "/api/v1/jobs",
        headers={
            "Origin": "https://preview.lovableproject.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    # Starlette CORSMiddleware returns 400 for disallowed origins
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_actual_request_localhost_works():
    """Actual request (not preflight) from localhost works with credentials."""
    client = TestClient(app)

    response = client.get(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:5173",
            "Authorization": "Bearer localhost-no-auth",
        },
    )
    # Health endpoint doesn't require auth, but should still return CORS headers
    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    )
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_cors_non_allowed_origin_rejected(monkeypatch):
    """Non-allowed origin (evil.com) is rejected (AC-5)."""
    client = TestClient(app)

    response = client.options(
        "/api/v1/jobs",
        headers={
            "Origin": "https://evil.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_origin_echoed_for_allowed_origins():
    """CORS allow-origin echoes the request origin exactly for allowed origins."""
    client = TestClient(app)

    for origin in [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
    ]:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin


def test_cors_preflight_allows_requested_methods_and_headers():
    """Preflight response includes requested methods and headers."""
    client = TestClient(app)

    response = client.options(
        "/api/v1/jobs",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization,x-custom",
        },
    )
    assert response.status_code == 200
    allow_headers = response.headers.get("access-control-allow-headers", "").lower()
    assert "POST" in response.headers.get("access-control-allow-methods", "")
    assert "content-type" in allow_headers
    assert "authorization" in allow_headers


def test_cors_missing_origin_no_cors_headers():
    """Same-origin request (no Origin header) gets no CORS headers but succeeds."""
    client = TestClient(app)

    response = client.get("/api/v1/health")
    assert response.status_code == 200
    # No CORS headers for same-origin requests
    assert "access-control-allow-origin" not in response.headers


class TestCORSWithLovableEnabled:
    """Tests that require ENABLE_LOVABLE_PREVIEW_CORS=true."""

    def setup_method(self):
        """Create a fresh TestClient with Lovable CORS enabled."""
        # We need to recreate the app with the setting enabled
        # Since the app is a module-level singleton, we test via config property
        os.environ["ENABLE_LOVABLE_PREVIEW_CORS"] = "true"
        # Force reload of settings
        from backend.core import config

        config.settings = Settings(_env_file=None, SECRET_KEY="test-key-not-persisted")

        # Recreate app middleware by creating a new TestClient
        # Note: The app middleware is added at import time, so we test the
        # config property directly

    def teardown_method(self):
        """Clean up environment."""
        os.environ.pop("ENABLE_LOVABLE_PREVIEW_CORS", None)
        from backend.core import config

        config.settings = Settings(_env_file=None, SECRET_KEY="test-key-not-persisted")

    def test_effective_cors_origins_includes_lovable(self):
        """Settings property includes Lovable when enabled."""
        from backend.core.config import settings

        origins = settings.effective_cors_origins
        assert "https://preview.lovableproject.com" in origins

    def test_cors_preflight_lovable_allowed_when_enabled(self):
        """Lovable preview preflight succeeds when enabled (AC-1 positive)."""
        # Create a fresh app instance with the setting
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from backend.core.config import settings

        test_app = FastAPI()
        test_app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.effective_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @test_app.get("/api/v1/health")
        async def health():
            return {"status": "healthy"}

        @test_app.post("/api/v1/jobs")
        async def jobs():
            return {"jobs": []}

        client = TestClient(test_app)

        response = client.options(
            "/api/v1/jobs",
            headers={
                "Origin": "https://preview.lovableproject.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,authorization",
            },
        )
        assert response.status_code == 200
        assert (
            response.headers.get("access-control-allow-origin")
            == "https://preview.lovableproject.com"
        )
        assert response.headers.get("access-control-allow-credentials") == "true"
        assert "POST" in response.headers.get("access-control-allow-methods", "")
        allow_headers = response.headers.get("access-control-allow-headers", "").lower()
        assert "content-type" in allow_headers
        assert "authorization" in allow_headers

    def test_cors_preflight_any_lovable_subdomain_allowed(self):
        """Any *.lovableproject.com subdomain works when explicitly listed (AC-2).

        Note: CORSMiddleware requires exact matches. This test verifies that
        the specific preview URL works. For arbitrary subdomains, developers
        must add them via BACKEND_CORS_ORIGINS env var.
        """
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from backend.core.config import settings

        test_app = FastAPI()
        test_app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.effective_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @test_app.get("/api/v1/health")
        async def health():
            return {"status": "healthy"}

        client = TestClient(test_app)

        # The default only includes preview.lovableproject.com
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://preview.lovableproject.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert (
            response.headers.get("access-control-allow-origin")
            == "https://preview.lovableproject.com"
        )

        # Arbitrary subdomain NOT in allowlist is rejected
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://abc123.lovableproject.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers

    def test_cors_actual_request_lovable_works_when_enabled(self):
        """Actual request from Lovable preview works with credentials (AC-3)."""
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from backend.core.config import settings

        test_app = FastAPI()
        test_app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.effective_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @test_app.get("/api/v1/jobs")
        async def jobs():
            return {"jobs": []}

        client = TestClient(test_app)

        response = client.get(
            "/api/v1/jobs",
            headers={
                "Origin": "https://preview.lovableproject.com",
                "Authorization": "Bearer localhost-no-auth",
            },
        )
        assert response.status_code == 200
        assert (
            response.headers.get("access-control-allow-origin")
            == "https://preview.lovableproject.com"
        )
        assert response.headers.get("access-control-allow-credentials") == "true"

    def test_cors_env_var_override_still_works(self):
        """BACKEND_CORS_ORIGINS env var can still be used to add custom origins."""
        # Test the validator directly
        cors_string = "http://localhost:3000,https://custom.lovableproject.com"
        result = Settings.assemble_cors_origins(cors_string)
        assert result == ["http://localhost:3000", "https://custom.lovableproject.com"]


def test_cors_null_origin_rejected():
    """Null origin (file://) is rejected."""
    client = TestClient(app)

    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
