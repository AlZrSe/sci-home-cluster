# Spec: Fix `seeding_disabled` Fixture Event Loop Issue

**Status: ✅ IMPLEMENTED — groomed, ready for SWE (work already complete)**

## Problem

The `seeding_disabled` fixture in `backend/tests/conftest.py` uses `asyncio.run()` to execute async setup/teardown code. This creates a new event loop, runs the coroutine, and **closes the loop** on exit. Subsequent async tests (using `pytest-asyncio`) then fail with:

```
RuntimeError: There is no current event loop in thread 'MainThread'.
```

**12 tests fail** across two integration test modules:
- `backend/tests/integration/test_production_empty.py` — 5 tests
- `backend/tests/integration/test_no_fabrication.py` — 7 tests (6 fail, 1 doesn't use the fixture)

## Root Cause

Lines 277-281 in `backend/tests/conftest.py`:

```python
import asyncio

asyncio.run(_clear())  # Setup: creates & closes a loop
yield store
asyncio.run(_clear())  # Teardown: creates & closes another loop
```

`asyncio.run()` is designed for **top-level entry points**, not for use inside a test fixture that runs within an existing event loop managed by `pytest-asyncio`. Each call destroys the loop, leaving the thread without a current event loop.

## Acceptance Criteria

| AC | Description |
|----|-------------|
| AC-1 | `seeding_disabled` fixture no longer calls `asyncio.run()` |
| AC-2 | Fixture uses the **existing event loop** provided by `pytest-asyncio` |
| AC-3 | All 12 previously failing tests pass |
| AC-4 | Fixture semantics unchanged: `SEED_DEMO_DATA=False`, tables cleared, `_seeded=False` on entry and exit |
| AC-5 | No production code changes — test-only fix |
| AC-6 | Works on Windows (ProactorEventLoop) and Unix |

## Fix Approach

Convert `seeding_disabled` from a **synchronous generator fixture** to an **async generator fixture** (`@pytest_asyncio.fixture` or `@pytest.fixture` with `async def`). This allows `await _clear()` directly on the test's event loop.

### Implementation

```python
@pytest_asyncio.fixture  # or @pytest.fixture with async def
async def seeding_disabled(monkeypatch) -> AsyncGenerator[DatabaseStore, None]:
    """
    Put the application store into production mode for a single test.
    ...
    """
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", False)
    store = get_store()

    async def _clear() -> None:
        await store.reset()
        store._seeded = False

    await _clear()          # Setup: runs on test's event loop
    try:
        yield store
    finally:
        await _clear()      # Teardown: runs on test's event loop
```

**Key changes:**
1. Change `def` → `async def`
2. Change `Generator[...]` → `AsyncGenerator[...]`
3. Replace `asyncio.run(_clear())` with `await _clear()`
4. Wrap teardown in `try/finally` to guarantee cleanup
5. Import `AsyncGenerator` from `typing`

### Dependencies

- `pytest-asyncio` already in `pyproject.toml` (used by all async tests)
- No new dependencies needed

## Files to Change

| File | Change |
|------|--------|
| `backend/tests/conftest.py` | Lines 250-281: convert `seeding_disabled` to async fixture |

## Verification

Run the previously failing test modules:

```bash
python -m pytest backend/tests/integration/test_production_empty.py -v
python -m pytest backend/tests/integration/test_no_fabrication.py -v
```

**Expected:** All 12 tests pass.

**Regression check:** Run full backend test suite to ensure no other fixtures/tests break:

```bash
python -m pytest backend/tests/ -v --tb=short
```

## Notes

- The `seeded_cluster` fixture (line 285) depends on `seeding_disabled` and is already `async def` — it will work seamlessly with the async version.
- The `reset_singleton_store` fixture (line 196) has a similar pattern but handles the "no running loop" case correctly by detecting `asyncio.get_running_loop()` vs `asyncio.run()`. The async fixture approach is cleaner here because `pytest-asyncio` guarantees a running loop for async tests.
- This fix aligns with the pattern used by `clean_database`, `mock_store`, `client`, and other async fixtures in the same file.

## Related Issues

- Issue #70: This bug report
- Issue #57: Intermittent database engine binding to wrong event loop (related symptom)

## Implementation Status

✅ **COMPLETE** — The fix has been applied in `backend/tests/conftest.py:339-371`:
```python
@pytest_asyncio.fixture
async def seeding_disabled(monkeypatch) -> AsyncGenerator[DatabaseStore, None]:
    """
    Put the application store into production mode for a single test.
    ...
    """
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", False)
    store = get_store()

    async def _clear() -> None:
        await store.reset()
        store._seeded = False

    await _clear()  # Setup: runs on test's event loop
    try:
        yield store
    finally:
        await _clear()  # Teardown: runs on test's event loop
```

Key changes applied:
- Changed from `def` to `async def`
- Changed return type from `Generator` to `AsyncGenerator`
- Replaced `asyncio.run(_clear())` with `await _clear()`
- Wrapped teardown in `try/finally`
- Added `AsyncGenerator` import from `typing`

All 12 previously failing tests in `test_production_empty.py` and `test_no_fabrication.py` now pass. Full backend test suite passes. Works on Windows and Unix.