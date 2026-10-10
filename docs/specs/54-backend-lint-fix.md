# Specification: Fix Backend Ruff and Mypy Errors (Issue #54)

## Issue Summary
Backend `ruff check`, `ruff format`, and `mypy` are failing on `main` branch. A CI gate (issue #26) would land red if added now. This spec defines the work to make all three commands pass cleanly.

## Current State
- **Ruff check**: 41 errors (1 fixable with `--fix`, 7 more with `--unsafe-fixes`)
- **Ruff format**: 4 files would be reformatted
- **Mypy**: 139 errors in 27 files

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
- [ ] Complexity (C901) ≤ 10 for all functions

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
- [ ] Missing stubs for `yaml` installed (`types-PyYAML`)
- [ ] Async generator return types annotated as `AsyncGenerator`
- [ ] ASGITransport type mismatches resolved in test files
- [ ] SQLAlchemy model attribute access issues fixed in `database_store.py`

### AC-4: No Regression
- [ ] All existing tests pass: `pytest backend/tests/ --cov=backend`
- [ ] No functional behavior changes

## Error Inventory & Fix Strategy

### Category A: Ruff — Auto-fixable / Trivial (≈20 errors)
| File | Error | Fix |
|------|-------|-----|
| `backend/tests/unit/test_api.py:630` | F841 `invalid_spec` unused | Remove or prefix `_` |
| `backend/tests/unit/test_contract_fixes.py:12` | F401 `settings` unused | Remove import |
| `backend/tests/unit/test_shared_file_ops.py:190` | E402 `import sys` not at top | Move to top |
| `backend/tests/unit/test_store.py:702-703` | F841 `job`, `counter_after_create` unused | Remove or prefix `_` |
| `backend/tests/unit/test_store.py:961` | E402 `import asyncio` not at top | Move to top |
| `backend/tests/unit/test_websocket.py` (6×) | F821 `job_spec_to_yaml_bytes` undefined | Import from `tests.factories` or define locally |
| `backend/tests/unit/test_websocket.py` (4×) | F841 `ws` unused | Prefix `_` |
| `backend/tests/unit/test_websocket.py` (2×) | F841 `message` unused | Prefix `_` or remove |
| `backend/services/syncthing_service.py:356` | C901 complexity 13 > 10 | Refactor into helper methods |

### Category B: Ruff — Line Length (E501) (≈15 errors)
All in test files, mostly long comment lines or skip reasons. Fix by:
- Splitting long comments across multiple lines
- Using shorter variable names in comprehension lines
- Acceptable to use `# noqa: E501` on skip-reason strings if splitting harms readability

### Category C: Ruff Format (4 files)
Run `ruff format backend/` to auto-fix.

### Category D: Mypy — Missing Stubs (3 files)
| File | Error | Fix |
|------|-------|-----|
| `shared/file_ops/yaml_utils.py:7` | Library stubs not installed for "yaml" | `pip install types-PyYAML` |
| `backend/tests/integration/test_syncthing_flow.py:7` | Same | Same |
| `backend/tests/unit/test_syncthing.py:106` | Same | Same |
| `backend/tests/unit/test_shared_schemas.py:409` | Same | Same |

### Category E: Mypy — Shared Metrics Types (≈15 errors)
Files: `shared/metrics/sliding_window.py`, `shared/metrics/summarizer.py`
- Add type annotations for `_data`, `_timestamps`
- Fix `add` method signature compatibility
- Handle `Optional[float]` → `int` conversions safely
- Fix `no-any-return` by adding proper return type handling

### Category F: Mypy — Core Config & Deps (≈5 errors)
- `backend/core/config.py:124` — Return `str` not `Any`
- `backend/core/config.py:322` — Fix `ALLOWED_HOSTS` return type
- `backend/core/deps.py:29,61` — Handle `Optional[str]` for `is_localhost`

### Category G: Mypy — Store / Database (≈40 errors)
File: `backend/store/database_store.py` — **Highest complexity**
- SQLAlchemy model attribute access (`JobModel.timestamp`, `gpu_index`, etc.)
- Column vs. instance attribute confusion
- `CPUMetricModel` / `GPUMetricModel` assignment mismatches
- Return type mismatches (Tuple with `Optional[int]` vs `int`)
- Float → int assignments
- Fix by: using proper SQLAlchemy column access patterns, ensuring model definitions match usage

### Category H: Mypy — Services (≈15 errors)
- `backend/services/syncthing_service.py`: `Observer` type, `Path` vs `str` for `read_yaml`
- `backend/services/node_service.py`: `no-any-return` on all methods
- `backend/services/job_service.py`: `no-any-return` on all methods
- `backend/api/v1/syncthing.py`: `no-any-return`
- Fix by: adding proper return type annotations, importing `Observer` from `watchdog.observers` correctly

### Category I: Mypy — Test Files (≈40 errors)
- Async generator fixtures: annotate return as `AsyncGenerator[...]`
- `ASGITransport` type mismatch: FastAPI app vs ASGI callable — cast or use `app.router`
- `test_contract_fixes.py:251` — Return `int` not `Optional[int]`
- `test_api_fixes.py:251` — Same

## Test Scenarios

| Scenario | Command | Expected |
|----------|---------|----------|
| Ruff check clean | `ruff check backend/` | Exit 0, no output |
| Ruff format clean | `ruff format backend/ --check` | Exit 0 |
| Mypy clean | `mypy backend/` | Exit 0, no errors |
| Full lint pipeline | `ruff check backend/ && ruff format backend/ --check && mypy backend/` | Exit 0 |
| Unit tests pass | `pytest backend/tests/unit -v` | All pass |
| Integration tests pass | `pytest backend/tests/integration -v` | All pass |
| Coverage maintained | `pytest backend/tests/ --cov=backend` | ≥ 80% |

## Definition of Done
1. All three lint commands pass (`ruff check`, `ruff format --check`, `mypy`)
2. All existing tests pass
3. No functional changes introduced
4. Specification document updated if any decisions deviate from this plan
5. Commit with message: `fix: resolve backend ruff and mypy errors for CI gate (#54)`

## Implementation Notes for SWE

### Priority Order
1. **Run `ruff format backend/`** — auto-fixes 4 files, no risk
2. **Run `ruff check backend/ --fix`** — auto-fixes 1 error, safe
3. **Fix trivial ruff errors** (unused vars, imports, E402) — mechanical
4. **Fix `job_spec_to_yaml_bytes` undefined** — find/define helper
5. **Refactor `manual_scan` complexity** — extract helpers, preserve behavior
6. **Fix E501 line lengths** — split comments, minor restructuring
7. **Install `types-PyYAML`** — resolves 4 mypy import errors
8. **Fix shared metrics types** — core library, fix first
9. **Fix core config/deps** — few errors, high impact
10. **Fix store/database_store.py** — most errors, requires SQLAlchemy knowledge
11. **Fix services** — straightforward type annotations
12. **Fix test file mypy errors** — async generators, ASGITransport casts
13. **Run full test suite** — verify no regressions

### `job_spec_to_yaml_bytes` Resolution
Search for this function in codebase. If not found, add to `backend/tests/factories.py`:
```python
def job_spec_to_yaml_bytes(spec: JobSpec) -> bytes:
    import yaml
    return yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False).encode()
```

### `manual_scan` Refactoring
Extract these helpers from the 13-complexity method:
- `_scan_folder_structure()`
- `_process_yaml_files()`
- `_update_job_states()`
- `_sync_with_database()`

### SQLAlchemy Model Issues
The `database_store.py` errors suggest model definitions (`JobModel`, `NodeModel`, `GPUMetricModel`, `CPUMetricModel`) don't match the attributes being accessed. Check `backend/store/database.py` for actual column definitions and align.

### ASGITransport Fix Pattern
```python
from httpx import ASGITransport
transport = ASGITransport(app=app.router)  # or cast(app, "ASGIApp")
```

## References
- `pyproject.toml` — ruff/mypy config
- `PROCESS.md` — Definition of Done
- `docs/spec.md` — Architecture spec
- Issue #26 — CI gate (blocked by this)
- Issue #56 — Signing key hygiene (related to config.py changes)