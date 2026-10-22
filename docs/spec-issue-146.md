# Specification: Fix Playwright E2E Test Hangs (Issue #146)

## 1. User Stories

| ID | User Story | Priority |
|----|------------|----------|
| US-1 | As a **developer**, I want the CI e2e job to complete within 30 minutes so that PRs get timely feedback. | P0 |
| US-2 | As a **maintainer**, I want the submodule check to fail (not warn) if the frontend is missing the #141 fix, so that we never run tests against broken code. | P0 |
| US-3 | As a **developer**, I want individual tests to timeout after 60 seconds, so that a single hanging test doesn't block the entire suite. | P0 |
| US-4 | As a **developer**, I want clear logging when a test times out, so I can identify which test is hanging without manual investigation. | P1 |

---

## 2. Acceptance Criteria (from Issue #146)

| AC ID | Criterion | Verification |
|-------|-----------|--------------|
| AC-1 | Tests complete within 30 minutes (globalTimeout) | CI job `e2e` finishes < 30 min |
| AC-2 | Tests do not hang indefinitely | No CI timeout after 60 min |
| AC-3 | Submodule is at correct commit with #141 fix | Submodule check fails if missing |
| AC-4 | Per-test timeout catches individual hanging tests | Playwright `--timeout=60000` enforced |
| AC-5 | E2E job passes consistently across multiple runs | 3 consecutive green runs on `main` |
| AC-6 | Submodule check is a failure (not warning) if #141 fix missing | CI step exits non-zero |

---

## 3. Test Scenarios

### 3.1 Functional Scenarios

| Scenario | Description | Expected Result |
|----------|-------------|-----------------|
| TS-1 | Run full Playwright suite against docker-compose stack | All tests pass, suite completes < 30 min |
| TS-2 | Submodule at commit WITHOUT #141 fix | CI fails at submodule check step |
| TS-3 | Submodule at commit WITH #141 fix | CI proceeds to Playwright tests |
| TS-4 | Individual test hangs > 60 seconds | Test fails with timeout, suite continues |
| TS-5 | Test suite setup/teardown hangs | Global timeout (30 min) kills the run |

### 3.2 Negative / Edge Cases

| Scenario | Description | Expected Result |
|----------|-------------|-----------------|
| TS-6 | `PLAYWRIGHT_BASE_URL` not set | Tests fail fast (wrong origin) |
| TS-7 | webServer fails to start (uvicorn/vite) | CI fails at health check step |
| TS-8 | Network flakiness causes navigation timeout | Retries exhausted, test fails cleanly |
| TS-9 | LocalStorage.clear() on about:blank (pre-#141) | SecurityError thrown, test fails |

---

## 4. Technical Approach

### 4.1 Root Cause Analysis

The issue identifies four root causes:

1. **Submodule check is a warning, not failure** (CI line 537-539): The check for #141 fix uses `::warning::` and continues execution.
2. **Timeouts not covering all hang scenarios**: `globalTimeout` may not apply during fixture setup/teardown or webServer startup.
3. **`PLAYWRIGHT_BASE_URL` misconfiguration**: If not set, Playwright starts its own dev server on :5173, conflicting with the docker-compose stack serving on :8000.
4. **No per-test timeout enforcement in CI command**: The `npx playwright test` command uses `--timeout=60000` but this may not cover all code paths.

### 4.2 Changes Required

#### 4.2.1 CI Workflow (`.github/workflows/ci.yml`)

**Change 1: Make submodule check a hard failure**
```bash
# Current (warning):
if ! git -C frontend log --oneline --grep="#141" | grep -q .; then
  echo "::warning::submodule may not have #141 fix..."
fi

# New (failure):
if ! git -C frontend log --oneline --grep="#141" | grep -q .; then
  echo "::error::submodule missing #141 fix (localStorage.clear() after navigation)"
  git -C frontend log --oneline -3
  exit 1
fi
```

**Change 2: Add explicit timeout to Playwright command with verbose output**
```bash
# Current:
run: npx playwright test --fail-on-flaky-tests --retries=0 --timeout=60000

# New:
run: |
  npx playwright test \
    --fail-on-flaky-tests \
    --retries=0 \
    --timeout=60000 \
    --reporter=line \
    --output=playwright-results \
    2>&1 | tee playwright.log
```

**Change 3: Add diagnostic step on failure**
```bash
- name: Diagnose Playwright hang on failure
  if: failure()
  run: |
    echo "=== Last 100 lines of Playwright output ==="
    tail -100 playwright.log || true
    echo "=== Playwright report (if exists) ==="
    ls -la playwright-report/ 2>/dev/null || echo "No report directory"
    echo "=== Test results (if exists) ==="
    cat playwright-results/results.json 2>/dev/null | jq '.suites[].specs[] | {title, ok, errors, duration}' 2>/dev/null || true
```

#### 4.2.2 Playwright Configuration (`frontend/playwright.config.ts`)

**Change 1: Ensure `globalTimeout` is enforced for all phases**
- Current: `globalTimeout: 1800000` (30 min) - already set
- Verify: Add `globalTimeout` applies to setup/teardown by setting `timeout` on each test project

**Change 2: Add `expect` timeout for assertions**
- Already set: `expect: { timeout: 10000 }` - keep

**Change 3: Ensure `PLAYWRIGHT_BASE_URL` disables webServer**
- Current: `webServer: process.env.CI ? [...] : undefined` - correct
- Verify: When `PLAYWRIGHT_BASE_URL` is set, `webServer` should be `undefined`

**Change 4: Add `testConfig` hook for logging**
```typescript
// Add to config:
testConfig: {
  // Log which test is starting
  beforeAll: async () => {
    console.log('[Playwright] Test suite starting');
  },
  afterAll: async () => {
    console.log('[Playwright] Test suite completed');
  }
}
```

### 4.3 Frontend Submodule (rendering-replicate)

The submodule MUST be updated to include the #141 fix. The fix is:
- **File**: `frontend/tests/e2e/auth.spec.ts`
- **Change**: Move `localStorage.clear()` to `afterEach` AFTER navigation, not `beforeEach`

This is tracked in the upstream repo at `https://github.com/AlZrSe/rendering-replicate`. The CI check validates this by grepping commit messages for "#141".

---

## 5. Edge Cases and Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **R1**: `globalTimeout` not respected during webServer startup | Medium | High | CI starts services via docker-compose, not Playwright webServer; webServer is `undefined` when `PLAYWRIGHT_BASE_URL` set |
| **R2**: Playwright `globalTimeout` doesn't cover fixture `beforeAll`/`afterAll` | Medium | High | Add explicit timeout to CI command (`--timeout=60000` per test + job timeout 60 min) |
| **R3**: Submodule check false positive (grep matches wrong commit) | Low | Medium | Grep for exact commit hash `820a444` in addition to `#141` |
| **R4**: `PLAYWRIGHT_BASE_URL` not propagated to tests | Low | High | CI sets env var explicitly; verify in `playwright.config.ts` |
| **R5**: Flaky tests pass on retry locally but fail in CI (`--retries=0`) | Medium | Medium | This is intentional (AC-16 in CI); flaky tests must be fixed, not retried |
| **R6**: Docker volumes conflict on runner (shc-data, shc-state) | Low | High | Pre-check step already validates volumes are absent |

---

## 6. Implementation Checklist

### Phase 1: CI Workflow Fixes (Immediate)
- [ ] Change submodule check from warning to hard failure (exit 1)
- [ ] Add grep for exact commit hash `820a444` as secondary check
- [ ] Add verbose Playwright output (`--reporter=line`, tee to log)
- [ ] Add diagnostic step on failure to capture hang details
- [ ] Verify `PLAYWRIGHT_BASE_URL` is set in CI env

### Phase 2: Playwright Config Hardening
- [ ] Verify `globalTimeout` applies to all test phases
- [ ] Add test-level logging hooks
- [ ] Ensure `webServer` is disabled when `PLAYWRIGHT_BASE_URL` set

### Phase 3: Submodule Update
- [ ] Update frontend submodule to commit containing #141 fix
- [ ] Verify fix: `localStorage.clear()` in `afterEach` after navigation
- [ ] Bump submodule reference in parent repo

### Phase 4: Validation
- [ ] Run CI pipeline 3x on `main` to verify consistency
- [ ] Confirm all ACs met

---

## 7. Related Files

| File | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | CI pipeline with e2e job |
| `frontend/playwright.config.ts` | Playwright configuration |
| `frontend/tests/e2e/auth.spec.ts` | Test with localStorage.clear() fix (#141) |
| `docker-compose.yaml` | Stack under test |
| `docs/spec.md` | Overall project spec |

---

## 8. Definition of Done

- [ ] Spec document created and reviewed
- [ ] CI workflow updated (submodule check = failure, verbose output, diagnostics)
- [ ] Playwright config hardened
- [ ] Frontend submodule updated to include #141 fix
- [ ] Submodule reference bumped in parent repo
- [ ] CI passes 3 consecutive times on `main`
- [ ] PM accepts the work

---

*Generated during grooming of issue #146*
*Date: 2026-10-22*