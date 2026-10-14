# Spec: WebSocket Log-Stream Handshake Rejection for Unauthenticated Clients

## Issue Reference
- **Issue**: #53 — "WebSocket log-stream handshake hangs instead of rejecting an unauthenticated client"
- **Labels**: `bug`, `phase-1`, `ready-for-grooming` → will become `groomed`
- **Related**: #31 (localhost bypass alignment — exposed this defect), #35 (demo data flag)

---

## 1. Verified Problem Statement

### 1.1 Affected Endpoint
**`GET /api/v1/jobs/{job_id}/logs`** (WebSocket upgrade)

Registered in `backend/api/v1/jobs.py:163` as:
```python
@router.websocket("/{job_id}/logs")
async def stream_job_logs(
    websocket: WebSocket,
    job_id: str,
    payload: dict = Depends(get_ws_token_payload),
):
```

### 1.2 Current Behaviour (Defect)
When a client connects **without a valid token** and the `Host` header is **not** a localhost-bypass host:
1. `get_ws_token_payload` (in `backend/core/deps.py:49`) raises `auth_token_missing()` → `APIError(status_code=401)`.
2. Starlette's `ExceptionMiddleware` catches the `HTTPException` and invokes the **global** handler registered in `backend/main.py:197`:
   ```python
   @app.exception_handler(HTTPException)
   async def http_exception_handler(request: Request, exc: HTTPException):
       return JSONResponse(...)
   ```
3. On a **websocket scope**, `ExceptionMiddleware` calls `await handler(websocket, exc)` and **discards the return value**. A `JSONResponse` is never sent on the wire.
4. The handshake **never receives an `accept` frame nor a `close` frame**. The client blocks indefinitely (hangs).

**Reproduction evidence** (from issue #53, time-boxed 6 s):
| Request | Outcome |
|---------|---------|
| `Host: evil-localhost.com`, no token | **HANGS** — no accept, no close, connection stays open |
| `Host: localhost:8000`, no token | Correct: `WebSocketDisconnect(1008, 'Job not found')` (bypass reaches route) |

### 1.3 Expected Behaviour
An unauthenticated WebSocket handshake on a non-bypass host must be **rejected immediately at the protocol level** with a WebSocket close frame (code `1008` Policy Violation) and a human-readable reason. The client must not hang.

---

## 2. Root Cause

**Missing WebSocket-aware exception handling for auth failures.**

- The dependency `get_ws_token_payload` raises `HTTPException` (via `APIError`) on auth failure.
- The global `HTTPException` handler in `main.py` returns `JSONResponse`, which is **meaningless on a websocket scope**.
- Starlette discards the handler's return value for websocket scopes → nothing is sent → handshake hangs.

**Location of the defect:**
- Primary: `backend/main.py:197-229` — global `HTTPException` handler returns `JSONResponse`.
- Secondary: `backend/core/deps.py:81` — `get_ws_token_payload` raises `HTTPException` instead of a websocket-native rejection.

---

## 3. Scope Boundaries

| In Scope | Out of Scope |
|----------|--------------|
| Fix the rejection path for `/api/v1/jobs/{job_id}/logs` WebSocket | Other WebSocket endpoints (none exist yet) |
| Ensure unauthenticated non-bypass hosts get immediate close frame | Change the localhost bypass host list (done in #31) |
| Choose appropriate WebSocket close code (`1008`) | Modify REST auth behaviour |
| Add regression test driving a **real** `TestClient` handshake | Fix the skipped test suite (`test_websocket.py` is `@pytest.mark.skip`) |
| Ensure the fix works for all token sources (query, header, subprotocol) | Change the `APIError` hierarchy for REST |

---

## 4. Acceptance Criteria

All checkboxes must be verifiable with the commands shown.

### 4.1 Functional Acceptance

- [ ] **AC-1**: `curl`-style handshake without token on non-bypass host → immediate close frame (code 1008)
  ```bash
  # Using websocat or similar; times out if handshake hangs
  timeout 3 websocat -n1 ws://evil-localhost.com:8000/api/v1/jobs/any/logs 2>&1 | grep -q "1008"
  ```

- [ ] **AC-2**: `curl`-style handshake with invalid token on non-bypass host → immediate close frame (code 1008)
  ```bash
  timeout 3 websocat -n1 ws://evil-localhost.com:8000/api/v1/jobs/any/logs?token=bad 2>&1 | grep -q "1008"
  ```

- [ ] **AC-3**: Localhost bypass hosts still work (no regression on #31)
  ```bash
  timeout 3 websocat -n1 ws://localhost:8000/api/v1/jobs/<valid_job_id>/logs 2>&1 | grep -q "connected\|Job not found"
  ```

- [ ] **AC-4**: Valid token on non-bypass host works (no regression on legit auth)
  ```bash
  TOKEN=$(create_valid_jwt)
  timeout 5 websocat -n1 "ws://evil-localhost.com:8000/api/v1/jobs/<valid_job_id>/logs?token=$TOKEN" 2>&1 | head -1 | grep -q "log line pattern"
  ```

### 4.2 Technical Acceptance

- [ ] **AC-5**: No `HTTPException` handler returns `JSONResponse` on websocket scope. Either:
  - A dedicated websocket exception handler is registered, **or**
  - The auth dependency raises `WebSocketException(code=1008, ...)` directly, **or**
  - The route catches the exception and calls `await websocket.close(code=1008, reason=...)`.

- [ ] **AC-6**: Close code `1008` (Policy Violation) is used for auth rejection. Distinguish from "job not found" (also 1008 today) by reason string.

- [ ] **AC-7**: Regression test added in `backend/tests/unit/test_websocket.py` (or new file) that:
  - Drives a **real** `AsyncClient.websocket_connect()` handshake with a bad token on a non-bypass host.
  - Asserts the connection is closed with code 1008 within a timeout (so a hang becomes a readable test failure, not a suite timeout).
  - Is **not** marked `@pytest.mark.skip`.

### 4.3 Quality Acceptance

- [ ] **AC-8**: `ruff check . && ruff format . && mypy .` passes.
- [ ] **AC-9**: `pytest --cov=backend` passes (including new test).
- [ ] **AC-10**: No new `pytest.mark.skip` on WebSocket tests unless justified in a comment.

---

## 5. Test Scenarios

| Scenario | Host Header | Token | Expected Result |
|----------|-------------|-------|-----------------|
| T1 | `evil-localhost.com` | none | Close 1008 "Authentication required" |
| T2 | `evil-localhost.com` | invalid JWT | Close 1008 "Invalid or expired token" |
| T3 | `evil-localhost.com` | expired JWT | Close 1008 "Invalid or expired token" |
| T4 | `localhost:8000` | none | Bypass → reaches route → close 1008 "Job not found" (control) |
| T5 | `127.0.0.1:8000` | none | Bypass → reaches route (control) |
| T6 | `testserver` | none | Bypass → reaches route (control) |
| T7 | `evil-localhost.com` | valid JWT | Accept → stream logs (happy path) |
| T8 | `evil-localhost.com` | valid JWT in `Authorization` header | Accept → stream logs |
| T9 | `evil-localhost.com` | valid JWT in `sec-websocket-protocol` | Accept → stream logs |

**Test implementation note**: Scenarios T1–T3 must use a **real** `ASGITransport` handshake (`AsyncClient.websocket_connect`), not the mock `WebSocket` scope used in `TestWebSocketLocalhostBypass`, because the defect only manifests when `ExceptionMiddleware` processes the exception on a real websocket scope.

---

## 6. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Fix breaks REST error responses | Low | High | Only modify websocket-specific path; keep `http_exception_handler` for HTTP scopes. |
| Close code 1008 conflicts with "job not found" | Medium | Low | Use distinct reason strings; document in spec. If needed later, use 4001+ custom codes. |
| Test flakiness due to async timing | Medium | Medium | Use `asyncio.wait_for` with generous timeout (3–5 s) in regression test. |
| Subprotocol auth path not covered | Low | Medium | Ensure `get_ws_token_payload` raises the same rejection type for all three token sources. |
| `Exception` handler (500) also swallows websocket errors | Low | Medium | Apply same fix pattern to generic exception handler at `main.py:253`. |

---

## 6.1 Additional Notes

- The issue is **independent of the hostname list** (fixed in #31). Both REST and WebSocket bypass logic in `deps.py` are correct; the defect is purely in how the rejection is delivered on a websocket scope.
- The existing test class `TestWebSocketLocalhostBypass` (lines 593–666) tests the **dependency in isolation** with a mock `WebSocket` scope. This was a deliberate choice for #31 because a real handshake would hang. **After this fix, those tests should be supplemented (not replaced) with real-handshake tests.**
- The frontend (`frontend/src/services/websocket.ts`) currently has no explicit handling for a handshake that never settles; it will appear as a frozen log panel. This fix makes the frontend's error handling reachable.

---

## 7. Implementation Approach (for SWE reference)

Two viable approaches; choose one:

### Option A: Raise `WebSocketException` from the dependency
```python
# In backend/core/deps.py
from fastapi import WebSocketException, status

async def get_ws_token_payload(websocket: WebSocket) -> dict:
    ...
    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Authentication required. Please provide a valid bearer token."
        )
    ...
```
- Pros: Minimal change; Starlette converts `WebSocketException` to a close frame automatically.
- Cons: Dependency now imports `WebSocketException` (websocket-specific), slightly couples auth logic to transport.

### Option B: Register a websocket exception handler in `main.py`
```python
# In backend/main.py
from fastapi import WebSocket
from starlette.exceptions import WebSocketException as StarletteWebSocketException

@app.exception_handler(HTTPException)
async def http_exception_handler(request_or_ws: Request | WebSocket, exc: HTTPException):
    if isinstance(request_or_ws, WebSocket):
        # On websocket scope, send close frame instead of JSONResponse
        await request_or_ws.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc.detail))
        return  # No return value needed
    # HTTP scope: existing JSONResponse logic
    return JSONResponse(...)
```
- Pros: Keeps dependency transport-agnostic; centralizes the fix.
- Cons: Slightly more complex; must also handle generic `Exception` handler.

**Recommendation**: Option A is simpler and more explicit. The dependency is already websocket-specific (`get_ws_token_payload` takes a `WebSocket`), so raising `WebSocketException` is natural.

---

## 8. Spec Sign-off

- **PM**: ✅ Groomed — spec written, issue labelled `groomed`.
- **SWE**: (to implement per this spec)
- **QA**: (to verify against AC-1 through AC-10)
- **On-Call**: (if CI fails on merge)