# Specification: Fix Backend Ruff and Mypy Errors (Issue #54)

**Status: groomed, ready for SWE**

## Issue Summary
Backend `ruff check`, `ruff format`, and `mypy` are failing on `main` branch. A CI gate (issue #26) would land red if added now. This spec defines the work to make all three commands pass cleanly.

## Current State (as of grooming)
- **Ruff check**: ~41 errors reported in original spec; however, several have been addressed:
  - `job_spec_to_yaml_bytes` undefined (6× in `test_websocket.py`) — **FIXED** (function exists in `backend/tests/factories.py`)
  - `manual_scan` complexity (C901) — **FIXED** (method refactored into helpers: `_scan_folder_structure`, `_process_deleted_files`, `_process_new_files`)
  - Remaining: unused imports/variables (F401, F841), E402 import position, E501 line length
- **Ruff format**: 4 files would be reformatted (unchanged):
  - `backend/core/errors.py`
  - `backend/tests/unit/test_api_contract.py`
  - `backend/tests/unit/test_api_fixes.py`
  - `backend/tests/unit/test_config.py`
- **Mypy**: 139 errors in 27 files (original count); however:
  - **Dependencies already resolved** by issues #74, #69, #73:
    - `types-PyYAML==6.0.12.20260906` added to dev deps (fixes Category D)
    - `pytest-asyncio==0.23.8` pinned (fixes #69 collection crash)
    - `types-python-jose==3.3.4.20240106` fixed typo (fixes #73 install failure)
    - `typer[rich]` → `typer` (fixes #74 pip warning)
  - **Many errors suppressed via mypy overrides** in `pyproject.toml`:
    - All test files (`backend.tests.*`) have `ignore_errors = true`
    - `backend.core.config`, `backend.core.deps`, `backend.api.v1.jobs` ignored
    - This means `mypy backend/` currently exits 0, but errors exist in non-ignored code
  - **Real errors to fix** (not ignored):
    - `shared/metrics/sliding_window.py`, `shared/metrics/summarizer.py` (Category E)
    - `backend/store/database_store.py` SQLAlchemy attribute access (Category G)
    - `backend/services/*.py` return type annotations (Category H)

## User Stories

### US-1: Clean CI Gate
**As a** developer  
**I want** `ruff check . && ruff format . && mypy .` to pass on `main`  
**So that** a CI gate can be added without false failures

### US-2: Maintainable Code Quality
**As a** maintainer  
**I want** zero lint/type errors in the codebase  
**So that** new contributions don't accumulate technical debt

### US-3: Refactored Complex Code
**As a** developer  
**I want** the `manual_scan` method in `syncthing_service.py` to be under complexity threshold  
**So that** it's easier to maintain and test

## Acceptance Criteria

### AC-1: Ruff Check Passes
- [ ] `ruff check backend/` exits 0 with no errors
- [ ] No unused imports (F401), unused variables (F841), undefined names (F821)
- [ ] No line-too-long violations (E501) — wrap or restructure lines > 88 chars
- [ ] No import-not-at-top violations (E402)
- [ ] Complexity (C901) ≤ 10 for all functions (**already satisfied**)

### AC-2: Ruff Format Passes
- [ ] `ruff format backend/ --check` exits 0
- [ ] All 4 currently-misformatted files are reformatted:
  - `backend/core/errors.py`
  - `backend/tests/unit/test_api_contract.py`
  - `backend/tests/unit/test_api_fixes.py`
  - `backend/tests/unit/test_config.py`

### AC-3: Mypy Passes
- [ ] `mypy backend/` exits 0 with no errors
- [ ] All missing type annotations added
- [ ] All type incompatibilities resolved
- [ ] Missing stubs for `yaml` installed (`types-PyYAML`) — **DONE** (in pyproject.toml)
- [ ] Async generator return types annotated as `AsyncGenerator`
- [ ] ASGITransport type mismatches resolved in test files
- [ ] SQLAlchemy model attribute access issues fixed in `database_store.py`
- [ ] **Mypy overrides removed** from `pyproject.toml` for all fixed modules

### AC-4: No Regression
- [ ] All existing tests pass: `pytest backend/tests/ --cov=backend`
- [ ] No functional behavior changes

## Error Inventory & Fix Strategy

### Category A: Ruff — Auto-fixable / Trivial (≈20 errors, some already fixed)
| File | Error | Fix | Status |
|------|-------|-----|--------|
| `backend/tests/unit/test_api.py:630` | F841 `invalid_spec` unused | Remove or prefix `_` | **TODO** |
| `backend/tests/unit/test_contract_fixes.py:12` | F401 `settings` unused | Remove import | **TODO** |
| `backend/tests/unit/test_shared_file_ops.py:190` | E402 `import sys` not at top | Move to top | **TODO** |
| `backend/tests/unit/test_store.py:702-703` | F841 `job`, `counter_after_create` unused | Remove or prefix `_` | **TODO** |
| `backend/tests/unit/test_store.py:961` | E402 `import asyncio` not at top | Move to top | **TODO** |
| `backend/tests/unit/test_websocket.py` (6×) | F821 `job_spec_to_yaml_bytes` undefined | Import from `tests.factories` | **FIXED** (already imported) |
| `backend/tests/unit/test_websocket.py` (4×) | F841 `ws` unused | Prefix `_` | **TODO** |
| `backend/tests/unit/test_websocket.py` (2×) | F841 `message` unused | Prefix `_` or remove | **TODO** |
| `backend/services/syncthing_service.py:356` | C901 complexity 13 > 10 | Refactor into helper methods | **FIXED** (already refactored) |

### Category B: Ruff — Line Length (E501) (≈15 errors)
All in test files, mostly long comment lines or skip reasons. Fix by:
- Splitting long comments across multiple lines
- Using shorter variable names in comprehension lines
- Acceptable to use `# noqa: E501` on skip-reason strings if splitting harms readability

### Category C: Ruff Format (4 files)
Run `ruff format backend/` to auto-fix.

### Category D: Mypy — Missing Stubs (3 files) — **RESOLVED by #74**
| File | Error | Fix | Status |
|------|-------|-----|--------|
| `shared/file_ops/yaml_utils.py:7` | Library stubs not installed for "yaml" | `pip install types-PyYAML` | **FIXED** (in pyproject.toml) |
| `backend/tests/integration/test_syncthing_flow.py:7` | Same | Same | **FIXED** |
| `backend/tests/unit/test_syncthing.py:106` | Same | Same | **FIXED** |
| `backend/tests/unit/test_shared_schemas.py:409` | Same | Same | **FIXED** |

### Category E: Mypy — Shared Metrics Types (≈15 errors) — **NOT IGNORED, MUST FIX**
Files: `shared/metrics/sliding_window.py`, `shared/metrics/summarizer.py`
- Add type annotations for `_data`, `_timestamps`
- Fix `add` method signature compatibility
- Handle `Optional[float]` → `int` conversions safely
- Fix `no-any-return` by adding proper return type handling

### Category F: Mypy — Core Config & Deps (≈5 errors) — **IGNORED via mypy overrides**
- `backend/core/config.py:124` — Return `str` not `Any` (ignored)
- `backend/core/config.py:322` — Fix `ALLOWED_HOSTS` return type (ignored)
- `backend/core/deps.py:29,61` — Handle `Optional[str]` for `is_localhost` (ignored)
- **Note**: These are in modules with `ignore_errors = true` in pyproject.toml. To truly pass mypy without ignores, they need fixing.

### Category G: Mypy — Store / Database (≈40 errors) — **NOT IGNORED, MUST FIX**
File: `backend/store/database_store.py` — **Highest complexity**
- SQLAlchemy model attribute access (`JobModel.timestamp`, `gpu_index`, etc.)
- Column vs. instance attribute confusion
- `CPUMetricModel` / `GPUMetricModel` assignment mismatches
- Return type mismatches (Tuple with `Optional[int]` vs `int`)
- Float → int assignments
- Fix by: using proper SQLAlchemy column access patterns, ensuring model definitions match usage
- Currently uses `# type: ignore[attr-defined]` extensively

### Category H: Mypy — Services (≈15 errors) — **NOT IGNORED, MUST FIX**
- `backend/services/syncthing_service.py`: `Observer` type, `Path` vs `str` for `read_yaml`
- `backend/services/node_service.py`: `no-any-return` on all methods
- `backend/services/job_service.py`: `no-any-return` on all methods
- `backend/api/v1/syncthing.py`: `no-any-return` (line 18)
- Fix by: adding proper return type annotations, importing `Observer` from `watchdog.observers` correctly

### Category I: Mypy — Test Files (≈40 errors) — **IGNORED via mypy overrides**
- Async generator fixtures: annotate return as `AsyncGenerator[...]`
- `ASGITransport` type mismatch: FastAPI app vs ASGI callable — cast or use `app.router`
- `test_contract_fixes.py:251` — Return `int` not `Optional[int]`
- `test_api_fixes.py:251` — Same
- **Note**: All test files have `ignore_errors = true` in pyproject.toml. To truly pass mypy without ignores, they need fixing.

## Test Scenarios

| Scenario | Command | Expected |
|----------|---------|----------|
| Ruff check clean | `ruff check backend/` | Exit 0, no output |
| Ruff format clean | `ruff format backend/ --check` | Exit 0 |
| Mypy clean (no ignores) | `mypy backend/` | Exit 0, no errors |
| Full lint pipeline | `ruff check backend/ && ruff format backend/ --check && mypy backend/` | Exit 0 |
| Unit tests pass | `pytest backend/tests/unit -v` | All pass |
| Integration tests pass | `pytest backend/tests/integration -v` | All pass |
| Coverage maintained | `pytest backend/tests/ --cov=backend` | ≥ 80% |

**Note**: Currently `mypy backend/` exits 0 because of `ignore_errors = true` overrides. The true test is running mypy *without* those overrides, or removing them incrementally as modules are fixed.

## Definition of Done
1. All three lint commands pass (`ruff check`, `ruff format --check`, `mypy`)
2. All existing tests pass
3. No functional changes introduced
4. **All mypy overrides removed** from `pyproject.toml` (no `ignore_errors = true` remaining)
5. Specification document updated if any decisions deviate from this plan
6. Commit with message: `fix: resolve backend ruff and mypy errors for CI gate (#54)`

## Implementation Notes for SWE

### Priority Order
1. **Run `ruff format backend/`** — auto-fixes 4 files, no risk
2. **Run `ruff check backend/ --fix`** — auto-fixes 1 error, safe
3. **Fix trivial ruff errors** (unused vars, imports, E402) — mechanical
4. ~~Fix `job_spec_to_yaml_bytes` undefined~~ — **ALREADY FIXED** (exists in `backend/tests/factories.py`)
5. ~~Refactor `manual_scan` complexity~~ — **ALREADY FIXED** (extracted helpers: `_scan_folder_structure`, `_process_deleted_files`, `_process_new_files`)
6. **Fix E501 line lengths** — split comments, minor restructuring
7. ~~Install `types-PyYAML`~~ — **ALREADY DONE** by #74 (in pyproject.toml)
8. **Fix shared metrics types** — core library, fix first (Category E, not ignored)
9. **Fix core config/deps** — few errors, high impact (Category F, currently ignored but must be fixed for clean mypy)
10. **Fix store/database_store.py** — most errors, requires SQLAlchemy knowledge (Category G, not ignored)
11. **Fix services** — straightforward type annotations (Category H, not ignored)
12. **Fix test file mypy errors** — async generators, ASGITransport casts (Category I, currently ignored but must be fixed for clean mypy)
13. **Remove mypy overrides** in pyproject.toml incrementally as each module is fixed
14. **Run full test suite** — verify no regressions

### `manual_scan` Refactoring — **ALREADY COMPLETE**
The method has been refactored with these helpers:
- `_scan_folder_structure()`
- `_process_deleted_files()`
- `_process_new_files()`

### SQLAlchemy Model Issues
The `database_store.py` errors suggest model definitions (`JobModel`, `NodeModel`, `GPUMetricModel`, `CPUMetricModel`) don't match the attributes being accessed. Check `backend/store/database.py` for actual column definitions and align. Currently uses `# type: ignore[attr-defined]` extensively — replace with proper typing using SQLAlchemy's `Mapped` and type annotations.

### ASGITransport Fix Pattern
```python
from httpx import ASGITransport
transport = ASGITransport(app=app.router)  # or cast(app, "ASGIApp")
```

### Dependencies Already Fixed (No Action Needed)
The following dependency issues from related issues are **already resolved** in `pyproject.toml`:
- **#74**: `typer[rich]==0.9.0` → `typer==0.9.0` (line 26); `types-PyYAML==6.0.12.20260906` added (line 45)
- **#69**: `pytest-asyncio==0.23.0` → `pytest-asyncio==0.23.8` (line 35)
- **#73**: `types-python-jose==3.3.0.20240106` → `types-python-jose==3.3.4.20240106` (line 44)

Do not modify these pins — they are correct and verified.

## References
- `pyproject.toml` — ruff/mypy config
- `PROCESS.md` — Definition of Done
- `docs/spec.md` — Architecture spec
- Issue #26 — CI gate (blocked by this)
- Issue #56 — Signing key hygiene (related to config.py changes)