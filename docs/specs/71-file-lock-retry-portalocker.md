# Spec: Fix file_lock/shared_lock Retry Logic for portalocker LockException

**Status: ✅ IMPLEMENTED — groomed, ready for SWE (work already complete)**

## Issue
**#71** — `file_lock` and `shared_lock` functions in `shared/file_ops/locking.py` catch `OSError`/`IOError` to retry on lock contention, but `portalocker` raises `LockException` (which inherits from `Exception`, not `OSError`). The retry loop never triggers and the `timeout` parameter is effectively dead code when `portalocker` is available (which is the default).

## Root Cause
In `shared/file_ops/locking.py`:
- Lines 93 and 178: `except (OSError, IOError):` catches only OS-level errors
- `portalocker.lock()` raises `portalocker.exceptions.LockException` (and `AlreadyLocked`, `FileToLarge`) when a lock cannot be acquired
- `LockException.__mro__` = `(LockException, BaseLockException, Exception, BaseException, object)` — **not** an `OSError`
- Result: On lock contention, `LockException` propagates immediately, bypassing the retry loop and `timeout` logic entirely

## Affected Files
- `shared/file_ops/locking.py` — Primary fix location
- `backend/tests/unit/test_shared_file_ops.py` — Add test verifying retry behavior with portalocker

## Acceptance Criteria
1. **Retry works with portalocker**: When `portalocker.lock()` raises `LockException`/`AlreadyLocked`, the retry loop catches it, sleeps, and retries until `timeout` expires
2. **Timeout honored**: A contended lock raises `TimeoutError` after approximately `timeout` seconds (not immediately)
3. **Fallbacks unchanged**: Windows `msvcrt` and Unix `fcntl` paths still catch `OSError`/`IOError` and retry correctly
4. **Lock release on exception**: Existing `finally` block still releases locks properly when exceptions occur
5. **Tests pass**: All existing tests in `test_shared_file_ops.py` pass; new test for portalocker retry added

## Fix Approach
1. Import `portalocker.exceptions.BaseLockException` when `PORTALOCKER_AVAILABLE`
2. In both `file_lock()` and `shared_lock()`, expand the `except` clause to also catch `BaseLockException` (covers `LockException`, `AlreadyLocked`, `FileToLarge`)
3. Keep existing `OSError, IOError` for fallback paths
4. Add a test that:
   - Holds a lock in a background thread
   - Attempts to acquire the same lock with a short timeout (e.g., 1s)
   - Verifies it waits ~1s before raising `TimeoutError` (not immediate `LockException`)

## Verification Steps
1. Run existing tests: `pytest backend/tests/unit/test_shared_file_ops.py -v`
2. New test should pass on all platforms (Windows, Linux, macOS)
3. Manual verification: Run a script that holds a lock and times how long a second acquisition attempt blocks before `TimeoutError`

## Notes
- `portalocker` is a runtime dependency (listed in `docs/api-server-core-spec.md`)
- The fix must not change behavior when `portalocker` is unavailable (fallback paths)
- `IOError` is an alias for `OSError` in Python 3, but kept for explicitness
- The `test_lock_timeout` test is currently skipped on Windows — the fix should make it reliable everywhere

## Implementation Status

✅ **COMPLETE** — The fix has been applied:
- `shared/file_ops/locking.py:17` — `BaseLockException` imported from `portalocker.exceptions`
- `shared/file_ops/locking.py:95` — `except (OSError, IOError, BaseLockException):` in `file_lock()`
- `shared/file_ops/locking.py:180` — `except (OSError, IOError, BaseLockException):` in `shared_lock()`
- `backend/tests/unit/test_shared_file_ops.py` — Test for portalocker retry behavior added (verifies timeout is honored)

All acceptance criteria (1-5) verified by test suite. The `test_lock_timeout` test is now reliable on all platforms.