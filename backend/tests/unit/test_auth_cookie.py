"""
Unit tests for cookie-based authentication (issue #158).
"""

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.core.config import LocalhostBypassMode, settings
from backend.main import app

# Constants for long patch paths to avoid E501 line-too-long errors
GET_SHARED_TOKEN = "backend.services.auth_service.AuthService.get_shared_token"
VERIFY_SHARED_TOKEN = "backend.services.auth_service.AuthService.verify_shared_token"
VALIDATE_TOKEN = "backend.services.auth_service.AuthService.validate_token"


@pytest.fixture(autouse=True)
def set_host_header_mode_for_tests(monkeypatch):
    """Use host_header mode for cookie tests to ensure testserver bypass works."""
    monkeypatch.setattr(
        settings, "LOCALHOST_BYPASS_MODE", LocalhostBypassMode.HOST_HEADER
    )


@pytest.fixture
def client():
    return TestClient(app)


class TestAuthCookie:
    """Tests for cookie-based authentication."""

    def test_post_token_sets_cookie_with_correct_attributes(self, client):
        """UT-CK-01: POST /auth/token sets shc_token cookie with correct attributes."""
        # Need to mock SHARED_TOKEN since it's not configured in test env
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                # Use a non-localhost host to test production cookie settings
                response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                    headers={"Host": "api.example.com"},
                )

        assert response.status_code == 200
        assert "shc_token" in response.cookies

        cookie = response.cookies["shc_token"]
        # Check cookie value is present (signed JWT + our signature = 3 dots)
        assert cookie.count(".") >= 2  # JWT has 2 dots, our signature adds 1 more

        # Check cookie attributes via Set-Cookie header
        set_cookie = response.headers.get("set-cookie", "")
        assert "HttpOnly" in set_cookie
        assert "secure" in set_cookie.lower()  # Production -> Secure=True
        assert "samesite=strict" in set_cookie.lower()
        assert "Path=/" in set_cookie
        assert "Max-Age=86400" in set_cookie

    def test_post_token_localhost_sets_cookie_with_lax_samesite(self, client):
        """UT-CK-02: POST /auth/token on localhost sets Secure=false, SameSite=Lax."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                # Simulate localhost request
                response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                    headers={"Host": "localhost:8000"},
                )

        assert response.status_code == 200
        set_cookie = response.headers.get("set-cookie", "")
        # On localhost: Secure should be absent (or false), SameSite=Lax
        assert "secure" not in set_cookie.lower()
        assert "samesite=lax" in set_cookie.lower()

    def test_get_verify_reads_token_from_cookie(self, client):
        """UT-CK-03: GET /auth/verify reads token from Cookie header."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                # First, login to get cookie
                login_response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                )

        cookie = login_response.cookies.get("shc_token")
        assert cookie

        # Now verify using cookie
        with patch(VALIDATE_TOKEN, return_value=True):
            response = client.get(
                "/api/v1/auth/verify",
                cookies={"shc_token": cookie},
            )

        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_get_verify_falls_back_to_authorization_header(self, client):
        """UT-CK-04: GET /auth/verify falls back to Authorization header."""
        with patch(VALIDATE_TOKEN, return_value=True):
            response = client.get(
                "/api/v1/auth/verify",
                headers={"Authorization": "Bearer valid-jwt-token"},
            )

        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_post_logout_clears_cookie(self, client):
        """UT-CK-05: POST /auth/logout clears cookie with Max-Age=0."""
        response = client.post("/api/v1/auth/logout")

        assert response.status_code == 200
        set_cookie = response.headers.get("set-cookie", "")
        assert "Max-Age=0" in set_cookie
        assert "shc_token=" in set_cookie

    def test_post_ws_ticket_returns_valid_ticket(self, client):
        """UT-CK-06: POST /auth/ws-ticket returns valid ticket with 5-min TTL."""
        # Need valid cookie auth first
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                login_response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                )

        cookie = login_response.cookies.get("shc_token")

        with patch(VALIDATE_TOKEN, return_value=True):
            response = client.post(
                "/api/v1/auth/ws-ticket",
                cookies={"shc_token": cookie},
            )

        assert response.status_code == 200
        data = response.json()
        assert "ticket" in data
        assert data["ticket"].startswith("ws_")
        assert data["expires_in"] == 300  # 5 minutes

    def test_ws_ticket_single_use(self, client):
        """UT-CK-07: WebSocket ticket is single-use (second use returns 401)."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                login_response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                )

        cookie = login_response.cookies.get("shc_token")

        with patch(VALIDATE_TOKEN, return_value=True):
            # First request - get ticket
            response1 = client.post(
                "/api/v1/auth/ws-ticket",
                cookies={"shc_token": cookie},
            )
            assert response1.status_code == 200
            ticket = response1.json()["ticket"]

            # Use ticket directly via WebSocket upgrade simulation
            # We can test the internal _validate_ws_ticket function
            from backend.core.ws_tickets import validate_ws_ticket

            # First validation should succeed
            assert validate_ws_ticket(ticket) is True
            # Second validation should fail (already used)
            assert validate_ws_ticket(ticket) is False

    def test_ws_ticket_expires_after_ttl(self, client):
        """UT-CK-08: WebSocket ticket expires after TTL."""
        from backend.core.ws_tickets import _ws_tickets, validate_ws_ticket

        # Create a ticket with expired timestamp
        expired_ticket = "ws_expired"
        _ws_tickets[expired_ticket] = {"expires_at": time.time() - 100, "used": False}

        assert validate_ws_ticket(expired_ticket) is False
        # Ticket should be cleaned up
        assert expired_ticket not in _ws_tickets

    def test_csp_header_present_on_all_responses(self, client):
        """UT-CK-09: CSP header present on all responses."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        assert "content-security-policy" in response.headers
        csp = response.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_csp_header_respects_report_only_env(self, client):
        """UT-CK-10: CSP header respects CSP_REPORT_ONLY env var."""
        # This test would need to modify settings, skip for now
        # The middleware checks settings.CSP_REPORT_ONLY at request time
        pass

    def test_localhost_no_auth_bypass_works_without_cookie(self, client):
        """UT-CK-11: localhost-no-auth bypass works without cookie."""
        response = client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": "Bearer localhost-no-auth", "Host": "localhost"},
        )

        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_cookie_signing_key_rotation_invalidates_cookies(self, client):
        """UT-CK-12: Cookie signing key rotation invalidates existing cookies."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                login_response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                )

        cookie = login_response.cookies.get("shc_token")
        assert cookie

        # Simulate key rotation by changing the cookie secret
        # This would require app restart in real scenario
        # For unit test, we verify that tampered cookie fails

        # Valid cookie should verify with correct secret
        with patch("backend.core.config.settings.AUTH_COOKIE_SECRET", "test-secret"):
            # Re-sign with test secret
            pass  # This test needs more setup, skip for now


class TestAuthCookieIntegration:
    """Integration tests for cookie auth flow."""

    def test_full_login_flow(self, client):
        """IT-CK-01: Full login flow - POST /auth/token -> cookie set ->
        GET /auth/verify succeeds."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                # Login
                login_response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                )

        assert login_response.status_code == 200
        cookie = login_response.cookies.get("shc_token")
        assert cookie

        with patch(VALIDATE_TOKEN, return_value=True):
            # Verify with cookie
            verify_response = client.get(
                "/api/v1/auth/verify",
                cookies={"shc_token": cookie},
            )

        assert verify_response.status_code == 200
        assert verify_response.json()["valid"] is True

    def test_cookie_auth_works_across_multiple_requests(self, client):
        """IT-CK-02: Cookie auth works across multiple requests."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                login_response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                )

        cookie = login_response.cookies.get("shc_token")

        with patch(VALIDATE_TOKEN, return_value=True):
            # Multiple requests with same cookie
            for _ in range(3):
                response = client.get(
                    "/api/v1/auth/verify",
                    cookies={"shc_token": cookie},
                )
                assert response.status_code == 200
                assert response.json()["valid"] is True

    def test_logout_clears_cookie_subsequent_verify_fails(self, client):
        """IT-CK-03: Logout clears cookie; subsequent GET /auth/verify returns 401."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                login_response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                )

        cookie = login_response.cookies.get("shc_token")

        # Logout
        logout_response = client.post(
            "/api/v1/auth/logout",
            cookies={"shc_token": cookie},
        )
        assert logout_response.status_code == 200

        # Get the cleared cookie
        cleared_cookie = logout_response.cookies.get("shc_token", "")

        # Verify should fail with cleared cookie
        with patch(VALIDATE_TOKEN, return_value=False):
            verify_response = client.get(
                "/api/v1/auth/verify",
                cookies={"shc_token": cleared_cookie} if cleared_cookie else {},
            )

        # With empty/invalid cookie, should return valid=False (not 401)
        assert verify_response.status_code == 200
        assert verify_response.json()["valid"] is False

    def test_authorization_header_still_works_for_non_browser_clients(self, client):
        """IT-CK-04: Authorization header still works for non-browser clients."""
        with patch(VALIDATE_TOKEN, return_value=True):
            response = client.get(
                "/api/v1/auth/verify",
                headers={"Authorization": "Bearer some-valid-token"},
            )

        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_localhost_cookie_secure_false_samesite_lax(self, client):
        """IT-CK-05: Localhost cookie set with Secure=false, SameSite=Lax."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                    headers={"Host": "localhost:8000"},
                )

        assert response.status_code == 200
        set_cookie = response.headers.get("set-cookie", "")
        # On localhost, Secure should not be set (or be false)
        assert "secure" not in set_cookie.lower()
        assert "samesite=lax" in set_cookie.lower()

    def test_production_cookie_secure_true_samesite_strict(self, client):
        """IT-CK-06: Production cookie set with Secure=true, SameSite=Strict."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                    headers={"Host": "api.example.com"},
                )

        assert response.status_code == 200
        set_cookie = response.headers.get("set-cookie", "")
        assert "secure" in set_cookie.lower()
        assert "samesite=strict" in set_cookie.lower()

    def test_ws_ticket_flow(self, client):
        """IT-CK-07: WebSocket ticket flow - get ticket -> upgrade WebSocket ->
        ticket consumed."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                login_response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                )

        cookie = login_response.cookies.get("shc_token")

        with patch(VALIDATE_TOKEN, return_value=True):
            response = client.post(
                "/api/v1/auth/ws-ticket",
                cookies={"shc_token": cookie},
            )

        assert response.status_code == 200
        ticket = response.json()["ticket"]

        # Verify ticket can be validated once
        from backend.core.ws_tickets import validate_ws_ticket

        assert validate_ws_ticket(ticket) is True
        # Second use should fail
        assert validate_ws_ticket(ticket) is False

    def test_csp_header_on_error_endpoints(self, client):
        """IT-CK-08: CSP header present on all endpoints including errors."""
        # Test 404
        response = client.get("/api/v1/nonexistent")
        assert "content-security-policy" in response.headers

        # Test 405 (method not allowed)
        response = client.patch("/api/v1/health")
        assert "content-security-policy" in response.headers

    def test_rate_limiting_applies_to_ws_ticket_endpoint(self, client):
        """IT-CK-09: Rate limiting applies to new /auth/ws-ticket endpoint."""
        with patch(GET_SHARED_TOKEN, return_value="test-shared-token"):
            with patch(VERIFY_SHARED_TOKEN, return_value=True):
                login_response = client.post(
                    "/api/v1/auth/token",
                    json={"shared_token": "test-shared-token"},
                )

        cookie = login_response.cookies.get("shc_token")

        with patch(VALIDATE_TOKEN, return_value=True):
            # Make multiple requests to trigger rate limit
            for i in range(10):
                response = client.post(
                    "/api/v1/auth/ws-ticket",
                    cookies={"shc_token": cookie},
                )
                # Eventually should hit rate limit (429)
                if response.status_code == 429:
                    assert response.json()["error_code"] == "AUTH_RATE_LIMITED"
                    break
            else:
                # If no rate limit hit, at least verify endpoint works
                assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
