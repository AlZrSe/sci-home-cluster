# e2e Tests Failing: Dashboard Elements Not Rendering in CI

## Problem
The e2e Playwright tests are consistently failing in CI because the dashboard page elements are not rendering/becoming visible within the timeout periods, even after increasing timeouts to 45s for critical elements.

## Current Failure Pattern
All e2e tests fail with similar patterns:
- `aside a[href="/jobs"]` - navigation link not becoming visible
- `table` (jobs table) - not visible within 45s
- `header button:has(svg)` (theme toggle) - not found
- `button:has-text("Create job")` - timeout on click

## Root Cause Analysis
The dashboard page in the CI environment appears to not be rendering the expected UI elements. Possible causes:

1. **No test data in the database**: The compose stack starts with an empty database, so the dashboard shows empty states instead of metric cards/table with data
2. **React hydration issues**: The SPA may not be fully hydrating in the CI environment
4. **Missing test data seeding**: No mechanism to seed test data before e2e tests run
4. **CSS/layout differences**: Headless CI environment may have different rendering

## Evidence from CI Logs
```
Error: expect(locator).toBeVisible() failed
Error: element(s) not found
- waiting for locator('aside a[href="/jobs"]').first()
TimeoutError: page.waitForSelector: Timeout 45000ms exceeded.
- waiting for locator('table') to be visible
TimeoutError: page.waitForSelector: Timeout 45000ms exceeded.
- waiting for locator('header button:has(svg)') to be visible
```

## Current CI Status (as of 2026-10-22)
- ✅ backend-gates: PASSING
- ✅ frontend-gates: PASSING  
- ✅ compose-suite: PASSING
- ❌ e2e: FAILING

## Recommended Fixes

### 1. Add Test Data Seeding (High Priority)
- Create a database seeding script that runs in the compose stack before e2e tests
- Seed at least:
  - 3-5 sample jobs with different statuses (RUNNING, COMPLETED, FAILED, PENDING)
  - 2-3 sample nodes (ONLINE, OFFLINE)
  - Sample profiles (VASP, LAMMPS, etc.)
  - This will populate the dashboard with actual data to test against

### 2. Add Data Seeding to Compose Stack (High Priority)
- Add a `db-seed` service to `docker-compose.yaml` that runs after database is ready
- Use the existing backend API to create test data
- Ensure it runs before the e2e tests start

### 3. Add Debug Test for Dashboard Rendering (Medium Priority)
- Add a debug e2e test that dumps the full page HTML and screenshots
- This will help identify what's actually being rendered in CI
- Run this test first to diagnose rendering issues

### 4. Verify Frontend Build in CI (Medium Priority)
- Add a step to verify the frontend build output includes all expected routes
- Check that the SPA build includes the login page bundle
- Verify the `_shell.html` contains the expected structure

### 5. Consider Test Data Isolation (Lower Priority)
- Use database transactions that rollback after each test
- Or use a separate test database that gets reset between runs
- Currently tests share state which can cause flakiness

### 6. Review Selectors for Robustness (Ongoing)
- Use more stable selectors (data-testid attributes preferred)
- Add data-testid attributes to critical UI elements
- Reduce reliance on text-based selectors that may change

## Files to Modify
- `docker-compose.yaml` - Add db-seed service
- `backend/tests/compose/conftest.py` - Add fixture for data seeding
- `frontend/tests/e2e/` - Add debug test, improve selectors
- `frontend/src/components/` - Add data-testid attributes

## Priority Order
1. **Immediate**: Add test data seeding to compose stack
2. **Immediate**: Add debug e2e test to diagnose rendering
3. **Short-term**: Seed test data via backend API in compose
4. **Short-term**: Add data-testid attributes to critical elements
5. **Ongoing**: Improve e2e test reliability and coverage

## Related Files
- `docker-compose.yaml` - Compose stack definition
- `backend/tests/compose/conftest.py` - Compose test fixtures
- `frontend/tests/e2e/dashboard.spec.ts` - Dashboard e2e tests
- `frontend/tests/e2e/test-utils.ts` - Test utilities
- `frontend/src/components/layout/Shell.tsx` - Navigation components
- `frontend/src/routes/index.tsx` - Dashboard page component

## Success Criteria
- All 4 CI jobs pass consistently
- e2e tests run in < 10 minutes
- Tests are reliable (no flaky failures)
- Dashboard shows expected data in e2e tests