# Spec: pytest-asyncio Version Conflict Fix (Issue #69)

## Problem Statement

The pinned development dependencies in `pyproject.toml` contain an incompatible version pair:
- `pytest==8.0.0`
- `pytest-asyncio==0.23.0`

When running `pytest --cov=backend` from a clean environment, test collection fails with an `AttributeError` inside `pytest-asyncio`'s plugin before any test module body runs:

```
AttributeError: 'Package' object has no attribute 'obj'
```

This occurs because `pytest-asyncio==0.23.0` assumes `collector.obj` exists on a `Package` node, but pytest 8.0.0 removed this attribute from `Package` nodes. The fix was introduced in `pytest-asyncio==0.23.8` which checks the collector type before accessing `.obj`.

This is a critical blocker: **no backend tests can run from a clean checkout**, making all test-related claims in other issues unverifiable.

## Acceptance Criteria

| ID | Criterion | Verification |
|----|-----------|--------------|
| AC-1 | `pip install -e ".[dev]"` installs without conflict | Fresh venv, command succeeds |
| AC-2 | `pytest --cov=backend` collects the full test suite (505+ items) instead of erroring at collection | Run command, verify collection count > 0 |
| AC-3 | The `pytest-asyncio` pin in `pyproject.toml` is updated to `0.23.8` (staying on 0.23.x series) | Check `pyproject.toml` line 35 |
| AC-4 | `pytest` pin remains at `8.0.0` (no version change) | Check `pyproject.toml` line 34 |
| AC-5 | Backend lint/type gates (`ruff check . && ruff format . && mypy .`) pass without new violations | Run lint/type commands |
| AC-6 | No new test failures introduced by the version bump (delta zero vs known failures) | Compare test results to known baseline |

## Test Scenarios

### Scenario 1: Clean Environment Collection
**Given** a fresh Python 3.11+ virtual environment  
**When** running `pip install -e ".[dev]"` followed by `pytest --cov=backend --collect-only`  
**Then** test collection completes without errors and reports 505+ collected items

### Scenario 2: Test Execution
**Given** a fresh environment with dependencies installed  
**When** running `pytest --cov=backend`  
**Then** tests execute (some may pass/fail/skip per existing baseline, but no collection crash)

### Scenario 3: Lint/Type Gates
**Given** dependencies installed  
**When** running `ruff check . && ruff format . && mypy .` from repo root  
**Then** commands exit with code 0 (no new violations introduced)

## Files to Change

| File | Change | Rationale |
|------|--------|-----------|
| `pyproject.toml` | Line 35: `"pytest-asyncio==0.23.0"` → `"pytest-asyncio==0.23.8"` | Fixes the pytest 8.0.0 compatibility issue while staying on 0.23.x series (0.24.x changes `event_loop` fixture contract) |

## Verification Commands

```bash
# 1. Fresh environment setup (run once)
python -m venv .venv-test
.venv-test\Scripts\activate
pip install -e ".[dev]"

# 2. Verify collection works
pytest --cov=backend --collect-only -q

# 3. Run full test suite
pytest --cov=backend

# 4. Verify lint/type gates
ruff check . && ruff format . && mypy .

# 5. Verify no other version changes
grep -E "(pytest|pytest-asyncio)==" pyproject.toml
# Expected output:
# pytest==8.0.0
# pytest-asyncio==0.23.8
```

## Notes

- The fix is a single-line pin change in `pyproject.toml`
- Do not upgrade to `pytest-asyncio>=0.24.x` — it changes the `event_loop` fixture contract and would require test modifications
- This unblocks issues #54 (lint/type gates), #57 (sqlite lock flakiness), #60 (CI workflow), and #26 (CI workflow)
- After this fix, the test suite will expose existing failures (13 fail, 29 skip on Linux per issue) which are tracked separately