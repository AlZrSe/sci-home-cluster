# Specification: Remove WebSocket Auth via Query Parameter (Issue #156)

## Problem Statement

The WebSocket authentication in `backend/core/deps.py` currently accepts bearer tokens via three methods:
1. **Query parameter** (`?token=xxx`) — **leaks credentials** in reverse proxy logs, access logs, browser history, and Referer headers
2. `Authorization: Bearer <token>` header — secure, doesn't leak
3. `Sec-WebSocket-Protocol: bearer, <token>` subprotocol — secure, doesn't leak

**Impact**: Tokens in URLs are logged by reverse proxies (nginx, Apache, Traefik), appear in browser history, and are sent in `Referer` headers when navigating to external links. This allows credential disclosure and replay attacks until token expiry.

## User Stories

### US-1: Security Engineer
**As a** security engineer  
**I want** WebSocket connections to reject tokens passed via query parameters  
**So that** credentials are not leaked in proxy logs, browser history, or Referer headers

### US-2: Frontend Developer
**As a** frontend developer  
**I want** clear migration guidance for moving from query-param auth to header/subprotocol auth  
**So that** the log streaming feature continues to work after the change

### US-3: Cluster Operator
**As a** cluster operator  
**I want** the localhost bypass to continue working unchanged  
**So that** local development workflows are not disrupted

## Acceptance Criteria

### AC-1: Query Parameter Rejection
**Given** a WebSocket connection request with `?token=<valid_token>` in the query string  
**When** `LOCALHOST_BYPASS` is disabled and the host is not a bypass host  
**Then** the connection MUST be rejected with `WS_1008_POLICY_VIOLATION` and reason "Authentication required. Query parameter authentication is not supported. Use Authorization header or WebSocket subprotocol."

### AC-2: Authorization Header Acceptance
**Given** a WebSocket connection request with `Authorization: Bearer <valid_token>` header  
**When** `LOCALHOST_BYPASS` is disabled and the host is not a bypass host  
**Then** the connection MUST be accepted and the token validated normally

### AC-3: Subprotocol Acceptance
**Given** a WebSocket connection request with `Sec-WebSocket-Protocol: bearer, <valid_token>`  
**When** `LOCALHOST_BYPASS` is disabled and the host is not a bypass host  
**Then** the connection MUST be accepted, the token validated normally, and the server MUST echo back `bearer` as the accepted subprotocol

### AC-4: Localhost Bypass Unchanged
**Given** a WebSocket connection request to a bypass host (localhost, 127.0.0.1, ::1, .local, .lovable.app)  
**When** `LOCALHOST_BYPASS` is enabled  
**Then** the connection MUST be accepted without any token, returning payload `{"sub": "localhost_user"}`, regardless of whether a token is present in query params, headers, or subprotocol

### AC-5: Invalid Token Rejection
**Given** a WebSocket connection with an invalid/expired token in Authorization header or subprotocol  
**When** `LOCALHOST_BYPASS` is disabled  
**Then** the connection MUST be rejected with `WS_1008_POLICY_VIOLATION` and reason "Invalid or expired token. Please log in again to get a new access token."

### AC-6: No Token Provided
**Given** a WebSocket connection with no token in Authorization header, subprotocol, or query params  
**When** `LOCALHOST_BYPASS` is disabled and host is not a bypass host  
**Then** the connection MUST be rejected with `WS_1008_POLICY_VIOLATION` and reason "Authentication required. Please provide a valid bearer token."

### AC-7: Frontend Migration Complete
**Given** the frontend connects to `/jobs/{id}/logs` WebSocket endpoint  
**When** the frontend is updated to use Authorization header or subprotocol  
**Then** the WebSocket connection MUST succeed without query parameter token

### AC-8: Documentation Updated
**Given** the API documentation (OpenAPI/spec.md)  
**When** the change is made  
**Then** the documentation MUST reflect that query parameter auth is no longer supported for WebSocket

## API Changes

### Breaking Change
- **Removed**: `?token=<token>` query parameter for WebSocket endpoint `/jobs/{id}/logs`
- **Required**: Clients MUST use either:
  - `Authorization: Bearer <token>` header, OR
  - `Sec-WebSocket-Protocol: bearer, <token>` subprotocol

### Migration Path for Clients
1. **Browser/JavaScript clients**: Use `Sec-WebSocket-Protocol: bearer, <token>` (only way to auth WebSocket from browser without custom headers)
2. **Non-browser clients (CLI, scripts)**: Use `Authorization: Bearer <token>` header
3. **Localhost development**: No changes needed — bypass continues to work

### OpenAPI/Spec Update
Update `docs/spec.md` section "Real-time: WebSocket Log Streaming" to document:
- Authentication via Authorization header or Sec-WebSocket-Protocol subprotocol only
- Query parameter authentication removed for security
- Example connection code for both methods

## Test Scenarios

### Unit Tests (backend/tests/unit/test_websocket.py)

#### Test 1: `test_websocket_rejects_query_param_token`
```python
async def test_websocket_rejects_query_param_token(test_job_id):
    """Test WebSocket rejects token in query parameter."""
    with patch.object(settings, "LOCALHOST_BYPASS", False):
        token = create_access_token({"sub": "test-user"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                async with client.websocket_connect(
                    f"/api/v1/jobs/{test_job_id}/logs/stream?token={token}"
                ) as _ws:
                    pass
            assert excinfo.value.code == 1008
            assert "Query parameter authentication is not supported" in excinfo.value.reason
```

#### Test 2: `test_websocket_accepts_auth_header`
```python
async def test_websocket_accepts_auth_header(test_job_id):
    """Test WebSocket accepts token in Authorization header."""
    with patch.object(settings, "LOCALHOST_BYPASS", False):
        token = create_access_token({"sub": "test-user"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.websocket_connect(
                f"/api/v1/jobs/{test_job_id}/logs/stream",
                headers={"Authorization": f"Bearer {token}"},
            ) as ws:
                assert ws is not None
```

#### Test 3: `test_websocket_accepts_subprotocol`
```python
async def test_websocket_accepts_subprotocol(test_job_id):
    """Test WebSocket accepts token in Sec-WebSocket-Protocol."""
    with patch.object(settings, "LOCALHOST_BYPASS", False):
        token = create_access_token({"sub": "test-user"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.websocket_connect(
                f"/api/v1/jobs/{test_job_id}/logs/stream",
                headers={"Sec-WebSocket-Protocol": f"bearer, {token}"},
            ) as ws:
                assert ws is not None
                # Verify subprotocol was echoed back
                assert ws.headers.get("sec-websocket-protocol") == "bearer"
```

#### Test 4: `test_websocket_localhost_bypass_unchanged`
```python
@pytest.mark.parametrize("host_header", [
    "localhost:8000", "127.0.0.1:8000", "[::1]:8000",
    "0.0.0.0:8000", "testserver", "myhost.local", "preview.lovable.app"
])
def test_websocket_localhost_bypass_unchanged(host_header):
    """Test localhost bypass still works with query param (ignored)."""
    with patch.object(settings, "LOCALHOST_BYPASS", True):
        payload = asyncio.run(get_ws_token_payload(websocket=_ws(host_header)))
    assert payload == {"sub": "localhost_user"}
```

#### Test 5: `test_websocket_rejects_invalid_token_in_header`
```python
async def test_websocket_rejects_invalid_token_in_header(test_job_id):
    """Test WebSocket rejects invalid token in Authorization header."""
    with patch.object(settings, "LOCALHOST_BYPASS", False):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                async with client.websocket_connect(
                    f"/api/v1/jobs/{test_job_id}/logs/stream",
                    headers={"Authorization": "Bearer invalid-token"},
                ) as _ws:
                    pass
            assert excinfo.value.code == 1008
            assert "Invalid or expired token" in excinfo.value.reason
```

#### Test 6: `test_websocket_rejects_invalid_token_in_subprotocol`
```python
async def test_websocket_rejects_invalid_token_in_subprotocol(test_job_id):
    """Test WebSocket rejects invalid token in Sec-WebSocket-Protocol."""
    with patch.object(settings, "LOCALHOST_BYPASS", False):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                async with client.websocket_connect(
                    f"/api/v1/jobs/{test_job_id}/logs/stream",
                    headers={"Sec-WebSocket-Protocol": "bearer, invalid-token"},
                ) as _ws:
                    pass
            assert excinfo.value.code == 1008
            assert "Invalid or expired token" in excinfo.value.reason
```

#### Test 7: `test_websocket_rejects_no_token`
```python
async def test_websocket_rejects_no_token(test_job_id):
    """Test WebSocket rejects connection with no token on non-bypass host."""
    with patch.object(settings, "LOCALHOST_BYPASS", False):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://evil.com") as client:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                async with client.websocket_connect(
                    f"/api/v1/jobs/{test_job_id}/logs/stream",
                    headers={"Host": "evil.com"},
                ) as _ws:
                    pass
            assert excinfo.value.code == 1008
            assert "Authentication required" in excinfo.value.reason
```

#### Test 8: `test_websocket_subprotocol_echo`
```python
async def test_websocket_subprotocol_echo(test_job_id):
    """Test server echoes 'bearer' subprotocol when client offers it."""
    with patch.object(settings, "LOCALHOST_BYPASS", False):
        token = create_access_token({"sub": "test-user"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.websocket_connect(
                f"/api/v1/jobs/{test_job_id}/logs/stream",
                headers={"Sec-WebSocket-Protocol": f"bearer, {token}, other-protocol"},
            ) as ws:
                # Server should accept and echo back 'bearer'
                assert ws.headers.get("sec-websocket-protocol") == "bearer"
```

### Integration Tests (backend/tests/integration/test_websocket_flow.py)

#### Test 9: `test_websocket_flow_with_auth_header`
```python
async def test_websocket_flow_with_auth_header(auth_client, test_job_id):
    """Full integration: create job, start it, stream logs via auth header."""
    token = auth_client.headers["Authorization"].split(" ")[1]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ws_client:
        async with ws_client.websocket_connect(
            f"/api/v1/jobs/{test_job_id}/logs/stream",
            headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            message = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
            assert isinstance(message, str)
```

#### Test 10: `test_websocket_flow_with_subprotocol`
```python
async def test_websocket_flow_with_subprotocol(auth_client, test_job_id):
    """Full integration: stream logs via Sec-WebSocket-Protocol."""
    token = auth_client.headers["Authorization"].split(" ")[1]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ws_client:
        async with ws_client.websocket_connect(
            f"/api/v1/jobs/{test_job_id}/logs/stream",
            headers={"Sec-WebSocket-Protocol": f"bearer, {token}"},
        ) as ws:
            message = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
            assert isinstance(message, str)
```

#### Test 11: `test_websocket_query_param_rejected_in_integration`
```python
async def test_websocket_query_param_rejected_in_integration(auth_client, test_job_id):
    """Integration test: query param token is rejected even with valid token."""
    token = auth_client.headers["Authorization"].split(" ")[1]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://evil.com") as ws_client:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            async with ws_client.websocket_connect(
                f"/api/v1/jobs/{test_job_id}/logs/stream?token={token}",
                headers={"Host": "evil.com"},
            ) as _ws:
                pass
        assert excinfo.value.code == 1008
        assert "Query parameter authentication is not supported" in excinfo.value.reason
```

### Dependency Tests (backend/tests/unit/test_auth.py or test_websocket.py)

#### Test 12: `test_get_ws_token_payload_rejects_query_param`
```python
async def test_get_ws_token_payload_rejects_query_param():
    """Test get_ws_token_payload rejects token from query_params."""
    with patch.object(settings, "LOCALHOST_BYPASS", False):
        ws = _ws("evil.com")
        ws._query_params = {"token": "valid-token"}  # Simulate query param
        with pytest.raises(WebSocketException) as excinfo:
            await get_ws_token_payload(websocket=ws)
        assert excinfo.value.code == 1008
        assert "Query parameter authentication is not supported" in excinfo.value.reason
```

#### Test 13: `test_get_ws_token_payload_accepts_auth_header`
```python
async def test_get_ws_token_payload_accepts_auth_header():
    """Test get_ws_token_payload accepts token from Authorization header."""
    with patch.object(settings, "LOCALHOST_BYPASS", False):
        token = create_access_token({"sub": "test-user"})
        ws = _ws("evil.com")
        ws._headers = {"authorization": f"Bearer {token}"}
        payload = await get_ws_token_payload(websocket=ws)
        assert payload["sub"] == "test-user"
```

#### Test 14: `test_get_ws_token_payload_accepts_subprotocol`
```python
async def test_get_ws_token_payload_accepts_subprotocol():
    """Test get_ws_token_payload accepts token from Sec-WebSocket-Protocol."""
    with patch.object(settings, "LOCALHOST_BYPASS", False):
        token = create_access_token({"sub": "test-user"})
        ws = _ws("evil.com")
        ws._headers = {"sec-websocket-protocol": f"bearer, {token}"}
        payload = await get_ws_token_payload(websocket=ws)
        assert payload["sub"] == "test-user"
```

### Frontend Tests (frontend/tests/)

#### Test 15: Frontend WebSocket uses subprotocol
```typescript
// frontend/tests/services/websocket-auth.test.ts
test("streamJobLogs uses Sec-WebSocket-Protocol for authentication", async () => {
  const token = "test-token-123";
  const { unsubscribe } = httpService.streamJobLogs("job-123", onLine, onStatus);
  // Verify WebSocket was created with correct subprotocol
  expect(WebSocket).toHaveBeenCalledWith(expect.stringContaining("/jobs/job-123/logs"));
  // The WebSocket constructor should receive protocols: ["bearer", token]
});
```

## Implementation Changes

### Backend: `backend/core/deps.py`

**Function to modify**: `get_ws_token_payload`

**Current behavior** (lines 65-78):
```python
# Extract token from query parameters
token = websocket.query_params.get("token")

# If not in query params, try Authorization header
if not token:
    auth_header = websocket.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]

# Fall back to the WebSocket subprotocol
if not token:
    token = _token_from_subprotocol(websocket)
```

**New behavior**:
```python
# FIRST: Check Authorization header (preferred for non-browser clients)
auth_header = websocket.headers.get("Authorization")
if auth_header and auth_header.startswith("Bearer "):
    token = auth_header[7:]

# SECOND: Check Sec-WebSocket-Protocol subprotocol (required for browsers)
if not token:
    token = _token_from_subprotocol(websocket)

# THIRD: Reject query parameter tokens with clear error
query_token = websocket.query_params.get("token")
if query_token:
    raise WebSocketException(
        code=status.WS_1008_POLICY_VIOLATION,
        reason=(
            "Authentication required. Query parameter authentication is not supported. "
            "Use Authorization header or WebSocket subprotocol."
        ),
    )

if not token:
    raise WebSocketException(
        code=status.WS_1008_POLICY_VIOLATION,
        reason="Authentication required. Please provide a valid bearer token.",
    )
```

**Docstring update**: Update the docstring to reflect new behavior:
```python
"""
Validate the token for WebSocket connections and return the payload.
Extracts token from Authorization header or Sec-WebSocket-Protocol subprotocol.
Query parameter authentication is NOT supported (security: tokens in URLs leak).
If LOCALHOST_BYPASS is True and the host is local, return a fake payload.
"""
```

### Frontend: `frontend/src/services/http.ts`

**Function to modify**: `streamJobLogs` (lines 100-114)

**Current behavior**: Creates WebSocket with URL only, no subprotocol

**New behavior**: Pass token via `Sec-WebSocket-Protocol` subprotocol:
```typescript
streamJobLogs(
  id: string,
  onLine: (line: string) => void,
  onStatus: (status: LogStreamStatus) => void,
): Unsubscribe {
  if (typeof window === "undefined") return () => {};
  const { token } = getSettings();
  const url = `${base().replace(/^http/, "ws")}/jobs/${id}/logs`;
  onStatus("connecting");
  
  // Pass token via Sec-WebSocket-Protocol subprotocol for security
  // (query parameters leak tokens in logs/history/Referer)
  const socket = new WebSocket(url, ["bearer", token]);
  socket.onopen = () => onStatus("open");
  socket.onmessage = (e) => onLine(String(e.data));
  socket.onclose = () => onStatus("closed");
  socket.onerror = () => onStatus("closed");
  return () => socket.close();
}
```

### Documentation: `docs/spec.md`

Update section "Real-time: WebSocket Log Streaming" (around line 148):
```markdown
### Real-time: WebSocket Log Streaming

- **Endpoint**: `GET /jobs/{id}/logs` (WebSocket upgrade)
- **Messages**: Plain text log lines (one line per message)
- **Authentication**: 
  - **Browser clients**: `Sec-WebSocket-Protocol: bearer, <token>` subprotocol
  - **Non-browser clients**: `Authorization: Bearer <token>` header
  - Query parameter `?token=` is **not supported** (security: tokens in URLs leak in proxy logs, browser history, and Referer headers)
- **Status values**: Provided via `onStatus` callback: `"connecting" | "open" | "closed"`
- **Reconnect**: Exponential backoff (configurable via `wsReconnectMs`, default 3000ms)
- **Fallback**: HTTP `GET /jobs/{id}/logs/history` for initial load
```

## Migration Notes

### For Frontend (already in this repo as submodule)
1. Update `frontend/src/services/http.ts` `streamJobLogs` to use subprotocol
2. Commit and push to `rendering-replicate` repo
3. Update git submodule reference in this repo

### For External Clients
- **CLI tools**: Add `Authorization: Bearer <token>` header to WebSocket handshake
- **Browser-based tools**: Use `new WebSocket(url, ["bearer", token])`
- **curl/wscat**: Not directly supported; use a proper WebSocket client library

### Rollback Plan
If issues arise:
1. Revert `backend/core/deps.py` to accept query params (but log a warning)
2. Revert frontend submodule
3. Investigate and fix properly

## Security Considerations

1. **Defense in depth**: Even with this fix, tokens can still leak if users manually put them in URLs. Consider adding a `Referrer-Policy: strict-origin-when-cross-origin` header.
2. **Token rotation**: Short token expiry (current: 30 min) limits replay window
3. **HTTPS enforcement**: In production, enforce TLS to prevent token sniffing
4. **Proxy configuration**: Configure reverse proxies to strip `Authorization` headers from logs

## Definition of Done

- [ ] Backend: `get_ws_token_payload` rejects query param tokens with clear error
- [ ] Backend: Accepts Authorization header and subprotocol tokens
- [ ] Backend: Localhost bypass unchanged
- [ ] Backend: All new unit tests pass
- [ ] Backend: All existing tests pass (update any that used query param)
- [ ] Frontend: `streamJobLogs` uses `Sec-WebSocket-Protocol` subprotocol
- [ ] Frontend: TypeScript typecheck passes
- [ ] Frontend: Tests pass
- [ ] Documentation: `docs/spec.md` updated
- [ ] Integration tests pass
- [ ] CI pipeline passes (all 4 jobs: backend-gates, frontend-gates, compose-suite, e2e)