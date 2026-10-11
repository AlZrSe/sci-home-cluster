# Spec: Python Version Alignment (README vs pyproject.toml)

## Issue
- **Issue #76**: README says Python 3.9+ but `pyproject.toml` has `requires-python = ">=3.11"`.
- The codebase uses `tomllib` (standard library in Python 3.11+) in `backend/core/config.py`.
- Multiple documentation files incorrectly state Python 3.9+.

## Root Cause
The `backend/core/config.py` module imports `tomllib` (line 10) to read the version from `pyproject.toml`. `tomllib` was added to the standard library in Python 3.11. There is no `tomli` backport dependency in `pyproject.toml`, so the codebase **requires Python 3.11+**.

## Files to Update
All references to "Python 3.9+" or "Python 3.9 or higher" must be changed to "Python 3.11+":

1. **README.md** (line 73): `- Python 3.9+` → `- Python 3.11+`
2. **docs/backend-setup-spec.md** (lines 11, 116): `Python 3.9+` / `Python 3.9 or higher` → `Python 3.11+`
3. **docs/project-setup-spec.md** (line 26): `Python 3.9+` → `Python 3.11+`
4. **.github/ISSUE_TEMPLATE/001-project-setup.md** (line 20): `Python 3.9+` → `Python 3.11+`

## Acceptance Criteria
1. All documentation files agree on the minimum Python version: **3.11+**.
2. `pyproject.toml` remains unchanged (`requires-python = ">=3.11"`).
3. The codebase continues to work (uses `tomllib` which is stdlib in 3.11+).
4. No version-specific syntax other than `tomllib` is used (verified by grep).

## Test Plan
- Verify all updated files show "Python 3.11+" or equivalent.
- Run `pip install -e .[dev]` in a fresh Python 3.11+ environment — should succeed.
- Run backend tests: `cd backend && pytest --cov=backend` — should pass.
- Verify no `tomli` dependency is needed (stdlib `tomllib` is used).

## Out of Scope
- Adding `tomli` backport to support Python 3.9/3.10 — not required per current `pyproject.toml` and would be a separate enhancement.
- Updating any CI matrix (none exists currently).