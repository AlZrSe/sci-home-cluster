# Spec: Fix Documented Dev Install Failure and Test Collection Blockers (Issue #107)

## Issue

[#107](https://github.com/AlZrSe/scientific-home-cluster/issues/107) — The documented dev install (`pip install -e ".[dev]"`) fails on Windows (and potentially other platforms) because pinned dependencies lack pre-built wheels for Python 3.12+. Additionally, the issue description notes that two related defects (#73 and #69) are already fixed on main, but the overall dev install remains broken.

## Problem Statement

### Primary Blocker: Missing Wheels for Pinned Dependencies

The following pinned dependencies in `pyproject.toml` **do not have pre-built wheels for Python 3.12 on Windows**, forcing `pip` to build from source, which requires Microsoft Visual C++ Build Tools:

| Dependency | Pinned Version | Has Wheel for Py3.12 Windows? | Latest with Wheel |
|------------|----------------|-------------------------------|-------------------|
| `psutil` | `==5.9.0` | ❌ No | `7.2.2` (abi3) |
| `pyyaml` | `==5.3.1` | ❌ No | `6.0.3` (cp312) |
| `watchdog` | `==4.0.0` | ✅ Yes (but 6.0.0 was pulled) | `6.0.0` |

**Error message when building from source fails:**
```
error: Microsoft Visual C++ 14.0 or greater is required. Get it with "Microsoft C++ Build Tools"
```

This makes the documented install command **unresolvable on a clean Windows machine without Visual Studio Build Tools installed**, which is the standard development environment for most contributors.

### Secondary: Version Conflicts After Manual Fix

Even if a user manually installs newer versions with wheels (`psutil>=7`, `pyyaml>=6`), the package metadata still declares the old pinned versions, causing `pip check` to report conflicts:
```
scientific-home-cluster 0.1.0 requires psutil==5.9.0, but you have psutil 7.2.2 which is incompatible.
scientific-home-cluster 0.1.0 requires pyyaml==5.3.1, but you have pyyaml 6.0.3 which is incompatible.
```

### Already Fixed (Per Related Issues)

The following issues mentioned in #107 are **already fixed on main** and should remain fixed:

- **#73**: `types-python-jose==3.3.0.20240106` → `3.3.4.20240106` (typo fix, line 44) ✅ **Already correct in current pyproject.toml**
- **#69**: `pytest-asyncio==0.23.0` → `0.23.8` (pytest 8.0.0 compatibility, line 35) ✅ **Already correct in current pyproject.toml**

These fixes unblocked test collection (716 tests now collect successfully), but the dev install itself remains broken due to the wheel issue above.

## Root Cause Analysis

1. **Dependency pinning strategy**: The project pins exact versions (`==`) for reproducibility, but the pinned versions were chosen when Python 3.11 was current. Python 3.12+ dropped wheels for some older releases.

2. **Windows-specific wheel gap**: `psutil 5.9.0` and `pyyaml 5.3.1` were released before Python 3.12 and don't have `cp312` wheels on PyPI. The maintainers of these packages have since released newer versions with abi3/cp312 wheels.

3. **watchdog version drift**: `watchdog==4.0.0` has a wheel, but when `pip` resolves dependencies with `--no-deps` workarounds, it pulled `watchdog 6.0.0` (latest) due to `pytest-watch` dependency. The pin should be updated to a version compatible with the test suite.

4. **No CI validation of clean install**: The CI pipeline may not test `pip install -e ".[dev]"` from a clean environment on Windows, so this regression wasn't caught.

## Acceptance Criteria

| ID | Criterion | Verification |
|----|-----------|--------------|
| **AC-1** | `pip install -e ".[dev]"` completes successfully in a clean venv on Windows (Python 3.12+) without requiring Visual C++ Build Tools | Fresh venv, command exits 0 |
| **AC-2** | `pip install -e ".[dev]"` completes successfully in a clean venv on Linux/macOS (Python 3.11+) | CI passes on Ubuntu/macOS runners |
| **AC-3** | `pip check` reports **no conflicts** after install | Run `pip check`, exit code 0 |
| **AC-4** | `pytest --cov=backend --collect-only` collects **716+ tests** without collection errors | Run collection, verify count ≥ 716 |
| **AC-5** | `pytest --cov=backend` runs the full test suite (some may fail per existing baseline, but **no collection crash**) | Run full suite, verify no `AttributeError` at collection |
| **AC-6** | Backend lint/type gates pass: `ruff check . && ruff format . --check && mypy .` | Run lint/type commands, exit code 0 |
| **AC-7** | Only the necessary dependency versions are updated; no unrelated version changes | Diff `pyproject.toml` shows only intended lines changed |
| **AC-8** | The `types-python-jose` fix from #73 (`3.3.4.20240106`) and `pytest-asyncio` fix from #69 (`0.23.8`) remain in place | Verify lines 35 and 44 unchanged |

## Test Scenarios

### Scenario 1: Clean Windows Environment Install
**Given** a fresh Python 3.12+ virtual environment on Windows **without** Visual C++ Build Tools  
**When** running `pip install -e ".[dev]"`  
**Then** installation completes using only pre-built wheels (no source builds), exits 0

### Scenario 2: Clean Linux/macOS Environment Install
**Given** a fresh Python 3.11+ virtual environment on Linux or macOS  
**When** running `pip install -e ".[dev]"`  
**Then** installation completes, exits 0

### Scenario 3: Post-Install Validation
**Given** a successful dev install  
**When** running `pip check`  
**Then** no dependency conflicts reported

### Scenario 4: Test Collection
**Given** a successful dev install  
**When** running `pytest --cov=backend --collect-only`  
**Then** ≥ 716 tests collected, no `AttributeError` or `ImportError` during collection

### Scenario 5: Test Execution (Smoke)
**Given** a successful dev install  
**When** running `pytest --cov=backend -x -q` (stop on first failure, quiet)  
**Then** tests execute (baseline failures acceptable, but no infrastructure crashes)

### Scenario 6: Lint/Type Gates
**Given** a successful dev install  
**When** running `ruff check . && ruff format . --check && mypy .`  
**Then** all commands exit 0

## Files to Change

| File | Line | Change | Rationale |
|------|------|--------|-----------|
| `pyproject.toml` | 13 | `"psutil==5.9.0"` → `"psutil>=5.9.0,<8.0.0"` | Allow versions with abi3 wheels (7.x works on Py3.12). Upper bound <8 for safety. |
| `pyproject.toml` | 18 | `"pyyaml==5.3.1"` → `"pyyaml>=5.3.1,<7.0.0"` | Allow versions with cp312 wheels (6.x works on Py3.12). Upper bound <7 for safety. |
| `pyproject.toml` | 28 | `"watchdog==4.0.0"` → `"watchdog>=4.0.0,<7.0.0"` | Allow newer versions with wheels; 6.x is compatible. Upper bound <7 for safety. |

**Note**: Use flexible version ranges (`>=x.y.z,<next_major`) rather than exact pins for these three dependencies only. This preserves the intent of "known good minimum version" while allowing wheel-compatible upgrades. All other pins remain exact.

## Verification Commands

```bash
# 1. Fresh environment setup (Windows PowerShell)
python -m venv .venv-test
.venv-test\Scripts\activate
pip install -e ".[dev]"

# 2. Verify no conflicts
pip check

# 3. Verify test collection works
pytest --cov=backend --collect-only -q
# Expected: 716+ collected, no errors

# 4. Run lint/type gates
ruff check . && ruff format . --check && mypy .

# 5. Smoke test run (optional, may have baseline failures)
pytest --cov=backend -x -q

# 6. Verify critical pins unchanged
grep -E "(pytest|pytest-asyncio|types-python-jose)==" pyproject.toml
# Expected:
# pytest==8.0.0
# pytest-asyncio==0.23.8
# types-python-jose==3.3.4.20240106
```

## Technical Notes

### Version Compatibility Analysis

#### psutil
- `5.9.0` (current pin): Last release with `cp311` wheel, no `cp312` wheel
- `6.x`: Dropped Python 3.7 support, added `cp312` wheels
- `7.x`: abi3 wheel (works on all Python ≥3.7), current latest `7.2.2`
- **Recommendation**: `>=5.9.0,<8.0.0` — allows 7.x (abi3), blocks 8.x (unknown)

#### pyyaml
- `5.3.1` (current pin): No `cp312` wheel
- `6.x`: Added `cp312` wheels, current latest `6.0.3`
- **Recommendation**: `>=5.3.1,<7.0.0` — allows 6.x, blocks 7.x

#### watchdog
- `4.0.0` (current pin): Has `cp312` wheel
- `5.x` / `6.x`: Also have wheels, `6.0.0` is current latest
- **Recommendation**: `>=4.0.0,<7.0.0` — allows 4.x-6.x, blocks 7.x

### Why Not Exact Pins to Latest?

Exact pins to latest (`psutil==7.2.2`, `pyyaml==6.0.3`, `watchdog==6.0.0`) would work today but create maintenance burden — every patch release would require a PR. Flexible ranges with upper bounds capture "known compatible major version" intent while allowing automatic wheel-compatible updates.

### Impact on Other Dependencies

- **Runtime dependencies** (`dependencies` section): Only the three above changed. All others already have wheels or are pure Python.
- **Dev dependencies** (`dev` optional-dependencies): Already correct per #69 and #73 fixes. No changes needed.
- **Agent/CLI**: Use same `pyproject.toml`, benefit automatically.

## Related Issues

- **#73**: `types-python-jose` version typo — **ALREADY FIXED** (line 44 correct)
- **#69**: `pytest-asyncio` version conflict — **ALREADY FIXED** (line 35 correct)
- **#72**: `pytest-cov` subprocess leak — test infrastructure, separate fix
- **#70**: `seeding_disabled` fixture event loop — test fixture, separate fix
- **#57**: SQLite lock flakiness — intermittent, related to event loop handling

## Notes

- This fix unblocks **all development work** — it is the #1 blocker per AGENTS.md
- The version ranges are conservative: lower bound = current pin (known working), upper bound = next major (semver safety)
- After this fix, the documented install command in README/AGENTS.md will work as written
- CI should add a "clean install" job on Windows to prevent regression
- No production code changes — only `pyproject.toml` dependency metadata