# Spec — Issue #114: the agent suite kills the pytest process, and one signal test silently tests nothing

**Issue:** [#114](https://github.com/AlZrSe/sci-home-cluster/issues/114) — *agent/tests kills the pytest process: signal_handlers_supported() restores SIG_DFL, then the test signals its own PID*
**Status:** groomed, ready for SWE
**Author:** PM
**Baseline commit:** `593c4dd` (`feat: fix backend lint and type errors for CI gate (#54)`), branch `main`, clean tree
**Merge that introduced both defects:** PR #112 (issue #93)
**Measured on:** Linux, Python 3.13.5, pytest 8.0.0, pytest-asyncio 0.23.8, `.venv/bin/python`

**This file is the authoritative input for the SWE cycle.** It is self-sufficient. Do not go read the
issue thread; §1–§3 explain why you should not need to, and §3 corrects four claims in it.

---

## 0. Baseline, as re-measured by PM

Everything in this document was verified on the baseline commit, not taken from the issue. Commands
and outputs are in §11.

| Scope | Result on `593c4dd` |
|---|---|
| `.venv/bin/python -m pytest backend/tests -q` | **521 passed, 29 skipped** (exit 0) |
| `.venv/bin/python -m pytest agent/tests -q` | **killed by SIGTERM inside `agent/tests/test_loop.py`, exit 143, no summary** |
| `.venv/bin/python -m pytest` (default `testpaths`) | **killed by SIGTERM, exit 143, no summary** |
| `agent/tests` collection | **166 tests**, 8 files |
| `pytest agent/tests/test_loop.py -k sigterm_triggers` | **exit 143**, < 1 s |

Per-file collection counts (the shape of what the crash hides):

```
 46  agent/tests/test_config.py
 14  agent/tests/test_logging_config.py
 29  agent/tests/test_loop.py        <- crash happens in this file
  9  agent/tests/test_no_cluster_required.py
  8  agent/tests/test_paths.py
 18  agent/tests/test_run_agent.py
 19  agent/tests/test_supervisor.py
 23  agent/tests/test_watcher.py
```

---

## 1. Problem restatement

`pytest` — the Definition of Done for **every issue in this repository** — cannot run to completion on
`main`. The agent half of the suite sends `SIGTERM` to the pytest process while that process's signal
disposition is `SIG_DFL`, so the kernel applies the default action and the whole interpreter dies.
There is no assertion failure, no traceback, and no summary line: pytest is killed mid-file.

That is only the first of two defects, and the second is the quieter one. A third test,
`test_signal_handlers_fall_back_where_unsupported`, tries to force `add_signal_handler` to fail by
monkeypatching `asyncio.AbstractEventLoop`. On Linux the running loop is `_UnixSelectorEventLoop`,
which **overrides** that method in `asyncio.unix_events`, so the patch is never reached. The test then
asserts on a warning that the production code never emits, and fails with `assert 0 == 1` — on the one
platform this repo targets first, and only on that platform.

So today: `main` has no runnable test gate, the two signal tests are a live hazard, and the
Windows-portability behaviour the tests claim to cover (issue #93 R7) is untested everywhere.

**Why it is not the same as #107.** #107 reports the suite as *uncollectable* because the checked-in
venv drifted to `pytest-asyncio==0.23.0`. With the pinned `0.23.8` the suite collects 166 agent tests
and 550 backend tests cleanly. Two different failures; #114 is the one that survives a correct
environment. See §4.

---

## 2. Root cause analysis — both defects confirmed by reading and running the code

### 2.1 Defect 1 — the capability probe disarms the agent's own handler, then the test kills pytest

**The probe** — `agent/tests/test_loop.py:82-97`:

```python
def signal_handlers_supported(sig: Any = signal.SIGTERM) -> bool:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    try:
        loop.add_signal_handler(sig, lambda: None)   # line 93: installs a NO-OP
    except (NotImplementedError, RuntimeError, ValueError, OSError, AttributeError):
        return False
    loop.remove_signal_handler(sig)                  # line 96: restores SIG_DFL
    return True
```

`remove_signal_handler` is not an undo of *whatever was there before*. On POSIX it calls
`signal.signal(sig, signal.SIG_DFL)` unconditionally. Measured:

```
after agent install : <function _sighandler_noop>
after no-op probe   : <function _sighandler_noop>
after remove        : 0    is SIG_DFL: True
```

**The delivery site** — `agent/tests/test_loop.py:139-140`, inside `drive()`:

```python
if sig is not None and signal_handlers_supported(sig):   # line 139
    os.kill(os.getpid(), sig)                            # line 140
```

`drive()` starts `agent.run()` on line 131. `Agent.run()` calls `_install_signal_handlers()`
(`agent/loop.py:80`), which does `loop.add_signal_handler(sig, stop.set)` for SIGTERM and SIGINT
(`agent/loop.py:132`). By line 139 the agent's handler is armed — and line 139 then calls the probe,
which **overwrites it with a no-op and then resets it to `SIG_DFL`**. Line 140 delivers the signal to
a process with no handler. Default action. Dead.

Reproduced standalone, exactly the probe's add/remove/kill sequence:

```
$ python -c "... add_signal_handler(SIGTERM, lambda: None); remove_signal_handler(SIGTERM); \
              os.kill(os.getpid(), SIGTERM); await asyncio.sleep(2)"
EXIT=143     # 'SURVIVED' never printed
```

Two tests hit this: `test_sigterm_triggers_orderly_shutdown` (line 161) and
`test_sigint_triggers_orderly_shutdown` (line 169), both via `drive(agent, sig)`.

**`return True` on line 97 is a correct answer to the wrong question.** As a *capability* question
("can this loop accept a handler?") it is right. As a *delivery* question ("is a handler armed that
this signal will reach?") it is wrong, because the act of asking has already disarmed the one that
was there. The docstring at line 83 — *"probed before acting"* — states the invariant the call site
then violates: **the probe is only safe before anything is armed, and line 139 runs it after.**

The same probe also fires earlier, from `require_signal_delivery()` (line 110), which runs *before*
`agent.run()`. That call is **not** the bug: nothing is armed yet, so `remove_signal_handler` restores
`SIG_DFL`, which is what was there. It is only unsafe in principle — it mutates process-global
disposition in a process that also runs real signal tests — and §6 D2 addresses that.

### 2.2 Defect 2 — the fallback test patches a class the loop never looks up

`agent/tests/test_loop.py:200-213`:

```python
def refuse(*_a: Any, **_k: Any) -> None:
    raise NotImplementedError("add_signal_handler is not supported here")

monkeypatch.setattr(asyncio.AbstractEventLoop, "add_signal_handler", refuse)   # line 206
...
warnings = [r for r in caplog.records if "signal handlers" in r.getMessage()]
assert len(warnings) == 1, "the fallback warns once, not once per signal"      # line 213
```

Measured on this platform:

```
loop class          -> _UnixSelectorEventLoop
resolved module     -> asyncio.unix_events
resolved qualname   -> _UnixSelectorEventLoop.add_signal_handler
base class module   -> asyncio.events
has own override    -> True
```

Attribute lookup finds the subclass override first and never reaches the patched base class, so
`refuse` is never called, `Agent._install_signal_handlers()` takes the success path, and no warning is
emitted. Confirmed by running it alone:

```
FAILED agent/tests/test_loop.py::test_signal_handlers_fall_back_where_unsupported
E   AssertionError: the fallback warns once, not once per signal
E   assert 0 == 1
```

**The production code is correct here.** `agent/loop.py:130-146` catches the failure, logs once, and
`return`s. Only the test's patch target is wrong.

### 2.3 The suite's own guard is a casualty

`agent/tests/test_no_cluster_required.py:162` `test_agent_suite_passes_with_hostile_environment`
shells out to `pytest agent/tests -q --deselect <itself>` and asserts `returncode == 0` (line 193).
Re-measured in isolation:

```
E   AssertionError: ============================= test session starts ==============================
E       agent/tests/test_loop.py
E   assert -15 == 0
```

The guard that exists to prove the agent suite is clean is killed by the bug it was meant to police.

### 2.4 The window between tests is also fatal — measured

pytest-asyncio closes the event loop between tests, and `loop.close()` removes its signal handlers.
So **outside** a test the process sits at `SIG_DFL` for SIGTERM and SIGINT:

```
in loop  : <function _sighandler_noop>
next test: 0    is SIG_DFL: True
```

This is what makes the failure so untriageable: a stray signal delivered *between* tests, or in the
part of a test before `Agent.run()` arms the loop, kills pytest with no test failure and no traceback.
It also means the fix must add a guard at the **conftest/session** level (§6 D4), not only at the
delivery site — a delivery-site assertion covers only the microseconds around `os.kill`.

---

## 3. Corrections to the issue

PM is the acceptance authority. These four claims in the issue body are wrong or incomplete.

### C1 — The issue undercounts the damage: **106 tests** are unreported, not 89.

The issue lists "all 29 `test_loop.py` tests plus `test_run_agent.py`, `test_supervisor.py`,
`test_watcher.py`". Collection order is alphabetical within the directory, and two more files sort
**after** `test_loop.py`:

```
29 test_loop + 9 test_no_cluster_required + 8 test_paths + 18 test_run_agent
             + 19 test_supervisor + 23 test_watcher = 106
```

So 106 of 166 agent tests (64%) never report on a default run. This matters for the AC that counts
reported tests — count **106**, not 89.

### C2 — "the crash occurs in the first file" is wrong, and the issue contradicts itself.

`test_loop.py` is the **third** file collected (`test_config.py`, `test_logging_config.py`,
`test_loop.py`). Cosmetic, but it is the sentence that sets up the undercount in C1.

### C3 — `pytest agent/tests/test_loop.py -k sigterm_triggers` does not "never return"; it exits 143 in under a second.

Measured: `collected 29 items / 28 deselected / 1 selected`, then `agent/tests/test_loop.py`, exit 143.
"Never returns" describes the *symptom a human sees*, not the mechanism, and an implementer who
trusts it will add a timeout workaround for a hang that does not exist.

### C4 — The backend timing does not reproduce: 52.8 s, not 28 s.

Counts are exactly as stated (**521 passed, 29 skipped**). The issue's "in 28s" is a
machine-dependent number and was not reproducible here (52.81 s, 73 warnings). **Accept counts, not
durations** — no AC in this spec asserts a wall-clock time for the backend suite.

### C5 — The Windows claim is plausible but **not verified** from this platform, and it does not affect the fix.

The issue states the fallback test "passes on Windows — where `_WindowsProactorEventLoop` has no
override". This is consistent with CPython's layout (the Windows loops inherit
`BaseEventLoop.add_signal_handler`, which raises `NotImplementedError`), but PM could not execute it
from Linux and it is **not** treated as established fact anywhere in this spec. The fix (§6 D3) is
specified to work on both platforms without needing that claim to be true.

### C6 — One defect the issue does not mention, recorded as a **risk**, not as required work.

`Agent.run()` / `_install_signal_handlers()` installs SIGTERM and SIGINT handlers and **never removes
them** — `agent/loop.py` contains no `remove_signal_handler` call anywhere (verified by grep). Today
this is benign only because pytest-asyncio closes each test's loop, which drops them. The moment two
tests share a loop, or `run()` raises before teardown, a stale `_sighandler_noop` outlives the agent
it belonged to. Fixing that in production code is **out of scope** (§4) but the SWE must not make it
worse, and the new guard must not assume a clean disposition at session start. See R3.

---

## 4. Scope

### In scope

1. **`agent/tests/test_loop.py` — `drive()` and `signal_handlers_supported()`.** Stop re-probing after
   the agent has armed the loop; assert the disposition before delivering a signal.
2. **`agent/tests/test_loop.py` — `test_signal_handlers_fall_back_where_unsupported`.** Patch the class
   the running loop actually resolves, on every platform.
3. **A regression guard** so that a signalled pytest process fails a test loudly instead of dying.
4. **Making the two signal tests assert real delivery**, so they cannot pass by taking the fallback
   and cannot pass by dying.
5. Test-only changes to `agent/tests/conftest.py` if the guard needs a session-level hook.

### Out of scope — explicitly

| Not here | Why | Where it lives |
|---|---|---|
| The `.venv` pytest-asyncio `0.23.0` drift and the unresolvable documented install | A **different** failure — collection-time `AttributeError`, fixed by the pinned `0.23.8`. With the correct pair this issue reproduces perfectly. | **#107** |
| `backend/tests` | Green: 521 passed / 29 skipped, exit 0, unchanged by this work. | — |
| CI workflow changes | A workflow would go red here, but authoring the pipeline is separate work. | **#26** |
| Changing `Agent._install_signal_handlers()` production behaviour | The production fallback (log once, `return`) is correct. **Do not "fix" `agent/loop.py`.** | — |
| Removing the signal handlers `Agent.run()` installs (C6) | Real, but a separate production change with its own risk. | tracked as risk R3; file a follow-up |
| `Makefile` targets, Windows CI runners | | **#26** |
| Anything in `frontend/` (a submodule) | Unrelated. | — |

**Production code is not to be touched.** `agent/loop.py:115-146` is correct as written. If the SWE
believes it needs changing, that is a spec-level discovery, not an implementation decision — stop and
raise it.

---

## 5. User stories

### US-1 — Agent developer

> As someone working on `agent/`, I run `pytest` before every commit. Today that command kills my
> shell's child with no message, so I cannot tell a pass from a crash, and I cannot trust the green
> `backend` half to mean the agent half was checked. I want `pytest` to finish, print a summary, and
> exit 0 — and I want it to keep finishing six months from now.

**Acceptance:** `python -m pytest` prints a summary line and exits 0; a regression fails loudly instead
of killing the process.

### US-2 — Agent developer

> When I change `Agent._install_signal_handlers()`, I want the tests that cover it to actually cover it.
> Today the fallback test patches a class the loop never uses, so it is green on Windows and red on
> Linux while asserting nothing either way; and the delivery tests "pass" by taking the fallback. I
> want a green test run to mean the signal path was really exercised.

**Acceptance:** on Linux, `test_signal_handlers_fall_back_where_unsupported` passes **because**
`add_signal_handler` genuinely raised, and the two shutdown tests pass **because** a real signal
reached the agent.

### US-3 — Repo maintainer

> `pytest` is the Definition of Done in `AGENTS.md` and `PROCESS.md`. Right now no issue can be
> completed to spec on `main`. I also do not want to add CI (#26) on top of a suite that can be
> killed by its own tests. I want the gate trustworthy before I automate it.

**Acceptance:** the suite completes and exits 0; the guard makes a future recurrence a red test, not a
triage mystery.

### US-4 — Maintainer working on portability

> The agent claims graceful-degradation behaviour when `add_signal_handler` is unavailable (issue #93
> R7). That claim is currently unverified on every platform. I want the fallback covered by a test
> that works on POSIX and Windows alike, so the claim stops being aspirational.

**Acceptance:** the fallback test's patch target is resolved from the running loop, not hard-coded to
a base class, and it passes on both platforms.

---

## 6. Decisions — design guidance for the SWE

These are **decisions**, not suggestions. Each one exists because the naive fix re-creates the bug.

### D1 — `drive()` must never probe-and-remove once the agent has armed the loop. **Delete the re-probe.**

`agent/tests/test_loop.py:139` must stop calling `signal_handlers_supported(sig)`. The capability
question was answered correctly the first time; asking it again is what disarms the handler. The
`if` becomes "the caller asked for a signal" and nothing else.

If a capability check is still wanted, it must be a **non-mutating** one — read
`type(asyncio.get_running_loop()).add_signal_handler` and its `__module__`, or check
`sys.platform` — never install-and-remove.

### D2 — The capability probe must not run in the same process as a real signal test.

`signal_handlers_supported()` mutates process-global disposition. `require_signal_delivery()` (line 110)
calls it at the top of every signal test. Once per test is tolerable today (nothing is armed yet), but
it is the same hazard one edit away from being fatal.

**Preferred: delete `require_signal_delivery`'s probe and invert it.** Instead of *probing whether
delivery works and skipping if not*, **assert that delivery happened**. If a signal cannot be
delivered on this platform, the test must say so **at teardown** — not skip up front — so the report
shows a failure or an explicit skip for the right reason, never a silent pass.

Where a non-mutating platform predicate is genuinely needed (e.g. Windows lacks `SIGTERM` handler
support), use `type(loop).add_signal_handler.__module__ == "asyncio.unix_events"`, or
`hasattr(signal, "SIGTERM")` — both read-only.

### D3 — Patch `type(asyncio.get_running_loop())`, not the base class. Make it cross-platform.

`monkeypatch.setattr(asyncio.AbstractEventLoop, "add_signal_handler", refuse)` must become a patch on
the class the running loop actually resolves to:

```python
loop = asyncio.get_running_loop()
monkeypatch.setattr(type(loop), "add_signal_handler", refuse, raising=True)
```

This works on POSIX (`_UnixSelectorEventLoop`) **and** on Windows (no override, so the attribute is
inherited from the base and `type(loop)` is still the right object to patch). No `sys.platform`
branch, no hard-coded `_UnixSelectorEventLoop` import.

**Required assertions inside that test, so "green" cannot mean "the patch silently missed" again:**

1. `refuse` recorded that it was actually called (count the calls in a list).
2. The expected warning was emitted — exactly one.
3. The agent still shut down in order, having taken the fallback path.

Assertion 1 is the one that was missing and is the direct cause of defect 2.

### D4 — A regression guard is required, and it must live in `agent/tests/conftest.py`.

The issue's AC asks for "a test that fails if the pytest process is signalled". A test cannot do this
alone: if the process dies, the test never runs (and neither would any session fixture finaliser —
see §2.4). So the guard is **two layers**:

**Layer 1 — at the delivery site (mandatory, in `test_loop.py`).** Immediately before every
`os.kill`, assert the disposition is a handler, not `SIG_DFL`:

```python
disposition = signal.getsignal(sig)
assert disposition is not signal.SIG_DFL, (
    f"{sig.name} is at SIG_DFL -- refusing to signal the pytest process; "
    "a handler was disarmed after the agent armed it"
)
```

Measured behaviour of that assertion: `signal.getsignal` returns
`<function _sighandler_noop>` while an asyncio handler is armed and `0` after
`remove_signal_handler`. This is exact, not heuristic, and it is the one thing that turns "runner
died" into "test failed". Verified end to end:

```
SURVIVED; stop set = True
EXIT=0
```

**Layer 2 — at the session level (mandatory, in `agent/tests/conftest.py`).** Covers the between-tests
window from §2.4:

- `pytest_configure` (or a session-scoped autouse fixture) records the original `SIGTERM`/`SIGINT`
  dispositions and installs a **recording** handler — one that appends the signum to a module-level
  list and does *not* re-raise, so the process survives.
- An autouse **function-scoped** fixture **re-arms** that handler on setup. This matters:
  `Agent.run()`'s `add_signal_handler` clobbers it during the signal tests, and per C6 the loop-close
  teardown drops it. Re-arming at setup means a stray signal is recorded on the next test boundary.
- Teardown checks whether a signal was recorded during the test that just ran. If so, that test
  **fails** with a message naming the signum and the test — and it must survive to the summary, so
  it is reported as a failure, not a crash.
- `pytest_sessionfinish` reports the accumulated list, so the evidence survives even if teardown
  ordering surprises.

**Coordination with the deliberate deliveries.** The two signal tests *intend* to signal the process.
The guard must not count those. Give `drive()` a flag it sets around its own `os.kill`, and have the
recording handler ignore (or classify-as-expected) signals arriving while that flag is set. Do **not**
have the guard simply ignore all SIGINT/SIGTERM — that would re-open the hole.

Keep it to `SIGTERM` and `SIGINT`. `SIGQUIT` would suppress core dumps and `SIGHUP` is not what this
issue is about; adding them is a judgement call for QA, not a requirement here.

### D5 — Do not weaken `drive()`'s signature contract; keep callers explicit.

`drive(agent, sig=...)` must keep working for the ~14 call sites that pass no `sig` and take the
`stop.set()` fallback (lines 177-228, 245-321, 396-413, 558-565). Those are the tests that cover the
fallback path, and the issue acknowledges them. No caller outside the signal tests needs changing.

### D6 — The regression guard must be verifiable by a test, not just asserted in prose.

AC-9 in §7 is checked by **re-introducing defect 1 in a scratch copy and observing the failure**. If
the SWE cannot make a deliberate regression produce a red test, the guard is not a guard. Record the
command used in the PR description.

---

## 7. Acceptance criteria

Each item is objectively verifiable by running the stated command. PM will run them.

### Suite-level

- [ ] **AC-1** `.venv/bin/python -m pytest` (no arguments, default `testpaths`) runs to completion,
      prints a summary line, and exits `0`.
      *Verify: `python -m pytest | tail -3; echo $?`*
- [ ] **AC-2** `.venv/bin/python -m pytest agent/tests -q` exits `0` with no signal in the output.
      *Verify: run it; `echo $?` → `0`.*
- [ ] **AC-3** **All 166 collected tests in `agent/tests` are reported** — no crash, and none
      unreachable as a side effect of an earlier failure. The count to compare against is **166**
      today, and must not drop below it. (Today 106 of them never report; see C1.)
      *Verify: `pytest agent/tests -q --collect-only | tail -2` before and after; the "N tests
      collected" line must not decrease, and a full run must report N outcomes.*
- [ ] **AC-4** The suite reports **zero failures and zero errors**, and any skip is justified in its
      own skip message.
      *Verify: the `short test summary info` section lists no `FAILED` / `ERROR`; grep skips
      individually and confirm each says why.*
- [ ] **AC-5** `pytest backend/tests -q` is unchanged: **521 passed, 29 skipped**, exit 0. Counts only
      — do **not** assert a wall-clock time (C4).
      *Verify: run it and compare the counts to the baseline in §0.*

### The two signal tests

- [ ] **AC-6** `test_sigterm_triggers_orderly_shutdown` and `test_sigint_triggers_orderly_shutdown`
      assert shutdown ordering **after a real delivered signal**. Concretely, each must fail if the
      delivered signal is the *only* thing that stops the agent — i.e. the agent must not have been
      stopped by any other means. `drive()` must no longer reach its `stop.set()` fallback for these
      two tests, and the test must prove it rather than assume it (D2: assert delivery, do not
      pre-skip).
      *Verify: `pytest agent/tests/test_loop.py -k "sigterm_triggers or sigint_triggers" -v` — two
      passed, **no skips**, and `echo $?` → `0` (today: exit 143).*
- [ ] **AC-7** No path in the fix re-introduces install-then-remove of the same signum while the
      agent's handler is armed.
      *Verify: `grep -n "remove_signal_handler" agent/` — the only remaining call site must be inside
      the (now-deleted or repurposed) capability probe, and `agent/loop.py` must still contain none.*

### The fallback test

- [ ] **AC-8** `test_signal_handlers_fall_back_where_unsupported` **passes on Linux** by genuinely
      forcing `add_signal_handler` to raise, and the patch target is derived from the running loop
      rather than hard-coded to `AbstractEventLoop` or `_UnixSelectorEventLoop`.
      *Verify: `pytest agent/tests/test_loop.py::test_signal_handlers_fall_back_where_unsupported -v`
      → passed. And: `grep -n "asyncio.AbstractEventLoop\|unix_events" agent/tests/test_loop.py` must
      find no hard-coded patch target.*
- [ ] **AC-9** **The fallback test asserts that its patch took effect** (a call counter on `refuse`),
      so a missed patch fails the test rather than silently passing it. This is the specific check
      whose absence caused defect 2.
      *Verify: temporarily change `raise NotImplementedError` to `return None` in the test's `refuse`
      and confirm the test goes **red**. Revert.*

### The regression guard

- [ ] **AC-10** A guard exists that fails a test loudly if the pytest process is signalled — at both
      layers in D4: an assertion immediately before every `os.kill`, and a session-level recording
      handler in `agent/tests/conftest.py` that survives the window between tests.
      *Verify: the conftest hook and the pre-kill assertion are both present
      (`grep -n "getsignal\|pytest_configure\|runtest_setup" agent/tests/`).*
- [ ] **AC-11** **The guard is proven by re-introducing the defect.** Restore the probe call at
      `agent/tests/test_loop.py:139` in a scratch state, run the signal tests, and observe a **red
      test with a readable message** — never exit 143. Revert. Record the command and output in the
      PR description.
      *Verify: as above; `echo $?` must be `1`, and the failure message must name the signum and say
      the disposition was `SIG_DFL`.*
- [ ] **AC-12** The suite's own guard passes again:
      `test_agent_suite_passes_with_hostile_environment` asserts `returncode == 0` and must no longer
      see `-15`.
      *Verify: `pytest agent/tests/test_no_cluster_required.py::test_agent_suite_passes_with_hostile_environment -v`
      → passed. (Today: `assert -15 == 0`.)*
- [ ] **AC-13** No test file changes the production behaviour of `agent/loop.py`.
      *Verify: `git diff --stat` shows `agent/loop.py` untouched. Only `agent/tests/**` changed.*

### Quality gates

- [ ] **AC-14** Backend gates are clean: `ruff check . && ruff format --check . && mypy .` exits 0.
      *Verify: run it.*
- [ ] **AC-15** `pytest` is deterministic across **5 consecutive full runs** of `agent/tests` — 5/5
      exit 0, 5/5 identical counts. (This class of bug is order- and timing-sensitive; one green run
      proves nothing.)
      *Verify: `for i in 1 2 3 4 5; do pytest agent/tests -q | tail -1; done` — all identical.*

---

## 8. Test scenarios

### 8.1 Modify — `test_sigterm_triggers_orderly_shutdown`, `test_sigint_triggers_orderly_shutdown`

**Files:** `agent/tests/test_loop.py:161-174`, plus `drive()` at `:117-145`.

**Genuinely exercising the intended path.** This is the part that needs the most care, because the
current failure mode is a test that passes for the wrong reason. For each, the SWE must be able to
answer "how do I know the signal, not the fallback, stopped the agent?" — and the answer must be in
the test, not in a comment:

| Check | How it proves the path |
|---|---|
| `require_signal_delivery`'s up-front probe is gone or made read-only | The test no longer skips on a capability guess (D2). |
| `drive()` does not call `stop.set()` when `sig` is given | If delivery failed, `asyncio.wait_for(running, 5.0)` times out and the test fails. A timeout is a failure, not a pass. |
| Disposition asserted not `SIG_DFL` immediately before `os.kill` | Proves a handler was armed at the instant of delivery — which is exactly what defect 1 destroyed. |
| Ordering assertion `["hand_back", "watcher.stop", "write_state"]` retained | The shutdown contract is unchanged; only the trigger is now provable. |
| The guard's expected-signal flag is set around the `os.kill` | Keeps AC-10's session guard from reporting these two deliberate deliveries (D4). |

**Negative control the SWE must run:** comment out the `os.kill` in `drive()` and confirm both tests
go **red** (timeout). A test that stays green without the `os.kill` is not a delivery test. Record
this in the PR description — it is the same evidence AC-11 asks for.

### 8.2 Modify — `test_signal_handlers_fall_back_where_unsupported`

**File:** `agent/tests/test_loop.py:200-213`.

- Patch `type(asyncio.get_running_loop())` instead of `asyncio.AbstractEventLoop` (D3). Works on
  POSIX and Windows with no `sys.platform` branch.
- Count calls to `refuse` in a list; assert it was called — **the assertion whose absence caused
  defect 2** (AC-9).
- Assert exactly one `"signal handlers"` warning from logger `agent.loop` (unchanged intent).
- Assert the agent still shut down in order via the fallback.
- No new skips. The fallback is exercised by construction here, not by the probe.

**Genuinely exercising the intended path:** the call counter. Without it this test can be green
whether or not the patch landed, which is precisely what happened.

### 8.3 Add — regression guard, session level

**File:** `agent/tests/conftest.py` (new fixture + `pytest_configure`).

- Recording handler for `SIGTERM` and `SIGINT` that logs the signum and **survives** (does not
  re-raise, does not exit).
- Autouse function-scoped fixture re-arming it on setup and asserting on teardown that no unexpected
  signal was recorded during that test (D4 Layer 2).
- Coordination flag readable by `drive()` so deliberate deliveries are not counted.
- Restores the original dispositions at session end, so the guard does not leak into
  `test_agent_suite_passes_with_hostile_environment`'s subprocess expectations.

**Naming:** the PM has not fixed a name. Pick one that reads as a guard
(`fail_on_stray_signal`, `signal_safety_guard`) and say so in the PR.

### 8.4 Add — a direct unit test for the delivery helper

Recommended, and cheap: a synchronous test asserting that the helper **refuses** to signal when the
disposition is `SIG_DFL`, and that it refuses *before* calling `os.kill` (monkeypatch `os.kill` to a
recorder and assert it was never called). This is the cheapest possible proof of D4 Layer 1 and it
runs without any risk to the process. Name it for what it asserts, not for the bug number.

### 8.5 Do not touch

- The ~14 `drive(agent)` call sites with no `sig` — they cover the fallback path and stay as they are
  (D5).
- `test_shutdown_sequence_runs_steps_in_order`, `test_stop_is_safe_to_call_twice` — pass today, must
  keep passing.
- `agent/loop.py` — production code (AC-13).
- `test_agent_suite_passes_with_hostile_environment` itself — it is a **victim**, not a bug. It starts
  failing only because the suite dies. Its body should not need to change.

---

## 9. Risks and open questions for the SWE

### R1 — **The guard can be clobbered.** Highest risk in this work.

`Agent.run()` installs its handlers mid-test, overwriting whatever conftest installed, and (per C6)
never removes them. Two consequences:

- While an agent is running, the session guard is **not** in place. A stray signal in that window is
  swallowed by asyncio's loop rather than recorded. That is survivable (the loop does not die), but it
  is invisible to the guard.
- After `Agent.run()` returns, stale handlers may persist on that test's loop.

**Mitigation:** re-arm in `pytest_runtest_setup`/`runtest_setup` rather than once at
`pytest_configure`, and do **not** assume a clean disposition at session start. If the SWE finds a
cleaner hook, use it and explain it in the PR.

### R2 — **Disposing of `signal_handlers_supported` and `require_signal_delivery` is the real design
decision, and deleting both is acceptable.**

They exist to turn "we cannot test delivery here" into a skip. D2 replaces that with an
assert-on-delivery. But the *fallback* tests still need to run on Windows, where delivery is genuinely
impossible. The SWE must therefore keep **some** platform predicate for `drive()`'s `else` branch,
and that predicate must be read-only. **Open question for the SWE:** is a read-only predicate enough,
or should the two signal tests skip explicitly on platforms that cannot deliver — and if so, does that
reintroduce the "silently tests nothing" problem? Recommendation: let it skip, but **only** after the
test has *tried* and can name the platform reason, never as an up-front capability guess.

### R3 — `Agent.run()` leaks signal handlers (C6). Out of scope, but do not make it worse.

If the SWE adds anything that installs a handler and relies on the agent's to be absent, it will break
when loop-teardown stops saving it. File a follow-up issue for the production fix; mention it in the
PR.

### R4 — Re-arming a handler between tests can mask a real bug.

If the session guard re-arms `SIGTERM`/`SIGINT` on every setup, a genuinely leaked handler from a
previous test becomes invisible. That is the trade the guard buys: visibility of stray signals in
exchange for hiding leaks. R3's follow-up is where the leak gets fixed properly.

### R5 — Flakiness under load.

Signal delivery through asyncio's self-pipe needs a loop iteration to land. A loaded machine can starve
it, so `drive()`'s `asyncio.wait_for(running, 5.0)` may fire. **Do not fix this with a longer
timeout** — the codebase has already been bitten by that class of fix (issue #93 QA F5, and the
comment at `test_loop.py:305`). Wait on the property (`stop.is_set()`), keep the ceiling at 5 s, and
make the failure message say which wait expired. AC-15's five consecutive runs exist to catch this.

### R6 — Windows is unverified from Linux.

PM could not execute the Windows path (C5). The fix is specified to be platform-neutral (D3), but the
Windows claim in issue #93 R7 will **still** be unverified until a Windows runner exists — which
is #26. The SWE should not weaken the cross-platform requirement to make the Linux run green.

### R7 — Coverage gate.

`pyproject.toml` sets `fail_under = 80` over `backend` + `agent`. If CI later runs
`pytest --cov=backend` (the command in `AGENTS.md`), the agent half is not measured by it today. Out
of scope; note it if the SWE touches coverage config.

### Open questions — answer in the PR, do not block on them

1. What is the new helper's name, and where does it live — `test_loop.py` or `conftest.py`?
2. Does the expected-delivery flag live in `conftest` (shared) or `test_loop.py` (local)?
3. Does the guard add `SIGHUP`/`SIGQUIT`, or stay at `SIGTERM`/`SIGINT`? (PM's default: stay.)
4. Does AC-4's "zero skips" hold for `agent/tests`, or do pre-existing platform skips
   (`REAL_WATCH_SUPPORTED` in `conftest.py`) need an explicit carve-out? PM's default: they exist and
   are justified — keep them, and AC-4 is about *new* unjustified skips.

---

## 10. Definition of done

- All of AC-1 … AC-15 pass, with the commands recorded in the PR description.
- AC-11 and the §8.1 negative control were **actually executed**, and the output is in the PR. A guard
  that has never been seen to fire is not evidence.
- AC-15 run **5 times**, not once.
- `agent/loop.py` untouched (AC-13).
- No new file outside `agent/tests/`.
- Commit message: `fix: stop the agent test suite from signalling the pytest process (#114)`.

---

## 11. Verification commands — the baseline, re-measured

```bash
PY=.venv/bin/python

# 0. environment (the pinned pair; 0.23.0 is issue #107, a different failure)
$PY -m pytest --version && $PY -c "import pytest_asyncio; print(pytest_asyncio.__version__)"
# -> 8.0.0 / 0.23.8

# 1. the backend half is healthy (AC-5) — counts only, no timing (C4)
$PY -m pytest backend/tests -q
# -> 521 passed, 29 skipped      exit 0

# 2. the agent half kills the runner (AC-1, AC-2)
$PY -m pytest agent/tests -q
# -> dies inside agent/tests/test_loop.py, no summary, exit 143

# 3. the full default run (AC-1)
$PY -m pytest
# -> exit 143, no summary

# 4. collection: 166 today; must not decrease (AC-3)
$PY -m pytest agent/tests -q --collect-only | tail -2
# -> 166 tests collected

# 5. defect 2 in isolation (AC-8) — the wrong patch target
$PY -m pytest "agent/tests/test_loop.py::test_signal_handlers_fall_back_where_unsupported" -q
# -> FAILED ... assert 0 == 1

# 6. the loop resolves to the subclass, not the patched base class (D3)
$PY -c "import asyncio; l=asyncio.new_event_loop(); \
        print(type(l).__name__, type(l).add_signal_handler.__module__)"
# -> _UnixSelectorEventLoop asyncio.unix_events

# 7. remove_signal_handler restores SIG_DFL, it does not restore the old handler
$PY -c "
import asyncio, signal
async def m():
    l = asyncio.get_running_loop(); l.add_signal_handler(signal.SIGTERM, lambda: None)
    print('armed :', signal.getsignal(signal.SIGTERM))
    l.remove_signal_handler(signal.SIGTERM)
    print('removed:', signal.getsignal(signal.SIGTERM),
          'is SIG_DFL:', signal.getsignal(signal.SIGTERM) is signal.SIG_DFL)
asyncio.run(m())"
# -> armed : <function _sighandler_noop>
# -> removed: 0 is SIG_DFL: True

# 8. probe-then-kill kills the interpreter (defect 1, standalone)
$PY -c "
import asyncio, os, signal
async def m():
    l = asyncio.get_running_loop()
    l.add_signal_handler(signal.SIGTERM, lambda: None)
    l.remove_signal_handler(signal.SIGTERM)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.sleep(2); print('SURVIVED')
asyncio.run(m())"
# -> no output, exit 143

# 9. the suite's own guard is a casualty (AC-12)
$PY -m pytest "agent/tests/test_no_cluster_required.py::test_agent_suite_passes_with_hostile_environment" -q
# -> FAILED ... assert -15 == 0

# 10. the between-tests window is also fatal (D4 / R1)
$PY -c "
import asyncio, signal
async def m(): asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: None)
asyncio.run(m()); d = signal.getsignal(signal.SIGTERM)
print('after the loop closes:', d, 'is SIG_DFL:', d is signal.SIG_DFL)"
# -> after the loop closes: 0 is SIG_DFL: True

# 11. the Layer-1 guard works: handler armed -> survives, disposition asserted
$PY -c "
import asyncio, os, signal
async def m():
    l = asyncio.get_running_loop(); stop = asyncio.Event()
    for s in (signal.SIGTERM, signal.SIGINT): l.add_signal_handler(s, stop.set)
    for s in (signal.SIGTERM, signal.SIGINT):
        assert signal.getsignal(s) is not signal.SIG_DFL, f'{s.name} is at SIG_DFL'
        os.kill(os.getpid(), s)
    await asyncio.sleep(0.3); print('SURVIVED; stop set =', stop.is_set())
asyncio.run(m())"
# -> SURVIVED; stop set = True     exit 0
```

**Commands 7, 8 and 10 are the evidence the issue's two root causes are real**, re-run by PM on
`593c4dd`. Commands 11 and 10 are the evidence the prescribed fix is sufficient: `getsignal` is an
exact, non-heuristic test of whether a handler is armed, and it holds both while a handler is armed
and across a loop's lifetime.