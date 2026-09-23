"""
Unit tests for authentication service and endpoints.
"""

import pytest
from datetime import timedelta
from unittest.mock import patch

from backend.services.auth_service import AuthService
from backend.core.config import settings
from backend.core.security import create_access_token, decode_access_token


class TestAuthService:
    """Tests for AuthService."""

    @pytest.fixture
    def auth_service(self):
        """Create an AuthService instance."""
        return AuthService()

    @pytest.mark.asyncio
    async def test_validate_token_empty(self, auth_service):
        """Test validation of empty token."""
        assert await auth_service.validate_token("") is False
        assert await auth_service.validate_token(None) is False
        assert await auth_service.validate_token("short") is False

    @pytest.mark.asyncio
    async def test_validate_token_shared_token(self, auth_service):
        """Test validation against shared token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            # Valid shared token
            assert await auth_service.validate_token("test-shared-token-123") is True
            # Invalid token
            assert await auth_service.validate_token("invalid-token") is False

    @pytest.mark.asyncio
    async def test_validate_token_jwt(self, auth_service):
        """Test validation of valid JWT token."""
        with patch.object(settings, "SHARED_TOKEN", None):
            # Create a valid JWT
            token = create_access_token({"sub": "test-user"})
            assert await auth_service.validate_token(token) is True

    @pytest.mark.asyncio
    async def test_validate_token_expired_jwt(self, auth_service):
        """Test validation of expired JWT token."""
        with patch.object(settings, "SHARED_TOKEN", None):
            # Create an expired JWT
            token = create_access_token(
                {"sub": "test-user"}, expires_delta=timedelta(seconds=-1)
            )
            assert await auth_service.validate_token(token) is False

    @pytest.mark.asyncio
    async def test_create_token(self, auth_service):
        """Test creating a new JWT token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token"):
            token = await auth_service.create_token("test-user")
            assert token is not None
            assert isinstance(token, str)
            # Verify it can be decoded
            payload = decode_access_token(token)
            assert payload["sub"] == "test-user"

    @pytest.mark.asyncio
    async def test_refresh_token_valid(self, auth_service):
        """Test refreshing a valid token."""
        with patch.object(settings, "SHARED_TOKEN", None):
            original_token = create_access_token({"sub": "test-user"})
            new_token = await auth_service.refresh_token(original_token)
            assert new_token is not None
            # Verify new token is valid (may be same if created in same second)
            payload = decode_access_token(new_token)
            assert payload["sub"] == "test-user"

    @pytest.mark.asyncio
    async def test_refresh_token_invalid(self, auth_service):
        """Test refreshing an invalid token."""
        with patch.object(settings, "SHARED_TOKEN", None):
            new_token = await auth_service.refresh_token("invalid-token")
            assert new_token is None

    @pytest.mark.asyncio
    async def test_refresh_token_expired(self, auth_service):
        """Test refreshing an expired token."""
        with patch.object(settings, "SHARED_TOKEN", None):
            expired_token = create_access_token(
                {"sub": "test-user"}, expires_delta=timedelta(seconds=-1)
            )
            new_token = await auth_service.refresh_token(expired_token)
            assert new_token is None

    def test_get_shared_token(self, auth_service):
        """Test getting shared token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            assert auth_service.get_shared_token() == "test-shared-token-123"

    @pytest.mark.asyncio
    async def test_verify_shared_token(self, auth_service):
        """Test verifying shared token directly."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            assert (
                await auth_service.verify_shared_token("test-shared-token-123") is True
            )
            assert await auth_service.verify_shared_token("wrong-token") is False


class TestAuthAPI:
    """Tests for authentication API endpoints."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from backend.main import app

        return TestClient(app)

    def test_validate_token_endpoint_valid_shared(self, client):
        """Test POST /auth/validate with valid shared token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = client.post(
                "/api/v1/auth/validate", json={"token": "test-shared-token-123"}
            )
            assert response.status_code == 200
            assert response.json()["valid"] is True

    def test_validate_token_endpoint_invalid(self, client):
        """Test POST /auth/validate with invalid token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = client.post(
                "/api/v1/auth/validate", json={"token": "invalid-token"}
            )
            assert response.status_code == 200
            assert response.json()["valid"] is False

    def test_validate_token_endpoint_too_short(self, client):
        """Test POST /auth/validate with too short token."""
        response = client.post("/api/v1/auth/validate", json={"token": "short"})
        assert response.status_code == 422  # Validation error

    def test_create_token_endpoint_success(self, client):
        """Test POST /auth/token with valid shared token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = client.post(
                "/api/v1/auth/token", json={"shared_token": "test-shared-token-123"}
            )
            assert response.status_code == 200
            data = response.json()
            assert "access_token" in data
            assert data["token_type"] == "bearer"
            assert "expires_in" in data

    def test_create_token_endpoint_invalid_shared(self, client):
        """Test POST /auth/token with invalid shared token."""
        with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
            response = client.post(
                "/api/v1/auth/token", json={"shared_token": "wrong-token"}
            )
            assert response.status_code == 401

    def test_create_token_endpoint_no_shared_configured(self, client):
        """Test POST /auth/token when shared token not configured."""
        with patch.object(settings, "SHARED_TOKEN", None):
            response = client.post(
                "/api/v1/auth/token", json={"shared_token": "any-token"}
            )
            assert response.status_code == 503

    def test_refresh_token_endpoint_success(self, client):
        """Test POST /auth/refresh with valid token."""
        with patch.object(settings, "SHARED_TOKEN", None):
            # First create a token
            token = create_access_token({"sub": "test-user"})
            response = client.post("/api/v1/auth/refresh", json={"token": token})
            assert response.status_code == 200
            data = response.json()
            assert "access_token" in data
            assert data["token_type"] == "bearer"
            # Verify new token is valid (may be same if created in same second)
            payload = decode_access_token(data["access_token"])
            assert payload["sub"] == "test-user"

    def test_refresh_token_endpoint_invalid(self, client):
        """Test POST /auth/refresh with invalid token."""
        with patch.object(settings, "SHARED_TOKEN", None):
            response = client.post(
                "/api/v1/auth/refresh", json={"token": "invalid-token"}
            )
            assert response.status_code == 401

    def test_refresh_token_endpoint_expired(self, client):
        """Test POST /auth/refresh with expired token."""
        with patch.object(settings, "SHARED_TOKEN", None):
            expired_token = create_access_token(
                {"sub": "test-user"}, expires_delta=timedelta(seconds=-1)
            )
            response = client.post(
                "/api/v1/auth/refresh", json={"token": expired_token}
            )
            assert response.status_code == 401
