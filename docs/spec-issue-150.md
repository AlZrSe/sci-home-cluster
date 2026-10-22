# Specification: Frontend Authentication Bypass — Login Form Not Rendering on Localhost

**Issue**: #150  
**Status**: Groomed  
**Labels**: bug, frontend, integration  
**Target Branch**: `task/150-frontend-auth-bypass-login`

---

## 1. Problem Statement

On localhost (and other bypass hosts: `127.0.0.1`, `::1`, `.local`, `.lovable.app`), the Login page (`/login`) was not rendering because the frontend was auto-injecting a bypass token (`localhost-no-auth`) into localStorage via `getSettings()` before the user could see the login form. This caused the Shell component to redirect authenticated users away from `/login` immediately.

**Root Cause** (in `frontend/src/lib/settings.ts:80`):
```typescript
if (!cache.token && isLocalhost()) cache = { ...cache, token: LOCAL_TOKEN };
```

This logic meant:
1. User visits `/login` on localhost
2. `getSettings()` runs, sees no token, detects localhost, injects `LOCAL_TOKEN`
3. Shell component sees token exists, redirects to `/`
4. Login form never renders

---

## 2. User Stories

| ID | As a… | I want… | So that… |
|----|--------|---------|----------|
| US-1 | Developer on localhost | To see the login form when I visit `/login` | I can test the login flow, change tokens, or simulate unauthenticated state |
| US-2 | Developer on `.local` / `.lovable.app` | Same as US-1 | The login form is accessible on all bypass hosts |
| US-3 | QA running e2e tests | The login form to render on localhost during test setup | E2E tests can exercise the actual login form instead of being forced to seed localStorage |
| US-4 | Operator on remote cluster | No change in behaviour | Auth bypass still works for legitimate localhost development |

---

## 3. Acceptance Criteria

| AC ID | Criterion | Verification |
|-------|-----------|--------------|
| AC-1 | Visiting `/login` on `localhost:5173` renders the login form (token field, API URL field, Connect button) | Manual + e2e |
| AC-2 | Visiting `/login` on `127.0.0.1:5173` renders the login form | Manual + e2e |
| AC-3 | Visiting `/login` on `::1:5173` renders the login form | Manual (IPv6) |
| AC-4 | Visiting `/login` on `*.local` renders the login form | Manual (mDNS) |
| AC-5 | Visiting `/login` on `*.lovable.app` renders the login form | Manual (Lovable preview) |
| AC-6 | After entering a valid token on localhost and clicking Connect, the user is redirected to `/` and the app works | Manual + e2e |
| AC-7 | Backend `/auth/verify` and `/auth/validate` still accept `localhost-no-auth` token when `Host` header is a bypass host | Backend unit tests |
| AC-8 | Signing out on localhost clears the token and returns user to `/login` | Manual + e2e |
| AC-9 | No redirect loop occurs on logout/login cycle on localhost | Manual + e2e |
| AC-10 | Non-bypass hosts (e.g., `staging.example.com`) continue to require a real token — no bypass token is injected | Manual + e2e |

---

## 4. Technical Approach

### 4.1 Files Changed

| File | Change |
|------|--------|
| `frontend/src/lib/settings.ts` | **Removed** auto-injection of `LOCAL_TOKEN` in `getSettings()` (line 80) |
| `frontend/src/routes/login.tsx` | Added check to only redirect if token exists **and is not** the bypass token (line 39) |
| `backend/api/v1/auth.py` | Added localhost bypass logic to `/verify` (line 45) and `/validate` (line 65) endpoints |
| `frontend/src/components/layout/Shell.tsx` | Added `shc.loggedOut` flag handling to prevent redirect loops on logout (line 44) |
| `frontend/src/routes/settings.tsx` | Sign out now sets `shc.loggedOut` flag (line 53) |
| `frontend/src/routes/login.tsx` | Clears `shc.loggedOut` flag on successful login (line 53) |
| `frontend/tests/e2e/test-utils.ts` | `resetAuthState()` clears `shc.loggedOut` flag (line 129) |

### 4.2 Key Design Decisions

1. **Frontend no longer auto-injects the bypass token** — the token is now treated as a *user-provided* value like any other token. This gives the user full control over whether they want to use the bypass.

2. **Backend owns the bypass decision** — the `/auth/verify` and `/auth/validate` endpoints check:
   - Is the request from a localhost host? (via `get_current_token_payload` dependency which sets `sub: "localhost_user"`)
   - Is the token exactly `"localhost-no-auth"`?
   - If both true → return `valid: true`

3. **`shc.loggedOut` flag prevents redirect loops** — When the user signs out on localhost:
   - Token is cleared
   - `shc.loggedOut` flag is set in localStorage
   - Shell sees the flag and allows `/login` to render even though the host is localhost
   - On successful login, the flag is cleared

4. **E2E tests updated** — The `login()` helper now drives the actual login form on bypass hosts instead of seeding localStorage and navigating to `/`. This tests the real user flow.

---

## 5. Test Scenarios

### 5.1 Unit Tests (Backend)

| Test | Description |
|------|-------------|
| `test_verify_token_localhost_bypass` | `GET /auth/verify` with `Host: localhost` and `Authorization: Bearer localhost-no-auth` → 200, `valid: true` |
| `test_verify_token_localhost_wrong_token` | `GET /auth/verify` with `Host: localhost` and `Authorization: Bearer wrong-token` → 200, `valid: false` |
| `test_validate_token_localhost_bypass` | `POST /auth/validate` with `Host: 127.0.0.1` and body `{token: "localhost-no-auth"}` → 200, `valid: true` |
| `test_verify_token_non_localhost` | `GET /auth/verify` with `Host: staging.example.com` and `Authorization: Bearer localhost-no-auth` → 200, `valid: false` |
| `test_is_localhost_exact_hosts` | `is_localhost("localhost")`, `"127.0.0.1"`, `"::1"`, `"0.0.0.0"`, `"testserver"` → true |
| `test_is_localhost_suffixes` | `is_localhost("host.local")`, `"preview.lovable.app"` → true |
| `test_is_localhost_non_bypass` | `is_localhost("example.com")`, `"staging.example.com"` → false |

### 5.2 Unit Tests (Frontend)

| Test | Description |
|------|-------------|
| `test_getSettings_no_auto_bypass` | `getSettings()` on localhost with empty localStorage returns `token: ""` (not `LOCAL_TOKEN`) |
| `test_isLocalhost_exact_hosts` | `isLocalhost()` returns true for `localhost`, `127.0.0.1`, `::1` |
| `test_isLocalhost_suffixes` | `isLocalhost()` returns true for `host.local`, `preview.lovable.app` |
| `test_isLocalhost_non_bypass` | `isLocalhost()` returns false for `example.com` |

### 5.3 Integration / E2E Tests

| Test | Description |
|------|-------------|
| `e2e_login_form_renders_on_localhost` | Navigate to `/login` on localhost → form is visible with token input, API URL input, Connect button |
| `e2e_login_form_renders_on_127_0_0_1` | Same as above on `127.0.0.1` |
| `e2e_login_form_renders_on_ipv6` | Same as above on `[::1]` |
| `e2e_login_flow_localhost` | On `/login`, enter `localhost-no-auth`, click Connect → redirected to `/`, dashboard loads |
| `e2e_signout_returns_to_login` | On `/settings`, click Sign out → redirected to `/login`, form renders |
| `e2e_no_redirect_loop_on_logout_login` | Sign out → login → sign out → login (3 cycles) → no infinite redirects |
| `e2e_non_bypass_host_requires_token` | On `staging.example.com` (mocked), `/login` renders, entering `localhost-no-auth` fails validation |

---

## 6. Edge Cases & Risks

| Risk | Mitigation |
|------|------------|
| **Redirect loop on logout** | `shc.loggedOut` flag breaks the loop; cleared on login |
| **User clears localStorage manually** | `getSettings()` returns empty token; Shell redirects to `/login`; form renders |
| **Backend `is_localhost` and frontend `isLocalhost` drift** | Shared `shared/auth/localhost_hosts.json` + test `test_frontend_alignment.py` fails on drift |
| **E2E tests on CI (non-localhost host)** | Tests run against `localhost` in CI; `TEST_TOKEN = "localhost-no-auth"` is correct for bypass hosts |
| **Lovable preview (`.lovable.app`) bypass** | Already in suffix list; no code change needed |
| **Starlette TestClient (`testserver` host)** | Backend-only entry; not in frontend list (browser never sends it) — correct |
| **`0.0.0.0` host** | Backend-only entry; browser never reports it — correct |

---

## 7. Rollback Plan

If the fix introduces regressions:

1. Revert `frontend/src/lib/settings.ts` line 80 to re-enable auto-injection
2. Revert `frontend/src/routes/login.tsx` redirect logic
3. Revert `backend/api/v1/auth.py` bypass logic (or guard with `LOCALHOST_BYPASS` setting)
4. Revert `shc.loggedOut` flag handling in Shell, settings, login, test-utils

All changes are isolated and reversible.

---

## 8. Related Documentation

- `docs/spec.md#authentication` — authentication architecture
- `shared/auth/localhost_hosts.json` — canonical bypass host list
- `shared/auth/README.md` — why the list is duplicated, not imported
- `PROCESS.md` — Definition of Done, testing strategy

---

## 9. Implementation Checklist

- [x] Remove auto-bypass token injection in `frontend/src/lib/settings.ts`
- [x] Update login redirect logic in `frontend/src/routes/login.tsx`
- [x] Add backend bypass logic in `backend/api/v1/auth.py` (`/verify`, `/validate`)
- [x] Add `shc.loggedOut` flag handling in `frontend/src/components/layout/Shell.tsx`
- [x] Set `shc.loggedOut` on sign out in `frontend/src/routes/settings.tsx`
- [x] Clear `shc.loggedOut` on login in `frontend/src/routes/login.tsx`
- [x] Update `resetAuthState()` in `frontend/tests/e2e/test-utils.ts`
- [ ] Backend unit tests for bypass logic (verify/validate)
- [ ] Frontend unit tests for `getSettings` / `isLocalhost`
- [ ] E2E tests for login form rendering on all bypass hosts
- [ ] E2E test for logout/login cycle on localhost
- [ ] Update issue to "groomed" state

---

## 10. Definition of Done

- [ ] All acceptance criteria (AC-1 through AC-10) verified
- [ ] Backend tests pass: `pytest backend/tests/unit -k "auth or localhost"`
- [ ] Frontend tests pass: `cd frontend && npm test`
- [ ] E2E tests pass: `cd frontend && npm run test:e2e`
- [ ] Lint/type gates pass: `ruff check . && ruff format --check . && mypy .` (root), `cd frontend && npm run lint && npm run typecheck`
- [ ] PM accepts the implementation