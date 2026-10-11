# Spec: Test Teardown Order — Dispose Engine Before Deleting Temp Database

## Issue
**#111** — Test teardown deletes the temp SQLite DB before the engine is disposed, leaking `shc-test-db-*` dirs and a post-summary "Event loop is closed"

**Labels**: `bug`, `phase-1`, `ready-for-grooming` → will become `groomed`

---

## 1. Verified Problem Statement

### 1.1 Symptoms
After a full test suite run (`pytest backend/tests`), the following are observed:
1. **Leaked temporary directories**: `shc-test-db-XXXXXX` directories remain in the system temp directory (typically `/tmp/` or `tempfile.gettempdir()`). These are created by `tempfile.mkdtemp(prefix="shc-test-db-")` in the `isolated_database` fixture but not cleaned up.
2. **"Event loop is closed" errors**: Post-summary `RuntimeError: Event loop is closed` messages appear, originating from aiosqlite worker threads that were still alive when the event loop was closed.

### 1.2 Reproduction
```bash
# Run the full backend test suite
pytest backend/tests -q

# Check for leaked temp directories
ls -la /tmp/shc-test-db-* 2>/dev/null || echo "none found"

# Observe stderr for "Event loop is closed" after the test summary
```

### 1.3 Expected Behaviour
- Zero `shc-test-db-*` directories remain after the test suite completes.
- Zero "Event loop is closed" errors in the test output.
- Clean exit (exit code 0) with no post-summary stderr noise.

---

## 2. Root Cause

### 2.1 Fixture Teardown Order Violation
In `backend/tests/conftest.py`, two session-scoped `autouse=True` fixtures manage the test database lifecycle:

| Fixture | Lines | Responsibility |
|---------|-------|----------------|
| `isolated_database` | 86–142 | Creates temp dir (`shc-test-db-*`), sets `settings.DATABASE_URL`, creates schema via sync engine, yields URL. **Teardown**: `shutil.rmtree(tmpdir, ignore_errors=True)` deletes the temp dir and database file. |
| `dispose_database_engine` | 258–281 | Depends on `event_loop`. **Teardown**: `event_loop.run_until_complete(close_database())` disposes the global async engine (`await _engine.dispose()`). |

**Critical defect**: `dispose_database_engine` does **not** depend on `isolated_database`. Pytest therefore makes no guarantee about their relative teardown order. In practice, `isolated_database` often tears down **first**, deleting the SQLite file while the async engine still holds open connections (and aiosqlite worker threads). When `dispose_database_engine` subsequently runs, `engine.dispose()` operates on connections whose underlying file has vanished, stranding the aiosqlite worker threads on a closed loop → "Event loop is closed". The `ignore_errors=True` in `rmtree` then fails to remove the directory because the SQLite file may still be locked by the dying threads.

### 2.2 Why This Was Not Caught Earlier
- The `event_loop` fixture (session-scoped) was introduced in #57 to prevent loop-close-under-thread failures *during* tests.
- The `dispose_database_engine` fixture correctly depends on `event_loop` so it tears down **before** the loop closes.
- However, `isolated_database` has **no dependency** on either, so it can tear down before `dispose_database_engine`.
- The leak is intermittent because pytest's fixture ordering for independent same-scope fixtures is implementation-dependent (often declaration order, but not guaranteed).

---

## 3. Scope Boundaries

| In Scope | Out of Scope |
|----------|--------------|
| Fix the teardown order between `isolated_database` and `dispose_database_engine` | Change the `event_loop` fixture scope or implementation |
| Ensure the async engine is fully disposed before the temp directory is deleted | Modify `close_database()` or engine creation logic |
| Verify no leaked temp dirs and no "Event loop is closed" errors | Fix any other test fixtures that might leak resources |
| Keep `isolated_database` synchronous (per #57 rationale) | Convert `isolated_database` to an async fixture |

---

## 4. Acceptance Criteria

All checkboxes must be verifiable with the commands shown.

### 4.1 Functional Acceptance

- [ ] **AC-1**: No `shc-test-db-*` directories remain after a clean test run
  ```bash
  pytest backend/tests -q
  ls /tmp/shc-test-db-* 2>/dev/null && echo "LEAKED" || echo "CLEAN"
  # Must print CLEAN
  ```

- [ ] **AC-2**: No "Event loop is closed" errors in stderr after test summary
  ```bash
  pytest backend/tests -q 2>&1 | tail -20
  # Must not contain "Event loop is closed" or "RuntimeError"
  ```

- [ ] **AC-3**: Test suite passes with same test counts as before
  ```bash
  pytest backend/tests -q
  # Must show same passed/failed/skipped counts as main branch
  ```

- [ ] **AC-4**: Multiple consecutive runs do not accumulate leaks
  ```bash
  for i in {1..5}; do pytest backend/tests -q; ls /tmp/shc-test-db-* 2>/dev/null | wc -l; done
  # Each iteration must show 0 leaked directories
  ```

### 4.2 Technical Acceptance

- [ ] **AC-5**: `dispose_database_engine` fixture explicitly depends on `isolated_database`
  ```python
  @pytest.fixture(scope="session", autouse=True)
  def dispose_database_engine(event_loop, isolated_database):  # <-- added dependency
      ...
  ```

- [ ] **AC-6**: Teardown order is guaranteed: engine disposed → temp dir deleted
  - Verified by adding temporary logging to both fixture teardowns and confirming order.

- [ ] **AC-7**: No changes to `event_loop` fixture or its dependencies.

### 4.3 Quality Acceptance

- [ ] **AC-8**: `ruff check . && ruff format . && mypy .` passes (from repo root).
- [ ] **AC-9**: `pytest --cov=backend` passes with no new warnings.

---

## 5. Test Scenarios

| Scenario | Command | Expected |
|----------|---------|----------|
| Single clean run | `pytest backend/tests -q` | Exit 0, no leaked dirs, no loop errors |
| 5 consecutive runs | `for i in {1..5}; do pytest backend/tests -q; done` | All 5 runs exit 0, 0 leaked dirs each |
| Only unit tests | `pytest backend/tests/unit -q` | Exit 0, no leaks |
| Only integration tests | `pytest backend/tests/integration -q` | Exit 0, no leaks |
| Stress: parallel collection | `pytest backend/tests --collect-only -q` | No leaks during collection phase |

---

## 6. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Adding dependency breaks other fixtures that depend on `dispose_database_engine` | Low | Medium | Only `event_loop` is a dependency; no other fixture depends on `dispose_database_engine`. Verify with `pytest --fixtures`. |
| `isolated_database` teardown runs before `dispose_database_engine` setup (circular) | None | N/A | Dependencies are acyclic: `dispose_database_engine` → `isolated_database` (new), `dispose_database_engine` → `event_loop` (existing). |
| Temp dir cleanup fails due to permissions on CI | Low | Low | `ignore_errors=True` remains; the fix ensures file is not locked, so cleanup succeeds. |
| Test session starts but crashes before `dispose_database_engine` teardown | Low | Medium | Unchanged from current behavior; OS cleans temp dirs on reboot. Not a new risk. |

---

## 7. Implementation Approach (for SWE Reference)

### 7.1 Minimal Fix
Modify `backend/tests/conftest.py`, `dispose_database_engine` fixture (line 258):

**Before:**
```python
@pytest.fixture(scope="session", autouse=True)
def dispose_database_engine(event_loop):
```

**After:**
```python
@pytest.fixture(scope="session", autouse=True)
def dispose_database_engine(event_loop, isolated_database):
```

That's it. The `isolated_database` parameter is not used in the fixture body; its presence solely establishes the dependency so pytest orders teardown correctly:
1. `dispose_database_engine` teardown runs first → `close_database()` disposes engine → aiosqlite threads stop
2. `isolated_database` teardown runs second → `shutil.rmtree(tmpdir)` succeeds because no locks remain
3. `event_loop` teardown runs last → loop closes cleanly

### 7.2 Verification (Optional, for Confidence)
Add temporary debug logging to both teardowns to confirm order:
```python
# In isolated_database teardown
logger.debug("isolated_database teardown: removing %s", tmpdir)

# In dispose_database_engine teardown
logger.debug("dispose_database_engine teardown: disposing engine")
```
Run once, confirm "disposing engine" appears before "removing", then remove logging.

### 7.3 Files to Modify
- `backend/tests/conftest.py` — single line change (add `isolated_database` parameter)

---

## 8. Spec Sign-off

- **PM**: ✅ Groomed — spec written, issue labelled `groomed`.
- **SWE**: (to implement per this spec)
- **QA**: (to verify against AC-1 through AC-9)
- **On-Call**: (if CI fails on merge)