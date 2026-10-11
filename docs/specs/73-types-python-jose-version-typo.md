# Spec: Fix `types-python-jose` Version Typo in pyproject.toml

## Issue
[#73](https://github.com/AlZrSe/scientific-home-cluster/issues/73) — `pip install -e ".[dev]"` fails because `types-python-jose==3.3.0.20240106` has never been published on PyPI.

## Problem Statement
The `pyproject.toml` file pins `types-python-jose==3.3.0.20240106` in the `[project.optional-dependencies] dev` section (line 44). This version does not exist on PyPI. The correct version with the `20240106` date suffix is `3.3.4.20240106`.

This blocks:
- Clean environment installation (`pip install -e ".[dev]"`)
- Test collection (verified in issue #69's AC-1)
- CI/CD pipeline execution

## Root Cause
Typo in the patch version number: `3.3.0.20240106` → should be `3.3.4.20240106`.

## Acceptance Criteria

### AC-1: Clean install succeeds
```bash
pip install -e ".[dev]"
```
Completes without errors in a clean virtual environment.

### AC-2: No other version changes
Only the `types-python-jose` version is changed. All other dependencies remain at their current pinned versions.

### AC-3: Lint/type delta zero
After the fix:
- `ruff check . && ruff format . --check` passes (no new lint errors)
- `mypy .` passes (no new type errors)
- `pytest --cov=backend` passes (all existing tests pass)

## Test Scenarios

| Scenario | Expected Result |
|----------|-----------------|
| Fresh venv + `pip install -e ".[dev]"` | Installs successfully |
| `pip install -e ".[dev]"` in existing venv | Upgrades/installs correct version |
| `ruff check . && ruff format . --check` | Zero lint/format errors |
| `mypy .` | Zero type errors (baseline unchanged) |
| `pytest --cov=backend` | All tests pass, coverage ≥80% |

## Files to Change

| File | Line | Change |
|------|------|--------|
| `pyproject.toml` | 44 | `"types-python-jose==3.3.0.20240106"` → `"types-python-jose==3.3.4.20240106"` |

## Verification Commands

```bash
# 1. Verify clean install works
python -m venv test_venv && test_venv\Scripts\activate && pip install -e ".[dev]"

# 2. Verify lint/type checks pass
ruff check . && ruff format . --check && mypy .

# 3. Verify tests pass
pytest --cov=backend

# 4. Verify the installed version
pip show types-python-jose | grep Version
# Expected: Version: 3.3.4.20240106
```

## Notes
- This is a **typo fix**, not a version upgrade. The intent (date suffix `20240106`) is preserved.
- The `python-jose[cryptography]==3.3.0` runtime dependency (line 21) is correct and unchanged.
- Frontend submodule (`frontend/`) is untouched.