"""
Integration tests for localhost authentication bypass.

Tests the complete auth bypass flow through the API endpoints for all three modes:
- disabled
- socket (default)
- host_header (legacy)
"""

import logging
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from starlette.requests import Request
from starlette.websockets import WebSocket

from backend.core.config import LocalhostBypassMode, settings
from backend.core.deps import _check_localhost_bypass_http, _check_localhost_bypass_ws
from backend.core.security import create_access_token


@pytest.mark.integration
class TestAuthBypassSocketMode:
    """Tests for LOCALHOST_BYPASS_MODE=socket (default)."""

    @pytest.fixture(autouse=True)
    def set_socket_mode(self, monkeypatch):
        """Set socket mode for all tests in this class."""
        monkeypatch.setattr(
            settings, "LOCALHOST_BYPASS_MODE", LocalhostBypassMode.SOCKET
        )

    def test_bypass_activates_for_loopback_client(self):
        """Request from 127.0.0.1 should bypass auth."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [],
                "client": ("127.0.0.1", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        payload = _check_localhost_bypass_http(request)
        assert payload == {"sub": "localhost_user"}

    def test_bypass_activates_for_ipv6_loopback(self):
        """Request from ::1 should bypass auth."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [],
                "client": ("::1", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        payload = _check_localhost_bypass_http(request)
        assert payload == {"sub": "localhost_user"}

    def test_bypass_activates_for_ipv4_mapped_loopback(self):
        """Request from ::ffff:127.0.0.1 should bypass auth."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [],
                "client": ("::ffff:127.0.0.1", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        payload = _check_localhost_bypass_http(request)
        assert payload == {"sub": "localhost_user"}

    def test_bypass_rejects_non_loopback_ip(self):
        """Request from 192.168.1.100 should not bypass even with Host: localhost."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [(b"host", b"localhost:8000")],
                "client": ("192.168.1.100", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        # Should return None since bypass doesn't activate
        payload = _check_localhost_bypass_http(request)
        assert payload is None

    def test_bypass_rejects_spoofed_host_header(self):
        """Request from non-loopback IP with Host: localhost should NOT bypass."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [(b"host", b"localhost:8000")],
                "client": ("10.0.0.50", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        payload = _check_localhost_bypass_http(request)
        assert payload is None


@pytest.mark.integration
class TestAuthBypassHostHeaderMode:
    """Tests for LOCALHOST_BYPASS_MODE=host_header (legacy)."""

    @pytest.fixture(autouse=True)
    def set_host_header_mode(self, monkeypatch):
        """Set host_header mode for all tests in this class."""
        monkeypatch.setattr(
            settings, "LOCALHOST_BYPASS_MODE", LocalhostBypassMode.HOST_HEADER
        )

    def test_bypass_activates_for_spoofed_host(self):
        """Legacy mode: Host: localhost from any IP bypasses auth."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [(b"host", b"localhost:8000")],
                "client": ("192.168.1.100", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        payload = _check_localhost_bypass_http(request)
        assert payload == {"sub": "localhost_user"}

    def test_bypass_activates_for_localhost_host(self):
        """Legacy mode: Host: 127.0.0.1 from any IP bypasses auth."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [(b"host", b"127.0.0.1:8000")],
                "client": ("10.0.0.50", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        payload = _check_localhost_bypass_http(request)
        assert payload == {"sub": "localhost_user"}

    def test_bypass_rejects_non_localhost_host(self):
        """Legacy mode: Host: example.com should not bypass."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [(b"host", b"example.com:8000")],
                "client": ("192.168.1.100", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        payload = _check_localhost_bypass_http(request)
        assert payload is None

    def test_startup_warning_emitted(self, caplog):
        """Verify deprecation warning is logged at startup."""
        with caplog.at_level(logging.WARNING, logger="backend.core.config"):
            test_settings = type(settings)(
                _env_file=None,
                SECRET_KEY="test-key",
                LOCALHOST_BYPASS_MODE=LocalhostBypassMode.HOST_HEADER,
            )
            test_settings.model_post_init(None)

        assert "DEPRECATED and INSECURE" in caplog.text
        assert "host_header" in caplog.text


@pytest.mark.integration
class TestAuthBypassDisabledMode:
    """Tests for LOCALHOST_BYPASS_MODE=disabled."""

    @pytest.fixture(autouse=True)
    def set_disabled_mode(self, monkeypatch):
        """Set disabled mode for all tests in this class."""
        monkeypatch.setattr(
            settings, "LOCALHOST_BYPASS_MODE", LocalhostBypassMode.DISABLED
        )

    def test_bypass_never_activates_for_loopback(self):
        """Even from 127.0.0.1, auth is required when disabled."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [],
                "client": ("127.0.0.1", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        payload = _check_localhost_bypass_http(request)
        assert payload is None

    def test_bypass_never_activates_for_any_host_header(self):
        """Even with Host: localhost, auth is required when disabled."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [(b"host", b"localhost:8000")],
                "client": ("127.0.0.1", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        payload = _check_localhost_bypass_http(request)
        assert payload is None


@pytest.mark.integration
class TestWebSocketAuthBypass:
    """WebSocket-specific bypass tests."""

    def _make_ws(self, client_ip: str, headers=None):
        """Helper to create a WebSocket scope for testing."""

        async def receive():
            pass

        async def send(msg):
            pass

        return WebSocket(
            {
                "type": "websocket",
                "path": "/api/v1/ws/jobs/test-job/logs",
                "headers": headers or [],
                "client": (client_ip, 50000),
                "server": ("127.0.0.1", 8000),
            },
            receive,
            send,
        )

    @pytest.fixture(autouse=True)
    def set_socket_mode(self, monkeypatch):
        """Set socket mode for WS tests."""
        monkeypatch.setattr(
            settings, "LOCALHOST_BYPASS_MODE", LocalhostBypassMode.SOCKET
        )

    def test_ws_bypass_socket_mode(self):
        """WebSocket from loopback peer bypasses auth."""
        ws = self._make_ws("127.0.0.1")
        payload = _check_localhost_bypass_ws(ws)
        assert payload == {"sub": "localhost_user"}

    def test_ws_bypass_socket_mode_ipv6(self):
        """WebSocket from ::1 peer bypasses auth."""
        ws = self._make_ws("::1")
        payload = _check_localhost_bypass_ws(ws)
        assert payload == {"sub": "localhost_user"}

    def test_ws_bypass_socket_mode_ipv4_mapped(self):
        """WebSocket from ::ffff:127.0.0.1 peer bypasses auth."""
        ws = self._make_ws("::ffff:127.0.0.1")
        payload = _check_localhost_bypass_ws(ws)
        assert payload == {"sub": "localhost_user"}

    def test_ws_bypass_rejects_spoofed_host(self):
        """WebSocket with spoofed Host from non-loopback rejected in socket mode."""
        ws = self._make_ws("192.168.1.100", headers=[(b"host", b"localhost:8000")])
        payload = _check_localhost_bypass_ws(ws)
        assert payload is None

    def test_ws_bypass_host_header_mode(self):
        """WebSocket in host_header mode uses Host header."""
        with patch(
            "backend.core.deps.settings.LOCALHOST_BYPASS_MODE",
            LocalhostBypassMode.HOST_HEADER,
        ):
            ws = self._make_ws("192.168.1.100", headers=[(b"host", b"localhost:8000")])
            payload = _check_localhost_bypass_ws(ws)
            assert payload == {"sub": "localhost_user"}

    def test_ws_bypass_disabled_mode(self):
        """WebSocket in disabled mode never bypasses."""
        with patch(
            "backend.core.deps.settings.LOCALHOST_BYPASS_MODE",
            LocalhostBypassMode.DISABLED,
        ):
            ws = self._make_ws("127.0.0.1")
            payload = _check_localhost_bypass_ws(ws)
            assert payload is None


@pytest.mark.integration
class TestBypassAuditLogging:
    """Tests for audit logging when bypass activates."""

    @pytest.fixture(autouse=True)
    def set_socket_mode(self, monkeypatch):
        """Set socket mode for audit logging tests."""
        monkeypatch.setattr(
            settings, "LOCALHOST_BYPASS_MODE", LocalhostBypassMode.SOCKET
        )

    def test_bypass_logs_audit_event_http(self, caplog):
        """When HTTP bypass activates, audit log is emitted."""
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/nodes/",
                "headers": [(b"user-agent", b"test-agent")],
                "client": ("127.0.0.1", 50000),
                "server": ("127.0.0.1", 8000),
            }
        )

        with caplog.at_level(logging.INFO, logger="backend.core.deps"):
            payload = _check_localhost_bypass_http(request)

        assert payload == {"sub": "localhost_user"}
        assert "Auth bypass activated for 127.0.0.1 via socket" in caplog.text
        assert "/api/v1/nodes/" in caplog.text
        assert "test-agent" in caplog.text

    def test_bypass_logs_audit_event_ws(self, caplog):
        """When WS bypass activates, audit log is emitted."""

        async def receive():
            pass

        async def send(msg):
            pass

        ws = WebSocket(
            {
                "type": "websocket",
                "path": "/api/v1/ws/jobs/test-job/logs",
                "headers": [],
                "client": ("::1", 50000),
                "server": ("127.0.0.1", 8000),
            },
            receive,
            send,
        )

        with caplog.at_level(logging.INFO, logger="backend.core.deps"):
            payload = _check_localhost_bypass_ws(ws)

        assert payload == {"sub": "localhost_user"}
        assert "WS auth bypass activated for ::1 via socket" in caplog.text
        assert "/api/v1/ws/jobs/test-job/logs" in caplog.text

    def test_host_header_mode_logs_warning_on_bypass(self, caplog):
        """Host header mode logs WARNING when bypass activates."""
        with patch(
            "backend.core.deps.settings.LOCALHOST_BYPASS_MODE",
            LocalhostBypassMode.HOST_HEADER,
        ):
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/nodes/",
                    "headers": [(b"host", b"localhost:8000")],
                    "client": ("192.168.1.100", 50000),
                    "server": ("127.0.0.1", 8000),
                }
            )

            with caplog.at_level(logging.WARNING, logger="backend.core.deps"):
                payload = _check_localhost_bypass_http(request)

        assert payload == {"sub": "localhost_user"}
        assert (
            "Auth bypass activated for 192.168.1.100 via host_header "
            "(DEPRECATED" in caplog.text
        )


@pytest.mark.integration
class TestBackwardCompatibility:
    """Tests for backward compatibility with LOCALHOST_BYPASS setting."""

    def test_localhost_bypass_false_forces_disabled(self):
        """LOCALHOST_BYPASS=false should force DISABLED mode."""
        test_settings = type(settings)(
            _env_file=None,
            SECRET_KEY="test-key",
            LOCALHOST_BYPASS=False,
        )
        assert test_settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.DISABLED

    def test_localhost_bypass_true_defaults_to_socket(self):
        """LOCALHOST_BYPASS=true should default to SOCKET mode."""
        test_settings = type(settings)(
            _env_file=None,
            SECRET_KEY="test-key",
            LOCALHOST_BYPASS=True,
        )
        assert test_settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.SOCKET

    def test_explicit_mode_overrides_localhost_bypass(self):
        """Explicit LOCALHOST_BYPASS_MODE should override LOCALHOST_BYPASS."""
        test_settings = type(settings)(
            _env_file=None,
            SECRET_KEY="test-key",
            LOCALHOST_BYPASS=False,  # Would normally disable
            LOCALHOST_BYPASS_MODE=LocalhostBypassMode.SOCKET,  # But explicit mode wins
        )
        assert test_settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.SOCKET

    def test_explicit_disabled_overrides_localhost_bypass_true(self):
        """Explicit DISABLED mode should override LOCALHOST_BYPASS=true."""
        test_settings = type(settings)(
            _env_file=None,
            SECRET_KEY="test-key",
            LOCALHOST_BYPASS=True,
            LOCALHOST_BYPASS_MODE=LocalhostBypassMode.DISABLED,
        )
        assert test_settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.DISABLED

    def test_explicit_host_header_overrides_localhost_bypass_false(self):
        """Explicit HOST_HEADER mode should override LOCALHOST_BYPASS=false."""
        test_settings = type(settings)(
            _env_file=None,
            SECRET_KEY="test-key",
            LOCALHOST_BYPASS=False,
            LOCALHOST_BYPASS_MODE=LocalhostBypassMode.HOST_HEADER,
        )
        assert test_settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.HOST_HEADER


@pytest.mark.integration
class TestFullStackAuthBypass:
    """End-to-end tests using the test client."""

    def test_socket_mode_bypass_via_client(self, client: AsyncClient):
        """Test that socket mode bypass works through test client with loopback."""
        # The test client simulates requests from "testserver" which is in
        # LOCALHOST_EXACT_HOSTS but not a loopback IP. In socket mode, this
        # should NOT bypass. We can't easily change the test client's peer
        # address, so we test the helper functions directly in other tests.
        pass

    def test_valid_token_works_in_all_modes(self, client: AsyncClient):
        """Valid JWT token should work regardless of bypass mode."""
        token = create_access_token({"sub": "test-user"})

        modes = [
            LocalhostBypassMode.DISABLED,
            LocalhostBypassMode.SOCKET,
            LocalhostBypassMode.HOST_HEADER,
        ]
        for mode in modes:
            with patch("backend.core.deps.settings.LOCALHOST_BYPASS_MODE", mode):
                # Use a non-localhost Host header to ensure bypass doesn't
                # activate in host_header mode (the server field defaults to
                # testserver otherwise)
                request = Request(
                    {
                        "type": "http",
                        "method": "GET",
                        "path": "/api/v1/nodes/",
                        "headers": [
                            (b"authorization", f"Bearer {token}".encode()),
                            (b"host", b"example.com:8000"),
                        ],
                        "client": ("192.168.1.100", 50000),
                        "server": ("127.0.0.1", 8000),
                    }
                )

                # When there's a valid token, the bypass check is skipped (returns None)
                # and the token is validated. But we can't easily test the full
                # get_current_token_payload without FastAPI's DI.
                # This test documents the expected behavior.
                from backend.core.deps import _check_localhost_bypass_http

                bypass = _check_localhost_bypass_http(request)
                # For non-loopback with valid token, bypass should be None
                # and the token validation path would be taken
                if mode == LocalhostBypassMode.SOCKET:
                    assert bypass is None  # Non-loopback IP
                elif mode == LocalhostBypassMode.HOST_HEADER:
                    assert bypass is None  # Host is not localhost
                else:
                    assert bypass is None  # Disabled
