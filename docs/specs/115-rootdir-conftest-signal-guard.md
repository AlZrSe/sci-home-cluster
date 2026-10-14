# Spec: Rootdir `conftest.py` Signal Guard

## Issue
**#115** — The signal safety guard from #114 lives only in `agent/tests/conftest.py`. There is no rootdir `conftest.py`, so the guard arms ~1s into the test run (after plugin loading and `agent/tests` collection). During the collection window and the entire `backend/tests` run (~50s), the process sits at `SIG_DFL` for SIGTERM/SIGINT. A stray signal there kills the interpreter with exit 143, no summary, no failing test — the exact untriageable failure mode #114 was filed to prevent.

## Verified Evidence (Reproduced)
| Command | Delay | Exit | Behaviour |
|---------|-------|------|-----------|
| `pytest backend/tests -q` | 0.4s | 143 | Guard not armed |
| `pytest backend/tests -q` | 5.0s | 143 | Guard never arms (backend doesn't load agent/tests/conftest) |
| `pytest agent/tests -q` | 0.4s | 143 | Guard not armed yet |
| `pytest agent/tests -q` | 2.0s | 1 | Guard fires, stray recorded, exit 1 |
| `pytest --collect-only -q` | 0.3s | 143 | Guard not armed during collection |

## Fix Approach

### 1. Add Rootdir `conftest.py`
Create `conftest.py` at the repository root. Its `pytest_configure` hook installs the **same** `SignalSafetyGuard` instance that `agent/tests/conftest.py` uses, so the guard is armed **before any testpath is collected**.

### 2. Share the Implementation
- Move `SignalSafetyGuard`, `GUARDED_SIGNALS`, `signal_name`, `_ExpectedSignal`, `SIGNAL_GUARD`, and the hook implementations (`pytest_configure`, `pytest_unconfigure`, `pytest_runtest_logreport`, `pytest_runtest_protocol`, `pytest_sessionfinish`) to a **shared module**: `shared/testing/signal_guard.py`.
- Both `conftest.py` (rootdir) and `agent/tests/conftest.py` import and drive the **same** `SIGNAL_GUARD` singleton.
- `agent/tests/conftest.py` keeps its per-test `signal_safety_guard` fixture (re-arms across the asyncio loop close boundary) but delegates to the shared guard.

### 3. Guard Design (Preserved from #114)
- **Two-layer**: pre-kill disposition assert (in `test_loop.py` delivery sites) + session-level recording handler.
- **Recording handler**: appends to `caught`, returns (never re-raises, never ignores).
- **Per-test attribution**: `pytest_runtest_logreport` (teardown) drains `take_recorded()` → fails the test with a readable message naming the signum.
- **Inter-test gap coverage**: `pytest_runtest_protocol` re-arms before and after each test (covers the asyncio loop close window).
- **Session finish correction**: `pytest_sessionfinish` prints evidence and forces `session.exitstatus = pytest.ExitCode.TESTS_FAILED` if any stray was recorded (so a stray with no test in flight still costs non-zero exit).
- **Signals handled**: `SIGTERM` and `SIGINT` (same `GUARDED_SIGNALS` tuple).

### 4. Non-Goals
- Do **not** modify `agent/loop.py` or any production code.
- Do **not** widen `GUARDED_SIGNALS` (SIGQUIT/SIGHUP remain excluded per #114).
- Do **not** change `backend/tests/conftest.py` fixtures (auth, database, settings) — the rootdir hook runs before them and is orthogonal.

## File Changes

### New File: `shared/testing/signal_guard.py`
Contains the complete guard implementation extracted from `agent/tests/conftest.py` (lines 36–231). Public exports:
- `GUARDED_SIGNALS`
- `signal_name`
- `SignalSafetyGuard`
- `SIGNAL_GUARD` (module-level singleton)
- `pytest_configure`
- `pytest_unconfigure`
- `pytest_runtest_logreport`
- `pytest_runtest_protocol`
- `pytest_sessionfinish`

### New File: `conftest.py` (rootdir)
```python
"""Rootdir conftest: arms the shared signal safety guard at session start."""
from shared.testing.signal_guard import pytest_configure, pytest_unconfigure, pytest_runtest_logreport, pytest_runtest_protocol, pytest_sessionfinish

# Re-export the hooks so pytest discovers them at rootdir level
__all__ = [
    "pytest_configure",
    "pytest_unconfigure",
    "pytest_runtest_logreport",
    "pytest_runtest_protocol",
    "pytest_sessionfinish",
]
```

### Modified: `agent/tests/conftest.py`
- Remove the duplicated guard implementation (lines 36–231).
- Import from `shared.testing.signal_guard`: `SIGNAL_GUARD`, `GUARDED_SIGNALS`, `signal_name`, `SignalSafetyGuard`, `_ExpectedSignal`.
- Keep the `signal_safety_guard` fixture (re-arms per test) and the `__all__` exports that tests depend on.
- The hooks (`pytest_configure`, etc.) are now provided by the rootdir conftest; **do not redefine them here** (pytest would see two definitions and the rootdir one wins anyway).

## Acceptance Criteria (Verifiable Commands)

- [ ] **AC-1**: `pytest --collect-only -q & sleep 0.3 && kill -TERM %1; wait %1` → exit 1, output contains "stray SIGTERM reached the pytest process during collection"
- [ ] **AC-2**: `pytest --collect-only -q & sleep 0.3 && kill -INT %1; wait %1` → exit 1, output contains "stray SIGINT reached the pytest process during collection"
- [ ] **AC-3**: `pytest backend/tests -q & sleep 0.3 && kill -TERM %1; wait %1` → exit 1, failing test names `SIGTERM`
- [ ] **AC-4**: `pytest backend/tests -q & sleep 0.3 && kill -INT %1; wait %1` → exit 1, failing test names `SIGINT`
- [ ] **AC-5**: `pytest agent/tests -q & sleep 0.3 && kill -TERM %1; wait %1` → exit 1, failing test names `SIGTERM`
- [ ] **AC-6**: `pytest agent/tests -q & sleep 0.3 && kill -INT %1; wait %1` → exit 1, failing test names `SIGINT`
- [ ] **AC-7**: `pytest backend/tests -q & sleep 5.0 && kill -TERM %1; wait %1` → exit 1 (guard armed for entire backend run)
- [ ] **AC-8**: `pytest` (full suite) & sleep 0.3 && kill -TERM %1 → exit 1 during collection/backend phase
- [ ] **AC-9**: Single shared `SignalSafetyGuard` implementation — no second divergent guard in rootdir conftest
- [ ] **AC-10**: `python -m pytest` (clean run) → 691 passed, 29 skipped, exit 0 — no behaviour change
- [ ] **AC-11**: 5 consecutive `pytest agent/tests` runs → identical counts, exit 0 each (flakiness gate from #114 AC-15)
- [ ] **AC-12**: `backend/tests/conftest.py` fixtures (auth token, database, settings) work unchanged — run `pytest backend/tests/integration/test_auth_flow.py -q` → passes
- [ ] **AC-13**: Lint/type gates pass: `ruff check . && ruff format . && mypy .` (from repo root)

## Test Scenarios

| Scenario | Command | Expected |
|----------|---------|----------|
| Collection window SIGTERM | `pytest --collect-only -q & sleep 0.3 && kill -TERM %1` | Exit 1, named SIGTERM in output |
| Collection window SIGINT | `pytest --collect-only -q & sleep 0.3 && kill -INT %1` | Exit 1, named SIGINT in output |
| Backend tests early SIGTERM | `pytest backend/tests -q & sleep 0.3 && kill -TERM %1` | Exit 1, named SIGTERM |
| Backend tests late SIGTERM | `pytest backend/tests -q & sleep 10.0 && kill -TERM %1` | Exit 1, named SIGTERM |
| Agent tests early SIGTERM | `pytest agent/tests -q & sleep 0.3 && kill -TERM %1` | Exit 1, named SIGTERM |
| Agent tests early SIGINT | `pytest agent/tests -q & sleep 0.3 && kill -INT %1` | Exit 1, named SIGINT |
| Full suite early SIGTERM | `pytest -q & sleep 0.3 && kill -TERM %1` | Exit 1 during collection/backend |
| Clean full suite | `pytest` | 691 passed, 29 skipped, exit 0 |
| Flakiness gate | `for i in {1..5}; do pytest agent/tests -q; done` | All 5 runs exit 0, identical counts |

## Implementation Notes

1. **Import order**: The rootdir `conftest.py` must import the hooks from `shared.testing.signal_guard` and re-export them. Pytest discovers hooks in `conftest.py` files from rootdir down to test directories; the rootdir one runs first.
2. **Singleton identity**: `SIGNAL_GUARD` in `shared.testing.signal_guard` is a module-level instance. Both conftest files import the **same** module, so they share the **same** object — no duplication.
3. **Hook precedence**: `pytest_runtest_logreport` with `tryfirst=True` in the shared module ensures the stray-signal failure is recorded before the terminal reporter prints the progress character.
4. **Idempotent install**: `SignalSafetyGuard.install()` records original dispositions on first call only, so multiple `install()` calls (rootdir `pytest_configure` + per-test fixture + `pytest_runtest_protocol` re-arms) are safe.
5. **Restore on teardown**: Rootdir `pytest_unconfigure` calls `SIGNAL_GUARD.restore()` to leave the process as found.

## Related
- #114 — Agent tests kill pytest (signal safety guard in `agent/tests/conftest.py`)
- #54 — ruff/mypy red on main (unrelated, but lint gate currently red)
- #26 — CI shape (cancelled runner sends SIGTERM to process group)
- #57 — Database isolation / event loop scope (context for backend conftest)