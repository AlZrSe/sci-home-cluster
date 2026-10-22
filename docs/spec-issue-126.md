# Specification: Windows-Compatible Atomic YAML Writes (Issue #126)

## 1. Problem Summary

`write_yaml` in `shared/file_ops/yaml_utils.py` uses `os.replace` for atomic writes. On Windows, `os.replace` fails with `PermissionError: [WinError 5]` when the destination file is open by any handle not opened with `FILE_SHARE_DELETE`. This affects:

- **Node registration** (`agent/hardware.py:write_node_spec`) — called every 30s heartbeat
- **Job state** (`agent/claim.py`, `backend/services/job_folder.py`)
- **Terminal state** (`agent/terminal.py`)
- **JWT signing key** (`backend/core/config.py:_write_state_secret`)
- **Any other atomic writer** using the same pattern

The failure is swallowed by `write_yaml` → caller treats as lost heartbeat → node marked `OFFLINE`. The atomicity invariant is also broken: reader sees neither old nor new file.

## 2. User Stories

### US-1: Node Heartbeat Survives Concurrent Readers on Windows
**As a** cluster operator running the agent on Windows  
**I want** the node's 30-second heartbeat write to succeed even when the backend watcher or another process has the node file open  
**So that** the node stays `ONLINE` and the dashboard accurately reflects cluster health

### US-2: Atomicity Guarantee Preserved on All Platforms
**As a** developer relying on `write_yaml` for critical state files  
**I want** readers to never observe a truncated or partial YAML document  
**So that** the backend watcher never drops a node/job/terminal state due to a malformed file

### US-3: Failed Writes Are Visible and Actionable
**As a** operator debugging a stuck `OFFLINE` node  
**I want** the logs to show the exact path, error code, and number of retry attempts when a write ultimately fails  
**So that** I can distinguish a transient conflict from a genuine filesystem problem

### US-4: Cross-Platform Test Coverage
**As a** contributor running CI on Windows  
**I want** the atomicity test (`test_a_reader_never_sees_a_truncated_node_file`) to pass on Windows without being skipped  
**So that** the atomicity guarantee is verified on every platform

## 3. Acceptance Criteria

| ID | Criterion | Verification |
|----|-----------|--------------|
| AC-1 | `write_yaml` succeeds on Windows when destination is open by a concurrent reader | `test_a_reader_never_sees_a_truncated_node_file` passes on Windows |
| AC-2 | A reader never observes a truncated `nodes/<node_id>.yaml` on any platform | Same test validates: `unexpected == []` (no partial documents seen) |
| AC-3 | Failed atomic replace is retried with bounded backoff (configurable max attempts) | Log shows retry count; test injects persistent `PermissionError` and verifies max attempts |
| AC-4 | Ultimate failure after retries is logged with path, errno, attempt count | Log assertion in integration test |
| AC-5 | Caller (`_registration_tick`, job folder, terminal) receives the exception after retries exhausted | Unit test mocks `write_yaml` to raise after retries; verifies propagation |
| AC-6 | Equivalent writers (`_write_state_secret` in `config.py`) use the same fix or are documented as follow-up | Code review checklist item; separate issue filed if not fixed |
| AC-7 | No regression on POSIX: atomicity and performance unchanged | Existing test suite passes on Linux/macOS; benchmark shows <5% overhead |

## 4. Test Scenarios

### 4.1 Unit Tests (Shared File Ops)

| Test Case | Description | Platform |
|-----------|-------------|----------|
| `test_write_yaml_retries_on_permission_error` | Mock `os.replace` to raise `PermissionError` first 2 times, succeed on 3rd; verify 3 attempts | All |
| `test_write_yaml_exhausts_retries` | Mock `os.replace` to always raise `PermissionError`; verify exception raised after max attempts | All |
| `test_write_yaml_logs_attempts` | Capture log output; verify each attempt logged with attempt number | All |
| `test_write_yaml_no_retry_on_other_errors` | Mock `os.replace` to raise `OSError(errno.ENOSPC)`; verify immediate failure, no retry | All |
| `test_write_yaml_atomicity_posix` | Existing atomicity test (already covers POSIX) | POSIX |
| `test_write_yaml_atomicity_windows` | Same atomicity property, but with Windows-style handle contention simulation | Windows |

### 4.2 Integration Tests (Agent Hardware)

| Test Case | Description | Platform |
|-----------|-------------|----------|
| `test_node_registration_survives_concurrent_reader_windows` | Spawn reader thread holding file open; writer (heartbeat) succeeds | Windows |
| `test_node_registration_atomicity_windows` | `test_a_reader_never_sees_a_truncated_node_file` — must pass | Windows |
| `test_registration_tick_logs_write_failure` | Mock `write_node_spec` to fail after retries; verify log contains path, errno, attempts | All |

### 4.3 Integration Tests (Backend Config)

| Test Case | Description | Platform |
|-----------|-------------|----------|
| `test_write_state_secret_retries_on_windows` | `_write_state_secret` uses same retry logic; verify JWT key write survives contention | Windows |
| `test_write_state_secret_logs_failure` | Ultimate failure logged with details | All |

### 4.4 E2E / Compose Tests (Opt-in)

| Test Case | Description |
|-----------|-------------|
| `test_agent_heartbeat_on_windows_container` | Run agent in Windows container; verify node stays ONLINE for 5 minutes |

## 5. Technical Approach

### 5.1 Core Strategy: Bounded Retry with Exponential Backoff

**Rationale**: 
- Retry is portable (works on POSIX and Windows)
- Heartbeat interval is 30s; a few hundred ms of retry is invisible
- `ReplaceFileW` via `ctypes` adds platform-specific complexity and only helps if reader holds file open indefinitely (rare; watchdog/editor/tail close quickly)
- Delete-then-write loses atomicity and must not be default

### 5.2 Implementation: `shared/file_ops/yaml_utils.py`

Add a private retry helper and modify `write_yaml`:

```python
# Constants (module-level, overridable for tests)
_MAX_REPLACE_ATTEMPTS: Final[int] = 5
_BASE_RETRY_DELAY_S: Final[float] = 0.05  # 50ms
_MAX_RETRY_DELAY_S: Final[float] = 0.5    # 500ms cap

def _atomic_replace_with_retry(src: Path, dst: Path) -> None:
    """
    Atomically replace dst with src, retrying on Windows PermissionError.
    
    On Windows, os.replace fails with WinError 5 if dst is open without
    FILE_SHARE_DELETE. We retry with exponential backoff because readers
    (watchdog, editors, tail) typically close the handle within milliseconds.
    
    Raises:
        OSError: If all retries exhausted, the last PermissionError is raised.
        Other OSErrors are raised immediately (no retry).
    """
    attempt = 0
    last_error: Optional[OSError] = None
    
    while attempt < _MAX_REPLACE_ATTEMPTS:
        try:
            os.replace(src, dst)
            if attempt > 0:
                logger.info(
                    "Atomic replace succeeded on attempt %d/%d: %s -> %s",
                    attempt + 1, _MAX_REPLACE_ATTEMPTS, src, dst
                )
            return
        except PermissionError as e:
            # Windows: ERROR_ACCESS_DENIED (5) when destination is open
            # POSIX: EACCES (13) - unlikely for replace but handle uniformly
            last_error = e
            attempt += 1
            if attempt < _MAX_REPLACE_ATTEMPTS:
                delay = min(_BASE_RETRY_DELAY_S * (2 ** (attempt - 1)), _MAX_RETRY_DELAY_S)
                logger.warning(
                    "Atomic replace failed (attempt %d/%d): %s -> %s: %s. Retrying in %.3fs...",
                    attempt, _MAX_REPLACE_ATTEMPTS, src, dst, e, delay
                )
                time.sleep(delay)
            continue
        except OSError as e:
            # Any other OSError (ENOSPC, EROFS, etc.) - fail immediately
            logger.error(
                "Atomic replace failed with non-retryable error: %s -> %s: %s",
                src, dst, e
            )
            raise
    
    # All retries exhausted
    logger.error(
        "Atomic replace failed after %d attempts: %s -> %s: %s",
        _MAX_REPLACE_ATTEMPTS, src, dst, last_error
    )
    raise last_error  # type: ignore[raise]
```

Modify `write_yaml` to use `_atomic_replace_with_retry` instead of bare `os.replace`.

### 5.3 Propagate to Other Writers

**Option A (Preferred)**: Extract atomic write to shared utility `shared/file_ops/atomic_write.py`:
```python
# shared/file_ops/atomic_write.py
def atomic_write(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    """Write content to path atomically with Windows-compatible retry."""
    # ... temp file + _atomic_replace_with_retry ...
```

Then update:
- `yaml_utils.write_yaml` → uses `atomic_write`
- `config._write_state_secret` → uses `atomic_write`
- Any future atomic writers

**Option B**: Duplicate retry logic in `_write_state_secret` (less DRY, faster to implement).

**Decision**: Option A — single source of truth for atomic write semantics. File as follow-up if time-constrained.

### 5.4 Configuration

Make retry parameters configurable via environment (for tuning in containers):
- `SHC_ATOMIC_WRITE_MAX_ATTEMPTS` (default: 5)
- `SHC_ATOMIC_WRITE_BASE_DELAY_MS` (default: 50)
- `SHC_ATOMIC_WRITE_MAX_DELAY_MS` (default: 500)

Read once at module import in `atomic_write.py`.

### 5.5 Logging Format

All logs use structured format with these fields:
- `path`: destination file path
- `attempt`: 1-based attempt number
- `max_attempts`: configured maximum
- `errno`: `e.errno` / `e.winerror`
- `delay_ms`: backoff delay (on retry)

Example:
```
WARNING shared.file_ops.atomic_write Atomic replace failed (attempt 2/5): /data/nodes/node-01.yaml.tmp -> /data/nodes/node-01.yaml: [WinError 5] Access is denied. Retrying in 100.000ms...
```

## 6. Edge Cases and Risks

### 6.1 Edge Cases

| Edge Case | Handling |
|-----------|----------|
| Reader holds file open indefinitely (e.g., `tail -f`, editor) | Retries exhaust; exception propagates; caller logs and retries at heartbeat interval |
| Temp file cleanup on retry failure | `NamedTemporaryFile(delete=False)` + manual `unlink` in `except` block (already exists) |
| `os.replace` succeeds but file is corrupted | Not in scope; YAML validation happens on read |
| Concurrent writers (two processes writing same file) | Last writer wins; `os.replace` is atomic per-call. Not a new problem. |
| Network filesystem (SMB, NFS) where `os.replace` not atomic | Documented limitation; `SHC_ATOMIC_WRITE_MAX_ATTEMPTS=1` disables retry |
| Process killed between temp write and replace | Temp file leaked (cleaned on next run or manually). Acceptable. |
| `chmod` on temp file before replace (config.py) | Preserved: `atomic_write` accepts `mode` parameter, applies before replace |

### 6.2 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Retry masks genuine permission problem (ACL, readonly FS) | Low | Medium | Non-`PermissionError` `OSError` fails immediately; log shows errno |
| Retry storm under heavy contention | Low | Low | Max 5 attempts, 500ms cap = max 1.5s per write; heartbeat is 30s |
| Temp file name collision | Negligible | Low | `NamedTemporaryFile` uses random suffix |
| POSIX performance regression | Low | Low | No retry on POSIX unless `PermissionError` (rare); benchmark in CI |
| Forgotten writer not migrated to `atomic_write` | Medium | Medium | Code review checklist; grep for `os.replace` in codebase |

### 6.3 Open Questions (from Issue)

1. **Is retry acceptable, or does the cluster need `ReplaceFileW`?**  
   → Retry is the default. `ReplaceFileW` is a follow-up if indefinite-hold readers are observed in production.

2. **Does `write_yaml` already have a call-site contract about who may retry?**  
   → `agent/hardware.py` retries at tick level (30s). `backend/store/database_store.py` does not. **Retry belongs in the helper** so all callers benefit uniformly.

## 7. Implementation Plan

### Phase 1: Core Fix (Shared File Ops)
1. Create `shared/file_ops/atomic_write.py` with `atomic_write()` and retry logic
2. Refactor `yaml_utils.write_yaml` to use `atomic_write`
3. Add unit tests for retry behavior (mock `os.replace`)
4. Run existing test suite on POSIX — no regressions

### Phase 2: Windows Validation
5. Run `test_a_reader_never_sees_a_truncated_node_file` on Windows (CI or local)
6. Verify it passes with new retry logic
7. Add Windows-specific test variant if needed

### Phase 3: Migrate Other Writers
8. Refactor `config._write_state_secret` to use `atomic_write`
9. Add test for JWT key write under contention
10. Audit codebase for other `os.replace` atomic writers

### Phase 4: Observability
11. Verify log output matches acceptance criteria (path, errno, attempts)
12. Update any documentation referencing atomic write semantics

## 8. Definition of Done

- [ ] All acceptance criteria (AC-1 through AC-7) verified
- [ ] Unit tests pass on Linux, macOS, Windows (CI matrix)
- [ ] Integration test `test_a_reader_never_sees_a_truncated_node_file` passes on Windows
- [ ] No lint/type errors (`ruff check . && ruff format --check . && mypy .`)
- [ ] Backward compatible: existing callers unchanged
- [ ] Documentation updated if public API changes
- [ ] PM accepts the implementation

---

## Appendix: Files to Modify

| File | Change |
|------|--------|
| `shared/file_ops/atomic_write.py` | **New file** — core retry logic |
| `shared/file_ops/yaml_utils.py` | Refactor `write_yaml` to use `atomic_write` |
| `shared/file_ops/__init__.py` | Export `atomic_write` if public |
| `backend/core/config.py` | Refactor `_write_state_secret` to use `atomic_write` |
| `shared/file_ops/tests/test_atomic_write.py` | **New file** — unit tests for retry logic |
| `agent/tests/test_hardware.py` | Ensure Windows test passes (no code change needed if fix works) |
| `backend/tests/unit/test_config.py` | Add test for `_write_state_secret` retry behavior |

## Appendix: Related Issues

- **#95**: Node registration file (introduced `write_node_spec`)
- **#67**: JWT signing key persistence (uses `_write_state_secret`)
- **#125**: Windows job execution (tracks this as dependency)
- **#56**: Published key rotation (context for `_write_state_secret`)