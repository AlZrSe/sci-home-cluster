# Specification: Fix Agent Tests on Windows (Issue #127)

## Overview

**Issue**: Fourteen agent tests fail on Windows due to POSIX-only filesystem idioms:
1. `test_claim_marker_cleanup_on_failure` uses `chmod(0o555)` to make a directory read-only — this doesn't work on Windows
2. `BoundedLineWriter` leaks file handles on Windows because it only closes on explicit `close()` call or context manager exit

**Goal**: Make all agent tests pass on Windows while preserving POSIX behavior.

---

## 1. User Stories

### US-1: Cross-Platform Test Reliability
**As a** developer running tests on Windows  
**I want** all agent tests to pass without POSIX-specific failures  
**So that** I can develop and verify changes on any platform

**Acceptance Criteria:**
- AC-1.1: `test_claim_marker_cleanup_on_failure` passes on Windows and POSIX
- AC-1.2: All `BoundedLineWriter` tests pass on Windows and POSIX
- AC-1.3: No test behavior changes on POSIX platforms
- AC-1.4: File handle leak in `BoundedLineWriter` is fixed on Windows

### US-2: Robust Claim Failure Cleanup
**As a** node operator  
**I want** the claim protocol to clean up marker files reliably on all platforms  
**So that** failed claims don't leave orphaned markers blocking future claims

**Acceptance Criteria:**
- AC-2.1: Marker cleanup works when state.yaml write fails (any platform)
- AC-2.2: Marker cleanup works when marker creation fails (any platform)
- AC-2.3: Test simulates write failure without relying on `chmod`

### US-3: Proper Resource Management in BoundedLineWriter
**As a** developer using `BoundedLineWriter`  
**I want** file handles to be closed automatically when the writer is garbage collected  
**So that** temporary log files can be cleaned up on Windows

**Acceptance Criteria:**
- AC-3.1: `BoundedLineWriter` implements `__del__` to close file handle
- AC-3.2: All existing tests pass without modification
- AC-3.3: Context manager and explicit `close()` still work correctly
- AC-3.4: No file handle leaks on Windows or POSIX

---

## 2. Acceptance Criteria (from Issue)

### Claim Protocol Tests
| Test | Current Failure | Required Fix |
|------|----------------|--------------|
| `test_claim_marker_cleanup_on_failure` | `chmod(0o555)` no-op on Windows; directory remains writable | Replace with cross-platform write-failure simulation (e.g., mock `write_yaml` or use a read-only file attribute on Windows) |

### BoundedLineWriter Tests
| Test | Current Failure | Required Fix |
|------|----------------|--------------|
| All tests except `test_context_manager` | `PermissionError: [WinError 32]` on temp dir cleanup because file handle not closed | Implement `__del__` method to close file handle; ensure `__exit__` and `close()` set `_file = None` to prevent double-close |

---

## 3. Test Scenarios

### 3.1 Claim Protocol — Cross-Platform Write Failure Simulation

| Scenario | Test ID | Given | When | Then |
|----------|---------|-------|------|------|
| Marker creation fails due to permission error | TC-01 | Job directory exists, marker creation fails (simulated) | `try_claim_job` called | Returns `(False, "failed to create marker: ...")`; no marker file left behind |
| State write fails after marker created | TC-02 | Job directory exists, marker created successfully, state write fails (simulated) | `try_claim_job` called | Returns `(False, "failed to write RUNNING state: ...")`; marker file cleaned up |
| Read-only directory on POSIX | TC-03 | Job directory `chmod 0o555` (POSIX only) | `try_claim_job` called | Returns appropriate error; cleanup attempted |
| Read-only attribute on Windows | TC-04 | Job directory has `FILE_ATTRIBUTE_READONLY` (Windows only) | `try_claim_job` called | Returns appropriate error; cleanup attempted |

### 3.2 BoundedLineWriter — File Handle Management

| Scenario | Test ID | Given | When | Then |
|----------|---------|-------|------|------|
| Basic write and implicit cleanup | TL-01 | `BoundedLineWriter` created, line written | Writer goes out of scope (no explicit close) | File handle closed; temp dir cleanup succeeds |
| Multiple writes and implicit cleanup | TL-02 | `BoundedLineWriter` created, multiple lines written | Writer goes out of scope | File handle closed; all lines persisted; temp dir cleanup succeeds |
| Explicit close then write | TL-03 | Writer created, line written, `close()` called, another line written | Second write | New file handle opened; both lines in file; temp dir cleanup succeeds |
| Context manager | TL-04 | `with BoundedLineWriter(...) as writer:` | Write inside block | File closed on exit; temp dir cleanup succeeds (already passes) |
| Exception during write | TL-05 | Writer created, exception raised during `write_line` | Exception propagates | File handle closed via `__del__`; temp dir cleanup succeeds |
| Flush then implicit cleanup | TL-06 | Writer created, line written, `await flush()` called | Writer goes out of scope | File handle closed; data flushed; temp dir cleanup succeeds |

### 3.3 Regression Tests (must continue to pass on POSIX)

| Scenario | Test ID | Description |
|----------|---------|-------------|
| POSIX read-only directory | TR-01 | `chmod 0o555` still triggers write failure on Linux/macOS |
| Concurrent marker creation | TR-02 | `O_EXCL` race condition handling unchanged |
| Line truncation | TR-03 | `MAX_LINE_LENGTH` enforcement unchanged |
| Undecodable bytes | TR-04 | `errors="replace"` policy unchanged |
| Unicode content | TR-05 | UTF-8 encoding with emoji/non-ASCII unchanged |

---

## 4. Technical Approach

### 4.1 Fix: Cross-Platform Write Failure Simulation in `test_claim_marker_cleanup_on_failure`

**Current Problem**: The test uses `job_dir.chmod(0o555)` which is a no-op on Windows. The directory remains writable, so the test's assertion fails because the claim succeeds instead of failing.

**Solution**: Replace the `chmod`-based approach with a cross-platform failure injection using `unittest.mock.patch` to mock `write_yaml` (for state write failure) or `os.open` (for marker creation failure).

**Implementation Plan**:

```python
# In test_claim.py, replace test_claim_marker_cleanup_on_failure:

async def test_claim_marker_cleanup_on_failure(
    self, temp_paths, node_spec, job_state
):
    setup_job_dir(temp_paths, "job-1", job_state)
    state_file = temp_paths.jobs_dir / "job-1" / "state.yaml"
    marker_file = temp_paths.jobs_dir / "job-1" / ".claim.node-01"

    # Test 1: Marker creation fails
    with patch("agent.claim.os.open", side_effect=PermissionError("Simulated marker failure")):
        success, reason = await try_claim_job(temp_paths, "job-1", "node-01", node_spec)
        assert success is False
        assert "failed to create marker" in reason
        assert not marker_file.exists()  # Marker should not exist

    # Test 2: State write fails after marker created
    with patch("agent.claim.write_yaml", side_effect=PermissionError("Simulated state write failure")):
        success, reason = await try_claim_job(temp_paths, "job-1", "node-01", node_spec)
        assert success is False
        assert "failed to write RUNNING state" in reason
        assert not marker_file.exists()  # Marker should be cleaned up
```

**Benefits**:
- Works identically on Windows and POSIX
- Tests the actual error paths in the code
- No filesystem permission manipulation needed
- More reliable and faster

### 4.2 Fix: BoundedLineWriter File Handle Leak

**Current Problem**: `BoundedLineWriter` opens a file handle in `_ensure_open()` but only closes it in `close()` or `__exit__`. If the user forgets to call `close()` or use a context manager, the handle leaks. On Windows, this prevents file deletion.

**Solution**: Add a `__del__` method that calls `close()`, and ensure `close()` is idempotent (safe to call multiple times).

**Implementation Plan**:

```python
# In agent/log_writer.py, modify BoundedLineWriter:

class BoundedLineWriter:
    MAX_LINE_LENGTH: int = 10240

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file: Optional[IO[str]] = None

    def _ensure_open(self) -> None:
        if self._file is None:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._file_path.open("a", encoding="utf-8")

    def write_line(self, line: bytes) -> None:
        self._ensure_open()
        # ... existing logic ...

    async def flush(self) -> None:
        if self._file is not None:
            self._file.flush()
            await asyncio.sleep(0)

    def close(self) -> None:
        """Close the underlying file. Idempotent."""
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "BoundedLineWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        """Ensure file handle is closed on garbage collection."""
        try:
            self.close()
        except Exception:
            # Ignore errors during finalization
            pass
```

**Key Points**:
- `__del__` calls `close()` which is idempotent (checks `if self._file is not None`)
- `close()` sets `self._file = None` after closing, preventing double-close
- Wrapped in `try/except` to avoid exceptions during interpreter shutdown
- Context manager and explicit `close()` continue to work as before

### 4.3 Alternative Considered: `atexit` Registration

**Rejected**: Registering with `atexit` adds complexity and doesn't handle early garbage collection. `__del__` is simpler and sufficient for this use case.

### 4.4 Alternative Considered: `__del__` Only, No `close()` Idempotency

**Rejected**: If `close()` is called explicitly AND `__del__` runs, double-close would occur. Making `close()` idempotent prevents this.

---

## 5. Additional Considerations

### 5.1 Windows File Locking Semantics

On Windows, an open file handle prevents deletion (WinError 32). On POSIX, an open file can be deleted (unlink succeeds, data persists until last handle closes). The `__del__` fix ensures handles are closed promptly on both platforms.

### 5.2 Interpreter Shutdown Order

During Python shutdown, modules may be cleared before objects are finalized. The `try/except` in `__del__` handles `AttributeError` (if `_file` already cleared) and `ReferenceError` (if `Path` methods unavailable).

### 5.3 Thread Safety

`BoundedLineWriter` is not thread-safe (single `_file` handle). This is unchanged — the class is designed for single-threaded use in the agent's log streaming pipeline.

### 5.4 Test Coverage

Add a new test to verify implicit cleanup:

```python
def test_implicit_cleanup_on_gc(self):
    """Test that file handle is closed when writer is garbage collected."""
    import gc
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "test.log"
        writer = BoundedLineWriter(file_path)
        writer.write_line(b"hello")
        del writer
        gc.collect()  # Force collection

        # Should be able to read/delete the file
        content = file_path.read_text(encoding="utf-8")
        assert content == "hello\n"
```

### 5.5 Backward Compatibility

- **Claim protocol**: No behavioral change — only test implementation changes
- **BoundedLineWriter**: No API change — only adds safety net for forgotten `close()`
- Existing code using context managers or explicit `close()` unaffected

### 5.6 CI/CD Impact

- Windows CI runners will now pass all agent tests
- No new dependencies required
- Test runtime unchanged (mock-based test may be slightly faster)

---

## 6. Definition of Done

- [ ] `test_claim_marker_cleanup_on_failure` refactored to use mock-based failure injection
- [ ] `BoundedLineWriter` implements `__del__` with idempotent `close()`
- [ ] All 14 previously failing tests pass on Windows
- [ ] All agent tests continue to pass on POSIX (Linux/macOS)
- [ ] New test `test_implicit_cleanup_on_gc` added for `BoundedLineWriter`
- [ ] No lint/type errors (`ruff check . && ruff format . && mypy .`)
- [ ] PM acceptance review complete

---

## 7. Implementation Priority

1. **Phase 1**: Fix `BoundedLineWriter` (high impact — 12 tests)
   - Add `__del__` method
   - Make `close()` idempotent
   - Add `test_implicit_cleanup_on_gc`

2. **Phase 2**: Fix `test_claim_marker_cleanup_on_failure` (1 test)
   - Refactor to use `unittest.mock.patch`
   - Split into two sub-tests (marker failure, state write failure)

3. **Phase 3**: Verify cross-platform
   - Run full agent test suite on Windows
   - Verify no regressions on POSIX (via CI)

---

## 8. Files to Modify

| File | Changes |
|------|---------|
| `agent/log_writer.py` | Add `__del__`, make `close()` idempotent |
| `agent/tests/test_log_writer.py` | Add `test_implicit_cleanup_on_gc` |
| `agent/tests/test_claim.py` | Replace `test_claim_marker_cleanup_on_failure` with mock-based version |

---

*Specification created for Issue #127 — ready for SWE implementation.*