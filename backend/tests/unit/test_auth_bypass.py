"""
Unit tests for localhost authentication bypass logic.

Tests the new LOCALHOST_BYPASS_MODE configuration and the is_loopback_ip utility.
"""

import logging
from unittest.mock import patch

import pytest

from backend.core.config import LocalhostBypassMode, Settings
from backend.core.utils import is_loopback_ip


@pytest.mark.unit
class TestIsLoopbackIP:
    """Tests for the is_loopback_ip utility function."""

    @pytest.mark.parametrize(
        "ip,expected",
        [
            # IPv4 loopback
            ("127.0.0.1", True),
            ("127.0.0.2", True),
            ("127.255.255.255", True),
            # IPv4 non-loopback
            ("192.168.1.1", False),
            ("10.0.0.1", False),
            ("172.16.0.1", False),
            ("8.8.8.8", False),
            # IPv6 loopback
            ("::1", True),
            # IPv4-mapped IPv6 loopback
            ("::ffff:127.0.0.1", True),
            ("::ffff:127.0.0.2", True),
            # IPv4-mapped IPv6 non-loopback
            ("::ffff:192.168.1.1", False),
            ("::ffff:10.0.0.1", False),
            # Other IPv6
            ("2001:db8::1", False),
            ("fe80::1", False),  # link-local, not loopback
            # Invalid inputs
            ("not-an-ip", False),
            ("", False),
            (None, False),
        ],
    )
    def test_is_loopback_ip(self, ip, expected):
        """Test is_loopback_ip with various IP addresses."""
        assert is_loopback_ip(ip) is expected


@pytest.mark.unit
class TestLocalhostBypassMode:
    """Tests for the LocalhostBypassMode enum."""

    def test_enum_values(self):
        """Test that enum has expected values."""
        assert LocalhostBypassMode.DISABLED.value == "disabled"
        assert LocalhostBypassMode.SOCKET.value == "socket"
        assert LocalhostBypassMode.HOST_HEADER.value == "host_header"


@pytest.mark.unit
class TestConfigLocalhostBypassMode:
    """Tests for LOCALHOST_BYPASS_MODE configuration resolution."""

    def test_default_mode_is_socket(self):
        """Default mode should be SOCKET when LOCALHOST_BYPASS is not
        explicitly false."""
        settings = Settings(
            _env_file=None, SECRET_KEY="test-key", LOCALHOST_BYPASS=True
        )
        assert settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.SOCKET

    def test_explicit_disabled_via_localhost_bypass_false(self):
        """LOCALHOST_BYPASS=false should force DISABLED mode."""
        settings = Settings(
            _env_file=None, SECRET_KEY="test-key", LOCALHOST_BYPASS=False
        )
        assert settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.DISABLED

    def test_explicit_mode_via_env(self, monkeypatch):
        """Explicit LOCALHOST_BYPASS_MODE should be respected."""
        monkeypatch.setenv("LOCALHOST_BYPASS_MODE", "disabled")
        settings = Settings(_env_file=None, SECRET_KEY="test-key")
        assert settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.DISABLED

    def test_explicit_host_header_mode(self, monkeypatch):
        """Explicit host_header mode should be respected."""
        monkeypatch.setenv("LOCALHOST_BYPASS_MODE", "host_header")
        settings = Settings(_env_file=None, SECRET_KEY="test-key")
        assert settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.HOST_HEADER

    def test_explicit_socket_mode(self, monkeypatch):
        """Explicit socket mode should be respected."""
        monkeypatch.setenv("LOCALHOST_BYPASS_MODE", "socket")
        settings = Settings(_env_file=None, SECRET_KEY="test-key")
        assert settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.SOCKET

    def test_invalid_mode_defaults_to_socket(self, monkeypatch, caplog):
        """Invalid mode should default to SOCKET with warning."""
        monkeypatch.setenv("LOCALHOST_BYPASS_MODE", "invalid")
        with caplog.at_level(logging.WARNING, logger="backend.core.config"):
            settings = Settings(_env_file=None, SECRET_KEY="test-key")
        assert settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.SOCKET
        assert any(
            "Invalid LOCALHOST_BYPASS_MODE" in r.getMessage() for r in caplog.records
        )

    def test_case_insensitive_mode(self, monkeypatch):
        """Mode values should be case-insensitive."""
        for mode in ["SOCKET", "Socket", "socket", "HOST_HEADER", "host_header"]:
            monkeypatch.setenv("LOCALHOST_BYPASS_MODE", mode)
            settings = Settings(_env_file=None, SECRET_KEY="test-key")
            is_socket = mode.lower() == "socket"
            expected = (
                LocalhostBypassMode.SOCKET
                if is_socket
                else LocalhostBypassMode.HOST_HEADER
            )
            assert settings.LOCALHOST_BYPASS_MODE == expected


@pytest.mark.unit
class TestConfigStartupLogging:
    """Tests for startup logging of localhost bypass configuration."""

    def test_disabled_mode_logs_info(self, caplog):
        """DISABLED mode should log INFO message."""
        with caplog.at_level(logging.INFO, logger="backend.core.config"):
            settings = Settings(
                _env_file=None,
                SECRET_KEY="test-key",
                LOCALHOST_BYPASS_MODE=LocalhostBypassMode.DISABLED,
            )
            settings.model_post_init(None)
        assert "Localhost authentication bypass is DISABLED" in caplog.text

    def test_socket_mode_logs_info(self, caplog):
        """SOCKET mode should log INFO message with mode and warning
        about convenience."""
        with caplog.at_level(logging.INFO, logger="backend.core.config"):
            settings = Settings(
                _env_file=None,
                SECRET_KEY="test-key",
                LOCALHOST_BYPASS_MODE=LocalhostBypassMode.SOCKET,
            )
            settings.model_post_init(None)
        assert (
            "Localhost authentication bypass is ENABLED (mode: socket)" in caplog.text
        )
        assert "developer convenience, not a security boundary" in caplog.text
        assert "Accepted loopback IPs for socket mode" in caplog.text
        assert "127.0.0.1" in caplog.text
        assert "::1" in caplog.text

    def test_host_header_mode_logs_warning(self, caplog):
        """HOST_HEADER mode should log WARNING about deprecation."""
        with caplog.at_level(logging.WARNING, logger="backend.core.config"):
            settings = Settings(
                _env_file=None,
                SECRET_KEY="test-key",
                LOCALHOST_BYPASS_MODE=LocalhostBypassMode.HOST_HEADER,
            )
            settings.model_post_init(None)
        assert "DEPRECATED and INSECURE" in caplog.text
        assert "host_header" in caplog.text
        assert "client-supplied Host header" in caplog.text


@pytest.mark.unit
class TestAuthBypassHelpers:
    """Tests for the internal bypass helper functions."""

    def test_check_localhost_bypass_http_disabled(self):
        """HTTP bypass should return None when DISABLED."""
        from backend.core.deps import _check_localhost_bypass_http
        from starlette.requests import Request

        with patch(
            "backend.core.deps.settings.LOCALHOST_BYPASS_MODE",
            LocalhostBypassMode.DISABLED,
        ):
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/test",
                    "headers": [],
                    "client": ("127.0.0.1", 12345),
                    "server": ("127.0.0.1", 8000),
                }
            )
            result = _check_localhost_bypass_http(request)
            assert result is None

    def test_check_localhost_bypass_http_socket_mode_loopback(self):
        """HTTP bypass should activate for loopback IP in SOCKET mode."""
        from backend.core.deps import _check_localhost_bypass_http
        from starlette.requests import Request

        with patch(
            "backend.core.deps.settings.LOCALHOST_BYPASS_MODE",
            LocalhostBypassMode.SOCKET,
        ):
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/test",
                    "headers": [],
                    "client": ("127.0.0.1", 12345),
                    "server": ("127.0.0.1", 8000),
                }
            )
            result = _check_localhost_bypass_http(request)
            assert result == {"sub": "localhost_user"}

    def test_check_localhost_bypass_http_socket_mode_non_loopback(self):
        """HTTP bypass should NOT activate for non-loopback IP in SOCKET mode."""
        from backend.core.deps import _check_localhost_bypass_http
        from starlette.requests import Request

        with patch(
            "backend.core.deps.settings.LOCALHOST_BYPASS_MODE",
            LocalhostBypassMode.SOCKET,
        ):
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/test",
                    "headers": [],
                    "client": ("192.168.1.100", 12345),
                    "server": ("127.0.0.1", 8000),
                }
            )
            result = _check_localhost_bypass_http(request)
            assert result is None

    def test_check_localhost_bypass_http_host_header_mode(self):
        """HTTP bypass should use Host header in HOST_HEADER mode."""
        from backend.core.deps import _check_localhost_bypass_http
        from starlette.requests import Request

        with patch(
            "backend.core.deps.settings.LOCALHOST_BYPASS_MODE",
            LocalhostBypassMode.HOST_HEADER,
        ):
            # Request from non-loopback IP but with localhost Host header
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/test",
                    "headers": [(b"host", b"localhost:8000")],
                    "client": ("192.168.1.100", 12345),
                    "server": ("127.0.0.1", 8000),
                }
            )
            result = _check_localhost_bypass_http(request)
            assert result == {"sub": "localhost_user"}

    def test_check_localhost_bypass_ws_disabled(self):
        """WS bypass should return None when DISABLED."""
        from backend.core.deps import _check_localhost_bypass_ws
        from starlette.websockets import WebSocket

        async def receive():
            pass

        async def send(msg):
            pass

        with patch(
            "backend.core.deps.settings.LOCALHOST_BYPASS_MODE",
            LocalhostBypassMode.DISABLED,
        ):
            ws = WebSocket(
                {
                    "type": "websocket",
                    "path": "/ws",
                    "headers": [],
                    "client": ("127.0.0.1", 12345),
                    "server": ("127.0.0.1", 8000),
                },
                receive,
                send,
            )
            result = _check_localhost_bypass_ws(ws)
            assert result is None

    def test_check_localhost_bypass_ws_socket_mode_loopback(self):
        """WS bypass should activate for loopback IP in SOCKET mode."""
        from backend.core.deps import _check_localhost_bypass_ws
        from starlette.websockets import WebSocket

        async def receive():
            pass

        async def send(msg):
            pass

        with patch(
            "backend.core.deps.settings.LOCALHOST_BYPASS_MODE",
            LocalhostBypassMode.SOCKET,
        ):
            ws = WebSocket(
                {
                    "type": "websocket",
                    "path": "/ws",
                    "headers": [],
                    "client": ("::1", 12345),
                    "server": ("127.0.0.1", 8000),
                },
                receive,
                send,
            )
            result = _check_localhost_bypass_ws(ws)
            assert result == {"sub": "localhost_user"}

    def test_check_localhost_bypass_ws_socket_mode_ipv4_mapped(self):
        """WS bypass should activate for IPv4-mapped loopback in SOCKET mode."""
        from backend.core.deps import _check_localhost_bypass_ws
        from starlette.websockets import WebSocket

        async def receive():
            pass

        async def send(msg):
            pass

        with patch(
            "backend.core.deps.settings.LOCALHOST_BYPASS_MODE",
            LocalhostBypassMode.SOCKET,
        ):
            ws = WebSocket(
                {
                    "type": "websocket",
                    "path": "/ws",
                    "headers": [],
                    "client": ("::ffff:127.0.0.1", 12345),
                    "server": ("127.0.0.1", 8000),
                },
                receive,
                send,
            )
            result = _check_localhost_bypass_ws(ws)
            assert result == {"sub": "localhost_user"}
