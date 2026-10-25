# Specification: Fix Auth Bypass from Client-Controlled Host Header

**Issue**: #51  
**Status**: Groomed  
**Author**: PM Agent  
**Date**: 2026-10-25  

---

## Problem Statement

The current localhost authentication bypass in `backend/core/deps.py` derives its decision from `request.url.hostname`, which is parsed from the client-supplied `Host` header. This creates a security vulnerability:

- Any client that can reach the backend can set an arbitrary `Host` header (e.g., `Host: localhost`)
- This grants the localhost bypass without actually being on localhost
- The bypass is a developer-convenience affordance, not a security boundary
- The trust assumption is implicit and unenforced

### Current Code Location

- **Backend**: `backend/core/deps.py:28` and `backend/core/deps.py:65` — reads `request.url.hostname` / `websocket.url.hostname`
- **Validation**: `backend/core/utils.py:72` — `is_localhost()` function
- **Config**: `backend/core/config.py:558` — `LOCALHOST_BYPASS: bool = True`
- **Shared List**: `shared/auth/localhost_hosts.json` — canonical list of hosts/suffixes
- **Frontend Mirror**: `frontend/src/lib/settings.ts:46` — `isLocalhost()` function

---

## User Stories

### US1: Developer Convenience (Preserved)
> As a developer running the backend locally, I want authentication to be bypassed when I access the API from my local machine, so I can develop without managing tokens.

### US2: Secure Default (New)
> As a security-conscious operator, I want the localhost bypass to only activate when the request genuinely originates from localhost (based on socket peer address), not when a client spoofs the `Host` header.

### US3: Explicit Opt-In (New)
> As an operator deploying to a shared network, I want the localhost bypass to be an explicit, documented opt-in with a startup warning, so I am aware when the security boundary is lowered.

### US4: Audit Trail (New)
> As an operator, I want authentication bypass events to be logged, so I can audit when and why the bypass was triggered.

---

## Acceptance Criteria

### AC1: Bind Bypass to Socket Peer Address (Primary Fix)
- [ ] The auth bypass decision MUST be based on `request.client.host` (the TCP peer IP address) rather than `request.url.hostname` (the client-supplied Host header)
- [ ] For WebSocket connections, use `websocket.client.host` equivalently
- [ ] The bypass SHOULD only trigger when `request.client.host` is a loopback address (`127.0.0.1`, `::1`, `::ffff:127.0.0.1`)
- [ ] IPv6 loopback in IPv4-mapped form (`::ffff:127.0.0.1`) MUST be handled

### AC2: Explicit Configuration Flag
- [ ] Add a new setting `LOCALHOST_BYPASS_MODE` with enum values:
  - `"disabled"` — Bypass never activates (secure default for production)
  - `"socket"` — Bypass activates only for loopback socket peers (new default for development)
  - `"host_header"` — Legacy behavior (client-controlled Host header) — DEPRECATED, emits startup WARNING
- [ ] Default value: `"socket"` (secure by default, preserves developer convenience)
- [ ] When `LOCALHOST_BYPASS_MODE=host_header`, emit a WARNING log at startup: `"LOCALHOST_BYPASS_MODE=host_header is DEPRECATED and INSECURE: it trusts the client-supplied Host header. Use 'socket' mode instead."`

### AC3: Startup Warning for Enabled Bypass
- [ ] When `LOCALHOST_BYPASS_MODE` is not `"disabled"`, emit an INFO log at startup: `"Localhost authentication bypass is ENABLED (mode: <mode>). This is a developer convenience, not a security boundary."`
- [ ] Log the resolved loopback IPs that will be accepted

### AC4: Bypass Audit Logging
- [ ] When the bypass activates (authentication is skipped), emit an INFO log: `"Auth bypass activated for <client_ip> via <mode> (path: <request.path>)"`
- [ ] Include: client IP, bypass mode, request path, user agent (if available)

### AC5: Frontend Alignment (No Functional Change)
- [ ] The frontend `isLocalhost()` function in `frontend/src/lib/settings.ts` uses `window.location.hostname` (browser-controlled, not spoofable in normal browser contexts)
- [ ] This is acceptable — the frontend cannot be spoofed by a malicious client in the same way
- [ ] No changes needed to frontend logic, but ensure `shared/auth/localhost_hosts.json` stays in sync

### AC6: Configuration Documentation
- [ ] Document `LOCALHOST_BYPASS_MODE` in `docs/spec.md#authentication`
- [ ] Update `README.md` with configuration guidance
- [ ] Add migration note for existing deployments using the legacy behavior

### AC7: Tests
- [ ] Unit tests for `is_localhost()` with socket peer IPs
- [ ] Integration tests verifying bypass activates for `127.0.0.1` peer but not for spoofed `Host: localhost` from non-loopback IP
- [ ] Integration tests for all three modes: `disabled`, `socket`, `host_header`
- [ ] Test that WebSocket bypass uses `websocket.client.host`
- [ ] Test startup warnings are emitted correctly

---

## Technical Design

### Configuration Changes

```python
# backend/core/config.py

from enum import Enum

class LocalhostBypassMode(str, Enum):
    DISABLED = "disabled"
    SOCKET = "socket"          # NEW DEFAULT: trust socket peer
    HOST_HEADER = "host_header"  # DEPRECATED: trust client Host header

class Settings(BaseSettings):
    ...
    LOCALHOST_BYPASS_MODE: LocalhostBypassMode = LocalhostBypassMode.SOCKET
    ...
```

### Utility Function: Loopback IP Detection

```python
# backend/core/utils.py

import ipaddress
from typing import Optional

def is_loopback_ip(ip: Optional[str]) -> bool:
    """
    Check if an IP address is a loopback address.
    
    Handles:
    - IPv4: 127.0.0.1, 127.0.0.0/8
    - IPv6: ::1, ::ffff:127.0.0.1 (IPv4-mapped)
    - IPv6: ::ffff:127.0.0.0/104 (IPv4-mapped loopback range)
    """
    if not ip:
        return False
    
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_loopback
    except ValueError:
        return False
```

### Updated Bypass Logic in `deps.py`

```python
# backend/core/deps.py

from backend.core.utils import is_loopback_ip
from backend.core.config import settings, LocalhostBypassMode

async def get_current_token_payload(
    request: Request,
    token: HTTPAuthorizationCredentials = Depends(reusable_oauth2),
) -> dict:
    # Check if we are in localhost bypass mode
    if settings.LOCALHOST_BYPASS_MODE != LocalhostBypassMode.DISABLED:
        client_host = request.client.host if request.client else None
        
        if settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.SOCKET:
            # NEW: Trust the socket peer address
            if is_loopback_ip(client_host):
                logger.info(
                    "Auth bypass activated for %s via socket (path: %s)",
                    client_host, request.url.path
                )
                return {"sub": "localhost_user"}
        
        elif settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.HOST_HEADER:
            # DEPRECATED: Trust the client-supplied Host header
            hostname = request.url.hostname
            if is_localhost(hostname):
                logger.warning(
                    "Auth bypass activated for %s via host_header (DEPRECATED, path: %s)",
                    client_host, request.url.path
                )
                return {"sub": "localhost_user"}

    # ... rest of existing token validation
```

### WebSocket Equivalent

```python
# backend/core/deps.py — get_ws_token_payload()

if settings.LOCALHOST_BYPASS_MODE != LocalhostBypassMode.DISABLED:
    client_host = websocket.client.host if websocket.client else None
    
    if settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.SOCKET:
        if is_loopback_ip(client_host):
            logger.info(
                "WS auth bypass activated for %s via socket (path: %s)",
                client_host, websocket.url.path
            )
            return {"sub": "localhost_user"}
    
    elif settings.LOCALHOST_BYPASS_MODE == LocalhostBypassMode.HOST_HEADER:
        hostname = websocket.url.hostname
        if is_localhost(hostname):
            logger.warning(
                "WS auth bypass activated for %s via host_header (DEPRECATED, path: %s)",
                client_host, websocket.url.path
            )
            return {"sub": "localhost_user"}
```

### Startup Logging

```python
# backend/core/config.py — in model_post_init() or a new init function

def _log_localhost_bypass_config() -> None:
    mode = settings.LOCALHOST_BYPASS_MODE
    if mode == LocalhostBypassMode.DISABLED:
        logger.info("Localhost authentication bypass is DISABLED")
        return
    
    logger.info(
        "Localhost authentication bypass is ENABLED (mode: %s). "
        "This is a developer convenience, not a security boundary.",
        mode.value
    )
    
    if mode == LocalhostBypassMode.HOST_HEADER:
        logger.warning(
            "LOCALHOST_BYPASS_MODE=host_header is DEPRECATED and INSECURE: "
            "it trusts the client-supplied Host header. Use 'socket' mode instead."
        )
    
    # Log accepted loopback IPs
    loopback_ips = ["127.0.0.1", "::1", "::ffff:127.0.0.1"]
    logger.info("Accepted loopback IPs for socket mode: %s", ", ".join(loopback_ips))
```

---

## Migration Notes

### For Existing Deployments

1. **Default behavior change**: Previously `LOCALHOST_BYPASS=true` used Host header. Now default is `LOCALHOST_BYPASS_MODE=socket` (peer IP based).
2. **To preserve legacy behavior**: Set `LOCALHOST_BYPASS_MODE=host_header` (will emit deprecation warning).
3. **To disable entirely**: Set `LOCALHOST_BYPASS_MODE=disabled`.
4. **Environment variable**: `LOCALHOST_BYPASS_MODE` (values: `disabled`, `socket`, `host_header`)

### Backward Compatibility

- The old `LOCALHOST_BYPASS: bool` setting is DEPRECATED but still read for backward compatibility
- If `LOCALHOST_BYPASS=false`, it forces `LOCALHOST_BYPASS_MODE=disabled`
- If `LOCALHOST_BYPASS=true` and `LOCALHOST_BYPASS_MODE` not set, defaults to `socket` (new behavior)
- Add a field validator to handle the transition gracefully

---

## Test Scenarios

### Unit Tests: `backend/tests/unit/test_utils.py`

```python
def test_is_loopback_ip_ipv4():
    assert is_loopback_ip("127.0.0.1") is True
    assert is_loopback_ip("127.0.0.2") is True
    assert is_loopback_ip("192.168.1.1") is False
    assert is_loopback_ip("10.0.0.1") is False

def test_is_loopback_ip_ipv6():
    assert is_loopback_ip("::1") is True
    assert is_loopback_ip("::ffff:127.0.0.1") is True
    assert is_loopback_ip("::ffff:192.168.1.1") is False
    assert is_loopback_ip("2001:db8::1") is False

def test_is_loopback_ip_invalid():
    assert is_loopback_ip("not-an-ip") is False
    assert is_loopback_ip("") is False
    assert is_loopback_ip(None) is False
```

### Integration Tests: `backend/tests/integration/test_auth_bypass.py`

```python
import pytest
from httpx import AsyncClient
from backend.main import app

class TestAuthBypassSocketMode:
    """Tests for LOCALHOST_BYPASS_MODE=socket (default)"""
    
    @pytest.fixture(autouse=True)
    def override_settings(self, monkeypatch):
        monkeypatch.setenv("LOCALHOST_BYPASS_MODE", "socket")
        # Re-import settings
        from backend.core.config import settings
        settings.LOCALHOST_BYPASS_MODE = "socket"
    
    async def test_bypass_activates_for_loopback_client(self, async_client: AsyncClient):
        """Request from 127.0.0.1 should bypass auth"""
        # TestClient uses testserver, but we can mock request.client.host
        # For real test, need to test with actual socket peer
        pass
    
    async def test_bypass_rejects_spoofed_host_header(self, async_client: AsyncClient):
        """Request from non-loopback IP with Host: localhost should NOT bypass"""
        # Make request from a non-loopback IP (simulated)
        # with Host: localhost header
        # Should return 401
        pass
    
    async def test_bypass_rejects_non_loopback_ip(self, async_client: AsyncClient):
        """Request from 192.168.1.100 should not bypass even with Host: localhost"""
        pass

class TestAuthBypassHostHeaderMode:
    """Tests for LOCALHOST_BYPASS_MODE=host_header (legacy)"""
    
    @pytest.fixture(autouse=True)
    def override_settings(self, monkeypatch):
        monkeypatch.setenv("LOCALHOST_BYPASS_MODE", "host_header")
        from backend.core.config import settings
        settings.LOCALHOST_BYPASS_MODE = "host_header"
    
    async def test_bypass_activates_for_spoofed_host(self, async_client: AsyncClient):
        """Legacy mode: Host: localhost from any IP bypasses auth"""
        # This is the INSECURE behavior we're fixing
        pass
    
    async def test_startup_warning_emitted(self, caplog):
        """Verify deprecation warning is logged at startup"""
        pass

class TestAuthBypassDisabledMode:
    """Tests for LOCALHOST_BYPASS_MODE=disabled"""
    
    @pytest.fixture(autouse=True)
    def override_settings(self, monkeypatch):
        monkeypatch.setenv("LOCALHOST_BYPASS_MODE", "disabled")
        from backend.core.config import settings
        settings.LOCALHOST_BYPASS_MODE = "disabled"
    
    async def test_bypass_never_activates(self, async_client: AsyncClient):
        """Even from 127.0.0.1, auth is required"""
        pass

class TestWebSocketAuthBypass:
    """WebSocket-specific bypass tests"""
    
    async def test_ws_bypass_socket_mode(self):
        """WebSocket from loopback peer bypasses auth"""
        pass
    
    async def test_ws_bypass_rejects_spoofed_host(self):
        """WebSocket with spoofed Host from non-loopback rejected"""
        pass
```

### Test: Startup Logging

```python
# backend/tests/unit/test_config.py

def test_localhost_bypass_startup_logs(caplog):
    """Verify correct startup logs for each mode"""
    import logging
    from backend.core.config import Settings, LocalhostBypassMode
    
    # Test socket mode (default)
    with caplog.at_level(logging.INFO):
        settings = Settings(LOCALHOST_BYPASS_MODE=LocalhostBypassMode.SOCKET)
        settings.model_post_init(None)
    
    assert "Localhost authentication bypass is ENABLED (mode: socket)" in caplog.text
    assert "developer convenience, not a security boundary" in caplog.text
    assert "Accepted loopback IPs" in caplog.text
    
    caplog.clear()
    
    # Test host_header mode (deprecated)
    with caplog.at_level(logging.WARNING):
        settings = Settings(LOCALHOST_BYPASS_MODE=LocalhostBypassMode.HOST_HEADER)
        settings.model_post_init(None)
    
    assert "DEPRECATED and INSECURE" in caplog.text
    assert "host_header" in caplog.text
    
    caplog.clear()
    
    # Test disabled mode
    with caplog.at_level(logging.INFO):
        settings = Settings(LOCALHOST_BYPASS_MODE=LocalhostBypassMode.DISABLED)
        settings.model_post_init(None)
    
    assert "Localhost authentication bypass is DISABLED" in caplog.text
```

### Test: Audit Logging on Bypass Activation

```python
# backend/tests/integration/test_auth_bypass.py

async def test_bypass_logs_audit_event(caplog, async_client: AsyncClient):
    """When bypass activates, audit log is emitted"""
    import logging
    caplog.set_level(logging.INFO)
    
    # Make request that triggers bypass (from loopback peer)
    # Verify log contains: client IP, mode, path
    assert "Auth bypass activated for" in caplog.text
    assert "via socket" in caplog.text
    assert "/api/v1/" in caplog.text
```

---

## API Changes Summary

| Setting | Old | New |
|---------|-----|-----|
| `LOCALHOST_BYPASS` | `bool` (default: `true`) | **DEPRECATED** — still read for compat |
| `LOCALHOST_BYPASS_MODE` | N/A | `Enum[disabled, socket, host_header]` (default: `socket`) |

### Environment Variables

```bash
# New (preferred)
LOCALHOST_BYPASS_MODE=socket      # default, secure
LOCALHOST_BYPASS_MODE=disabled    # production secure
LOCALHOST_BYPASS_MODE=host_header # legacy, DEPRECATED, warns

# Legacy (still works, maps to new)
LOCALHOST_BYPASS=false            # -> MODE=disabled
LOCALHOST_BYPASS=true             # -> MODE=socket (unless MODE explicitly set)
```

---

## Files to Modify

1. **`backend/core/config.py`**
   - Add `LocalhostBypassMode` enum
   - Add `LOCALHOST_BYPASS_MODE` field with default `SOCKET`
   - Add field validator for backward compat with `LOCALHOST_BYPASS`
   - Add `_log_localhost_bypass_config()` called in `model_post_init()`

2. **`backend/core/utils.py`**
   - Add `is_loopback_ip()` function
   - Keep existing `is_localhost()` for Host-header mode and frontend sync

3. **`backend/core/deps.py`**
   - Update `get_current_token_payload()` to use socket peer logic
   - Update `get_ws_token_payload()` equivalently
   - Import `is_loopback_ip`, `LocalhostBypassMode`, `logger`

3. **`backend/tests/unit/test_utils.py`**
   - Add tests for `is_loopback_ip()`

4. **`backend/tests/integration/test_auth_bypass.py`** (new file)
   - Comprehensive integration tests for all three modes
   - WebSocket tests
   - Audit logging tests

5. **`backend/tests/unit/test_config.py`**
   - Add startup logging tests

6. **`docs/spec.md`**
   - Update authentication section with new configuration

7. **`README.md`**
   - Document new `LOCALHOST_BYPASS_MODE` setting

---

## Definition of Done

- [ ] All acceptance criteria (AC1–AC7) verified
- [ ] Backend lint gates pass: `ruff check . && ruff format --check . && mypy .` (from repo root)
- [ ] Backend tests pass: `pytest --cov=backend`
- [ ] Frontend gates unaffected (no frontend changes required)
- [ ] Spec documented in `docs/spec.md#authentication`
- [ ] README updated with migration guide
- [ ] PM accepts the implementation

---

## Related Issues

- #31 — Added `::1` to localhost hosts (fixed IPv6 bracket normalization)
- #56 — Rotated published JWT signing keys
- #157 — Auth rate limiting (related security hardening)
- #158 — Cookie-based auth / CSP (related security hardening)