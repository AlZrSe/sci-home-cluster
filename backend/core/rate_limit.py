"""
Rate limiting middleware for authentication endpoints.

Implements per-IP rate limiting with exponential backoff and lockout
to prevent brute-force attacks on the shared token.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import Request, Response
from backend.core.config import settings
from backend.core.metrics import (
    _counter,
)

# Load localhost bypass hosts from shared config
_localhost_hosts_path = (
    Path(__file__).resolve().parents[2] / "shared" / "auth" / "localhost_hosts.json"
)
if _localhost_hosts_path.exists():
    import json as _json

    with open(_localhost_hosts_path) as f:
        _localhost_config = _json.load(f)
    RATE_LIMIT_EXEMPT_EXACT = set(_localhost_config.get("exact", []))
    RATE_LIMIT_EXEMPT_SUFFIXES = tuple(_localhost_config.get("suffixes", []))
else:
    RATE_LIMIT_EXEMPT_EXACT = {"localhost", "127.0.0.1", "::1"}
    RATE_LIMIT_EXEMPT_SUFFIXES = (".local", ".lovable.app")

logger = logging.getLogger(__name__)

# Metrics instruments
_rate_limit_checks_total = _counter(
    "auth_rate_limit_checks_total",
    "Total number of rate limit checks performed on auth endpoints.",
    "1",
)
_rate_limit_rejections_total = _counter(
    "auth_rate_limit_rejections_total",
    "Total number of requests rejected due to rate limiting.",
    "1",
)
_rate_limit_lockouts_total = _counter(
    "auth_lockouts_total",
    "Total number of IP lockouts due to repeated authentication failures.",
    "1",
)


@dataclass
class RateLimitState:
    """State for a single IP address."""

    failure_count: int = 0
    window_start: float = field(default_factory=time.time)
    lockout_until: float = 0.0
    last_failure_time: float = 0.0


class AuthRateLimiter:
    """
    In-memory rate limiter for authentication endpoints.

    Tracks failures per client IP with a rolling window, exponential backoff,
    and hard lockout. Thread-safe via asyncio.Lock.

    Limitation: In-memory store does not work across multiple API replicas.
    For distributed deployments, a Redis backend would be needed.
    """

    def __init__(self) -> None:
        self._store: dict[str, RateLimitState] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start_cleanup_task(self, interval_seconds: int = 300) -> None:
        """Start background task to evict expired entries."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(interval_seconds)
            )

    async def stop_cleanup_task(self) -> None:
        """Stop background cleanup task."""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def _cleanup_loop(self, interval_seconds: int) -> None:
        """Periodically evict expired entries from the store."""
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await self._evict_expired()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in rate limiter cleanup loop")

    async def _evict_expired(self) -> None:
        """Remove entries whose window and lockout have both expired."""
        now = time.time()
        async with self._lock:
            expired_keys = [
                ip
                for ip, state in self._store.items()
                if (
                    state.window_start + settings.AUTH_RATE_LIMIT_WINDOW_SECONDS < now
                    and state.lockout_until < now
                )
            ]
            for ip in expired_keys:
                del self._store[ip]

    def _get_client_ip(self, request: Request) -> str:
        """
        Extract client IP from request, respecting X-Forwarded-For.

        Returns the first IP in X-Forwarded-For header if present,
        otherwise falls back to request.client.host.
        """
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the first IP (original client)
            return forwarded_for.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    def _is_exempt(self, request: Request) -> bool:
        """
        Check if request is exempt from rate limiting.

        Localhost bypass hosts (from shared/auth/localhost_hosts.json) are exempt.
        Exemption applies regardless of X-Forwarded-For header.
        """
        hostname = request.url.hostname
        if not hostname:
            return False
        host = hostname.strip().lower()
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return host in RATE_LIMIT_EXEMPT_EXACT or any(
            host.endswith(suffix) for suffix in RATE_LIMIT_EXEMPT_SUFFIXES
        )

    def _calculate_backoff(self, failure_count: int) -> tuple[int, bool]:
        """
        Calculate backoff delay and lockout status for a given failure count.

        The failure_count is the number of failures ALREADY recorded.
        The backoff applies to the NEXT request (which would be failure
        number = failure_count + 1).

        Returns:
            tuple: (delay_seconds, is_locked_out)
        """
        base_limit = settings.AUTH_RATE_LIMIT_BASE
        lockout_threshold = settings.AUTH_RATE_LIMIT_LOCKOUT_THRESHOLD
        backoff_base = settings.AUTH_RATE_LIMIT_BACKOFF_BASE_SECONDS
        max_backoff = settings.AUTH_RATE_LIMIT_MAX_BACKOFF_SECONDS
        lockout_seconds = settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS

        # The next failure would be failure number = failure_count + 1
        # First BASE_LIMIT failures allowed (failure numbers 1 to BASE_LIMIT)
        # Failure BASE_LIMIT+1 gets exponential backoff (2s)
        # Next (LOCKOUT_THRESHOLD - BASE_LIMIT - 1) failures get exponential backoff
        # Further failures get hard lockout
        # We apply backoff when failure_count >= base_limit
        # attempts = (failure_count + 1) - base_limit = failure_count - base_limit + 1
        attempts_over_base = failure_count - base_limit + 1

        if attempts_over_base <= 0:
            return 0, False

        # Backoff steps: e.g., for base=5, lockout=11: 5 steps (failures 6-10)
        backoff_steps = lockout_threshold - base_limit - 1

        if attempts_over_base <= backoff_steps:
            # Exponential backoff: 2, 4, 8, 16, 32 seconds
            # attempts_over_base=1 -> 2*2^0=2, =2 -> 2*2^1=4, etc.
            delay = backoff_base * (2 ** (attempts_over_base - 1))
            delay = min(delay, max_backoff)
            return delay, False

        # Hard lockout
        return lockout_seconds, True

    def _get_window_reset_time(self, state: RateLimitState) -> int:
        """Get Unix timestamp when the current window resets."""
        return int(state.window_start + settings.AUTH_RATE_LIMIT_WINDOW_SECONDS)

    async def _handle_exempt_request(self, response: Response) -> tuple[bool, None]:
        """Handle exempt request by adding headers and allowing."""
        reset = int(time.time()) + settings.AUTH_RATE_LIMIT_WINDOW_SECONDS
        response.headers["X-RateLimit-Limit"] = str(settings.AUTH_RATE_LIMIT_BASE)
        response.headers["X-RateLimit-Remaining"] = str(settings.AUTH_RATE_LIMIT_BASE)
        response.headers["X-RateLimit-Reset"] = str(reset)
        return True, None

    async def _handle_locked_out(
        self, state: RateLimitState, response: Response, endpoint: str, now: float
    ) -> tuple[bool, str]:
        """Handle locked out state."""
        retry_after = max(1, int(state.lockout_until - now))
        response.headers["X-RateLimit-Limit"] = str(settings.AUTH_RATE_LIMIT_BASE)
        response.headers["X-RateLimit-Remaining"] = "0"
        response.headers["X-RateLimit-Reset"] = str(int(state.lockout_until))
        response.headers["Retry-After"] = str(retry_after)

        if _rate_limit_rejections_total is not None:
            try:
                _rate_limit_rejections_total.add(
                    1, attributes={"endpoint": endpoint, "result": "lockout"}
                )
            except Exception:
                pass

        return False, (
            f"Locked out due to repeated authentication failures. "
            f"Try again in {retry_after} seconds."
        )

    async def _handle_backoff(
        self,
        state: RateLimitState,
        response: Response,
        endpoint: str,
        client_ip: str,
        now: float,
    ) -> tuple[bool, Optional[str]]:
        """Handle backoff period."""
        delay, is_locked_out = self._calculate_backoff(state.failure_count)

        if delay > 0:
            retry_after = delay

            reset_time = self._get_window_reset_time(state)
            response.headers["X-RateLimit-Limit"] = str(settings.AUTH_RATE_LIMIT_BASE)
            response.headers["X-RateLimit-Remaining"] = "0"
            response.headers["X-RateLimit-Reset"] = str(reset_time)
            response.headers["Retry-After"] = str(retry_after)

            if _rate_limit_rejections_total is not None:
                try:
                    _rate_limit_rejections_total.add(
                        1, attributes={"endpoint": endpoint, "result": "backoff"}
                    )
                except Exception:
                    pass

            if is_locked_out:
                # This shouldn't happen in _handle_backoff since we check lockout first
                # but handle it just in case
                state.lockout_until = now + settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS
                await self._log_lockout(
                    client_ip, endpoint, state.failure_count, state.lockout_until - now
                )
                return False, (
                    f"Locked out due to repeated authentication failures. "
                    f"Try again in {retry_after} seconds."
                )
            return False, f"Rate limited. Try again in {retry_after} seconds."

        # No backoff - request allowed
        remaining = max(0, settings.AUTH_RATE_LIMIT_BASE - state.failure_count)
        response.headers["X-RateLimit-Limit"] = str(settings.AUTH_RATE_LIMIT_BASE)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(self._get_window_reset_time(state))
        return True, None

    async def _log_lockout(
        self, client_ip: str, endpoint: str, total_failures: int, duration: float
    ) -> None:
        """Log lockout event."""
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        log_entry = {
            "event": "auth_lockout",
            "timestamp": timestamp,
            "client_ip": client_ip,
            "endpoint": endpoint,
            "lockout_duration_seconds": int(duration),
            "total_failures": total_failures,
        }
        logger.error(json.dumps(log_entry))

        if _rate_limit_lockouts_total is not None:
            try:
                _rate_limit_lockouts_total.add(1, attributes={"endpoint": endpoint})
            except Exception:
                pass

    async def _log_failure(
        self,
        client_ip: str,
        endpoint: str,
        failure_reason: str,
        attempt_number: int,
        user_agent: str,
        backoff_seconds: int,
    ) -> None:
        """Log failure event."""
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        log_entry = {
            "event": "auth_failure",
            "timestamp": timestamp,
            "client_ip": client_ip,
            "endpoint": endpoint,
            "failure_reason": failure_reason,
            "attempt_number": attempt_number,
            "user_agent": user_agent,
            "backoff_seconds": backoff_seconds,
            "locked_out": False,
        }
        logger.warning(json.dumps(log_entry))

    async def check_rate_limit(
        self,
        request: Request,
        response: Response,
        endpoint: str,
        failure_reason: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if request should be rate limited.

        Args:
            request: The incoming request
            response: The response object to add headers to
            endpoint: The auth endpoint path (e.g., "/api/v1/auth/token")
            failure_reason: Reason for failure (if this is a failed attempt)

        Returns:
            tuple: (allowed, error_detail)
                - allowed: True if request should proceed, False if rate limited
                - error_detail: Error message if rate limited, None otherwise
        """
        # Increment metrics
        if _rate_limit_checks_total is not None:
            try:
                _rate_limit_checks_total.add(
                    1, attributes={"endpoint": endpoint, "result": "check"}
                )
            except Exception:
                pass

        client_ip = self._get_client_ip(request)

        # Check localhost exemption
        if self._is_exempt(request):
            return await self._handle_exempt_request(response)

        now = time.time()
        window_seconds = settings.AUTH_RATE_LIMIT_WINDOW_SECONDS

        async with self._lock:
            state = self._store.get(client_ip)
            if state is None:
                state = RateLimitState(window_start=now)
                self._store[client_ip] = state

            # Check if window has expired
            if now - state.window_start >= window_seconds:
                # Window expired, reset failure count
                state.failure_count = 0
                state.window_start = now
                state.lockout_until = 0

            # Check if currently locked out
            if state.lockout_until > now:
                return await self._handle_locked_out(state, response, endpoint, now)

            # Calculate current backoff based on existing failure count
            return await self._handle_backoff(state, response, endpoint, client_ip, now)

    async def record_failure(
        self,
        request: Request,
        endpoint: str,
        failure_reason: str,
    ) -> None:
        """
        Record a failed authentication attempt.

        Args:
            request: The incoming request
            endpoint: The auth endpoint path
            failure_reason: Reason for failure (e.g., "invalid_shared_token")
        """
        client_ip = self._get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "unknown")

        # Check localhost exemption - don't record failures for exempt hosts
        if self._is_exempt(request):
            return

        now = time.time()
        window_seconds = settings.AUTH_RATE_LIMIT_WINDOW_SECONDS

        async with self._lock:
            state = self._store.get(client_ip)
            if state is None:
                state = RateLimitState(window_start=now)
                self._store[client_ip] = state

            # Check if window has expired
            if now - state.window_start >= window_seconds:
                state.failure_count = 0
                state.window_start = now
                state.lockout_until = 0

            state.failure_count += 1
            state.last_failure_time = now

            # Calculate backoff and lockout for this failure
            delay, is_locked_out = self._calculate_backoff(state.failure_count)

            if is_locked_out:
                state.lockout_until = now + settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS
                await self._log_lockout(
                    client_ip, endpoint, state.failure_count, state.lockout_until - now
                )
            else:
                await self._log_failure(
                    client_ip,
                    endpoint,
                    failure_reason,
                    state.failure_count,
                    user_agent,
                    delay,
                )

    async def record_success(self, request: Request) -> None:
        """
        Record a successful authentication, resetting the failure counter.

        Args:
            request: The incoming request
        """
        client_ip = self._get_client_ip(request)

        # Check localhost exemption
        if self._is_exempt(request):
            return

        async with self._lock:
            state = self._store.get(client_ip)
            if state is not None:
                state.failure_count = 0
                state.lockout_until = 0


# Global rate limiter instance
_rate_limiter: Optional[AuthRateLimiter] = None


def get_rate_limiter() -> AuthRateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = AuthRateLimiter()
    return _rate_limiter


async def init_rate_limiter() -> AuthRateLimiter:
    """Initialize and start the rate limiter."""
    limiter = get_rate_limiter()
    await limiter.start_cleanup_task()
    return limiter


async def shutdown_rate_limiter() -> None:
    """Shutdown the rate limiter."""
    limiter = get_rate_limiter()
    await limiter.stop_cleanup_task()
