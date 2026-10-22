# Specification: Fix Agent Tests on Windows (Issue #124)

## 1. User Stories

### US-1: Windows Developer Can Run Agent Tests
**As a** developer working on Windows  
**I want** `pytest agent/tests` to complete and print a summary  
**So that** I can verify agent behavior locally without the pytest process dying silently (exit code 15)

### US-2: Clear Test Report on Windows
**As a** developer running tests on Windows  
**I want** signal-delivery tests to be visibly skipped with a stated reason  
**So that** I understand why certain tests don't run on my platform and don't mistake skipped tests for passing tests

### US-3: POSIX Developer Still Gets Real Signal Coverage
**As a** developer on Linux/macOS  
**I want** a real end-to-end signal test that proves a signal stops an agent process  
**So that** I have confidence the signal handling works in production

### US-4: Windows Behavior Is Pinned by Test
**As a** maintainer  
**I want** a test that asserts `Agent._install_signal_handlers` refuses, logs once, and relies on the injected stop event on Windows  
**So that** the fallback behavior is a deliberate, tested decision rather than an accident

---

## 2. Acceptance Criteria

| ID | Criterion | Verification |
|----|-----------|--------------|
| AC-1 | `pytest agent/tests` completes on Windows and prints a summary; the pytest process survives to the end | Run on Windows CI / local Windows |
| AC-2 | Signal-delivery tests (`test_sigterm_triggers_orderly_shutdown`, `test_sigint_triggers_orderly_shutdown`, `test_signal_delivery_sends_the_signal_once_a_handler_is_armed`, `test_signal_delivery_refuses_to_run_at_the_default_disposition`, `test_the_safety_guard_counts_only_signals_nobody_asked_for`) are skipped on Windows with a stated reason and run on POSIX | `pytest -v` shows `skipped` with reason on Windows, `passed` on POSIX |
| AC-3 | A POSIX-only test proves a real signal stops a real agent process end-to-end (spawns child agent process, signals that PID) | New test passes on POSIX, skipped on Windows |
| AC-4 | Windows behavior of `Agent._install_signal_handlers` (refuses, logs once, relies on injected stop event) is pinned by a test that runs on both platforms | `test_signal_handlers_fall_back_where_unsupported` passes on both |
| AC-5 | No test in the suite calls `os.kill` against `os.getpid()` unconditionally | Grep shows no unguarded `os.kill(os.getpid()` |
| AC-6 | The remaining ~32 tests in `agent/tests/test_loop.py` pass on Windows | `pytest agent/tests/test_loop.py` shows 32+ passed, 5 skipped on Windows |

---

## 3. Test Scenarios

### 3.1 Existing Tests to Modify

| Test | Current Behavior | New Behavior |
|------|------------------|--------------|
| `test_sigterm_triggers_orderly_shutdown` | Calls `drive(agent, signal.SIGTERM)` → `deliver_signal_to_self(SIGTERM)` → `os.kill(os.getpid(), SIGTERM)` (kills pytest on Windows) | Skip on Windows with `pytest.mark.skipif(not signal_delivery_is_supported(), reason="...")`. On POSIX: keep real delivery. |
| `test_sigint_triggers_orderly_shutdown` | Same as above for SIGINT | Same as above |
| `test_signal_delivery_sends_the_signal_once_a_handler_is_armed` | Mocks `os.kill` and `signal.getsignal`; tests the guard logic | Keep as-is (already mocks `os.kill`, doesn't actually signal self) |
| `test_signal_delivery_refuses_to_run_at_the_default_disposition` | Mocks `os.kill` and `signal.getsignal`; tests the guard logic | Keep as-is (already mocks `os.kill`, doesn't actually signal self) |
| `test_the_safety_guard_counts_only_signals_nobody_asked_for` | Tests `SignalSafetyGuard` logic only | Keep as-is (no signal delivery) |

**Key insight**: Only the first two tests actually call `deliver_signal_to_self()` which does `os.kill(os.getpid(), sig)`. The "signal delivery" unit tests (3, 4, 5) already mock `os.kill` and are safe. But they should still be skipped on Windows because they test signal delivery semantics that don't apply.

### 3.2 New Tests to Add

| Test | Purpose | Platform |
|------|---------|----------|
| `test_real_signal_stops_child_agent_process` | Spawn `Agent.run()` in a subprocess, send SIGTERM to that PID, assert orderly shutdown | POSIX only (`@pytest.mark.skipif(sys.platform == "win32", ...)`) |
| `test_signal_handlers_fall_back_where_unsupported` (exists) | Already pins the fallback behavior; ensure it runs on both platforms | Both |

### 3.3 Platform Detection Helper

Add to `agent/tests/conftest.py` or a new `agent/tests/platform.py`:

```python
import sys
import signal

def signal_delivery_is_supported() -> bool:
    """Whether this platform can deliver signals to a running asyncio loop.
    
    On Windows, `loop.add_signal_handler` raises NotImplementedError and
    `os.kill(pid, SIGTERM)` is TerminateProcess, not a signal.
    """
    if sys.platform == "win32":
        return False
    # Additional check: some POSIX environments may not support it either
    try:
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: None)
        loop.remove_signal_handler(signal.SIGTERM)
        return True
    except (NotImplementedError, RuntimeError, AttributeError):
        return False
```

---

## 4. Technical Approach

### 4.1 Separate "Handler Armed and Fired" from "Signal Reached Process"

The current `drive()` function conflates two things:
1. **Handler contract**: The agent installs a handler that calls `stop.set()` — testable everywhere via `spy_on_armed_signal_handlers`
2. **Delivery contract**: A real signal sent to the process reaches the handler — POSIX only

**Refactor `drive()`**:
- Default to `sig=None` (direct `stop.set()`) for shutdown-order tests
- Add explicit `deliver_real_signal` parameter that gates the `os.kill` call
- On Windows, `deliver_real_signal=True` causes a skip with reason

### 4.2 Skip Signal-Delivery Tests on Windows

Apply `@pytest.mark.skipif(not signal_delivery_is_supported(), reason="Signal delivery to self PID is TerminateProcess on Windows")` to:
- `test_sigterm_triggers_orderly_shutdown`
- `test_sigint_triggers_orderly_shutdown`

The signal-delivery unit tests (3, 4, 5 in table above) should also be skipped on Windows since they test POSIX signal semantics, but they're already safe because they mock `os.kill`.

### 4.3 Add POSIX-Only End-to-End Signal Test

Create a new test that:
1. Spawns `python -m agent.run_agent --node-id test-node --syncthing-root /tmp/...` as a subprocess
2. Waits for "agent starting" log line
3. Sends `signal.SIGTERM` to the child PID (not pytest's PID)
4. Waits for process to exit with `EXIT_OK`
5. Asserts shutdown sequence logged in order

This test is skipped on Windows entirely.

### 4.4 Pin Windows Fallback Behavior

The existing `test_signal_handlers_fall_back_where_unsupported` already does this by monkeypatching `add_signal_handler` to raise `NotImplementedError`. Ensure:
- It runs on both platforms (no skip)
- It asserts exactly one warning is logged
- It asserts the shutdown sequence still runs correctly via injected stop event

### 4.5 Remove Unguarded `os.kill(os.getpid())` Calls

Audit all test files for `os.kill(os.getpid()` and ensure every call is either:
- Inside `deliver_signal_to_self()` which is only called from skipped tests on Windows
- Mocked in unit tests
- Guarded by `signal_delivery_is_supported()` check

---

## 5. Edge Cases and Risks

### 5.1 Risk: `test_run_agent.py` Subprocess Test Has Same Problem

**Issue #124 Open Question 2**: `test_agents_md_quick_reference_command_runs` spawns a subprocess and calls `process.terminate()` (SIGTERM on POSIX, `TerminateProcess` on Windows). The child agent's `add_signal_handler` is unavailable on Windows, so the observable outcome differs.

**Mitigation**: 
- This test already uses `process.terminate()` on a *child* process, not `os.kill(os.getpid())` on self. The pytest runner survives.
- However, the test asserts the agent starts but doesn't assert clean shutdown. On Windows, `terminate()` kills the child without giving it a chance to run handlers.
- **Decision**: Keep this test as-is for now (it tests startup, not shutdown). Add a separate POSIX-only test for graceful shutdown via signal.

### 5.2 Risk: Signal Guard Interference

The `SignalSafetyGuard` installs handlers for SIGTERM/SIGINT at session start. On Windows, these handlers are never called because `TerminateProcess` bypasses them. But the guard's `record()` method could be called if a test somehow triggers a real signal.

**Mitigation**: The guard is harmless on Windows — it just won't catch anything because signals aren't delivered. No change needed.

### 5.3 Risk: False Confidence from Mocked Tests

The signal-delivery unit tests (3, 4, 5) mock `os.kill` and `signal.getsignal`. They test the *guard logic*, not real signal delivery. Skipping them on Windows is correct because the semantics they test don't exist on Windows.

**Mitigation**: Clear skip reason: "Signal delivery semantics tested here are POSIX-only; Windows uses TerminateProcess which cannot be intercepted."

### 5.4 Risk: CI Doesn't Run on Windows

If CI only runs on Linux, the Windows skip logic is never exercised.

**Mitigation**: 
- Add a Windows CI job (even if manual/optional) 
- Document in PR template: "Verify `pytest agent/tests` passes on Windows if you have access"
- The `signal_delivery_is_supported()` function is testable on any platform via monkeypatch

### 5.5 Risk: `asyncio.run()` vs Running Loop

`Agent._install_signal_handlers` calls `asyncio.get_running_loop()`. Tests that check the fallback behavior must run inside a running loop (which `pytest-asyncio` provides).

**Mitigation**: The existing `test_signal_handlers_fall_back_where_unsupported` is async and works correctly. No change needed.

---

## 6. Implementation Plan

### Phase 1: Platform Detection Helper
1. Add `signal_delivery_is_supported()` to `agent/tests/conftest.py`
2. Export it in `__all__`

### Phase 2: Skip Existing Signal-Delivery Tests on Windows
1. Add `@pytest.mark.skipif(not signal_delivery_is_supported(), reason=...)` to:
   - `test_sigterm_triggers_orderly_shutdown`
   - `test_sigint_triggers_orderly_shutdown`
2. Also skip the signal-delivery unit tests with same reason (for clarity):
   - `test_signal_delivery_sends_the_signal_once_a_handler_is_armed`
   - `test_signal_delivery_refuses_to_run_at_the_default_disposition`
   - `test_the_safety_guard_counts_only_signals_nobody_asked_for`

### Phase 3: Add POSIX-Only End-to-End Signal Test
1. Create `test_real_signal_stops_child_agent_process` in `test_loop.py`
2. Use `subprocess.Popen` to spawn agent
3. Send `signal.SIGTERM` to child PID
4. Assert orderly shutdown
5. Mark with `@pytest.mark.skipif(sys.platform == "win32", reason="Real signal delivery test requires POSIX")`

### Phase 4: Verify Fallback Test Runs on Both Platforms
1. Confirm `test_signal_handlers_fall_back_where_unsupported` has no skip decorator
2. Run on Windows and POSIX to confirm

### Phase 5: Audit and Clean Up
1. Grep for `os.kill(os.getpid()` — ensure no unguarded calls remain
2. Run full `agent/tests` suite on Windows (or simulated via monkeypatch)
3. Confirm 32+ tests pass, 5 skipped, 0 errors

---

## 7. File Changes Summary

| File | Changes |
|------|---------|
| `agent/tests/conftest.py` | Add `signal_delivery_is_supported()` helper |
| `agent/tests/test_loop.py` | Add skip decorators to 5 tests; add new POSIX-only child-process signal test |
| `agent/tests/test_run_agent.py` | No changes (but document known limitation in comments) |

---

## 8. Definition of Done

- [ ] Spec reviewed and approved by team
- [ ] Implementation complete per Phase 1-5
- [ ] `pytest agent/tests` passes on Windows (simulated or real)
- [ ] `pytest agent/tests` passes on POSIX with all signal tests running
- [ ] CI updated if needed
- [ ] Documentation updated if needed
- [ ] PM accepts

---

## Appendix: Current Test Inventory (test_loop.py)

Total tests: ~45 (as of issue filing)

**Signal-delivery tests (5) → Skip on Windows:**
1. `test_sigterm_triggers_orderly_shutdown`
2. `test_sigint_triggers_orderly_shutdown`
3. `test_signal_delivery_sends_the_signal_once_a_handler_is_armed`
4. `test_signal_delivery_refuses_to_run_at_the_default_disposition`
5. `test_the_safety_guard_counts_only_signals_nobody_asked_for`

**Fallback test (1) → Run on both:**
6. `test_signal_handlers_fall_back_where_unsupported`

**Shutdown-order tests (3) → Run on both (use `drive(agent)` with `sig=None`):**
7. `test_shutdown_sequence_runs_steps_in_order`
8. `test_hand_back_in_flight_job_precedes_watcher_stop`
9. `test_write_final_state_seam_is_called`

**Stop safety (1) → Run on both:**
10. `test_stop_is_safe_to_call_twice`

**Containment/criticality (4) → Run on both:**
11. `test_raising_task_does_not_stop_the_agent_process`
12. `test_dead_watcher_returns_exit_code_3`
13. `test_alive_watcher_does_not_exit`
14. `test_probe_liveness_raises_through_the_supervisor`

**No-crash-loop (4) → Run on both:**
15. `test_unwritable_root_logs_error_and_stays_alive`
16. `test_folder_retry_is_bounded_and_backs_off`
17. `test_no_retry_after_budget_exhausted`
18. `test_missing_subdirectories_are_created`

**Watcher resilience (4) → Run on both:**
19. `test_watcher_start_failure_is_not_fatal`
20. `test_no_liveness_task_without_a_folder`
21. `test_all_tasks_are_registered_when_ready`
22. `test_late_appearing_folder_activates_the_rest`
23. `test_watcher_starts_lazily_once_folder_appears`
24. `test_folder_watch_starts_the_watcher_after_the_folder_appears`
25. `test_activate_watcher_tasks_is_a_no_op_before_start`

**Exit code 4 (1) → Run on both:**
26. `test_shutdown_grace_expiry_returns_exit_code_4`

**Event drain (3) → Run on both:**
27. `test_event_drain_logs_observed_events_without_parsing`
28. `test_event_drain_is_safe_on_an_empty_queue`
29. `test_event_drain_interval_is_fixed`

**Misc (4) → Run on both:**
30. `test_paths_come_from_settings_not_the_environment`
31. `test_default_watcher_is_used_when_none_is_injected`
32. `test_heartbeat_jitter_is_seeded_and_deterministic`
33. `test_heartbeat_jitter_range`
34. `test_agent_writes_heartbeat_immediately_on_startup`
35. `test_agent_writes_heartbeat_immediately_on_resume`
36. `test_folder_event_shape`

**New (1) → POSIX only:**
37. `test_real_signal_stops_child_agent_process`

**Expected Windows result:** 32 passed, 5 skipped, 0 failed, 0 errors
**Expected POSIX result:** 37 passed, 0 skipped