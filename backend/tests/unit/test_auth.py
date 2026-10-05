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


class TestLocalhostBypassHostHeader:
    """
    T18-T29: the bypass, exercised through the real dependency with a real
    `Host` header (issue #31, AC-9).

    `get_current_token_payload` reads `request.url.hostname`, which Starlette
    derives from the client-supplied `Host` header - so this is the only place
    the two spellings of IPv6 loopback can be told apart. A unit test on
    `is_localhost` is not enough (T3/T4): it can be satisfied by a list holding
    `"[::1]"`, which `deps.py` never passes, leaving the real request path
    broken while the whole suite is green.

    No `Authorization` header is sent anywhere in this class. A 200 therefore
    means the bypass fired and a 401 means it did not - there is no third
    outcome that could be mistaken for success.
    """

    # A protected endpoint with no side effects and no fixtures to arrange.
    ENDPOINT = "/api/v1/nodes/"

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from backend.main import app

        return TestClient(app)

    @staticmethod
    def _get(client, host_header):
        """GET a protected endpoint with `Host` set explicitly and no token."""
        return client.get(
            TestLocalhostBypassHostHeader.ENDPOINT,
            headers={"Host": host_header},
        )

    @pytest.mark.parametrize(
        ("host_header", "expected"),
        [
            ("localhost:8000", 200),  # T18
            ("127.0.0.1:8000", 200),  # T19
            ("[::1]:8000", 200),  # T20 - the bug; was 401. The fix.
            ("::1", 401),  # T21 - see the note below; SPEC CORRECTION
            ("0.0.0.0:8000", 200),  # T22
            ("testserver", 200),  # T23
            ("myhost.local", 200),  # T24
            ("preview.lovable.app", 200),  # T25
            ("preview.lovableproject.com", 401),  # T26
            ("localhost.evil.com", 401),  # T27
            ("evil-localhost.com", 401),  # T28
            ("notlocal", 401),  # T29
        ],
    )
    def test_host_header_decides_the_bypass(self, client, host_header, expected):
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            response = self._get(client, host_header)
        assert (
            response.status_code == expected
        ), f"Host: {host_header} -> {response.status_code}"

    def test_ipv6_loopback_reaches_the_same_payload_as_ipv4(self, client):
        """
        T20 together: proving `200` is not enough on its own. A bypass that
        fired for the wrong reason would still be a 200, so assert the
        bracketed loopback form resolves to the same payload `localhost` does.
        """
        with patch.object(settings, "LOCALHOST_BYPASS", True):
            localhost = self._get(client, "localhost:8000")
            bracketed = self._get(client, "[::1]:8000")

        assert localhost.status_code == bracketed.status_code == 200
        assert bracketed.json() == localhost.json()

    def test_unbracketed_ipv6_host_header_is_401_and_cannot_be_anything_else(
        self, client
    ):
        """
        T21 CORRECTION - issue #31 specified `Host: ::1` -> 200. It cannot be.

        Starlette builds the URL from the raw `Host` header and reads it back
        through `urlsplit`, which partitions an unbracketed netloc on the FIRST
        colon:

            urlsplit("http://::1/") -> netloc "::1", hostname None

        So `request.url.hostname` is `None` and `is_localhost(None)` trips its
        empty guard. The hostname is destroyed by URL parsing, upstream of the
        list, so no entry in the list - `::1`, `[::1]` or anything else - can
        change the outcome.

        It is also unreachable in practice: RFC 3986 s3.2.2 requires an IPv6
        literal in a URI authority to be bracketed, so a conforming client
        sends `Host: [::1]:8000` (T20), never `Host: ::1`. Asserted here so the
        401 is a recorded decision rather than a mystery, and so the note stays
        true if the URL layer ever changes.

        Note the shape this leaves, which is the whole point of the fix: the
        HEADER is bracketed, the HOSTNAME is not. `Host: [::1]:8000` ->
        `request.url.hostname == "::1"` -> the canonical entry added in
        `backend/core/utils.py`. A list holding the literal `"[::1]"` would
        therefore fail T20 while passing T4 - which is why T3 asserts the
        canonical bracket-free form and T20 asserts the end-to-end 200.

        The `Request` below is built from a hand-written scope rather than
        through the client so the CAUSE is asserted, not just the symptom. If
        a future Starlette stops partitioning on the first colon, this test
        fails and says why, instead of the 401 becoming a mystery.
        """
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": self.ENDPOINT,
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"::1")],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
        }
        assert Request(scope).url.hostname is None

        with patch.object(settings, "LOCALHOST_BYPASS", True):
            response = self._get(client, "::1")
        assert response.status_code == 401

    def test_bypass_does_not_fire_when_disabled(self, client):
        """
        The bypass is opt-out, not implicit: with LOCALHOST_BYPASS off a local
        host is no longer special and must fall back to requiring a token.
        """
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            response = self._get(client, "[::1]:8000")
        assert response.status_code == 401

    def test_bypass_off_accepts_a_valid_token_on_ipv6_loopback(self, client):
        """
        The assertion above is only meaningful if the endpoint really can
        succeed: with the bypass off, a real token over `Host: [::1]:8000` must
        still authenticate.
        """
        token = create_access_token({"sub": "test-user"})
        with patch.object(settings, "LOCALHOST_BYPASS", False):
            response = client.get(
                self.ENDPOINT,
                headers={"Host": "[::1]:8000", "Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
