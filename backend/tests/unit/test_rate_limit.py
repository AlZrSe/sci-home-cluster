"""
Unit tests for authentication rate limiting.
"""

import asyncio
import time
from unittest.mock import Mock, patch

import pytest
from fastapi import Response
from starlette.datastructures import Headers

from backend.core.config import settings
from backend.core.rate_limit import AuthRateLimiter


class TestAuthRateLimiter:
    """Tests for AuthRateLimiter class."""

    @pytest.fixture
    def limiter(self):
        """Create a fresh rate limiter instance for each test."""
        return AuthRateLimiter()

    @pytest.fixture
    def mock_request(self):
        """Create a mock request with a client IP."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "example.com"
        request.headers = Headers({"user-agent": "test-agent"})
        request.client = Mock()
        request.client.host = "192.0.2.1"
        return request

    @pytest.fixture
    def mock_response(self):
        """Create a mock response."""
        return Response()

    def test_allows_up_to_base_failures(self, limiter, mock_request, mock_response):
        """UT-RL-01: Rate limiter allows up to BASE failures per window."""
        base = settings.AUTH_RATE_LIMIT_BASE

        # Record up to base - 1 failures (4 failures for base=5)
        # The BASE failures are 1 through BASE (1-5), so we record BASE-1=4
        for i in range(base - 1):
            asyncio.run(
                limiter.record_failure(
                    mock_request, "/api/v1/auth/token", "invalid_token"
                )
            )

        # Next check should be allowed (5th failure attempt, fc=4)
        mock_response2 = Response()
        allowed, _ = asyncio.run(
            limiter.check_rate_limit(mock_request, mock_response2, "/api/v1/auth/token")
        )
        assert allowed is True

        # Record the 5th failure (fc=5)
        asyncio.run(
            limiter.record_failure(mock_request, "/api/v1/auth/token", "invalid_token")
        )

        # Next check should be rate limited with 2s backoff (6th failure attempt, fc=5)
        mock_response3 = Response()
        allowed, error = asyncio.run(
            limiter.check_rate_limit(mock_request, mock_response3, "/api/v1/auth/token")
        )
        assert allowed is False
        assert "2 seconds" in error
        assert mock_response3.headers["Retry-After"] == "2"

    def test_exponential_backoff(self, limiter, mock_request, mock_response):
        """UT-RL-02: Rate limiter applies exponential backoff that increases."""
        base = settings.AUTH_RATE_LIMIT_BASE

        # Record failures up to base - 1 (4)
        for i in range(base - 1):
            asyncio.run(
                limiter.record_failure(
                    mock_request, "/api/v1/auth/token", "invalid_token"
                )
            )

        # Attempt 5: allowed (fc=4)
        mock_response2 = Response()
        allowed, _ = asyncio.run(
            limiter.check_rate_limit(mock_request, mock_response2, "/api/v1/auth/token")
        )
        assert allowed is True
        asyncio.run(
            limiter.record_failure(mock_request, "/api/v1/auth/token", "invalid_token")
        )  # fc=5

        # Subsequent attempts should get increasing backoff: 2s, 4s, 8s,
        # 16s, 32s, then lockout
        expected_delays = [2, 4, 8, 16, 32]
        for delay in expected_delays:
            mock_resp = Response()
            allowed, error = asyncio.run(
                limiter.check_rate_limit(mock_request, mock_resp, "/api/v1/auth/token")
            )
            assert allowed is False
            assert f"{delay} seconds" in error
            assert mock_resp.headers["Retry-After"] == str(delay)
            asyncio.run(
                limiter.record_failure(
                    mock_request, "/api/v1/auth/token", "invalid_token"
                )
            )

        # Next should be lockout (3600s)
        mock_resp = Response()
        allowed, error = asyncio.run(
            limiter.check_rate_limit(mock_request, mock_resp, "/api/v1/auth/token")
        )
        assert allowed is False
        assert "Locked out" in error
        retry_after = int(mock_resp.headers.get("Retry-After", "0"))
        assert retry_after >= settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS - 5
        assert retry_after <= settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS + 5

    def test_hard_lockout_at_threshold(self, limiter, mock_request, mock_response):
        """UT-RL-03: Hard-locks at LOCKOUT_THRESHOLD (11) for LOCKOUT_SECONDS."""
        lockout_threshold = settings.AUTH_RATE_LIMIT_LOCKOUT_THRESHOLD
        lockout_seconds = settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS

        # Record failures up to lockout_threshold - 1 (10 failures for threshold=11)
        # With base=5: failures 1-5 allowed, 6-10 backoff (2,4,8,16,32), 11+ lockout
        for i in range(lockout_threshold - 1):
            asyncio.run(
                limiter.record_failure(
                    mock_request, "/api/v1/auth/token", "invalid_token"
                )
            )

        # Next check should be locked out (11th failure attempt, fc=10)
        mock_response2 = Response()
        allowed, error = asyncio.run(
            limiter.check_rate_limit(mock_request, mock_response2, "/api/v1/auth/token")
        )
        assert allowed is False
        assert "Locked out" in error
        # retry_after should be approximately lockout_seconds (may be slightly less
        # due to test timing)
        retry_after = int(mock_response2.headers.get("Retry-After", "0"))
        assert retry_after >= lockout_seconds - 5  # Allow small timing variance
        assert retry_after <= lockout_seconds + 5

    def test_success_resets_failure_counter(self, limiter, mock_request, mock_response):
        """UT-RL-04: Successful auth resets failure counter for that IP."""
        base = settings.AUTH_RATE_LIMIT_BASE

        # Record some failures
        for i in range(3):
            asyncio.run(
                limiter.record_failure(
                    mock_request, "/api/v1/auth/token", "invalid_token"
                )
            )

        # Record success
        asyncio.run(limiter.record_success(mock_request))

        # Next check should have full allowance
        mock_response2 = Response()
        allowed, _ = asyncio.run(
            limiter.check_rate_limit(mock_request, mock_response2, "/api/v1/auth/token")
        )
        assert allowed is True
        assert mock_response2.headers["X-RateLimit-Remaining"] == str(base)

    def test_window_expiry_resets_counter(self, limiter, mock_request, mock_response):
        """UT-RL-05: Window expiry resets failure counter."""
        base = settings.AUTH_RATE_LIMIT_BASE
        window = settings.AUTH_RATE_LIMIT_WINDOW_SECONDS

        # Record some failures
        for i in range(3):
            asyncio.run(
                limiter.record_failure(
                    mock_request, "/api/v1/auth/token", "invalid_token"
                )
            )

        # Manually expire the window
        async def expire_window():
            async with limiter._lock:
                state = limiter._store["192.0.2.1"]
                state.window_start = time.time() - window - 1

        asyncio.run(expire_window())

        # Next check should have full allowance
        mock_response2 = Response()
        allowed, _ = asyncio.run(
            limiter.check_rate_limit(mock_request, mock_response2, "/api/v1/auth/token")
        )
        assert allowed is True
        assert mock_response2.headers["X-RateLimit-Remaining"] == str(base)

    def test_localhost_exempt(self, limiter, mock_request, mock_response):
        """UT-RL-06: Localhost IPs are exempt from rate limiting."""
        mock_request.url.hostname = "localhost"

        # Should allow unlimited requests
        for i in range(100):
            mock_response2 = Response()
            allowed, _ = asyncio.run(
                limiter.check_rate_limit(
                    mock_request, mock_response2, "/api/v1/auth/token"
                )
            )
            assert allowed is True
            # Remaining should always be at base (no decrement for exempt)
            assert mock_response2.headers["X-RateLimit-Remaining"] == str(
                settings.AUTH_RATE_LIMIT_BASE
            )

    def test_x_forwarded_for_used(self, limiter, mock_request, mock_response):
        """UT-RL-07: X-Forwarded-For header used when present (first entry)."""
        mock_request.headers = Headers(
            {
                "x-forwarded-for": "203.0.113.1, 198.51.100.1",
                "user-agent": "test-agent",
            }
        )

        # Record a failure
        asyncio.run(
            limiter.record_failure(mock_request, "/api/v1/auth/token", "invalid_token")
        )

        # Check that the IP is 203.0.113.1 (first in X-Forwarded-For)
        async def check_store():
            async with limiter._lock:
                return (
                    "203.0.113.1" in limiter._store,
                    "192.0.2.1" not in limiter._store,
                )

        has_ip1, not_ip2 = asyncio.run(check_store())
        assert has_ip1
        assert not_ip2

    def test_rate_limit_headers_present(self, limiter, mock_request, mock_response):
        """UT-RL-08: Rate limit headers present on all responses."""
        allowed, _ = asyncio.run(
            limiter.check_rate_limit(mock_request, mock_response, "/api/v1/auth/token")
        )
        assert allowed is True
        assert "X-RateLimit-Limit" in mock_response.headers
        assert "X-RateLimit-Remaining" in mock_response.headers
        assert "X-RateLimit-Reset" in mock_response.headers

    def test_429_response_body(self, limiter, mock_request, mock_response):
        """UT-RL-09: 429 response would have correct structure."""
        # Fill up to lockout
        for i in range(settings.AUTH_RATE_LIMIT_LOCKOUT_THRESHOLD):
            asyncio.run(
                limiter.record_failure(
                    mock_request, "/api/v1/auth/token", "invalid_token"
                )
            )

        mock_response2 = Response()
        allowed, error = asyncio.run(
            limiter.check_rate_limit(mock_request, mock_response2, "/api/v1/auth/token")
        )
        assert allowed is False
        assert error is not None
        assert "Retry-After" in mock_response2.headers

    @pytest.mark.asyncio
    async def test_concurrent_requests_same_ip(self, limiter):
        """UT-RL-10: Concurrent requests from same IP handled
        correctly (async safety)."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "example.com"
        request.headers = Headers({"user-agent": "test-agent"})
        request.client = Mock()
        request.client.host = "192.0.2.1"

        # Pre-record 6 failures to set up the bucket (fc=6 -> backoff on next request)
        for i in range(6):
            await limiter.record_failure(request, "/api/v1/auth/token", "invalid_token")

        async def make_request():
            response = Response()
            allowed, _ = await limiter.check_rate_limit(
                request, response, "/api/v1/auth/token"
            )
            return allowed

        # Run 10 concurrent requests
        tasks = [make_request() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All should be rate limited (backoff) since we already have 6 failures
        # But no exceptions should occur
        allowed_count = sum(1 for r in results if r)
        # Should be 0 allowed (all in backoff)
        assert allowed_count == 0

    def test_expired_entries_evicted(self, limiter, mock_request, mock_response):
        """UT-RL-11: Expired entries evicted from memory store."""
        # Record a failure
        asyncio.run(
            limiter.record_failure(mock_request, "/api/v1/auth/token", "invalid_token")
        )

        # Verify entry exists
        async def check_exists():
            async with limiter._lock:
                return "192.0.2.1" in limiter._store

        assert asyncio.run(check_exists())

        # Manually expire
        async def expire_entry():
            async with limiter._lock:
                state = limiter._store["192.0.2.1"]
                state.window_start = (
                    time.time() - settings.AUTH_RATE_LIMIT_WINDOW_SECONDS - 1
                )
                state.lockout_until = 0

        asyncio.run(expire_entry())

        # Run cleanup
        asyncio.run(limiter._evict_expired())

        # Entry should be gone
        async def check_gone():
            async with limiter._lock:
                return "192.0.2.1" not in limiter._store

        assert asyncio.run(check_gone())

    def test_config_via_env_vars(self):
        """UT-RL-12: Configuration via env vars works (override defaults)."""
        with patch.dict(
            "os.environ",
            {
                "AUTH_RATE_LIMIT_BASE": "3",
                "AUTH_RATE_LIMIT_WINDOW_SECONDS": "600",
                "AUTH_RATE_LIMIT_BACKOFF_BASE_SECONDS": "5",
                "AUTH_RATE_LIMIT_MAX_BACKOFF_SECONDS": "1800",
                "AUTH_RATE_LIMIT_LOCKOUT_THRESHOLD": "8",
                "AUTH_RATE_LIMIT_LOCKOUT_SECONDS": "1800",
            },
        ):
            # Need to reload settings
            from backend.core.config import Settings

            new_settings = Settings()
            assert new_settings.AUTH_RATE_LIMIT_BASE == 3
            assert new_settings.AUTH_RATE_LIMIT_WINDOW_SECONDS == 600
            assert new_settings.AUTH_RATE_LIMIT_BACKOFF_BASE_SECONDS == 5
            assert new_settings.AUTH_RATE_LIMIT_MAX_BACKOFF_SECONDS == 1800
            assert new_settings.AUTH_RATE_LIMIT_LOCKOUT_THRESHOLD == 8
            assert new_settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS == 1800


class TestGetClientIP:
    """Tests for _get_client_ip method."""

    def test_x_forwarded_for_first_entry(self, limiter):
        """Test that first entry in X-Forwarded-For is used."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "example.com"
        request.headers = Headers({"x-forwarded-for": "203.0.113.1, 198.51.100.1"})
        request.client = Mock()
        request.client.host = "192.0.2.1"

        ip = limiter._get_client_ip(request)
        assert ip == "203.0.113.1"

    def test_fallback_to_client_host(self, limiter):
        """Test fallback to request.client.host when no X-Forwarded-For."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "example.com"
        request.headers = Headers({})
        request.client = Mock()
        request.client.host = "192.0.2.1"

        ip = limiter._get_client_ip(request)
        assert ip == "192.0.2.1"

    def test_unknown_when_no_client(self, limiter):
        """Test 'unknown' when no client info available."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "example.com"
        request.headers = Headers({})
        request.client = None

        ip = limiter._get_client_ip(request)
        assert ip == "unknown"


class TestIsExempt:
    """Tests for _is_exempt method."""

    def test_localhost_exact(self, limiter):
        """Test localhost exact match."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "localhost"
        request.headers = Headers({})
        request.client = Mock()
        request.client.host = "192.0.2.1"

        assert limiter._is_exempt(request) is True

    def test_127_0_0_1_exact(self, limiter):
        """Test 127.0.0.1 exact match."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "127.0.0.1"
        request.headers = Headers({})
        request.client = Mock()
        request.client.host = "192.0.2.1"

        assert limiter._is_exempt(request) is True

    def test_ipv6_loopback_exact(self, limiter):
        """Test ::1 exact match."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "::1"
        request.headers = Headers({})
        request.client = Mock()
        request.client.host = "192.0.2.1"

        assert limiter._is_exempt(request) is True

    def test_local_suffix(self, limiter):
        """Test .local suffix."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "myhost.local"
        request.headers = Headers({})
        request.client = Mock()
        request.client.host = "192.0.2.1"

        assert limiter._is_exempt(request) is True

    def test_lovable_app_suffix(self, limiter):
        """Test .lovable.app suffix."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "preview.lovable.app"
        request.headers = Headers({})
        request.client = Mock()
        request.client.host = "192.0.2.1"

        assert limiter._is_exempt(request) is True

    def test_non_localhost_not_exempt(self, limiter):
        """Test non-localhost hosts are not exempt."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "example.com"
        request.headers = Headers({})
        request.client = Mock()
        request.client.host = "192.0.2.1"

        assert limiter._is_exempt(request) is False

    def test_exempt_ignores_x_forwarded_for(self, limiter):
        """Test exemption applies regardless of X-Forwarded-For."""
        request = Mock(spec=["url", "headers", "client"])
        request.url = Mock()
        request.url.hostname = "localhost"
        request.headers = Headers({"x-forwarded-for": "203.0.113.1"})
        request.client = Mock()
        request.client.host = "192.0.2.1"

        assert limiter._is_exempt(request) is True


class TestCalculateBackoff:
    """Tests for _calculate_backoff method."""

    def test_baseline_no_delay(self, limiter):
        """Test no delay for failures within baseline (0 to BASE-1)."""
        base = settings.AUTH_RATE_LIMIT_BASE
        for i in range(base):  # 0 to 4 for base=5
            delay, locked = limiter._calculate_backoff(i)
            assert delay == 0, f"failure_count={i} should have no delay"
            assert locked is False

    def test_exponential_backoff_steps(self, limiter):
        """Test exponential backoff for attempts BASE+1 to LOCKOUT_THRESHOLD-1."""
        base = settings.AUTH_RATE_LIMIT_BASE
        backoff_base = settings.AUTH_RATE_LIMIT_BACKOFF_BASE_SECONDS

        # failure_count = base (5): 2s backoff (6th failure attempt)
        delay, locked = limiter._calculate_backoff(base)
        assert delay == backoff_base * 1
        assert locked is False

        # failure_count = base + 1 (6): 4s backoff (7th failure attempt)
        delay, locked = limiter._calculate_backoff(base + 1)
        assert delay == backoff_base * 2
        assert locked is False

        # failure_count = base + 2 (7): 8s backoff (8th failure attempt)
        delay, locked = limiter._calculate_backoff(base + 2)
        assert delay == backoff_base * 4
        assert locked is False

        # failure_count = base + 3 (8): 16s backoff (9th failure attempt)
        delay, locked = limiter._calculate_backoff(base + 3)
        assert delay == backoff_base * 8
        assert locked is False

        # failure_count = base + 4 (9): 32s backoff (10th failure attempt)
        delay, locked = limiter._calculate_backoff(base + 4)
        assert delay == backoff_base * 16
        assert locked is False

    def test_hard_lockout(self, limiter):
        """Test hard lockout at LOCKOUT_THRESHOLD and above."""
        lockout_threshold = settings.AUTH_RATE_LIMIT_LOCKOUT_THRESHOLD
        lockout_seconds = settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS

        # failure_count = lockout_threshold (11): lockout (12th failure attempt)
        delay, locked = limiter._calculate_backoff(lockout_threshold)
        assert delay == lockout_seconds
        assert locked is True

        # Beyond lockout threshold
        delay, locked = limiter._calculate_backoff(lockout_threshold + 1)
        assert delay == lockout_seconds
        assert locked is True

    def test_max_backoff_cap(self, limiter):
        """Test backoff is capped at MAX_BACKOFF_SECONDS."""
        with patch.object(settings, "AUTH_RATE_LIMIT_BACKOFF_BASE_SECONDS", 1000):
            with patch.object(settings, "AUTH_RATE_LIMIT_MAX_BACKOFF_SECONDS", 100):
                delay, locked = limiter._calculate_backoff(
                    settings.AUTH_RATE_LIMIT_BASE + 1
                )
                assert delay == 100  # Capped at max
                assert locked is False


# Need to import limiter fixture for these tests
@pytest.fixture
def limiter():
    return AuthRateLimiter()
