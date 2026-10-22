# Spec: E2E Login Helper Fix (Issue #143)

## Problem Statement

The `login()` helper in `frontend/tests/e2e/test-utils.ts` drives a login form that **never renders on localhost** because:

1. `getSettings()` in `frontend/src/lib/settings.ts` returns `LOCAL_TOKEN = "localhost-no-auth"` when:
   - No token is in localStorage, AND
   - The hostname matches a bypass host (`localhost`, `127.0.0.1`, `::1`, `.local`, `.lovable.app`)
2. The `LoginPage` component (in `src/routes/login.tsx`) has a `useEffect` that calls `getSettings()` and **immediately redirects to `/`** if a token exists
3. Result: The login form is unreachable on localhost; `login()` navigates to `/login`, the page redirects to `/`, then the helper tries to fill a token field that doesn't exist

This breaks all e2e tests that use the `login()` helper.

---

## User Stories

### As a QA Engineer
- I want the e2e test suite to run reliably on localhost
- I want the `login()` helper to work consistently regardless of the test environment
- I want authentication tests to verify actual login behavior, not just the bypass

### As a Developer
- I want to run e2e tests locally without configuring a non-localhost domain
- I want the test helper to reflect the actual authentication state of the application

---

## Acceptance Criteria

### AC1: `login()` works on localhost bypass hosts
- **Given** a test runs on a bypass host (`localhost`, `127.0.0.1`, `::1`, `.local`, `.lovable.app`)
- **When** `login(page)` is called
- **Then** the helper seeds localStorage with the test token and navigates to `/`
- **And** no navigation to `/login` occurs (the form cannot render)
- **And** subsequent assertions (e.g., `expect(page).toHaveURL("/")`) pass

### AC2: `login()` works on non-bypass hosts
- **Given** a test runs on a non-bypass host (e.g., `cluster.example.com`, `192.168.1.50`)
- **When** `login(page)` is called
- **Then** the helper navigates to `/login`
- **And** fills the token field and submits the form
- **And** waits for redirect to `/`
- **And** subsequent assertions pass

### AC3: Test suite passes on localhost
- All existing e2e tests pass when run against a localhost backend
- No test modifications required beyond the `login()` helper

### AC4: `resetAuthState()` continues to work
- `resetAuthState(page)` clears cookies and localStorage
- After reset, `login()` works correctly (re-seeds token on bypass hosts)

### AC5: Token validation still tested on non-bypass hosts
- The "invalid token" test in `auth.spec.ts` still exercises the actual login form
- This test should be marked to run only on non-bypass hosts, or use a different approach

---

## Test Scenarios

| Scenario | Host | Expected `login()` Behavior |
|----------|------|----------------------------|
| Local dev (Vite) | `localhost:5173` | Seed token, navigate to `/` |
| Local compose | `localhost:8000` | Seed token, navigate to `/` |
| Local IP | `127.0.0.1:5173` | Seed token, navigate to `/` |
| IPv6 loopback | `[::1]:5173` | Seed token, navigate to `/` |
| LAN IP | `192.168.1.50:5173` | Drive login form |
| Custom domain | `cluster.local` | Seed token (`.local` suffix), navigate to `/` |
| Lovable preview | `*.lovable.app` | Seed token (`.lovable.app` suffix), navigate to `/` |
| Production | `cluster.example.com` | Drive login form |

---

## Technical Approach

### Option 1: `login()` Reflects the Bypass (Recommended)

Modify `login()` in `test-utils.ts` to detect bypass hosts and behave accordingly:

```typescript
import { isLocalhost } from "@/lib/settings"; // Reuse frontend logic

export async function login(page: Page, token: string = TEST_TOKEN) {
  // Check if we're on a bypass host by evaluating in the page context
  const isBypassHost = await page.evaluate(() => {
    // Duplicate the logic from frontend/src/lib/settings.ts
    const raw = window.location.hostname.trim().toLowerCase();
    const h = raw.startsWith("[") && raw.endsWith("]") ? raw.slice(1, -1) : raw;
    const exact = ["localhost", "127.0.0.1", "::1"];
    const suffixes = [".local", ".lovable.app"];
    return exact.includes(h) || suffixes.some((s) => h.endsWith(s));
  });

  if (isBypassHost) {
    // On bypass hosts, auth is automatic. Seed localStorage and go home.
    await page.goto("/");
    await page.evaluate((t) => {
      const KEY = "shc.settings";
      const existing = localStorage.getItem(KEY);
      const settings = existing ? JSON.parse(existing) : {};
      settings.token = t;
      settings.apiBaseUrl = settings.apiBaseUrl || "http://localhost:8000/api/v1";
      settings.theme = settings.theme || "dark";
      settings.wsReconnectMs = settings.wsReconnectMs || 3000;
      settings.pollIntervalMs = settings.pollIntervalMs || 7000;
      localStorage.setItem(KEY, JSON.stringify(settings));
    }, token);
    await page.reload(); // Hydrate the React state
    await expect(page).toHaveURL("/");
  } else {
    // Non-bypass: drive the actual login form
    await page.goto("/login");
    await page.fill('input[id="token"]', token);
    await page.click('button:has-text("Sign in")');
    await expect(page).toHaveURL("/");
  }
}
```

**Key implementation details:**
- Use `page.evaluate()` to run the bypass detection in the browser context (same logic as frontend)
- On bypass hosts: navigate to `/` first (to establish origin), then seed localStorage, then reload to hydrate React state
- On non-bypass hosts: existing form-driven behavior
- `resetAuthState()` already navigates to baseURL before clearing localStorage, so it works correctly

### Option 2: Run Suite Off the Bypass (Not Recommended for Now)

Configure Playwright to run against a non-bypass hostname (e.g., `http://127.0.0.1.nip.io:5173` or a local DNS entry). This requires:
- Infrastructure changes (DNS, `/etc/hosts`, or nip.io)
- CI/CD configuration changes
- More complex local development setup

**Decision**: Option 1 is simpler, requires no infrastructure changes, and matches how the application actually behaves on localhost.

---

## Edge Cases & Risks

| Risk | Mitigation |
|------|------------|
| `page.evaluate()` bypass detection diverges from frontend | Use identical logic; add a test in `backend/tests/unit/test_frontend_alignment.py` that the two implementations match |
| localStorage seeding doesn't trigger React state update | Call `page.reload()` after seeding; the `useSettings` hook reads from localStorage on mount |
| `resetAuthState()` clears the seeded token | `resetAuthState()` runs in `beforeEach`; `login()` runs in the test body after reset — correct order |
| Tests that explicitly test the login form (e.g., invalid token) | Mark those tests to run only on non-bypass hosts using `test.skip(!isBypassHost)` or a fixture |
| Playwright `baseURL` includes port, but bypass check uses hostname only | The bypass check uses `window.location.hostname` (no port), matching frontend behavior |

---

## Files to Modify

1. **`frontend/tests/e2e/test-utils.ts`** — Main fix: update `login()` to detect bypass and seed localStorage
2. **`frontend/tests/e2e/auth.spec.ts`** — Update "invalid token" test to only run on non-bypass hosts (or remove if covered elsewhere)
3. **`backend/tests/unit/test_frontend_alignment.py`** — Add test verifying bypass detection logic matches between frontend and test-utils

---

## Implementation Checklist

- [ ] Update `login()` in `test-utils.ts` with bypass detection
- [ ] Add `page.reload()` after localStorage seeding to hydrate React state
- [ ] Verify `resetAuthState()` + `login()` sequence works in `beforeEach` → test order
- [ ] Update `auth.spec.ts` "invalid token" test to skip on bypass hosts
- [ ] Add alignment test in `test_frontend_alignment.py`
- [ ] Run full e2e suite on localhost (`npm run test:e2e`)
- [ ] Verify all existing tests pass

---

## Definition of Done

- [ ] All e2e tests pass on localhost (Vite dev server + backend)
- [ ] All e2e tests pass on compose stack (`make test-compose`)
- [ ] No TypeScript errors (`npm run typecheck`)
- [ ] No lint errors (`npm run lint`)
- [ ] Backend tests pass (`pytest --cov=backend`)
- [ ] PR merged to `main` with conventional commit: `fix(e2e): make login() work on localhost bypass hosts`