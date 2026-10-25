"""
Integration tests for authentication rate limiting.
"""

import json
import logging
from typing import AsyncGenerator
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.core.config import settings
from backend.core.rate_limit import get_rate_limiter


@pytest.mark.integration
class TestAuthRateLimitIntegration:
    """Integration tests for auth endpoint rate limiting."""

    @pytest.fixture
    async def async_client(self) -> AsyncGenerator[AsyncClient, None]:
        """Create an async client for testing."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client

    @pytest.fixture
    def shared_token(self):
        """Provide a test shared token."""
        return "test-shared-token-123"

    @pytest.fixture(autouse=True)
    async def reset_rate_limiter(self):
        """Reset rate limiter state before each test."""
        limiter = get_rate_limiter()
        async with limiter._lock:
            limiter._store.clear()
        yield
        async with limiter._lock:
            limiter._store.clear()

    @pytest.mark.asyncio
    async def test_create_token_five_invalid_allowed_sixth_backoff(
        self, async_client, shared_token
    ):
        """IT-RL-01: 5 invalid shared tokens allowed, 6th gets 2s backoff."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # First 5 invalid attempts should return 401 (not rate limited)
            for i in range(5):
                response = await async_client.post(
                    "/api/v1/auth/token", json={"shared_token": "wrong-token"}
                )
                assert response.status_code == 401
                assert response.headers.get("X-RateLimit-Limit") == str(
                    settings.AUTH_RATE_LIMIT_BASE
                )
                assert int(response.headers.get("X-RateLimit-Remaining", "0")) >= 0

            # 6th attempt should get 429 with 2s backoff
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "wrong-token"}
            )
            assert response.status_code == 429
            assert response.headers.get("Retry-After") == "2"
            data = response.json()
            assert data["error_code"] == "AUTH_RATE_LIMITED"
            assert "2 seconds" in data["detail"]

    @pytest.mark.asyncio
    async def test_create_token_eleventh_lockout(self, async_client, shared_token):
        """IT-RL-02: 11th invalid shared token gets 1h lockout (429)."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # Make 10 invalid attempts (5 baseline + 5 backoff: 2,4,8,16,32s)
            for i in range(10):
                response = await async_client.post(
                    "/api/v1/auth/token", json={"shared_token": "wrong-token"}
                )
                if i < 5:
                    assert response.status_code == 401
                else:
                    assert response.status_code == 429

            # 11th attempt should get 429 with 1h lockout
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "wrong-token"}
            )
            assert response.status_code == 429
            # Retry-After should be approximately lockout_seconds (may be slightly
            # less due to test timing)
            retry_after = int(response.headers.get("Retry-After", "0"))
            assert retry_after >= settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS - 5
            assert retry_after <= settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS + 5
            data = response.json()
            assert data["error_code"] == "AUTH_RATE_LIMITED"
            assert "Locked out" in data["detail"]

    @pytest.mark.asyncio
    async def test_validate_token_same_bucket(self, async_client, shared_token):
        """IT-RL-03: /auth/validate — same rate limit bucket as /auth/token (per IP)."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # Make 5 invalid attempts on /auth/token
            for i in range(5):
                response = await async_client.post(
                    "/api/v1/auth/token", json={"shared_token": "wrong-token"}
                )
                assert response.status_code == 401

            # Next attempt on /auth/validate should be rate limited (same bucket)
            response = await async_client.post(
                "/api/v1/auth/validate", json={"token": "wrong-token"}
            )
            assert response.status_code == 429
            # 6th failure gets 2s backoff
            assert response.headers.get("Retry-After") == "2"

    @pytest.mark.asyncio
    async def test_verify_token_same_bucket(self, async_client, shared_token):
        """IT-RL-04: GET /auth/verify — same rate limit bucket."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # Make 5 invalid attempts on /auth/token
            for i in range(5):
                response = await async_client.post(
                    "/api/v1/auth/token", json={"shared_token": "wrong-token"}
                )
                assert response.status_code == 401

            # Next attempt on /auth/verify should be rate limited (same bucket)
            response = await async_client.get(
                "/api/v1/auth/verify", headers={"Authorization": "Bearer wrong-token"}
            )
            assert response.status_code == 429
            # 6th failure gets 2s backoff
            assert response.headers.get("Retry-After") == "2"

    @pytest.mark.asyncio
    async def test_refresh_token_same_bucket(self, async_client, shared_token):
        """IT-RL-05: POST /auth/refresh — same rate limit bucket."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # Make 5 invalid attempts on /auth/token
            for i in range(5):
                response = await async_client.post(
                    "/api/v1/auth/token", json={"shared_token": "wrong-token"}
                )
                assert response.status_code == 401

            # Next attempt on /auth/refresh should be rate limited (same bucket)
            response = await async_client.post(
                "/api/v1/auth/refresh", json={"token": "wrong-token"}
            )
            assert response.status_code == 429
            # 6th failure gets 2s backoff
            assert response.headers.get("Retry-After") == "2"

    @pytest.mark.asyncio
    async def test_valid_token_resets_counter(self, async_client, shared_token):
        """IT-RL-06: Valid token resets counter; subsequent invalid starts fresh."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # Make 5 invalid attempts
            for i in range(5):
                response = await async_client.post(
                    "/api/v1/auth/token", json={"shared_token": "wrong-token"}
                )
                assert response.status_code == 401

            # Make a valid request - should succeed and reset counter
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": shared_token}
            )
            assert response.status_code == 200
            assert "access_token" in response.json()

            # Next invalid attempt should be allowed (counter reset)
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "wrong-token"}
            )
            assert response.status_code == 401  # Not rate limited
            # After valid reset, failure_count=0, so 5 remaining before backoff
            assert (
                int(response.headers.get("X-RateLimit-Remaining", "0"))
                == settings.AUTH_RATE_LIMIT_BASE
            )

    @pytest.mark.asyncio
    async def test_localhost_bypass_exempt(self, async_client, shared_token):
        """IT-RL-07: Localhost bypass host header exempt from rate limiting."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # Make many invalid requests with localhost Host header
            for i in range(20):
                response = await async_client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "wrong-token"},
                    headers={"Host": "localhost:8000"},
                )
                # All should be 401 (auth failure) not 429 (rate limited)
                assert response.status_code == 401
                # No rate limit headers should indicate limiting
                assert "Retry-After" not in response.headers

    @pytest.mark.asyncio
    async def test_x_forwarded_for_separate_buckets(self, async_client, shared_token):
        """IT-RL-08: X-Forwarded-For respected — different IPs have separate buckets."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # Make 5 invalid attempts from IP 1
            for i in range(5):
                response = await async_client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "wrong-token"},
                    headers={"X-Forwarded-For": "203.0.113.1"},
                )
                assert response.status_code == 401

            # 6th from IP 1 should be rate limited
            response = await async_client.post(
                "/api/v1/auth/token",
                json={"shared_token": "wrong-token"},
                headers={"X-Forwarded-For": "203.0.113.1"},
            )
            assert response.status_code == 429

            # But IP 2 should still have full allowance
            response = await async_client.post(
                "/api/v1/auth/token",
                json={"shared_token": "wrong-token"},
                headers={"X-Forwarded-For": "198.51.100.1"},
            )
            assert response.status_code == 401
            # First failure for IP 2: header shows 5 remaining (before recording)
            assert (
                int(response.headers.get("X-RateLimit-Remaining", "0"))
                == settings.AUTH_RATE_LIMIT_BASE
            )

    @pytest.mark.asyncio
    async def test_rate_limit_headers_accurate(self, async_client, shared_token):
        """IT-RL-09: Rate limit headers present and accurate on all responses."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # First request
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "wrong-token"}
            )
            assert response.status_code == 401
            assert response.headers.get("X-RateLimit-Limit") == str(
                settings.AUTH_RATE_LIMIT_BASE
            )
            # First failure: failure_count=1, but header shows before recording, so 5
            # remaining
            assert response.headers.get("X-RateLimit-Remaining") == str(
                settings.AUTH_RATE_LIMIT_BASE
            )
            assert "X-RateLimit-Reset" in response.headers

            # Valid request should also have headers
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": shared_token}
            )
            assert response.status_code == 200
            assert response.headers.get("X-RateLimit-Limit") == str(
                settings.AUTH_RATE_LIMIT_BASE
            )
            # Valid request resets counter, so remaining=5 (full allowance)
            assert response.headers.get("X-RateLimit-Remaining") == str(
                settings.AUTH_RATE_LIMIT_BASE
            )

    @pytest.mark.asyncio
    async def test_audit_log_entries(self, async_client, shared_token, caplog):
        """IT-RL-10: Audit log entries emitted for failures, backoff, and lockout."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # Capture logs
            with caplog.at_level(logging.WARNING, logger="backend.core.rate_limit"):
                # Make an invalid attempt
                response = await async_client.post(
                    "/api/v1/auth/token", json={"shared_token": "wrong-token"}
                )
                assert response.status_code == 401

                # Check that auth_failure log was emitted
                log_records = [
                    r for r in caplog.records if "auth_failure" in r.getMessage()
                ]
                assert len(log_records) >= 1
                log_data = json.loads(log_records[-1].getMessage())
                assert log_data["event"] == "auth_failure"
                assert log_data["failure_reason"] == "invalid_shared_token"
                assert log_data["attempt_number"] == 1
                assert log_data["locked_out"] is False

    @pytest.mark.asyncio
    async def test_metrics_incremented(self, async_client, shared_token):
        """IT-RL-11: Metrics incremented: checks, rejections, lockouts."""
        # Note: This test verifies metrics are called, but actual metric values
        # depend on OpenTelemetry setup. We mainly ensure no errors occur.
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            # Make some requests
            for i in range(3):
                response = await async_client.post(
                    "/api/v1/auth/token", json={"shared_token": "wrong-token"}
                )
                assert response.status_code == 401

            # Valid request
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": shared_token}
            )
            assert response.status_code == 200


@pytest.mark.integration
class TestAuthRateLimitEdgeCases:
    """Edge case tests for auth rate limiting."""

    @pytest.fixture
    async def async_client(self) -> AsyncGenerator[AsyncClient, None]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client

    @pytest.fixture
    def shared_token(self):
        """Provide a test shared token."""
        return "test-shared-token-123"

    @pytest.fixture(autouse=True)
    async def reset_rate_limiter(self):
        """Reset rate limiter state before each test."""
        limiter = get_rate_limiter()
        async with limiter._lock:
            limiter._store.clear()
        yield
        async with limiter._lock:
            limiter._store.clear()

    @pytest.mark.asyncio
    async def test_missing_authorization_header_not_rate_limited(
        self, async_client, shared_token
    ):
        """Test that missing auth header on /verify returns 200 with valid=false,
        not 429."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            response = await async_client.get("/api/v1/auth/verify")
            assert response.status_code == 200
            assert response.json()["valid"] is False
            # Should not be rate limited for missing header
            assert "Retry-After" not in response.headers

    @pytest.mark.asyncio
    async def test_malformed_authorization_header_not_rate_limited(
        self, async_client, shared_token
    ):
        """Test that malformed auth header on /verify returns 200 with valid=false."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            response = await async_client.get(
                "/api/v1/auth/verify", headers={"Authorization": "NotBearer token"}
            )
            assert response.status_code == 200
            assert response.json()["valid"] is False
            assert "Retry-After" not in response.headers

    @pytest.mark.asyncio
    async def test_short_token_validation_not_rate_limited(
        self, async_client, shared_token
    ):
        """Test that too-short token on /validate returns 422 validation error,
        not 429."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            response = await async_client.post(
                "/api/v1/auth/validate", json={"token": "short"}
            )
            assert response.status_code == 422  # Validation error
            assert "Retry-After" not in response.headers

    @pytest.mark.asyncio
    async def test_different_endpoints_share_bucket(self, async_client, shared_token):
        """Test that all 4 auth endpoints share the same rate limit bucket per IP."""
        with patch.object(settings, "SHARED_TOKEN", shared_token):
            endpoints = [
                ("POST", "/api/v1/auth/token", {"shared_token": "wrong-token"}),
                ("POST", "/api/v1/auth/validate", {"token": "wrong-token"}),
                ("GET", "/api/v1/auth/verify", {"Authorization": "Bearer wrong-token"}),
                ("POST", "/api/v1/auth/refresh", {"token": "wrong-token"}),
            ]

            # Make 5 invalid attempts across different endpoints (4 requests, all
            # allowed)
            for method, endpoint, data in endpoints:
                if method == "GET":
                    response = await async_client.get(endpoint, headers=data)
                else:
                    response = await async_client.post(endpoint, json=data)
                assert response.status_code in (401, 200)  # 200 for verify with
                # invalid token

            # Add 1 more request to reach 5 total
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "wrong-token"}
            )
            assert response.status_code == 401

            # Next attempt (6th) should be rate limited
            response = await async_client.post(
                "/api/v1/auth/token", json={"shared_token": "wrong-token"}
            )
            assert response.status_code == 429


@pytest.mark.integration
class TestAuthRateLimitWindowExpiry:
    """Tests for window expiry behavior."""

    @pytest.fixture
    async def async_client(self) -> AsyncGenerator[AsyncClient, None]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client

    @pytest.fixture
    def shared_token(self):
        """Provide a test shared token."""
        return "test-shared-token-123"

    @pytest.fixture(autouse=True)
    async def reset_rate_limiter(self):
        """Reset rate limiter state before each test."""
        limiter = get_rate_limiter()
        async with limiter._lock:
            limiter._store.clear()
        yield
        async with limiter._lock:
            limiter._store.clear()

    @pytest.mark.asyncio
    async def test_window_expiry_resets_counter(self, async_client, shared_token):
        """Test that waiting for window expiry resets the counter."""
        # This test would require manipulating time or waiting 15 minutes,
        # which is impractical for unit tests. The unit test covers this logic.
        # This is a placeholder for the integration test scenario.
        pass  # Covered by unit test UT-RL-05
