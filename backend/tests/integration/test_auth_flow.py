"""
Integration tests for authentication flow: token validation, creation, refresh.
"""

import pytest
from typing import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.core.security import create_access_token
from backend.core.config import settings
from datetime import timedelta
from unittest.mock import patch


@pytest.mark.integration
class TestAuthFlow:
    """Integration tests for authentication endpoints."""

    @pytest.fixture
    async def async_client(self) -> AsyncGenerator[AsyncClient, None]:
        """Create an async client for testing."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",  # type: ignore[arg-type]
        ) as client:
            yield client

    @pytest.fixture
    async def auth_client(self, async_client: AsyncClient) -> AsyncClient:
        """Create a client with shared token configured."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            # Get a JWT token first
            response = await async_client.post(
                "/api/v1/auth/token",
                json={"shared_token": "test-shared-token-123"},
            )
            assert response.status_code == 200
            token = response.json()["access_token"]
            async_client.headers["Authorization"] = f"Bearer {token}"
            yield async_client

    @pytest.mark.asyncio
    async def test_validate_token_with_shared_token(self, async_client):
        """Test token validation endpoint with valid shared token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/validate", json={"token": "test-shared-token-123"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_token_with_invalid_shared_token(self, async_client):
        """Test token validation endpoint with invalid shared token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/validate", json={"token": "invalid-token"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    @pytest.mark.asyncio
    async def test_validate_token_too_short(self, async_client):
        """Test token validation rejects too-short tokens."""
        response = await async_client.post(
            "/api/v1/auth/validate", json={"token": "short"}
        )

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_validate_token_with_jwt(self, async_client):
        """Test token validation with valid JWT."""
        with patch.object(settings, "SHARED_TOKEN", None):
            # Create a valid JWT
            token = create_access_token({"sub": "test-user"})
            response = await async_client.post(
                "/api/v1/auth/validate", json={"token": token}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_token_with_expired_jwt(self, async_client):
        """Test token validation with expired JWT."""
        with patch.object(settings, "SHARED_TOKEN", None):
            # Create an expired JWT
            token = create_access_token(
                {"sub": "test-user"}, expires_delta=timedelta(seconds=-1)
            )
            response = await async_client.post(
                "/api/v1/auth/validate", json={"token": token}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    @pytest.mark.asyncio
    async def test_create_token_success(self, async_client):
        """Test creating JWT token with valid shared token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "test-shared-token-123"}
            )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data
        assert data["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

        # Verify token is valid JWT
        from backend.core.security import decode_access_token

        payload = decode_access_token(data["access_token"])
        assert payload["sub"] == "user"  # Default subject from auth_service

    @pytest.mark.asyncio
    async def test_create_token_invalid_shared_token(self, async_client):
        """Test creating JWT token with invalid shared token fails."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "wrong-token"}
            )

        assert response.status_code == 401
        data = response.json()
        assert data["status"] == 401
        assert data["title"] == "Unauthorized"
        assert "invalid" in data["detail"].lower()
        assert "error_code" in data

    @pytest.mark.asyncio
    async def test_create_token_no_shared_configured(self, async_client):
        """Test creating JWT token when shared token not configured."""
        with patch.object(settings, "SHARED_TOKEN", None):
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "any-token"}
            )

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == 503
        assert "not configured" in data["detail"].lower()
        assert "error_code" in data

    @pytest.mark.asyncio
    async def test_create_token_missing_shared_token_field(self, async_client):
        """Test creating JWT token with missing shared_token field."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post("/api/v1/auth/token", json={})

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, async_client):
        """Test refreshing a valid JWT token."""
        with patch.object(settings, "SHARED_TOKEN", None):
            # Create a token
            original_token = create_access_token({"sub": "test-user"})
            response = await async_client.post(
                "/api/v1/auth/refresh", json={"token": original_token}
            )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data

        # Verify new token is valid
        from backend.core.security import decode_access_token

        payload = decode_access_token(data["access_token"])
        assert payload["sub"] == "test-user"

    @pytest.mark.asyncio
    async def test_refresh_token_invalid(self, async_client):
        """Test refreshing an invalid token fails."""
        with patch.object(settings, "SHARED_TOKEN", None):
            response = await async_client.post(
                "/api/v1/auth/refresh", json={"token": "invalid-token"}
            )

        assert response.status_code == 401
        data = response.json()
        assert data["status"] == 401
        assert data["title"] == "Unauthorized"

    @pytest.mark.asyncio
    async def test_refresh_token_expired(self, async_client):
        """Test refreshing an expired token fails."""
        with patch.object(settings, "SHARED_TOKEN", None):
            expired_token = create_access_token(
                {"sub": "test-user"}, expires_delta=timedelta(seconds=-1)
            )
            response = await async_client.post(
                "/api/v1/auth/refresh", json={"token": expired_token}
            )

        assert response.status_code == 401
        data = response.json()
        assert data["status"] == 401

    @pytest.mark.asyncio
    async def test_refresh_token_missing_field(self, async_client):
        """Test refreshing with missing token field."""
        with patch.object(settings, "SHARED_TOKEN", None):
            response = await async_client.post("/api/v1/auth/refresh", json={})

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_full_auth_flow(self, async_client):
        """Test complete auth flow: create token -> validate -> refresh -> validate."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            # 1. Create token
            create_response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "test-shared-token-123"}
            )
            assert create_response.status_code == 200
            token = create_response.json()["access_token"]

            # 2. Validate created token
            validate_response = await async_client.post(
                "/api/v1/auth/validate", json={"token": token}
            )
            assert validate_response.status_code == 200
            assert validate_response.json()["valid"] is True

            # 3. Refresh token
            refresh_response = await async_client.post(
                "/api/v1/auth/refresh", json={"token": token}
            )
            assert refresh_response.status_code == 200
            new_token = refresh_response.json()["access_token"]

            # 4. Validate refreshed token
            validate_response2 = await async_client.post(
                "/api/v1/auth/validate", json={"token": new_token}
            )
            assert validate_response2.status_code == 200
            assert validate_response2.json()["valid"] is True

    @pytest.mark.asyncio
    async def test_using_jwt_for_protected_endpoints(self, async_client):
        """Test using JWT token to access protected endpoints."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            # Create token
            create_response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "test-shared-token-123"}
            )
            token = create_response.json()["access_token"]

        # Use token to access protected endpoint
        async_client.headers["Authorization"] = f"Bearer {token}"
        response = await async_client.get("/api/v1/nodes/")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_endpoints_require_auth_without_localhost_bypass(self, async_client):
        """Test that endpoints require auth when not on localhost."""
        # This test verifies the endpoint structure - actual auth bypass
        # is tested via the TestClient which simulates localhost
        # Here we just ensure endpoints exist and return proper structure

        # These should not return 404 (router not included)
        endpoints = [
            "/api/v1/auth/validate",
            "/api/v1/auth/token",
            "/api/v1/auth/refresh",
            "/api/v1/jobs/",
            "/api/v1/nodes/",
            # Syncthing endpoints skipped
            # (require app.state.syncthing_service from lifespan)
        ]

        for endpoint in endpoints:
            # Use POST for auth endpoints that need body
            if "auth" in endpoint:
                if "validate" in endpoint:
                    response = await async_client.post(endpoint, json={"token": "test"})
                elif "token" in endpoint and "refresh" not in endpoint:
                    response = await async_client.post(
                        endpoint, json={"shared_token": "test"}
                    )
                else:
                    response = await async_client.post(endpoint, json={"token": "test"})
            else:
                response = await async_client.get(endpoint)

            # Should not be 404 (router not found)
            assert response.status_code != 404, f"Endpoint {endpoint} returned 404"


@pytest.mark.integration
class TestAuthEdgeCases:
    """Tests for authentication edge cases and error handling."""

    @pytest.fixture
    async def async_client(self) -> AsyncGenerator[AsyncClient, None]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client

    @pytest.mark.asyncio
    async def test_validate_token_empty_body(self, async_client):
        """Test validate with empty body."""
        response = await async_client.post("/api/v1/auth/validate", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_validate_token_null_token(self, async_client):
        """Test validate with null token."""
        response = await async_client.post(
            "/api/v1/auth/validate", json={"token": None}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_token_empty_shared_token(self, async_client):
        """Test create token with empty shared_token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": ""}
            )
        assert response.status_code == 422  # Validation error for min_length

    @pytest.mark.asyncio
    async def test_refresh_token_empty_body(self, async_client):
        """Test refresh with empty body."""
        response = await async_client.post("/api/v1/auth/refresh", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_token_payload_structure(self, async_client):
        """Test that token response has correct structure."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "test-shared-token-123"}
            )

        assert response.status_code == 200
        data = response.json()

        # Required fields per TokenCreateResponse model
        assert "access_token" in data
        assert "token_type" in data
        assert "expires_in" in data

        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0
        assert data["token_type"] == "bearer"
        assert isinstance(data["expires_in"], int)
        assert data["expires_in"] > 0

    @pytest.mark.asyncio
    async def test_validation_response_structure(self, async_client):
        """Test that validation response has correct structure."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.post(
                "/api/v1/auth/validate", json={"token": "test-shared-token-123"}
            )

        assert response.status_code == 200
        data = response.json()

        assert "valid" in data
        assert isinstance(data["valid"], bool)

    @pytest.mark.asyncio
    async def test_refresh_response_structure(self, async_client):
        """Test that refresh response has correct structure."""
        with patch.object(settings, "SHARED_TOKEN", None):
            token = create_access_token({"sub": "test-user"})
            response = await async_client.post(
                "/api/v1/auth/refresh", json={"token": token}
            )

        assert response.status_code == 200
        data = response.json()

        assert "access_token" in data
        assert "token_type" in data
        assert "expires_in" in data
        assert data["token_type"] == "bearer"


@pytest.mark.integration
class TestAuthVerifyGet:
    """Integration tests for GET /auth/verify endpoint (Bearer header)."""

    @pytest.fixture
    async def async_client(self) -> AsyncGenerator[AsyncClient, None]:
        """Create an async client for testing."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client

    @pytest.mark.asyncio
    async def test_verify_token_get_valid_shared_token(self, async_client):
        """Test GET /auth/verify with valid shared token via Bearer header."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.get(
                "/api/v1/auth/verify",
                headers={"Authorization": "Bearer test-shared-token-123"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True

    @pytest.mark.asyncio
    async def test_verify_token_get_invalid_shared_token(self, async_client):
        """Test GET /auth/verify with invalid shared token via Bearer header."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = await async_client.get(
                "/api/v1/auth/verify",
                headers={"Authorization": "Bearer invalid-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    @pytest.mark.asyncio
    async def test_verify_token_get_missing_header(self, async_client):
        """Test GET /auth/verify without Authorization header."""
        response = await async_client.get("/api/v1/auth/verify")

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    @pytest.mark.asyncio
    async def test_verify_token_get_malformed_header(self, async_client):
        """Test GET /auth/verify with malformed Authorization header."""
        response = await async_client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": "NotBearer token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    @pytest.mark.asyncio
    async def test_verify_token_get_with_jwt(self, async_client):
        """Test GET /auth/verify with valid JWT via Bearer header."""
        with patch.object(settings, "SHARED_TOKEN", None):
            token = create_access_token({"sub": "test-user"})
            response = await async_client.get(
                "/api/v1/auth/verify",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True

    @pytest.mark.asyncio
    async def test_verify_token_get_with_expired_jwt(self, async_client):
        """Test GET /auth/verify with expired JWT via Bearer header."""
        with patch.object(settings, "SHARED_TOKEN", None):
            expired_token = create_access_token(
                {"sub": "test-user"}, expires_delta=timedelta(seconds=-1)
            )
            response = await async_client.get(
                "/api/v1/auth/verify",
                headers={"Authorization": f"Bearer {expired_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    @pytest.mark.asyncio
    async def test_verify_vs_validate_consistency(self, async_client):
        """Test GET /verify and POST /validate return same result for same token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            # Test GET /verify
            get_response = await async_client.get(
                "/api/v1/auth/verify",
                headers={"Authorization": "Bearer test-shared-token-123"},
            )

            # Test POST /validate
            post_response = await async_client.post(
                "/api/v1/auth/validate", json={"token": "test-shared-token-123"}
            )

            assert get_response.status_code == 200
            assert post_response.status_code == 200
            assert get_response.json()["valid"] == post_response.json()["valid"]
