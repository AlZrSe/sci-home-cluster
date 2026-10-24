# CI Pipeline Improvements for Reliable e2e Tests

## Problem
The CI pipeline has several issues that make e2e tests unreliable and slow:
1. No test data seeding in compose stack
2. No debug capabilities for e2e failures
3. Docker build cache causing stale frontend builds
4. No database seeding before e2e tests
5. Long test run times due to missing optimizations

## Current CI Status
- ✅ backend-gates: PASSING
- ✅ frontend-gates: PASSING  
- ✅ compose-suite: PASSING
- ❌ e2e: FAILING (dashboard elements not rendering)

## Required CI Pipeline Improvements

### 1. Add Test Data Seeding to Compose Stack (High Priority)
- Add a `db-seed` service to `docker-compose.yaml` that:
  - Runs after database is healthy
  - Seeds test data via backend API
  - Completes before e2e tests start
- Seed data should include:
  - 5 sample jobs (RUNNING, COMPLETED, FAILED, PENDING, CANCELLED)
  - 3 nodes (2 ONLINE, 1 OFFLINE)
  - 4 builtin profiles (VASP, LAMMPS, GROMACS, Quantum ESPRESSO)

### 2. Add Debug E2E Test (High Priority)
- Add a debug test that runs first in the e2e suite
- Dumps full page HTML, screenshots, and console logs
- Helps diagnose rendering issues in CI
- Should run before other tests to diagnose failures quickly

### 3. Fix Docker Build Cache Issues (High Priority)
- e2e job uses `docker compose build --no-cache` but frontend-gates doesn't
- Add `--no-cache` to all Docker builds in CI to prevent stale frontend builds
- Ensure submodule changes are picked up in Docker builds

### 4. Add Database Seeding Fixture (High Priority)
- Add pytest fixture in `backend/tests/compose/conftest.py` that:
  - Seeds test data via backend API
  - Runs before e2e tests
  - Cleans up after tests (or uses transaction rollback)

### 5. Optimize CI Pipeline Speed (Medium Priority)
- Current CI takes ~15-20 minutes
- Target: < 10 minutes for full pipeline
- Parallelize more jobs
- Cache dependencies between runs

### 6. Add E2E Debug Test (High Priority)
- Add a diagnostic test that runs first in e2e suite
- Dumps page HTML, screenshots, console logs
- Helps diagnose rendering issues quickly
- Should run before other e2e tests

### 7. Fix Frontend Build Cache (Medium Priority)
- Ensure frontend submodule changes trigger Docker rebuild
- Add cache busting mechanism for frontend builds
- Verify submodule checkout in CI includes latest commits

### 8. Add Data-Testid Attributes (Ongoing)
- Add `data-testid` attributes to critical UI elements
- Update e2e tests to use data-testid selectors
- More stable than text/CSS selectors

## Files to Modify

### Docker/Compose
- `docker-compose.yaml` - Add db-seed service
- `docker/Dockerfile` - Add cache busting

### Backend
- `backend/tests/compose/conftest.py` - Add seeding fixture
- `backend/api/v1/auth.py` - Already has localhost bypass

### Frontend
- `frontend/tests/e2e/` - Add debug test, improve selectors
- `frontend/src/components/` - Add data-testid attributes
- `frontend/tests/e2e/dashboard.spec.ts` - Fix selectors, add waits
- `frontend/tests/e2e/profiles.spec.ts` - Fix selectors

### CI
- `.github/workflows/ci.yml` - Add cache busting, debug test, seeding step

## Success Criteria
- All 4 CI jobs pass consistently
- e2e tests run in < 10 minutes
- Tests are reliable (no flaky failures)
- Dashboard shows expected data in e2e tests
- Debug output available for failed runs

## Related Issues
- #149 - e2e tests failing: Dashboard elements not rendering in CI
- #150 - Frontend authentication bypass: Login form not rendering on localhost