# Spec: Fix pytest-cov Subprocess Environment Leak (Issue #72)

**Status: ✅ IMPLEMENTED — groomed, ready for SWE (work already complete)**

## Problem

Running `pytest --cov=backend` reports ~20% coverage instead of the expected ~89%. A subprocess test in `backend/tests/unit/test_config.py` leaks pytest-cov's `COV_CORE_*` environment variables into a spawned Python process, which defeats the coverage omit list and causes coverage measurement to track the wrong files.

## Root Cause

In `backend/tests/unit/test_config.py`, the test `test_import_has_no_filesystem_side_effects` (lines 146-175) spawns a subprocess to verify that importing the application writes nothing to the CWD:

```python
env = dict(os.environ)  # Copies ALL environment variables including COV_CORE_*
env["PYTHONPATH"] = str(REPO_ROOT)
env["SECRET_KEY"] = "test-key-not-persisted"
env.pop("DATABASE_URL", None)

result = subprocess.run(
    [sys.executable, "-c", "import backend.main; import backend.core.config"],
    cwd=str(tmp_path),
    env=env,  # Leaks COV_CORE_* to child process
    capture_output=True,
    text=True,
)
```

When pytest-cov runs, it sets `COV_CORE_SOURCE`, `COV_CORE_CONFIG`, `COV_CORE_DATA_FILE` and other `COV_CORE_*` environment variables. These are inherited by the subprocess, which then runs under coverage measurement with the parent's configuration. This causes:

1. The subprocess's coverage data to be written to the parent's coverage data file
2. The omit list (configured for the parent process) to not apply to the subprocess
3. Coverage to be measured for all files the subprocess touches, including stdlib and site-packages
4. The final coverage report to show artificially low percentages (~20% vs ~89%)

## Acceptance Criteria

| AC | Description |
|----|-------------|
| AC-1 | `pytest --cov=backend` reports ≥85% coverage (restoring the ~89% baseline) |
| AC-2 | `test_import_has_no_filesystem_side_effects` still passes and verifies no filesystem side effects |
| AC-3 | No `COV_CORE_*` environment variables are passed to the subprocess |
| AC-4 | The fix is minimal and doesn't change test behavior otherwise |
| AC-5 | Running the test in isolation (`pytest backend/tests/unit/test_config.py::test_import_has_no_filesystem_side_effects`) passes |

## Fix Approach

Filter out `COV_CORE_*` environment variables when constructing the subprocess environment. Two equivalent approaches:

**Option A (explicit unset):**
```python
env = dict(os.environ)
for key in list(env.keys()):
    if key.startswith("COV_CORE_"):
        del env[key]
```

**Option B (clean env with only needed vars):**
```python
env = {
    "PYTHONPATH": str(REPO_ROOT),
    "SECRET_KEY": "test-key-not-persisted",
    "PATH": os.environ.get("PATH", ""),
    "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),  # Windows needs this
    # Add other required vars explicitly
}
```

Option A is preferred as it's simpler and preserves any other environment variables the test might need.

## Files to Change

1. `backend/tests/unit/test_config.py` - Modify `test_import_has_no_filesystem_side_effects` to strip `COV_CORE_*` from the subprocess environment

## Verification

1. Run `pytest --cov=backend` — coverage should be ≥85%
2. Run `pytest backend/tests/unit/test_config.py::test_import_has_no_filesystem_side_effects` — test should pass
3. Run full backend test suite — all tests should pass
4. Verify `ruff check . && ruff format . && mypy .` passes

## Notes

- This is a test infrastructure bug, not a production code bug
- The subprocess test is valuable (verifies CWD independence) and must remain
- Similar patterns in other tests (e.g., `test_signing_key_hygiene.py` using `subprocess.run` for `git` commands) are not affected because they don't spawn Python processes that would inherit coverage config
- The fix should be applied before the `subprocess.run` call, not in a fixture, to keep the test self-contained

## Implementation Status

✅ **COMPLETE** — The fix has been applied in `backend/tests/unit/test_config.py:177-181`:
```python
env = dict(os.environ)
# Filter out pytest-cov's COV_CORE_* variables to prevent coverage
# measurement from leaking into the subprocess and corrupting the report.
for key in list(env.keys()):
    if key.startswith("COV_CORE_"):
        del env[key]
```

This is Option A from the spec (explicit unset). The test `test_import_has_no_filesystem_side_effects` now passes and `pytest --cov=backend` reports ~89% coverage (restored from ~20%). All acceptance criteria (AC-1 through AC-5) verified.