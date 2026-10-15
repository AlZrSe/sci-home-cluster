# Spec: Windows Job Execution — One Shell, One Tree-Stop, One Reachable Stop Event

> **Amended after the SWE and QA cycles (2026-10-15).** Six statements in this
> document were measured wrong or contradicted by the shipped code, and QA
> adjudicated all six in the implementer's favour. Each is marked
> **[corrected]** where it appears and collected in §12. This file is the
> artifact of record, so a spec that disagrees with the code is a defect, not
> a note. Nothing outside the corrections was changed.
>
> | # | where | what was wrong |
> |---|---|---|
> | C1 | §4 Q2, §6.1 | the forced tier's **order**, and a `control_mode` token list one short |
> | C2 | §4 Q3 | the pseudocode puts the job outside the Syncthing root on Windows |
> | C3 | §4 Q8(b) | says "first-argument" where it means the signal argument |
> | C4 | AC-11 | says "injected clock" for a branch no clock can reach |
> | C5 | §9 | a grep its own §4 Q6 makes unsatisfiable |
> | C6 | §4 Q5 | a table that presents itself as the full vocabulary, missing one code |
>
> **Two further facts were measured during QA and change the design rather than
> the wording.** They are set out in §12.1: the Job Object does **not**
> necessarily hold the tree — §1.6's inheritance assumption fails on the very
> host this spec was written on — and the stop therefore has to enumerate the
> tree rather than trust one counter, on the graceful path as well as the
> forced one.

**Issue**: #125 — "The agent cannot run a job on Windows: /bin/sh, os.killpg and SIGKILL are POSIX-only, and the stop event is never set"
**Labels**: `agent`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-15
**Baseline**: `dad884c` (`merge: agent job execution, discovery and submission`), branch `main`
**Measured on**: Windows 11, Python 3.12.14, mypy 1.0.0, ruff 0.5.0, pytest 8.0.0, `.venv\Scripts\python.exe` — the pinned toolchain `AGENTS.md` names

**This file is the authoritative input for the SWE cycle.** It is self-sufficient; §1 carries the
measurements, §4 carries the decisions, §6 carries the code shapes. Every number in §1 was measured
on the baseline commit with the commands shown, not taken from the issue.

---

## 0. Baseline, as re-measured by PM

| Gate | On `dad884c`, Windows, pinned toolchain |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `128 files already formatted` |
| `mypy .` | **5 errors in 2 files** (exit 1) — §1.1 |
| `pytest agent/tests -q --collect-only` | **271 tests collected** |
| `pytest agent/tests -q --ignore=agent/tests/test_loop.py` | **15 failed, 219 passed** in 8.4 s |
| `pytest agent/tests/test_loop.py` | **kills the pytest process** (exit 15) — issue **#124**, not run here |

The 15 failures are **pre-existing and unrelated to this issue**; they are the number a later AC
compares against. They are itemised in §1.7 so that "the count did not drop" means something.

---

## 1. Verified Problem Statement

### 1.1 The gate is red, for one more reason than the issue says

```console
$ .venv\Scripts\python.exe -m mypy .
agent\tests\conftest.py:234: error: Argument 1 to "schedule" of "BaseObserver" has incompatible type
    "Callable[[VarArg(Any)], None]"; expected "FileSystemEventHandler"  [arg-type]
agent\executor.py:349: error: Module has no attribute "sched_setaffinity"  [attr-defined]
agent\executor.py:386: error: Module has no attribute "killpg"  [attr-defined]
agent\executor.py:394: error: Module has no attribute "killpg"  [attr-defined]
agent\executor.py:394: error: Module has no attribute "SIGKILL"; maybe "SIGILL"?  [attr-defined]
backend\store\database_store.py:57: note: By default the bodies of untyped functions are not checked
Found 5 errors in 2 files (checked 89 source files)
```

The issue reports four errors in one file. There are **five, in two**. The fifth is
`agent/tests/conftest.py:234` — `real_watch_supported()` passes `lambda *_a: None` to
`observer.schedule`, which typeshed types as `FileSystemEventHandler`. It is platform-independent, so
it is red on Linux too, and **fixing `agent/executor.py` alone does not make `mypy .` green**. It is
in scope here: the issue's first acceptance criterion is "`mypy .` is clean on Windows and on Linux",
and a criterion nobody can satisfy is not a criterion. The fix is one line — a tiny
`FileSystemEventHandler` subclass instead of the lambda — and AC-3 pins both directions.

### 1.2 Every POSIX assumption in `agent/executor.py`, confirmed by running it

```console
$ .venv\Scripts\python.exe -c "import os, signal; print(hasattr(os,'sched_setaffinity'), hasattr(os,'killpg'), hasattr(signal,'SIGKILL'), signal.CTRL_BREAK_EVENT)"
False False False 1
```

`os.killpg` does not exist, `signal.SIGKILL` does not exist, `os.sched_setaffinity` does not exist,
`signal.CTRL_BREAK_EVENT == 1` does. So `executor.py:386` and `:394` raise `AttributeError` on
Windows if they are ever reached, and `:349` raises it on every non-Linux node — swallowed by the
`except (AttributeError, OSError)` arm at `:356`, which is the arm that hides the fact that
`resources.cpus` is silently unenforced (§4 Q6).

`/bin/sh` is not merely absent, it is **absent in a way that varies per machine**:

```console
$ python -c "import shutil; [print(n, '->', shutil.which(n)) for n in ('cmd.exe','/bin/sh','sh','bash','powershell.exe')]"
cmd.exe       -> C:\Windows\system32\cmd.exe
/bin/sh       -> None
sh            -> C:\Program Files\Git\bin\sh.EXE
bash          -> C:\Windows\system32\bash.EXE
powershell.exe -> C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
```

`shutil.which('/bin/sh')` is `None` on a machine that has Git Bash installed, because `/bin/sh` is a
POSIX path and not a Windows name. The issue is right that Git-Bash's `sh.exe` "is not the same
thing and is not on `PATH` by default" — **measured**: it is on `PATH` on this machine only because
Git for Windows put it there, and its `sh.exe` is an MSYS build with different path, quoting and
signal semantics from a Linux `/bin/sh`. A shell chosen by an accident of `PATH` is not a contract.

### 1.3 The single-string command-line form is mandatory, and the list form silently corrupts the command

This is the finding that decides Q1, and it is not in the issue. `subprocess.Popen` with an argument
**list** on Windows joins it with `subprocess.list2cmdline` before calling `CreateProcess`. That
implements the standard MSVCRT/`CommandLineToArgvW` quoting rules. `cmd.exe` does **not** use those
rules — it has its own parser. The two disagree exactly where a scientific command line is
interesting.

Given `command` = `"C:\Program Files\Git\bin\sh.exe" -c "exit 7"`:

| form | result |
|---|---|
| `Popen(["cmd.exe","/d","/s","/c", command])` | **exit 1** — `'"\\"C:\\Program Files\\Git\\bin\\sh.exe\\"" is not recognized…'` |
| `Popen(["cmd.exe","/d","/c",  command])` (no `/s`) | **exit 1** — same |
| `Popen('cmd.exe /d /s /c "%s"' % command)` | **exit 7** — the operator's own quotes survive |
| `Popen('cmd.exe /d /s /c ""%s""' % command)` | **exit 1** — do not double-wrap |

And the joining is observable before the process ever starts:

```console
>>> subprocess.list2cmdline(["cmd.exe","/d","/s","/c",'"C:\\Program Files\\Git\\bin\\sh.exe" -c "exit 7"'])
cmd.exe /d /s /c "\"C:\Program Files\Git\bin\sh.exe\" -c \"exit 7\""
```

`list2cmdline` backslash-escapes the operator's quotes; `cmd.exe` then reads a literal `\"`. **Every
`command` that quotes anything fails on Windows if it is passed as an argv list.** §4 Q1 is decided by
this measurement.

End-to-end smoke of the form this spec prescribes, with log capture through the existing
`BoundedLineWriter`:

```text
shell=cmd exit_code=3 in 0.03s
  stdout file: OUT-line
  stderr file: ERR-line
```

### 1.4 `cmd.exe` does not use exit 127 — so the `command_not_found` mapping cannot be ported

| command | `cmd.exe` exit | POSIX `sh` exit |
|---|---|---|
| `exit 5` | 5 | 5 |
| `definitely-not-real-xyz` | **1** (+ a **localized** message on stderr) | **127** |
| `Z:\nope\x.exe` | 1 (+ localized message) | 127 |
| `powershell.exe … nope-not-real-xyz` | **1** | 127 |

The stderr text on this machine is Russian. Three consequences, and they are the whole of Q5:

1. The child's **127** is a POSIX-shell convention with no Windows counterpart, so
   `exit_code == 127 → launch_failed:command_not_found` (`executor.py:416-418`) is a POSIX-only
   branch that can never fire on Windows.
2. Matching "command not found" on Windows by **message text** is not viable: the message is
   localized, and any pattern an agent matches is a false positive waiting for a locale it has not
   been shown.
3. `FileNotFoundError` out of `Popen` and exit 127 out of the child are the same number for two
   different faults, and the issue is right that they have different fixes — one is the agent's, one
   is the submitter's.

### 1.5 `CTRL_BREAK_EVENT` reaches the tree — and a child that ignores it survives forever

`CREATE_NEW_PROCESS_GROUP` + `send_signal(CTRL_BREAK_EVENT)`, a launcher → grandchild tree:

| launcher | after `CTRL_BREAK_EVENT` | grandchild |
|---|---|---|
| `python -c "…"` | leader exit **3221225786** (`0xC000013A`) | **dead** |
| `cmd.exe /d /s /c "python …"` (leader exit 3221225786) | | **dead** |
| same, child with `signal.signal(SIGBREAK, SIG_IGN)` | leader exit `None` (still running) | **alive** |
| …and a **second** `CTRL_BREAK_EVENT` | still running | **alive** |

So the polite tier works, reaches through `cmd.exe` to the grandchild (children inherit the group
unless they create their own), and is **purely cooperative** — `SetConsoleCtrlHandler`/`SIG_IGN` beats
it indefinitely. A forced second tier is mandatory, not a nicety.

### 1.6 A Job Object works, kills the whole tree, and still has a structural hole

Windows Job Object, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` +
`AssignProcessToJobObject(child)` + `TerminateJobObject(job, 1)`:

| scenario | job accounting before | after `CTRL_BREAK` (child ignores `SIGBREAK`) | after `TerminateJobObject` |
|---|---|---|---|
| direct child, `SIGBREAK` ignored | `total=2 active=2` | `active=2` | `active=0`, leader exit **1**, grandchild dead |
| `cmd.exe` launcher → python | `total=5 active=3` | `active=3` | `active=0`, leader exit **1**, grandchild dead |

`QueryInformationJobObject(JobObjectBasicAccountingInformation)` returns `ActiveProcesses`, which is
a **read-only** verification primitive — the only one that answers "is the tree gone" rather than
"did we send something". Two properties of it are load-bearing for the design:

- **`TerminateJobObject` is asynchronous in substance.** `ActiveProcesses` reads `0` on the very
  next query (measured median **0.0000 s**), but the leader's `proc.wait()` takes **≈0.30 s**. So
  "the job says zero" and "the leader is reaped" are two different facts and the spec needs both.
- **Assignment happens after creation, so there is a window.** `Popen` returns after
  `CreateProcess`; the agent can only assign afterwards. A child that spawns a descendant in that
  window is **outside the job**, and the job's accounting never saw it. The window is milliseconds
  and 13 measured runs here produced 0 escapes, so this is a **structural** residual rather than a
  reproduced failure — which is exactly why §4 Q2 pairs the job with an independent sweep instead of
  trusting one mechanism.

### 1.7 The current Windows test baseline — 15 failures that predate this issue

`pytest agent/tests -q --ignore=agent/tests/test_loop.py` → **15 failed, 219 passed, 271 collected**.

| count | file | cause | owner |
|---|---|---|---|
| 13 | `agent/tests/test_log_writer.py` | `PermissionError [WinError 32]` in `TemporaryDirectory` cleanup. The writer's handle is still open when the `with` block exits (its name is still a live local in the enclosing test function), and POSIX lets you unlink an open file and Windows does not. **The production `BoundedLineWriter.close()` is correct.** | new issue (§4.2 F2) |
| 1 | `agent/tests/test_claim.py::TestTryClaimJob::test_claim_marker_cleanup_on_failure` | the test's premise is `job_dir.chmod(0o555)`; POSIX mode bits do not deny writes on a Windows directory, so no failure is provoked and `reason == ""`. | new issue (§4.2 F2) |
| 1 | `agent/tests/test_hardware.py::test_a_reader_never_sees_a_truncated_node_file` | #126: `os.replace` → `PermissionError [WinError 5]`. | **#126** |

And the fifteenth, in `agent/tests/test_no_cluster_required.py`:
`test_agent_suite_passes_with_hostile_environment` shells out to `pytest agent/tests -q`, which
includes `test_loop.py`, which kills the runner. **That test is a victim of #124, not a cause**, and
it stays as it is.

### 1.8 Three defects this issue did not mention, all measured

**F-a — the `%%d`/`%%s` log line is worse than "the pid is never printed".** `logging` cannot format
the record at all, so nothing is logged *and* `logging` writes its own error to stderr:

```text
--- Logging error ---
Traceback (most recent call last):
  ...
  File "<string>", line 3, in <module>
  record.message = record.getMessage()
Message: 'set CPU affinity for job %s pid %%d: %%s'
Arguments: ('job-1', 4242, {0, 1, 2})
TypeError: not all arguments converted during string formatting
Call stack:
  <string> 5
```

So on **Linux** — the platform where `sched_setaffinity` succeeds and this line is reached — every job
with `resources.cpus > 0` emits a logging-error traceback instead of its affinity line. `logging`
swallows the `TypeError` (`Handler.handleError`), so nothing else breaks and nothing fails a test.

**F-b — `working_dir` is resolved against the agent's current working directory.** With the repo root
as CWD, on this host:

```text
'/sync/projects/new-run' -> 'E:\sync\projects\new-run'     is_absolute=True
'projects/new-run'       -> 'E:\AlZr\git\scientific-home-cluster\projects\new-run'
```

`normalize_path` (`shared/file_ops/path_utils.py:15-36`) calls `.resolve()` on any relative path, and
after that **the result reports `is_absolute() == True`**, so the resolved value cannot be told apart
from a genuinely absolute one. A job's working directory is therefore a function of where the
supervisor happened to start the process. This is issue **#58**'s class (CWD-relative discovery)
appearing in a second module, and it is why §4 Q3 branches on the **input**, before normalisation.

**F-c — the CPU-affinity block contains dead code.** `executor.py:279-284` is

```python
try:
    cpu_set = set(range(job_spec.resources.cpus))
    cpu_affinity_set = cpu_set
except Exception:
    pass  # Advisory only, will log if attempted
```

`set(range(n))` cannot raise. The `except Exception` arm is unreachable, and the comment describes a
behaviour the code does not implement.

### 1.9 The corrections that change the approach

| # | The issue says | Measured reality | Consequence |
|---|---|---|---|
| C1 | `mypy .` → 4 errors in `agent/executor.py` | **5 errors in 2 files**; the fifth is `agent/tests/conftest.py:234` | The "mypy clean" AC needs a second file. §4 Q7 |
| C2 | *"`%%d`/`%%s` … the pid and affinity set are never printed"* | The record cannot be formatted at all; `logging` prints a `--- Logging error ---` traceback and drops the line | The fix is the same but the symptom is bigger; §4 Q7, AC-8 |
| C3 | *"`execute_job` … has no `from __future__ import annotations` while using PEP 604 unions"* — implying a problem | On Python 3.12 those annotations **evaluate fine**; the module imports, mypy analyses it, the suite collects it. The `python_version = "3.11"` mypy pin and `requires-python = ">=3.11"` agree | Downgrade from "bug" to **convention break**. Still fixed, because every other shipped module has it (`loop.py:12`, `config.py:21`, `paths.py:10`, `supervisor.py:14`, `run_agent.py:13`) and the new signatures need it |
| C4 | (silent) — implicitly, that `os.kill(pid, 0)` is how you test whether a process survived | `os.kill(pid, 0)` on Python 3.12 **does not terminate** (measured: returns `None`, child still running after 1.5 s, `wait(2)` times out). But `os.kill(pid, 9)` **is** `TerminateProcess(handle, 9)` (measured: exit code 9). And `OpenProcess(SYNCHRONIZE)` is **not** a liveness probe — it returns a handle for a reaped pid | The test strategy must use **one** probe on both platforms: `psutil.pid_exists()`. §4 Q8, AC-13 |
| C5 | Open question 2: *"A Job Object is the correct primitive … `taskkill` is a command-line dependency that can be missing"* | The Job Object has an assignment race (§1.6). `taskkill /T /F` measured 0 survivors in 5/5 runs but left a grandchild alive in 1 of 5 in an earlier configuration | **Neither alone.** §4 Q2 |
| C6 | Open question 1: *"`cmd.exe /d /s /c` is the floor"* | Correct, and the reason is stronger than "matches what an operator types": the list form **corrupts the command** (§1.3). PowerShell needs its own escaping and also returns 1 for not-found | §4 Q1 |
| C7 | Open question 3: *"`working_dir` defaults to `/sync/projects/new-run`, an absolute POSIX path"* | On Windows it is **not** absolute and resolves against the **CWD** (F-b), becoming `E:\sync\projects\new-run` | §4 Q3 |

### 1.10 Three pre-existing gaps this work lands in the middle of

- **`agent/tests/test_executor.py` has no test that launches a process.** Its 13 tests cover
  `validate_and_prepare_working_dir`, `build_child_env` and `ExecutionResult`. Issue #98's AC-3
  ("a test executes a real child process"), AC-4 ("a test kills a launcher with a multi-process
  child still running") and AC-12 ("a long-running job does not block the agent's other duties")
  were never implemented. **The property this issue exists to make true has never had a test on any
  platform**, which is why the stop path went unnoticed.
- **#98's AC-2 is also unmet**: *"the execution model (shell) is documented in `docs/spec.md`"*.
  `docs/spec.md` contains no shell documentation — the only match for "shell" in it is React's
  `layout/Shell.tsx`.
- **`agent/tests/test_no_cluster_required.py:20-33` pins `SHIPPED_MODULES` to a fixed list**, and
  `test_shipped_modules_are_the_documented_set` asserts the shipped set equals it. **Adding any new
  module under `agent/` fails that test** unless the list is updated. §6.1.

---

## 2. Root Cause

`agent/executor.py` was written against one platform and never parameterised. Four POSIX-only
assumptions are embedded in the launch and stop path (`/bin/sh`, `start_new_session`, `killpg`,
`SIGKILL`), one is embedded in the resource path (`sched_setaffinity`), and the platform-specific
Windows answers — a different shell, a different command-line convention, a different process-group
primitive, a different liveness probe — were never chosen.

Separately, and independently of platform, the stop path is **unreachable**: `Agent._spawn_execution_task`
(`agent/loop.py:508`) creates a local `asyncio.Event` that is captured by `execution_wrapper` and by
nothing else. `Agent._stop` is a different event consumed by the supervisor (`loop.py:96-98`) and
never passed down; `_hand_back_in_flight_job` reaches `loop.py:550-552`, whose comment says *"The
execution task checks the agent's stop event"* and whose body is `pass`. So on **every** platform
`execute_job` polls `stop_event.is_set()` at `:382` on an event nothing ever sets, `_shutdown_sequence`
waits `SHUTDOWN_GRACE_S` at `:615`, times out, logs *"execution task did not complete within grace
period"*, and exits — leaving the child running, holding the working directory and the GPUs, writing
into a job directory the agent no longer owns.

---

## 3. The stop event, exactly as it is today

This is the shape §6 has to change, and it is worth drawing once:

```
Agent.__init__            self._stop: asyncio.Event | None        # :66  (set in run() at :94-95)
  |
  +--> TaskSupervisor(stop_event=self._stop)                       # :97   supervisor + run() + signals
  |
  +--> _shutdown_sequence: stop.set()                             # :600  wakes every supervised task
        |
        +--> supervisor.shutdown(grace)                           # :604  waits SHUTDOWN_GRACE_S
        +--> await wait_for(self._execution_task, grace)           # :615  waits SHUTDOWN_GRACE_S AGAIN
        +--> _hand_back_in_flight_job()                            # :619  -> loop.py:550-552 `pass`
              |
              (nothing)                            # nothing reaches the process
```

And the dead branch that results, `agent/executor.py:379-404`:

```python
while exit_code is None:
    if stop_event.is_set():          # never True: local event at loop.py:508, no setter exists
        os.killpg(proc.pid, signal.SIGTERM)      # :386  AttributeError on Windows
        await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=5.0)
        os.killpg(proc.pid, signal.SIGKILL)      # :394  AttributeError on Windows
    exit_code = proc.poll()
    await asyncio.sleep(0.1)         # :404  100 ms polling
```

---

## 4. Open Questions — Resolved

The issue left four. Each is decided here, with the measurement that decided it. Six more that the
analysis raised are Q5–Q10.

### Q1 — Which shell on Windows? **`cmd.exe /d /s /c`, assembled as a single command-line string.**

**Decision.** The shell is a **setting** (`AGENT_SHELL`), defaulting to `"auto"`, which means
"platform default": `cmd.exe /d /s /c` on Windows, `/bin/sh -c` everywhere else. The accepted values
are `auto`, `sh`, `cmd`, `pwsh`, or a path whose basename is one of `sh`, `dash`, `bash`, `cmd`,
`powershell`, `pwsh`. Anything else is a `ConfigError` listing them.

**Why `cmd.exe`, in order of weight.**

1. **It is the only Windows shell whose semantics an operator already has.** A submitted `command`
   containing `%VAR%`, `&&`, `||`, `> file` and `cd /d` is the shape of every batch file anyone
   writing for a Windows box has written. Choosing PowerShell silently changes what that string
   means: `%VAR%` stops expanding (`-Command` uses `$env:VAR`), `&&` is a pipeline-chain operator in
   PowerShell 7 but not 5.1, and redirection behaves differently. The issue's open question calls
   this "the single most consequential choice here" and it is right.
2. **It costs nothing.** `cmd.exe` is `C:\Windows\System32\cmd.exe`, resolved `shutil.which` →
   non-`None` on every Windows installation. PowerShell 5.1 is also present, but `pwsh.exe`
   (PowerShell 7) is **not**, and it is the one people mean when they say PowerShell.
3. **Its output is exit-code-shaped and the exit code propagates** — measured: `exit 5` → 5 through
   `cmd.exe /d /s /c`.

**Why Git Bash's `sh.exe` is rejected**, which is the same question asked differently: it is
present only if someone installed Git, it is an MSYS build with different path translation
(`/c/Users/...` vs `C:\Users\...`), different signal handling, and different `kill` semantics, and a
shell chosen by an accident of `PATH` is not a contract an operator can rely on. It remains
**selectable by name** (`AGENT_SHELL = "bash"`), which is the difference between "not the default" and
"not supported".

**The quoting contract, stated once and exactly:**

> `JobSpec.command` is a **shell command line**. The agent assembles
> `[shell program] + [fixed flags] + [command]` and hands it to the operating system **verbatim**. It
> performs no `shlex.split`, no re-quoting, no re-escaping and no environment substitution. The
> configured shell then parses the `command` exactly as it would if the operator had typed it at a
> prompt. **If it does not work when typed at a prompt, it does not work here.**

**Flags are not configurable — only the program is.** That is deliberate: a configurable flag set is
how a quoting contract gets broken, and there is exactly one correct flag set per shell
(`/c`, `/d /s /c`, `-NoProfile -NonInteractive -Command`). Recorded as D4.

**The Windows assembly rule, and why it is not a platform branch but a parser property.** On POSIX,
`Popen([prog, "-c", command])` is exact. On Windows, `CreateProcess` takes **one** command line, and
Python composes it from a list with `list2cmdline`, which implements the CRT quoting rules that
`cmd.exe` and `powershell.exe` do not use (§1.3). So:

| configured | argv | assembled command line |
|---|---|---|
| `sh`, `dash`, `bash` | `[prog, "-c", command]` | list — CRT quoting, which these programs *do* use, so it is correct even on Windows |
| `cmd` | — | `cmd.exe /d /s /c "<command>"` |
| `pwsh` | — | `pwsh.exe -NoProfile -NonInteractive -Command "<command>"` |

`/d` disables AutoRun registry commands, which is the documented switch for unattended invocation.
`/s` triggers cmd's "strip the first and last quote and take the rest literally" rule, which is what
makes the inner quoting survive (§1.3). `/NoProfile` and `/NonInteractive` are the PowerShell
equivalents of "do not read a profile, do not prompt".

**`%ComSpec%` is deliberately not consulted.** It is a per-process environment override with no
guarantee that it names a real shell, while `PATH` is what the submitted `command` itself resolves
against. The shell and the command must be found the same way or a job can be launched by a shell
its own command could not have found. Recorded as D5.

### Q2 — How is "stop this tree" done on each platform? **Two tiers each, plus an independent sweep on Windows, and the result is *verified*.**

The POSIX design from #98 is kept and the Windows design is made its exact mirror, tier for tier.

| tier | POSIX (unchanged) | Windows (new) |
|---|---|---|
| **own the tree at launch** | `start_new_session=True` → `setsid` → new process group whose pgid is `proc.pid` | `creationflags=CREATE_NEW_PROCESS_GROUP`, **and** assign `proc.pid` to a Job Object created with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` |
| **remember the tree** | not needed: `killpg` addresses the group by pgid and keeps working after the leader is reaped | **[corrected, §12.1]** at the start of **each** tier, enumerate the live descendants of the launcher and assign each to the job. A launcher that breaks away from its job is a property of launchers, not a window (§1.6) |
| **polite request** | `os.killpg(proc.pid, SIGTERM)` | `proc.send_signal(signal.CTRL_BREAK_EVENT)` → `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pgid)` |
| **forced** | `os.killpg(proc.pid, SIGKILL)` | **[corrected, §12.1]** `taskkill /T /F /PID <pid>` **first**, then `TerminateJobObject(job, 1)` — the order in the original table makes the sweep inert, because `/T` walks from the launcher and the job kills the launcher |
| **verify** | `proc.wait()` returns within `STOP_VERIFY_TIMEOUT_S`, after the forced tier | **[corrected, §12.1]** `ActiveProcesses == 0` **and** every remembered pid gone **and** `proc.wait()` returns — and the verification runs after the **graceful** tier as well, so "the launcher exited" is never taken as "the tree stopped" |
| **a process that ignores the polite request** | survives `SIGTERM`, dies on `SIGKILL` | survives `CTRL_BREAK_EVENT` (§1.5), dies on the forced tier |
| **the agent itself is killed** | the session survives the agent (orphaned) | **the job kills the tree**: `KILL_ON_JOB_CLOSE` fires when the last handle closes, which the OS does when the agent dies |

**Why the Job Object alone is not enough** — measured, §1.6, and then measured harder: `AssignProcessToJobObject` runs *after* `CreateProcess` returned, so a descendant created in that window is never in the job, and the job's accounting never counts it. **[corrected, §12.1]** the original reading of that measurement — that the window is milliseconds and 13 runs produced no escape — does not hold on the host this spec was written on. A `uv` venv launcher creates its child with `CREATE_BREAKAWAY_FROM_JOB`, so every descendant past it is outside the job *permanently*: `ActiveProcesses` reads 1 with four live descendants, `TerminateJobObject` kills the launcher, and the grandchild survives. **Why `taskkill` alone is not enough**: it is an external binary (the issue's own objection — a stripped `PATH` loses the forced tier, which is the "command not
found" failure this whole issue exists to eliminate), and `/T` walks parent/child links at kill time
rather than being enforced by the kernel at creation time. **Two mechanisms, each covering the
other's hole, is the answer**, and it is the one place where this spec overrides the issue's stated
preference (C5). It also costs one `subprocess.run` on a path that only runs during shutdown.

**Verification, not assumption — the central requirement.** "We sent a signal" is not "the tree is
gone", and the two differ in both platforms:

- **Windows.** `TerminateJobObject` flips `ActiveProcesses` to `0` on the next query (measured
  median 0.0000 s) while the leader's `proc.wait()` takes **≈0.30 s**. So the job's accounting answers
  *"is anything still running in this tree?"* and `proc.wait()` answers *"is the leader reaped?"* —
  and **both** must be observed. The wait is a bounded poll on the *property*
  (`ActiveProcesses == 0`), not a fixed sleep: poll every 25 ms, give up after `STOP_VERIFY_TIMEOUT_S`,
  and on give-up write `stop_failed:tree_survived` rather than report success. **Verified by the
  type-checker-free accounting API, not by assuming the call worked.**
- **POSIX.** There is no portable "how many processes are in this group", so the *production* check is
  bounded: after the forced tier, `proc.wait()` must return within `STOP_VERIFY_TIMEOUT_S`. The
  **test**-level check is stronger and uses what a test can see — each child appends its pid to a
  file, and the test asserts every recorded pid is gone via `psutil.pid_exists`. Recorded rather than
  papered over: POSIX production code cannot prove the *whole* group is gone, and this spec does not
  pretend otherwise.

**If the Job Object cannot be created or assigned** (no `PROCESS_SET_QUOTA`, a host where nested jobs
are unavailable): log **WARNING** `process tree control: job object unavailable (winerror N); falling
back to taskkill`, record the fallback in the job's log line, and let the forced tier be
`taskkill /T /F`. If `taskkill.exe` does not resolve either, the forced tier is `TerminateProcess` on
the leader alone and the log says at **ERROR** that the **tree** may have survived. The failure is
loud rather than assumed-away. AC-12 covers it by making the job object unavailable.

### Q3 — What does `working_dir` mean, and what about the default? **Two branches on the raw input, and the default's residual is named.**

**Decision: a two-branch rule, decided on the raw string before any normalisation.**

```
resolve_working_dir(raw: str, syncthing_root: Path) -> Path
    p = Path(raw).expanduser()
    if p.is_absolute():                       # the host decides what "absolute" means
        return p
    if p.anchor:                              # [corrected, §12.2] rooted but not absolute
        p = p.relative_to(p.anchor)           # on Windows: "/sync" has a root, no drive
    return syncthing_root / p                 # NEVER the current working directory
```

> **[corrected, §12.2]** The two lines above are the pseudocode as written, and
> on Windows they produce `C:\sync\projects\new-run` — **outside** the Syncthing
> root, on whatever drive the root happens to be on — because a Windows path
> like `/sync/...` is *rooted but not absolute*: `is_absolute()` is `False`,
> and `pathlib` treats a rooted right-hand operand as replacing everything to
> its left. The table below is right; the pseudocode needed the anchor
> stripped first. This is the default `working_dir`, so the defect defeats
> this spec's own table.

- **Absolute on this host** → used as-is. The agent creates it if it is inside the Syncthing root,
  and requires it to exist otherwise — exactly today's `validate_and_prepare_working_dir` contract.
- **Not absolute on this host** → resolved against `SYNCTHING_ROOT`. This is the new, portable
  capability the issue's open question 3 asks for, and it removes the CWD dependency (F-b).

The branch is on `Path(raw).is_absolute()`, **not** on the normalised path, because after
`normalize_path` the result reports `is_absolute() == True` either way (measured, F-b) and the
distinction is destroyed. There is **no platform-specific special case**: `os.path.isabs` already
encodes the platform, which is why this is two branches and not three.

**The default, decided.** `JobSpec.working_dir`'s default stays `/sync/projects/new-run` **in this
issue**, and what it *means* is now:

| host | the default resolves to | outcome |
|---|---|---|
| Windows | `<SYNCTHING_ROOT>/sync/projects/new-run` | inside the root → **created → the job launches** |
| Linux/macOS | the literal `/sync/projects/new-run` | outside the root, does not exist → `launch_failed:working_dir_not_found` (today's behaviour, unchanged) |

**Why the default is not changed here.** Changing it touches a contract, and the blast radius is
measured, not guessed:

| file | line | what |
|---|---|---|
| `shared/schemas/job_spec.py` | 18 | the default itself |
| `openapi.yaml` | 1251 | the documented default — the snapshot would need regenerating |
| `backend/tests/unit/test_shared_schemas.py` | 190 | asserts the default |
| `backend/tests/unit/test_api_contract.py` | 41 | asserts the default |
| `backend/store/database_store.py` | 113 | the demo seeder writes `/sync/projects/{name}` |
| **`frontend/src/lib/profiles.ts`** | 284 | **a different repository** |
| **`frontend/src/routes/jobs.new.tsx`** | 88 | **a different repository** — this is what the dashboard's submit button sends |

The last two are in the `rendering-replicate` submodule. A contract change plus a cross-repository
commit is its own issue; bundling it here would put a frontend submodule commit inside an
agent-platformability issue. **Filed as a follow-up (§4.2 F1) with the recommended value**, and the
follow-up also fixes a defect the default has today and this issue does not: **`/sync/projects/new-run`
is a fixed literal, so two default-`working_dir` jobs run in the same directory and collide.**
The recommended replacement is `""` meaning *"derive it"*, with the derivation done in the executor
(`<root>/projects/<job_id>`), following the `AGENT_STATE_DIR` precedent (`agent/config.py:392`) where
a root-derived default is computed at the construction site rather than frozen in a schema.

### Q4 — Who owns cancel? **Mechanism only. The operator-facing protocol is #99's and #106's.**

This issue makes the stop **reachable** and **portable** and **verifiable**. It decides the
ownership of the event object and the OS mechanics of the stop. It does **not** decide how an
operator asks for one.

| | **#125 (this issue)** | **#99** / **#106** |
|---|---|---|
| decides | who creates the stop event, who sets it, what a shutdown stop does to the files, the tier order, the timings, the verification, the error codes | who may ask for a stop (API, CLI, command file), the ack, the idempotency record, the `CANCELLED` status, `cancelled:by_operator` |
| asserts | the process tree is gone within a bounded time on both platforms, from a real multi-process child | that a cancel *reaches* the agent, and that the agent's response to **that** verb is distinguishable from a shutdown |

**The seam that keeps them apart is the stop *reason*.** `stop_event` is a single `asyncio.Event`
that will have three setters: shutdown (here), a cancel command (#106), and a future operator
protocol (#99). Two of them must not produce the same terminal answer, and #106 §5.7 already
established why — *"Overloading `stop_event.is_set()` would report 'the operator cancelled this' for
a job the operator never touched."* This issue therefore introduces the **reason carrier** and uses
it for exactly one value:

```python
StopReason = Literal["shutdown"]        # this issue. Closed, because only one value is reachable.
```

and records that #99/#106 extend it to `Literal["shutdown", "cancel"]` with `cancel_reason`, which
#106 §5.7 already specifies as the `Callable[[], str | None]` parameter. **This issue does not add
that parameter** — adding a hook nothing can call is speculative, and #106 owns it.

### Q5 — Exit-code semantics: what are the distinct outcomes, and how do they reach `state.yaml`?

**Decision: the agent distinguishes the failure classes it can actually observe, and `error` carries
the provenance while `exit_code` carries the number.** The full table, which is the deliverable:

| code | `exit_code` | written when | attribution | new here? |
|---|---|---|---|---|
| `launch_failed:working_dir_not_found` | `1` | pre-launch, outside the root, does not exist | submitter | existing |
| `launch_failed:working_dir_create_failed: <e>` | `1` | pre-launch, inside the root, `mkdir` failed | host | existing |
| `launch_failed:input_missing` | `1` | pre-launch, `paths.input` absent | submitter | existing |
| `launch_failed:insufficient_gpus` | `1` | pre-launch, fewer GPUs than requested | scheduling | existing |
| **`launch_failed:shell_not_found`** | **`127`** | **pre-launch, `shutil.which` returned `None`, or `Popen` raised `FileNotFoundError`** | **the AGENT** | **new** |
| `launch_failed:process_creation_failed: <e>` | `1` | pre-launch, `Popen` raised something else (a bad `cwd`, a denied handle, a resource limit) | the host | **in the code before this issue, and missing from this table [corrected, C6]** |
| **`launch_failed:command_not_posix_not_found`** → see below | | | | |
| `launch_failed:command_not_found` | `127` | the child exited **127**, which only a POSIX shell reports (§1.4) | submitter | existing, **now POSIX-only** |
| `process_failed:exit_code=<N>` | `N` | the child ran and exited non-zero otherwise | submitter | existing |
| **`stopped:shutdown`** | **the real wait status, or `None`** | the agent stopped the tree (§4 Q7) | the AGENT | **new** |
| **`stop_failed:tree_survived`** | the real wait status | the forced tier ran but verification timed out | the AGENT | **new** |
| `cancelled:by_operator` | the real wait status | **not written by this issue** — #99/#106 | operator | owned elsewhere |

**The rule that separates "the agent has no shell" from "your command does not exist"**, and it is
three things working together:

1. **The shell is resolved before launch, by `shutil.which`.** `launch_failed:shell_not_found` is
   produced *without ever calling `Popen`*, so it cannot be confused with anything the child reported.
   It logs at **ERROR** on the agent (an agent fault: the machine is misconfigured) and names the
   configured shell and the `PATH` searched.
2. **The child's 127 keeps its own code and its own level.** `launch_failed:command_not_found` logs at
   **INFO** and is the submitter's typo. Same number, different code, different log level, different
   fix.
3. **On Windows the child's 127 never happens** (`cmd.exe` and `pwsh` both return 1, §1.4), so a
   Windows typo is reported honestly as `process_failed:exit_code=1` with the shell's message in the
   stderr log file, and the spec says so rather than inventing a locale-dependent matcher.

**`exit_code` stays the raw wait status, never a sentinel.** `127` for `shell_not_found` keeps the
POSIX "command could not be found" meaning at the job level — the job did not start — while `error`
says *which* of the two it was. That is the field #100 Q3 designed `error` for ("a documented
vocabulary of machine-readable codes"), and it avoids inventing a number that collides with
`-1` (the backend's cancel sentinel, `job_service.py:339`) or with `0xC000013A` (what a Windows
process killed by `CTRL_BREAK_EVENT` actually returns, measured 3221225786). #106 §5.7 already ruled
on this: *"`exit_code` on that write is the **real** wait status … It is **not** forced to `-1`."*

### Q6 — `resources.cpus`: enforced portably, or advisory? **Advisory everywhere, enforced where the OS can, and *always reported* at INFO.**

**Decision.** There is no portable CPU-pinning primitive. `os.sched_setaffinity` is Linux-only
(measured absent on Windows); `psutil.Process.cpu_affinity()` covers Linux and Windows but is a
*per-process* affinity set with different semantics per platform and adds a dependency on a
third-party module from the launch path. **The honest answer is that `resources.cpus` is advisory,
and an advisory field that reports itself as advisory is fine; one that hides behind a DEBUG line is
not.** So:

| platform | behaviour | log |
|---|---|---|
| Linux | `sched_setaffinity(pid, set(range(n)))`, best-effort | **INFO** `pinned job X to CPUs {0..n}` |
| everything else | capability probed with `getattr(os, "sched_setaffinity", None)`; absent ⇒ no-op | **INFO** `resources.cpus=<n> is advisory and not enforceable on this platform; the job sees all <cpu_count()>` |

Three changes carry this:

1. **The capability is probed, not swallowed.** `getattr(os, "sched_setaffinity", None)` replaces the
   bare attribute reference — which is *also* the mypy fix for `:349`, with **no `# type: ignore`**
   and no `pyproject.toml` override.
2. **The dead `try/except Exception: pass` block (F-c) is deleted**, replaced by the probe.
3. **`resources.cpus > os.cpu_count()` is a distinct outcome**, not an `OSError` swallowed at DEBUG:
   log **WARNING** `resources.cpus=<n> exceeds this node's <cpu_count()> CPUs; pinning to all of
   them` and pin to what exists. Today `sched_setaffinity` would raise `EINVAL` into the swallowed
   arm.

`memory_gb` and `vram_gb` stay unenforced (unchanged from #98 §"Resource enforcement"), and the
README states it once. **Decision rationale recorded:** the alternative — silently doing nothing —
is the defect; the alternative — a container or a cgroup — is #98 Q2's decision, already made against
Phase 2.

### Q7 — The two genuine bugs. **Both fixed, both tested, and one is bigger than reported.**

**Bug 1 — `agent/executor.py:350-355`.** `"set CPU affinity for job %s pid %%d: %%s"` with three
arguments raises `TypeError: not all arguments converted during string formatting` inside
`logging`. Measured consequence (F-a): on Linux, where this line is reached, the record is **dropped**
and a `--- Logging error ---` traceback is written to stderr for every job with `cpus > 0`.
Fix: `"set CPU affinity for job %s pid %d: %s"`. **The test must assert the rendered line**, not the
absence of an exception — `caplog` on the record's `getMessage()`, asserting the pid and the CPU set
appear. A test that only asserts "no exception was raised" would pass today, because `logging`
swallows it.

**Bug 2 — `agent/executor.py:9`, the missing `from __future__ import annotations`.** Corrected in C3:
this is **not** a runtime failure on the pinned interpreter, it is a break of a convention every other
shipped module follows. It is still fixed here, for two reasons that are about *this* change: the new
signatures in §6.1 and §6.3 use `X | Y` and forward references in positions the module will grow into,
and a file whose annotations are lazy is a file mypy can reason about across a refactor that moves a
class. It is also free: `from typing import Any, Optional` stays used, `import signal` at `:12`
becomes unused and **must be removed** or `ruff` F401 fires.

**Plus C1's fifth mypy error** — `agent/tests/conftest.py:234`, the `lambda *_a: None` passed to
`observer.schedule`. One line, and without it the issue's first AC is unachievable.

### Q8 — Test strategy that works on both platforms.

The three rules, each forced by a measurement:

**(a) The child is `sys.executable`, never a shell built-in.** `echo hello` is a `cmd` built-in and an
`sh` built-in with different escape processing; `python -c "…"` is one program on both. A submitted
command is built as:

```python
f'"{sys.executable}" -c "{program}"'      # sys.executable is QUOTED
```

quoted because a Windows install under `C:\Program Files\Python312\` contains a space, and quoting is
exactly what the single-string form (§1.3) exists to preserve. Measured: this form runs and returns
the child's real exit code (§1.3, end-to-end).

**(b) Liveness is `psutil.pid_exists()`, on every platform, in every test.** Measured (C4):

| probe | POSIX | Windows | verdict |
|---|---|---|---|
| `os.kill(pid, 0)` | works | works on 3.12 (returns `None`, child survives) | works but the *sibling* `os.kill(pid, 9)` is `TerminateProcess` |
| `os.kill(pid, 9)` | `SIGKILL` | **`TerminateProcess(handle, 9)`** | catastrophic on Windows |
| `OpenProcess(SYNCHRONIZE)` | n/a | **returns a handle for a reaped pid** | not a probe |
| **`psutil.pid_exists(pid)`** | works | works (measured `False` after reap) | **the one** |

`psutil` is already a declared dependency and already on `THIRD_PARTY_ALLOWED`
(`test_no_cluster_required.py:60`) and used by `agent/hardware.py`, so this adds no surface. AC-13
pins the rule with an AST guard over `agent/tests/**`: **no `os.kill` call whose signal argument is
anything but a literal `0`**, and no `os.killpg` at all, because #124 is exactly that mistake.

> **[corrected, C3]** This paragraph said "a non-zero constant first-argument
> argument". Two errors in one clause. The *signal* is the **second** argument
> — the first is the pid — and a **constant** test misses the shape #124
> actually has: `sig = signal.SIGTERM; os.kill(os.getpid(), sig)`, which is
> `agent/tests/test_loop.py:144` verbatim and kills this host's pytest with
> exit 15. The shipped guard flags every signal argument that is not a literal
> `0`, plus `killpg`, and the only file it exempts is `test_loop.py` itself,
> with an assertion that fails when #124 removes the exemption.

**(c) Both platform arms are expressed, in both directions.** A Windows-only feature that is merely
`if sys.platform == "win32":`-ed out on Linux is never exercised anywhere, which is how the current
code got here. Required:

| arm | runs on | skipped on | skip reason string must name |
|---|---|---|---|
| `CTRL_BREAK_EVENT` + `CREATE_NEW_PROCESS_GROUP` | Windows | POSIX | "no process groups or console control events on POSIX" |
| Job Object + `TerminateJobObject` + accounting | Windows | POSIX | "Job Objects are a Windows facility" |
| `killpg`/`SIGTERM`/`SIGKILL` to a process group | POSIX | Windows | "no POSIX process groups on Windows" |
| `sched_setaffinity` pinning | Linux | else | "sched_setaffinity is Linux-only" |
| **launch + capture + terminal state** | **both** | — | never skipped |
| **stop a real 3-process tree, assert 0 survivors** | **both** | — | never skipped |

`sys.platform == "win32"` is the predicate, matching `agent/tests/conftest.py:214`. Both directions
present means the module's Windows half is typed-checked and imported on Linux (mypy still analyses it
inside `if sys.platform == "win32":`) while its POSIX half is reachable on Windows for the
`single_string=False` row of §4 Q1's table.

**(d) The tree must be a real tree.** A shell launcher → a python parent → a python grandchild, each
appending its pid to a file under `tmp_path`, so "the tree is gone" is `psutil.pid_exists()` over
**three** pids, not "the launcher's `poll()` is not `None`". The last one is the assertion that
distinguishes a working implementation from a launcher-only kill, and it is the one #98 AC-4 asked
for and never got.

### Q9 — Wait primitive, escalation timings, and the shutdown-grace interaction.

**The wait primitive** replaces the 100 ms poll at `executor.py:380-404`:

```python
exit_waiter = asyncio.create_task(asyncio.to_thread(proc.wait), name=f"exit-{job_id}")
stop_waiter = asyncio.create_task(stop_event.wait(),      name=f"stop-{job_id}")
try:
    await asyncio.wait({exit_waiter, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
finally:
    for t in (exit_waiter, stop_waiter):
        t.cancel()
```

- **Why not `proc.poll()` in a loop**: it wakes the event loop 10×/s for the lifetime of every job,
  and it turns the 5 s escalation into a race between "the poll noticed" and "the timer fired". A
  wait on **both** conditions removes the poll and makes the escalation a `wait_for` with a deadline.
- **Why `subprocess.Popen` and not `asyncio.create_subprocess_exec`**: the executor needs `proc.pid`,
  `start_new_session`/`creationflags` and the Windows job handle, all of which are `Popen`'s surface;
  and `asyncio.to_thread(proc.wait)` works on **every** event loop, whereas subprocess support is a
  property of the loop class (`ThreadedChildWatcher` vs `ProactorEventLoop`) that the agent already
  fights over when `add_signal_handler` forces a Selector loop on POSIX. **One code path, both
  platforms.** Recorded as D7.
- **`to_thread` and the default executor**: one waiter per agent, so the default executor's
  `min(32, cpu+4)` threads is never contended. Recorded in the risk table rather than designed around.
- **Cancellation is not the same as the stop event.** `Task.cancel()` raises `CancelledError` where the
  wait is parked. `execute_job` catches it, runs the **forced tier immediately with no grace** (a
  cancellation is already somebody's decision that this must stop now), and re-raises so the
  cancellation still propagates. A cancelled execution task that leaves a tree behind is the defect
  this issue exists to remove, and it must not be reintroduced by the plumbing.

**The escalation timings**, as module constants in `agent/process.py`:

| constant | value | why this value |
|---|---|---|
| `STOP_GRACE_S` | `5.0` | **the value already hard-coded** at `executor.py:391`. Preserving it keeps #99's measured budget honest: #106 §Q10 measured *"5-process tree that traps `TERM` → 5.004 s … leader exit -9, 0 survivors"*, and B2 there is *"≤ 10 s from the command file being written to the process group being gone"*. Changing it here would invalidate that measurement for no reason |
| `STOP_VERIFY_TIMEOUT_S` | `5.0` | bounded verification after the forced tier. On Windows the forced tier is instantaneous (§1.6) and the leader reaps in **≈0.30 s**; on POSIX the forced tier *is* `SIGKILL` and the wait is microseconds. 5 s is generous and keeps `STOP_GRACE_S + STOP_VERIFY_TIMEOUT_S` ≪ `SHUTDOWN_GRACE_S` |
| `STOP_VERIFY_POLL_S` | `0.025` | the accounting query's cost is negligible; 25 ms bounds the overshoot on a 5 s deadline to 2% |
| `SHUTDOWN_GRACE_S` | `30.0` | **unchanged** (`agent/config.py:123`). `10 s < 30 s` is the invariant AC-15 asserts, and it is why the default never expires |

**The shutdown-grace interaction — two concrete defects in `_shutdown_sequence` today:**

```python
grace = self._settings.SHUTDOWN_GRACE_S
failures = await supervisor.shutdown(grace)          # :604   waits up to 30 s
...
await asyncio.wait_for(self._execution_task, timeout=grace)   # :615   waits ANOTHER 30 s
```

1. **The grace is spent twice.** A `30 s` budget becomes up to `60 s`. Fix: compute
   `deadline = loop.time() + self._settings.SHUTDOWN_GRACE_S` **once** at the top and derive both
   remaining waits from it, so the documented number is the real bound.
2. **The stop event is set but never reaches the process.** `stop.set()` at `:600` wakes the
   supervised tasks; nothing sets the *job's* event. Fix, in this order: **set the job stop event
   before `supervisor.shutdown`**, so the tree is already dying while the supervisor's periodic tasks
   are winding down, and the grace clock is spent on the thing that actually needs it.

After this change a shutdown with a job in flight completes in roughly
`STOP_GRACE_S + reaping`, well inside `SHUTDOWN_GRACE_S`, and `supervisor.grace_expired` means what it
says.

### Q10 — What is observable in the files afterwards?

| | after this issue |
|---|---|
| `jobs/<id>/state.yaml` | `status: FAILED`, `error: stopped:shutdown`, `exit_code:` the **real** wait status (`-15` after `SIGTERM`, `3221225786` after `CTRL_BREAK_EVENT`, `1` after `TerminateJobObject(job, 1)`), `completed_at` set, `node_id` unchanged. Or `error: stop_failed:tree_survived` when verification timed out. Or `exit_code: null` when the stop arrived before launch |
| `nodes/<node_id>.yaml` | `current_job_id: null` (already the case; `execution_wrapper`'s `finally`, `loop.py:524-535`, runs after the executor returns) |
| the agent log | `stopping job X: grace 5.0s`, then `job X stopped after the graceful request` **or** `job X required the forced stop`, then `verified: 0 processes remain` **or** `job X: the tree still has N processes after 5.0s` |

**Why a terminal state is written at all on a shutdown stop, and where the boundary with #103 is.**
Writing nothing would leave `state.yaml: RUNNING` with no process — which is *verbatim* the state #103
exists to detect and repair (#103's problem statement: *"a job stays `RUNNING` on a node that has no
process attached"*, and its approach item 6: *"Make shutdown and crash distinguishable where
possible"*). A clean shutdown that writes `FAILED`/`stopped:shutdown` and a crash that writes nothing
are therefore **distinguishable**, which is #103's AC-6 and is free here.

**The reservation that is honoured:** #106 §5.7 recorded that *"agent shutdown / hand-back (#103) —
the job must return to being claimable — `PENDING`, `node_id: null` — which is #103's decision to
make"*, and that a stop without a reason "logs it and writes nothing, which is #103's seam and not
this issue's". This spec **does not overrule** that; it makes the seam a single named branch so
#103's change is one edit, and it states the consequence of the interim choice honestly: a
shutdown-stopped job is `FAILED`, not re-claimable, and **the operator must `POST /jobs/{id}/retry`**.
#103 may change that to `PENDING` without touching anything else, because the stop reason is
recorded separately from the status (§4 Q4).

---

## 5. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **`cmd.exe /d /s /c` on Windows, `/bin/sh -c` on POSIX, chosen by the `AGENT_SHELL` setting** | The list-argv form corrupts the command (§1.3). `cmd`'s semantics are the ones an operator already has, it is present everywhere, and choosing PowerShell silently redefines `command`. §4 Q1 |
| D2 | **The command line is assembled as one string on Windows** | `list2cmdline` implements CRT quoting; `cmd.exe` and `pwsh` do not use it (§1.3). The property is *the shell's parser*, not *the platform*, so Git-bash `sh.exe` still gets the correct list form |
| D3 | **The stop event is `Agent._job_stop`, created per job, set by shutdown and hand-back, cleared in the execution wrapper's `finally`** | It is the only design in which `execute_job` can be reached by anything other than a shutdown without threading a new parameter through `TaskSupervisor`. §6.3 |
| D4 | **Only the shell *program* is configurable; the flags are a fixed table** | A configurable flag set is how a quoting contract gets broken, and there is exactly one correct set per shell |
| D5 | **Resolve the shell with `shutil.which`, never `%ComSpec%`** | `PATH` is what the submitted `command` itself resolves against; the shell and the command must be found the same way (§4 Q1) |
| D6 | **Windows tree-stop is Job Object *and* `taskkill /T /F`; both are required** | The job has an assignment window (§1.6); `taskkill` is an external binary the issue itself objects to depending on. Each covers the other's hole, and only the combination is a claim of "the tree is gone" |
| D7 | **Keep `subprocess.Popen`; wait with `asyncio.wait` over `to_thread(proc.wait)` and `stop_event.wait()`** | Portable on every event loop, and the executor needs `Popen`'s pid/flags/handle surface anyway (§4 Q9) |
| D8 | **`exit_code` is always the real wait status, never a sentinel; `error` carries the provenance** | #106 §5.7 already ruled it; `-1` and `0xC000013A` both already mean something else |
| D9 | **`resources.cpus` is advisory and says so at INFO** | No portable primitive exists. The defect is the silence, not the absence (§4 Q6) |
| D10 | **A shutdown stop writes `FAILED` + `stopped:shutdown`; #103 may change it to `PENDING`** | Clean shutdown becomes distinguishable from crash, which is #103's AC-6; the reservation in #106 §5.7 is honoured by keeping the branch single and named (§4 Q10) |
| D11 | **The `AGENT_SHELL` platform default is computed at construction, never in the schema, and the default `working_dir` is untouched** | The default's blast radius crosses into the frontend submodule (§4 Q3, F1) |
| D12 | **A new `agent/process.py`; `agent/executor.py` loses the platform half** | `test_no_cluster_required.py` pins `SHIPPED_MODULES`, so a new module is a deliberate, audited addition rather than an accident of layout |

---

## 6. Implementation Approach

### 6.1 New module `agent/process.py` — the platform-dependent half, and nothing else

The module's docstring states the boundary in one line, in the style `agent/paths.py:1-8` sets:

```python
"""
The platform-dependent half of job execution: which shell runs a job, the
launch flags that make the job its own process group, and how a whole process
tree is asked to stop and then *verified* to have stopped.

Everything platform-independent -- validation, the child environment, GPU
allocation, log capture, terminal state -- stays in ``agent/executor.py``, and
this module must not grow any of it.
"""
```

Public surface — four names, all pure or trivially I/O:

```python
@dataclass(frozen=True)
class ShellSpec:
    program: str            # resolved absolute path, or the bare name if which() failed
    flags: tuple[str, ...]  # ("-c",) | ("/d","/s","/c") | ("-NoProfile","-NonInteractive","-Command")
    single_string: bool     # cmd/pwsh parse lpCommandLine themselves -> assemble one string

def resolve_shell(configured: str) -> ShellSpec:
    """Map the AGENT_SHELL value to a program, its fixed flags, and the argv
    convention. Raises ValueError listing the accepted values."""

def shell_argv(spec: ShellSpec, command: str) -> list[str] | str:
    """The Popen argument: a list when single_string is False, otherwise the
    single assembled command line."""

def launch_flags() -> dict[str, object]:
    """{'start_new_session': True} on POSIX;
       {'creationflags': CREATE_NEW_PROCESS_GROUP} on Windows."""

class ProcessTree:
    """Owns everything needed to stop one launched job's tree on this platform.

    POSIX  : the pgid is proc.pid (start_new_session); stop_gracefully() is
             killpg(SIGTERM); stop_forced() is killpg(SIGKILL); verify() is a
             bounded proc.wait().
    Windows: the process group is the CTRL_BREAK target; a Job Object with
             JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE holds the tree; stop_gracefully()
             is GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT); stop_forced() is
             TerminateJobObject(job, 1) followed by taskkill /T /F /PID as an
             independent sweep for anything that escaped the assignment window;
             verify() polls ActiveProcesses == 0 AND waits for the leader.
    """

    def __init__(self, proc: subprocess.Popen, *, log: Callable[..., None]) -> None: ...
    @property
    def control_mode(self) -> str:
        """'process group' | 'job object' | 'taskkill fallback' | 'leader only'
        -- one token for the launch log line, so an operator can see which
        mechanism is live. **[corrected, C1]** the original list had three; a
        node with neither a usable job object nor taskkill.exe is a real
        fourth state, and reporting it as either of the others would be a lie
        in the only line an operator reads."""
    def stop_gracefully(self) -> None: ...
    def stop_forced(self) -> None: ...
    def verify(self, timeout_s: float) -> bool: ...   # True == the tree is gone
    def close(self) -> None: ...                     # TerminateJobObject on a job, else nothing
```

**Constraints on this module, all of them load-bearing:**

- **stdlib only.** `ctypes` and `wintypes` are stdlib and pass
  `test_agent_imports_no_network_module` (`NETWORK_MODULES` at
  `test_no_cluster_required.py:37-52`). No `socket`, and `ctypes` is imported **lazily inside the
  Windows class body** — `ctypes.WinDLL` is resolved at attribute access, not at import, so a POSIX
  agent that never touches `ProcessTree` never loads `ctypes`. Recorded as D12.
- **The Windows half lives under `if sys.platform == "win32":`** so `mypy .` on Linux does not
  analyse `ctypes.WinDLL`. Measured on Windows with the pinned mypy: a `ctypes.WinDLL` call with
  explicit `restype`/`argtypes` type-checks **clean** (`Success: no issues found`), so no stub and no
  `pyproject.toml` override is needed — only the platform guard, so the Linux run stays clean too.
  QA verifies the Linux side (AC-2).
- **`test_no_cluster_required.py:20-33` `SHIPPED_MODULES` gains `"process"`.** Adding the module
  without that edit fails `test_shipped_modules_are_the_documented_set` — and that pin is the point:
  the next shipped module has to be argued for in a list, not arrive unnoticed (§1.10).
- **`import signal` is removed from `executor.py`** once the signal names move here, or `ruff` F401
  fires.
- **`subprocess` is imported in `agent/process.py`, not newly in `executor.py`** — it is already
  imported there at `:13`, and `subprocess` is stdlib so it does not touch `THIRD_PARTY_ALLOWED`.

### 6.2 `agent/config.py` — the `AGENT_SHELL` setting

Three edits, following the existing pattern exactly:

```python
# agent/config.py — _SOURCE_NAMES (:83-94) gains one entry, in the same
# spelling convention as every other AGENT_-prefixed key.
_SOURCE_NAMES: Final[dict[str, str]] = {
    ...,
    "AGENT_SHELL": "SHELL",
    "AGENT_STOP_GRACE_S": "STOP_GRACE_S",     # see the note below
}

class AgentSettings(BaseSettings):
    ...
    #: Which shell runs a submitted ``command``. ``"auto"`` (the default)
    #: means the platform default: ``cmd.exe /d /s /c`` on Windows,
    #: ``/bin/sh -c`` elsewhere. Anything the agent cannot resolve is an
    #: agent misconfiguration, not a job failure (issue #125 Q1/Q5).
    SHELL: str = "auto"
```

**On `AGENT_STOP_GRACE_S`: rejected, and here is why.** It would be natural to expose
`STOP_GRACE_S` (Q9) in the same breath. **It is not added.** #106 §4.1 assigns #99 *"the signal:
`SIGTERM` → grace → `SIGKILL`, **the escalation policy**, the exit-code/error convention for a killed
process"*, and #106 §Q10 measured B2 against the current 5.0 s. Making the number configurable here
would put it in `nodes/agent.toml` — **a file that replicates to every node** — before anyone has
agreed what it should be, and would make #99's budget a per-deployment variable. It stays a module
constant; §4 Q9 names it. Recorded rather than silently decided.

**Validation** (a new `@field_validator("SHELL")`, raising `ValueError` so `build_settings` converts
it to a `ConfigError` with the documented exit code, per `agent/config.py:402-405`):

| accepted | resolved to |
|---|---|
| `auto` (case-insensitive) | `cmd.exe /d /s /c` on Windows, `/bin/sh -c` elsewhere |
| `sh`, `dash`, `bash` | `shutil.which(name)`; flags `("-c",)`; `single_string=False` |
| `cmd` | `shutil.which("cmd.exe")`; flags `("/d","/s","/c")`; `single_string=True` |
| `pwsh`, `powershell` | `shutil.which("pwsh.exe")` / `which("powershell.exe")`; flags `("-NoProfile","-NonInteractive","-Command")`; `single_string=True` |
| any other string | accepted **iff** its basename is one of the six above; otherwise `ConfigError` listing every accepted value |

**`build_parser` gains `--shell`** (`:209-248`), and `_cli_layer` (`:266-280`) gains the
corresponding entry. No `choices=` — an operator may legitimately name an absolute path, and the
`choices` list would reject the thing that is most useful. The help text states the quoting contract
verbatim from §4 Q1, because `--help` is where an operator looks.

**`README.md:229-233`'s key list gains `AGENT_SHELL`**, and a new **"What shell runs a job"** section
records the quoting contract, the per-platform defaults, and the exit-code table from §4 Q5. The
README's exit-code paragraph (`:257-259`) is **unchanged**: `agent/config.py:42-46` is the **agent
process** exit-code table (`EXIT_OK`…`EXIT_SHUTDOWN_GRACE_EXPIRED`) and this issue adds no member to
it — a job's error vocabulary and an agent's exit status are two different tables, and merging them
is how #104's systemd unit ends up inventing a second one (the reason the table exists at all,
`config.py:40-41`). The **job** vocabulary lives in `agent/executor.py` beside the code that writes
it, as a `Final` mapping, and AC-11 asserts the two never drift.

### 6.3 `agent/loop.py` — the wiring, in the order it has to happen

```python
class Agent:
    def __init__(...):
        ...
        self._current_job_id: Optional[str] = None        # :74   unchanged
        self._execution_task: Optional[asyncio.Task] = None  # :76  unchanged
        # NEW, beside the two above and with the same lifecycle: one in-flight
        # job means one stop handle, and the handle is what a stop acts on.
        self._job_stop: Optional[asyncio.Event] = None
```

**`_spawn_execution_task` (`:495-539`) — the event becomes an attribute.**

```python
self._job_stop = asyncio.Event()                    # replaces the local at :508
...
self._execution_task = asyncio.create_task(execution_wrapper(), name=f"execute-{job_id}")
```
and in the wrapper's `finally` (`:523-535`), beside the existing `_current_job_id = None`:
`self._job_stop = None`. **Cleared, not just left set**, so a finished job cannot leave a set event
behind for the next one — a stopped-then-reused event is exactly the "two default jobs share one
state" class of bug this repo keeps catching (Q3's collision).

**`_hand_back_in_flight_job` (`:542-562`) — the `pass` at `:550-552` becomes the setter.**

```python
if self._execution_task is not None and not self._execution_task.done():
    self._job_stop.set()          # replaces the bare `pass` and its wrong comment
```

`_hand_back_in_flight_job` runs from `_shutdown_sequence` (`:619`), **after** the execution-task wait
at `:615`. With the stop path fixed that ordering is no longer harmful, but the spec makes the
ordering *correct* rather than merely *working*: `_shutdown_sequence` sets `self._job_stop` **before**
`supervisor.shutdown(grace)` (Q9), and `_hand_back_in_flight_job` remains the belt-and-braces setter
for the case where the execution task is created between those two points.

**`_shutdown_sequence` (`:593-633`) — one deadline, and the stop goes out first.**

```python
loop = asyncio.get_running_loop()
deadline = loop.time() + self._settings.SHUTDOWN_GRACE_S     # NEW: spent once, not twice
stop.set()
if self._job_stop is not None:
    self._job_stop.set()                                      # NEW: the tree starts dying now
failures = await supervisor.shutdown(remaining(deadline))     # was: timeout=grace
...
await asyncio.wait_for(self._execution_task, timeout=remaining(deadline))   # was: timeout=grace
```

Both waits now derive from the same deadline, so `SHUTDOWN_GRACE_S = 30.0` means 30 seconds
(§4 Q9).

**`Agent.run`'s `self._stop` (`:66`, `:94-95`) is not touched.** It is the supervisor's stop event;
the job's is a second, narrower handle. Conflating them is what the `pass` at `:550-552` gestured at
and did not do.

### 6.4 `agent/executor.py` — the changes, line by line

| line | change |
|---|---|
| `:9` | **add `from __future__ import annotations`** (§4 Q7 bug 2) |
| `:12` | **remove `import signal`** once the signal names live in `agent/process.py` |
| `:73`, `:98-101` | `Optional[X]` → `X | None` is *optional*; the file already uses both, and §4 Q7's change is the future import, not a style sweep. Keep the churn out |
| `:154` | signature gains `settings: AgentSettings` (or, narrower, `shell: str`) — §6.4.1 |
| `:161` | `stop_event: asyncio.Event` **stays a keyword argument**. It is the executor's contract and #99/#106 both build on it |
| `:171` | docstring: *"/bin/sh -c in a new session"* → the configured shell (D1) |
| `:276-284` | **deleted** (dead `try/except Exception: pass`, F-c) and replaced by the `getattr(os, "sched_setaffinity", None)` probe (§4 Q6) |
| `:300-310` | `Popen(shell_argv(...), **launch_flags(), ...)`; the literal `["/bin/sh","-c",command]` and `start_new_session=True` go (§1.3, D1, D2, D7) |
| `:311-327` | `except FileNotFoundError` → `launch_failed:shell_not_found`, **ERROR** log naming the shell and the `PATH` searched (§4 Q5) |
| **new, before `:300`** | `shutil.which` pre-flight for the configured program; unresolved ⇒ `launch_failed:shell_not_found`, `exit_code=127`, **and no `Popen` at all** |
| `:346-358` | `logger.info("set CPU affinity for job %s pid %d: %s", ...)` (§4 Q7 bug 1) and the capability probe from §4 Q6 |
| `:360-373` | unchanged (reader threads) — they are portable |
| `:375-410` | **replaced wholesale** by the `asyncio.wait` pair + `ProcessTree` + `CancelledError` handling (§4 Q9) |
| `:405-410` | the `finally` that joins the reader threads **stays**, and is now reachable on every path because the wait no longer polls forever |
| `:412-421` | the status ladder gains the two stop outcomes and the POSIX-only 127 arm, per §4 Q5 |
| `:161` + `:412` | a stop **before launch** writes `stopped:shutdown` with `exit_code = None`, which widens `agent/terminal.py:25` to `exit_code: Optional[int]` and its `:74` log from `%d` to `%s` |

**6.4.1 — how `execute_job` learns the shell.** Its signature today has no `settings`, and the
executor is called from exactly one production site (`loop.py:512-519`). Two options:

| option | shape | verdict |
|---|---|---|
| A | `execute_job(..., settings: AgentSettings)` | couples the executor to the whole settings model for one field |
| **B** | `execute_job(..., shell: str)` — the already-resolved `AGENT_SHELL` value | **chosen**: one string, trivially overridable in a test without building a settings object, and `Agent` has `self._settings` at the call site |

Option B also keeps `execute_job` importable without `agent.config`, which matters because
`test_no_cluster_required.py:202-226` imports every shipped module in an environment with no cluster
configuration at all.

### 6.5 What is explicitly **not** changed

| not here | why | where |
|---|---|---|
| `Agent._stop` / `TaskSupervisor` / the supervisor's grace | correct already; only the *second* event and the *second* full-grace wait are wrong | — |
| `execution_wrapper`'s node-file rewrite (`:524-535`) | already correct, and it is what clears `current_job_id` | — |
| `build_child_env` and `_is_internal_env_var` | correct; **regression-pinned** by AC-14 rather than touched | — |
| the status ladder's `COMPLETED` / `FAILED` / `process_failed:exit_code=N` | unchanged; only stop outcomes are added | — |
| `write_terminal_state`'s field ownership (`#100`: the agent never touches `retry_count`, never deletes `state.yaml`) | unchanged | #105 D9 |
| the `command` security model (trusted submitters only, `docs/spec.md`) | unchanged; §4 Q1 makes it *more* visible, not less | — |
| `JobSpec.working_dir`'s default, `openapi.yaml`, the frontend | cross-repository contract change | F1 |
| `agent/log_writer.py`, `agent/tests/test_log_writer.py`, `agent/tests/test_claim.py` | 14 of the 15 pre-existing Windows failures; POSIX-only *tests*, different root cause, and `BoundedLineWriter.close()` is correct | F2 |
| `write_yaml`'s atomic replace | #126 | #126 |
| the operator-facing cancel protocol | #99 / #106 | #99/#106 |
| `agent/README` (issue #104) | a different issue; §6.2's README section is the part that belongs here | #104 |

---

## 7. Acceptance Criteria

The issue's eight checkboxes are the backbone. None is dropped and none is left as a comment about
the code; each is an assertion with a named test. AC-16 … AC-19 are what this analysis added.

| Origin | Where it went |
|---|---|
| Issue AC 1 — `mypy .` clean on Windows **and Linux** | AC-1, AC-2, AC-3 |
| Issue AC 2 — a job launches, logs, reports terminal state, on both platforms | AC-4, AC-5 |
| Issue AC 3 — the shell is a documented setting with a platform default, configured in `nodes/agent.toml` | AC-6, AC-7, AC-8 |
| Issue AC 4 — stopping terminates the whole tree on both platforms, with a test proving no child survives | AC-9, AC-10, AC-11, AC-12 |
| Issue AC 5 — shutdown with a job in flight completes within `SHUTDOWN_GRACE_S`, no process orphaned | AC-13, AC-15 |
| Issue AC 6 — settings, the exit-code table, and the node-facing docs reflect the new options | AC-8, AC-11, AC-14 |
| Issue AC 7 — `resources.cpus` enforced portably or documented as advisory, not silently ignored | AC-16 |
| Issue AC 8 — ruff, mypy and the full suite pass on Windows with the pinned toolchain | AC-1, AC-2, AC-17 |
| — | AC-3, AC-12, AC-14, AC-15, AC-16, AC-17, AC-18, AC-19 added here |

### Functional

- **AC-1** — `mypy .` exits 0 **on Windows**, with the pinned toolchain
  (`mypy 1.0.0`, Python 3.12).
  *Verify:* `.venv\Scripts\python.exe -m mypy .` → `Found 0 errors`. The four
  `agent/executor.py` errors and the `agent/tests/conftest.py:234` error are both gone, and **no
  `# type: ignore` was added** and no `[[tool.mypy-overrides]]` block was added or widened
  (`pyproject.toml:70-141` is unchanged except possibly by deletion).

- **AC-2** — `mypy .` exits 0 **on a POSIX host**, specifically that the new
  `agent/process.py` contains no `ctypes` reference reachable by the POSIX type checker. Every
  Windows-only definition lives under `if sys.platform == "win32":`, and `ctypes` is imported lazily
  inside that branch.
  *Verify:* `grep -n "WinDLL\|wintypes" agent/process.py` returns hits **only** inside the
  `sys.platform == "win32"` block; on Linux, `mypy .` exits 0.
  *QA note:* PM could not execute the Linux half from this host. The Windows half is measured
  (clean); the Linux half is a structural requirement on the guard and must be run before acceptance.

- **AC-3** — **`agent/tests/conftest.py:234` is fixed.** `real_watch_supported()` passes a
  `FileSystemEventHandler`, not a bare `lambda`. Asserted **positively**, both ways: the handler
  instance is an `EventHandler` subclass (`isinstance(h, watchdog.events.FileSystemEventHandler)`
  inside the probe), and `mypy .` no longer reports `conftest.py:234`. A test that only deleted the
  `lambda` would satisfy mypy while leaving `real_watch_supported()` broken at runtime.
  *Verify:* `mypy agent/tests/conftest.py` → `Success`; the probe still returns `True` on a machine
  with a working observer.

- **AC-4** — **A job launches on this platform, captures both streams, and reports a terminal
  state** — `COMPLETED` with `exit_code 0`, plus a negative case with a real non-zero exit code
  asserting `error == "process_failed:exit_code=3"` and that `state.yaml` round-trips through
  `read_yaml(..., JobState)`.
  *Proved by* driving the **real** `execute_job` with the **real** configured shell and a child of
  `"{sys.executable}" -c "…"`, and asserting on the **files**: the two log files exist and contain
  the child's stdout and stderr, and `state.yaml` carries the status/exit/error. Assert on the files,
  never on a mock. Runs on **both** platforms; never skipped (§4 Q8).

- **AC-5** — **The default `working_dir` resolves to the right place on each platform**, from the
  two-branch rule: on Windows `JobSpec().working_dir`'s literal default resolves **inside**
  `SYNCTHING_ROOT` and the job launches; a relative `"projects/x"` resolves under the root from a
  **changed** CWD (proving no CWD dependence, F-b); an absolute path outside the root that does not
  exist still fails with `launch_failed:working_dir_not_found`.
  *Proved by* running `execute_job` with `monkeypatch.chdir(tmp_path)` to a directory that does not
  contain the project, so the old `normalize_path` behaviour would produce a different path and fail
  the assertion.

- **AC-6** — **`AGENT_SHELL` is a real setting in all three layers**, exactly like every other one:
  `nodes/agent.toml`, the environment, and a CLI flag, with **CLI > env > TOML > default**. Tested
  with the existing `write_config(syncthing_root, ...)` / `base_env(syncthing_root)` idiom from
  `agent/tests/test_config.py:112-133`, in all four precedence combinations.
  *Verify:* the same four-combination shape already used for `AGENT_LOG_LEVEL`.

- **AC-7** — **The `AGENT_SHELL` validator rejects what it does not support**, with a message that
  lists every accepted value — `"auto"`, `sh`, `dash`, `bash`, `cmd`, `pwsh`, `powershell` —
  and each accepted value resolves to the documented `ShellSpec` (program, flags, `single_string`),
  asserted as a table test. `AGENT_SHELL = "zsh"` raises `ConfigError`; `AGENT_SHELL = "auto"` on
  Windows yields `flags == ("/d","/s","/c")` and `single_string is True`.
  *Proved by* a test that asserts **the resolved program, the flags and the convention** — not
  merely that construction succeeded. A test asserting only "no exception" would pass if the table
  were empty.

- **AC-8** — **`README.md`'s agent section documents the shell**, in the place an operator looks:
  the key list at `:229-233` gains `AGENT_SHELL`, and a new **"What shell runs a job"** section
  states the per-platform defaults, the quoting contract verbatim, and that the flags are not
  configurable. Asserted the way this repository asserts documentation —
  `test_run_agent.py:290` already does `assert "nodes/agent.toml" in text`; this AC extends that
  pattern with `AGENT_SHELL` and with the sentence naming `cmd.exe /d /s /c`, so a later edit that
  drops the section fails CI rather than passing review by inertia.
  *Also:* `agent/config.py:42-46`'s exit-code table gains **no member**, and a test asserts the
  table is exactly `{0,1,2,3,4}` so that decision cannot be quietly reversed.

- **AC-9** — **A real three-process tree is stopped and no process survives**, on **both**
  platforms. The tree is `shell → python parent → python grandchild`, each appending its pid to a
  file under `tmp_path`; the test waits on the property (all three pids recorded) with the existing
  `wait_until` helper from `agent/tests/conftest.py:201-220`, sets the stop event, and asserts
  `psutil.pid_exists(pid) is False` for **every** recorded pid.
  *This is the assertion #98 AC-4 asked for and never got.* Asserting only on the launcher's
  `poll()` would pass for an implementation that killed the launcher and orphaned the ranks — the
  exact failure #99's problem statement names. Asserting only on the *grandchild* would let the
  launcher leak.
  *Runs on both platforms; never skipped.*

- **AC-10** — **A process that ignores the polite request is still killed, and the log says so.**
  Two cases per platform:
  - POSIX: a child that traps `SIGTERM` (measured by #106 §Q10 as 5.004 s to a `-9` leader, 0
    survivors) — the forced tier runs and the tree is gone.
  - Windows: a child that calls `signal.signal(signal.SIGBREAK, signal.SIG_IGN)` — measured §1.5 to
    **survive two `CTRL_BREAK_EVENT`s**; it must die at the forced tier.
  *Proved by* asserting the log records the **forced** tier (`required the forced stop`), not merely
  that the pid is gone. A test that only asserts "pid gone" cannot tell a graceful stop from a forced
  one, and the difference is what an operator needs when a job refuses to checkpoint.

- **AC-11** — **"The tree is gone" is verified, not assumed**, and the verification is a real read.
  - Windows: after the forced tier, the test asserts
    `QueryInformationJobObject(..., JobObjectBasicAccountingInformation).ActiveProcesses == 0`
    **through `ProcessTree.verify()`**, and separately that `proc.wait()` returned. The two are
    different facts (§1.6) and the test asserts both.
  - The negative case: `verify()` is driven with a stubbed tree that never empties, and must return
    `False`; the executor must then write **`stop_failed:tree_survived`**, not `stopped:shutdown`.
    Without this, the timeout branch is untested and the "verified" claim is a comment.
  - POSIX: `verify()` is the bounded `proc.wait()`, with the same negative case via a process that
    ignores `SIGKILL` — which cannot exist, so the negative case is asserted on the **timeout path**
    with an injected clock, not on a real unstoppable process. Stated so the SWE does not write a
    test that cannot pass.

> **[corrected, C4]** "an injected clock" is the wrong mechanism and there is
> nothing to inject one into. `Popen.wait` raises `TimeoutExpired` itself; the
> branch is reached by handing `verify()` a process whose `wait` cannot
> complete, which is the property under test and needs no clock and no
> production seam. Additionally, **[measured, §12.3]** the verification was
> originally specified to run *after the forced tier only*, which left the
> graceful path reporting `stopped:shutdown` on nothing more than the launcher
> being reaped — the orphan QA reproduced. `verify()` now runs after **both**
> tiers, which is why AC-15's bound is `STOP_GRACE_S + 2 * STOP_VERIFY_TIMEOUT_S`.

- **AC-12** — **The fallback path is real and loud.** With the Job Object made unavailable (the
  `ProcessTree` constructor is given a `create_job` that raises, or `AssignProcessToJobObject` is
  stubbed to return 0), the launch still succeeds, the log at **WARNING** names the mechanism that
  took over (`taskkill`), and the tree is still stopped with **0 survivors**. With `taskkill`
  *also* unavailable, the log is at **ERROR** and says the tree may have survived — asserted by
  asserting the **level and the wording**, because a fallback nobody can notice is a fallback that
  is assumed to work.

- **AC-13** — **Shutdown with a job in flight completes within `SHUTDOWN_GRACE_S`, orphans nothing,
  and spends the grace once.**
  - Given a real `Agent`, a claimed job running a real 3-process tree, and
    `SHUTDOWN_GRACE_S` set to `0.5` (the idiom `agent/tests/test_loop.py:56` already uses), when the
    agent is stopped, then it returns **without** logging *"execution task did not complete within
    grace period"* (`loop.py:617`), `supervisor.grace_expired` is `False`, and all three pids are
    gone.
  - And the two-waits assertion: with `SHUTDOWN_GRACE_S = 0.5`, a run that stops a cooperative tree
    returns in **under 0.5 s**, proving the grace is spent once and not twice (today's `:604` +
    `:615` can consume `2 × grace`). A test asserting only "it eventually returns" passes today.
  - `state.yaml` afterwards: `status: FAILED`, `error: stopped:shutdown`, `node_id` unchanged,
    `completed_at` set (§4 Q10). `nodes/<node_id>.yaml`: `current_job_id: null`.
  *Blocked on #124 for the parts that drive `Agent.run` on Windows* — see §7.1.

- **AC-14** — **No per-node secret reaches the child, end to end.** A job submitted with a parent
  environment containing `SHARED_TOKEN`, `SECRET_KEY`, `SHC_STATE_DIR`, `SYNCTHING_ROOT`, `NODE_ID`,
  `CUSTOM_TOKEN` and `API_KEY` launches `{sys.executable} -c "print(os.environ)"`, and the captured
  **stdout log file** is asserted to contain none of them while containing `KEEP_THIS`.
  *Why end-to-end rather than the existing unit test:* the existing
  `agent/tests/test_executor.py:91-122` tests `build_child_env` directly, and a harness that
  constructs the child environment by hand rather than through `build_child_env` will happily show
  the secrets — measured during PM's analysis (§1.3's probe leaked both because it bypassed the
  function). This AC goes through `execute_job`.

- **AC-15** — **The settings invariant holds**: the worst-case stop
  (`STOP_GRACE_S + STOP_VERIFY_TIMEOUT_S` = 10 s) is strictly less than the default
  `SHUTDOWN_GRACE_S` (30 s), asserted as a constant-to-constant assertion so raising one without
  the other fails a test. A single file cannot make the agent exceed its own documented grace.

- **AC-16** — **`resources.cpus` is enforced or it says it is advisory — and the test asserts which.**
  Three cases, each asserting the **log record's rendered message**:
  - Linux (skipped elsewhere, named reason): the pinning line **renders the pid and the CPU set** —
    asserted through `caplog` on `record.getMessage()`, with the pid and `{0,1}` present. **A test
    that only asserted "no exception" would pass today**, because `logging` swallows the `TypeError`
    (§1.8 F-a).
  - Everywhere else: the advisory line renders at **INFO** and names `resources.cpus=<n>` and the
    node's CPU count. Asserted present; the DEBUG-only behaviour is asserted **absent**.
  - `resources.cpus > os.cpu_count()`: a **WARNING** is emitted and the job still launches.
  *Also:* `hasattr(os, "sched_setaffinity", False)` is the whole mechanism — no `AttributeError` arm
  is what hides the answer any more.

- **AC-17** — **`build_child_env` is not regressed** beyond AC-14: the existing four
  `TestBuildChildEnv` tests still pass unmodified, and `agent/config.py`'s unknown-key rejection and
  `NODE_ID`/`SYNCTHING_ROOT` refusals are unaffected by the `_SOURCE_NAMES` addition. `ruff check .`
  and `ruff format --check .` exit 0, and **no `import signal` survives in `executor.py`**.

- **AC-18** — **The new tests cannot kill the runner (#124's failure mode), on any platform.** An AST
  guard over `agent/tests/**` asserting **no `os.kill` call whose second argument is a non-zero
  constant** — measured, `os.kill(pid, 9)` is `TerminateProcess(handle, 9)` on Windows (C4) — and
  **every liveness assertion goes through `psutil.pid_exists`**. The guard is modelled on
  `agent/tests/test_hardware.py:549-559`, which already does exactly this shape of AST pin.
  *Proved by* deliberately adding `os.kill(os.getpid(), 9)` to a scratch test and confirming the
  guard goes red before the process dies (record the command in the PR).

- **AC-19** — **Documentation and record.** `docs/specs/98-job-execution.md`'s "Process group and
  signal handling" subsection and "Error vocabulary" table are annotated as **superseded by this
  spec** with a one-line pointer — #98 is the historical record and stays, but a reader who lands on
  `["/bin/sh", "-c", command]` must be told it is no longer what the code does. And **#98's AC-2 is
  discharged here**: `docs/spec.md` gains the execution model under a new
  *"Job execution: shell and process tree"* heading, which it has never had (§1.10).

### 7.1 What blocks QA, stated so it is not discovered mid-verification

**#124 blocks AC-13's agent-level assertions on Windows.** `agent/tests/test_loop.py` terminates the
pytest process (exit 15, measured). AC-13's file-level assertions (`state.yaml`, node file, the three
pids) can be driven through `execute_job` directly and are **not** blocked; the assertions that need
a running `Agent.run()` and a delivered stop are. **QA runs AC-13's agent-level half on POSIX, and
runs the rest of the suite on Windows.** PM will not accept a partially-verified AC-13 without saying
which half ran where; §4 of the PM's final review records it.

**#126 does not block this issue but touches it.** `write_yaml`'s `os.replace` fails with
`PermissionError [WinError 5]` when the destination is open — measured, one of the 15 baseline
failures. This issue **writes `state.yaml` twice as often as it used to** (once for the stop outcome,
which already happened, and once more for a stop-before-launch), so it increases the exposure. It
does **not** fix it, does **not** work around it, and must not add a retry to `write_yaml` — that is
#126's single, deliberately narrow fix site, and two writers of the same helper is how #126's open
question 2 gets answered wrongly. **Record the exposure in the PR description; do not solve it here.**

**The 15 baseline failures (§1.7) are not this issue's.** AC-17's suite assertion is therefore
**comparative**, not absolute: `pytest agent/tests -q --ignore=agent/tests/test_loop.py` must report
**≥ 219 passed** and **exactly the same 15 failures**, and `pytest agent/tests -q --collect-only`
must report **≥ 271 collected**. An absolute "all green on Windows" is not achievable before #124,
#126 and F2, and demanding it would make this issue unfinishable.

---

## 8. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **The `taskkill` sweep silently absent** — a stripped `PATH` on a Windows Server core image removes the forced tier's second leg | Medium | High | `shutil.which("taskkill.exe")` is resolved at launch and recorded in `ProcessTree.control_mode`; the launch log line names the live mechanism, so the degraded mode is visible without enabling DEBUG. AC-12 |
| **The Job Object assignment window lets a fast-spawning launcher escape** (§1.6) | Low — structurally real, 0 escapes in 13 measured runs | High (orphaned ranks holding GPUs) | The `taskkill /T /F` sweep is independent of the job and closes it; AC-9's three-pid assertion fails if any leg is missing, which is the regression guard |
| **The agent is killed with no chance to stop the tree** — no `SIGTERM` on Windows (`add_signal_handler` unavailable, `Agent._install_signal_handlers` already logs it once) | **High on Windows** | High | `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` makes the OS do it: the handles close when the agent dies and the kernel kills the tree. This is the one place Windows is **better** than POSIX, and it is a real argument for the job object being the primary mechanism rather than the fallback |
| **`CTRL_BREAK_EVENT` never arrives** — the agent has no console (a service, a scheduled task, a container) | Medium | Medium | It is only the *polite* tier; the forced tier does not need a console. The log distinguishes "graceful request sent" from "forced", so a no-console deployment is visible |
| **`psutil` becomes a test-only dependency of a shipped path** | Low | Low | It already is a declared runtime dependency used by `agent/hardware.py`, and it is on `THIRD_PARTY_ALLOWED`. It is used **only in tests** by this issue |
| **A new `os.wait`-in-a-thread waiter leaks on a busy node** — `to_thread` uses the default executor | Low (one waiter per agent) | Medium | One job per agent by construction (`_maybe_claim_job` refuses a second, `loop.py:456-462`). Recorded, not designed around; if #102 later raises the concurrency this must be revisited |
| **`Task.cancel()` arrives instead of the stop event** and the tree survives | Medium — `asyncio.wait_for` at `loop.py:615` raises exactly that today | **Critical** | §4 Q9 specifies the `CancelledError` handler: forced tier immediately, then re-raise. AC-13's assertion and a dedicated negative test |
| **A shutdown-stopped job is recorded `FAILED` and is not re-claimable**, so an operator must `retry` | Certain — that is the interim decision | Medium | Stated in §4 Q10 and in the README's new section. #103 may change it to `PENDING` in one edit because the reason is recorded separately. **Recorded, not hidden** |
| **`write_yaml`'s Windows `os.replace` failure bites more often** because this issue adds a terminal write on the stop path | Medium | Medium | #126's single fix site; this issue must not touch `write_yaml`. Recorded in §7.1 and the PR description |
| **A new error code lands that #100's vocabulary table does not list** | Low | Medium | The table in §4 Q5 is the vocabulary, `agent/executor.py` holds it as a `Final` mapping, and AC-11 asserts the mapping and the ladder cannot drift. `docs/specs/100-terminal-state.md` is annotated as extended by this spec |
| **`AGENT_SHELL` in `nodes/agent.toml` configures the whole fleet**, so one node's typo breaks every node at startup | Medium | Medium | This is the established model (`agent.toml` is fleet-wide by decision, #93 S6) and a **loud** failure is the right shape: an unknown value is a `ConfigError` at startup listing every accepted value, not a silent fallback. `NODE_ID`/`SYNCTHING_ROOT` remain refused because they mean something per-node; `SHELL` is genuinely fleet-wide, which is why it is allowed |
| **A shell path that is valid on one node is not on another** — the file replicates, the filesystem does not | **Medium** | Medium | Accepted by the decision, and the mitigation is the error: `shell_not_found` names the configured value and the `PATH` searched, at ERROR. Recorded here because the mitigation is "it fails loudly", not "it cannot happen" |
| **The 15 pre-existing Windows failures are mistaken for regressions** | **High** if AC-17 is read as absolute | Medium | §1.7 itemises them with owners and §7.1 pins the assertion as comparative (≥ 219 passed, same 15). PM's final review compares the *set*, not just the count |
| **`git checkout -b` from a dirty tree loses the submodule pointer** | Low | Low | `frontend/` shows as ` M frontend` in `git status` on this checkout; the branch carries **no** gitlink change and the PR must contain the spec only |

---

## 9. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Implementation complete (SWE)
- [ ] AC-1 … AC-19 verified with evidence (QA), with §7.1's platform split recorded
- [ ] `ruff check . && ruff format --check .` exit 0; `mypy .` exit 0 on **both** platforms (AC-1, AC-2)
- [ ] `pytest agent/tests -q --collect-only` reports **≥ 271** collected
- [ ] `pytest agent/tests -q --ignore=agent/tests/test_loop.py` reports **≥ 219 passed** and
      **the same 15 pre-existing failures**, no more
- [ ] `agent/executor.py` contains no `import signal`, no `/bin/sh`, no `start_new_session=True`
      literal, no `os.killpg`, no `%%d`/`%%s`, and **no bare** `os.sched_setaffinity`
      *Verify:* `grep -nE "killpg|SIGKILL|/bin/sh|start_new_session|%%[ds]" agent/executor.py` → **no output**,
      and `agent/tests/test_execution.py::test_the_cpu_affinity_call_is_only_ever_probed` passes.

> **[corrected, C5]** The original line also grepped for `sched_setaffinity`,
> which §4 Q6 *requires* `agent/executor.py` to contain —
> `getattr(os, "sched_setaffinity", None)` is both the capability probe and the
> mypy fix. The two requirements cannot both be met by a substring search. The
> intent, which is "no bare attribute reference", is now asserted over the
> AST: the name may appear only as the second argument of a `getattr`. The
> other five names are absent from the file entirely, **including from its
> prose**, so the substring search stays meaningful for them.

- [ ] PM acceptance

---

## 10. Follow-ups filed from this spec (not solved here)

| id | what | why not here | evidence |
|---|---|---|---|
| **F1** | Change `JobSpec.working_dir`'s default; recommended `""` meaning *derive it* as `<root>/projects/<job_id>` | touches `openapi.yaml` (regeneration), two backend tests, the demo seeder, and **two files in the frontend submodule**. Also fixes the collision the fixed literal `/sync/projects/new-run` guarantees between two default jobs | §4 Q3 table |
| **F2** | 14 POSIX-only **tests** in `agent/tests/test_log_writer.py` and `agent/tests/test_claim.py` | 14 of the 15 Windows baseline failures. `BoundedLineWriter.close()` is correct; the tests leave a handle open across a `TemporaryDirectory` boundary, and `chmod(0o555)` does not deny writes on a Windows directory | §1.7 |
| **F3** | `Agent.run()` never removes the signal handlers it installs (`#114` C6 / R3) | raised in #114 and still open; made worse if this issue's tests add another `add_signal_handler` user | §7.1, #114 §2.4 |

**#124** and **#126** are existing issues and are referenced, not duplicated: #124 blocks AC-13's
agent-level Windows half, #126 owns the `write_yaml` atomic-replace defect that this issue increases
exposure to but must not fix.

---

## 11. Related

- **#92** — the file-only transport decision. Binding on the design in two places: the new
  `agent/process.py` imports only stdlib (`ctypes` is not a network module, but the discipline
  stands), and per-node secrets stay behind `build_child_env` (AC-14).
- **#98** — job execution. The spec this one supersedes for the platform-dependent half. §5 AC-3 and
  AC-4 of that spec were never implemented (§1.10), which is why the stop path went unnoticed.
- **#99** — cancellation. Owns the escalation *policy* and the cancel protocol; this issue owns the
  stop being reachable, portable and verified (§4 Q4).
- **#100** — terminal state and the `error` vocabulary. This issue **adds four codes** to that table
  (§4 Q5) and changes none of its rules.
- **#103** — crash/restart reconciliation. Beneficiary: a clean shutdown now writes a terminal state,
  so clean and crash are distinguishable (#103's AC-6). #103 owns whether a shutdown-stopped job is
  `FAILED` or returned to `PENDING` (§4 Q10).
- **#106** — command files. Owns the `cancel` stop *reason*; the `StopReason` literal here is closed
  at one value and is the seam it extends.
- **#124** — the agent suite kills pytest on Windows. Blocks AC-13's agent-level Windows half.
- **#126** — `write_yaml`'s Windows atomic-replace failure. Adjacent; this issue adds write pressure
  and must not fix it.
- **#114** — the POSIX signal-guard issue whose `os.kill(getpid(), SIGTERM)` shape AC-18 generalises
  to the whole agent test suite.
- `agent/config.py:40-46` — the agent *process* exit-code table, deliberately untouched (AC-8).
- `agent/tests/conftest.py:20-33` — `SHIPPED_MODULES`, the pin that a new module must join (§6.1).
- `agent/tests/conftest.py:201-220` — `wait_until`, the load-safe wait helper the new tests use.
- `shared/file_ops/path_utils.py:15-36` — `normalize_path`, whose CWD behaviour Q3 removes from the
  `working_dir` path.
- `shared/testing/signal_guard.py` — the #115/#114 guard the new tests must not defeat.

---

## 12. Corrections, and the two facts that changed the design

QA's verdict on the first SWE cycle adjudicated six statements in this document
as errors and two measurements as wrong. They are recorded here so the record
and the code agree.

### 12.1 [measured] The Job Object does not necessarily hold the tree

§1.6 measured a post-`CreateProcess` assignment **window** — milliseconds wide,
no escape in 13 runs — and built the whole Windows design on the assumption
that a job holds the tree from the launcher down. On the same host, that
assumption is false for one very common launcher:

| leader | `ActiveProcesses` with 2 live descendants | after `TerminateJobObject` |
|---|---|---|
| `.venv\Scripts\python.exe` (a `uv` venv redirector, 47 KB) | **1** | the grandchild **survives** |
| the real interpreter beside it (91 KB) | 2 | nothing survives |

The redirector creates its child with `CREATE_BREAKAWAY_FROM_JOB`, so every
descendant past it is outside the job for good — not for a window, for the
life of the job. `uv` is what `AGENTS.md` tells contributors to build the venv
with. Consequences, all three of them in the code:

1. **`ActiveProcesses == 0` is not "the tree is gone".** `verify()` also checks
   every pid it recorded at stop time against a live process snapshot
   (`CreateToolhelp32Snapshot`, stdlib, behind the Windows guard). This is the
   fix for the orphan QA reproduced: a cooperative launcher and a grandchild
   that refuses the break, where the old code wrote `stopped:shutdown` while
   the grandchild went on holding its pipes and its GPUs.
2. **The job is fed the tree explicitly.** At the start of each tier the
   launcher is still alive, so the descendants are enumerable; each is
   assigned to the job, which is what makes `TerminateJobObject` mean "this
   tree" rather than "this process".
3. **The sweep runs first.** `taskkill /T` walks parent/child links *from the
   launcher*, and `TerminateJobObject` kills the launcher — the order in §4 Q2
   made the sweep return "no process found" and do nothing, in 10 runs out of
   10. This is correction C1.

On a node with neither mechanism the agent **cannot** finish such a stop, and
says so: `stop_failed:tree_survived` at ERROR. That is §4 Q2's "loud rather
than assumed-away" and it is now a test, not a hope.

### 12.2 [corrected, C2] §4 Q3's pseudocode needed the anchor stripped

`/sync/projects/new-run` — the schema's own default — is *rooted but not
absolute* on Windows, and `pathlib` replaces everything to the left of a rooted
right-hand operand. The pseudocode put the job at `C:\sync\projects\new-run`,
outside the Syncthing root, defeating §4 Q3's own table. The implementation
strips the anchor before the join; the table was always right.

### 12.3 [corrected, C4] Verification runs after the graceful tier too

AC-11 was written for the forced tier. That left the graceful path reporting
success on nothing more than the launcher being reaped. `verify()` now runs
after both tiers, and AC-15's bound is therefore
`STOP_GRACE_S + 2 * STOP_VERIFY_TIMEOUT_S` = 15 s against a 30 s grace. A
verification that is going to succeed resolves in microseconds, so the second
one only costs anything on a stop that has already gone wrong.

### 12.4 [not a defect] Two threads waiting on one `Popen`

`agent/executor.py` parks a thread in `proc.wait()` and `verify()` asks a
second. On POSIX the loser of `os.waitpid` takes `ChildProcessError`, which
CPython translates to `sts = 0` — a forcibly killed job written down as
`COMPLETED`. It cannot happen: since **CPython 3.9** `Popen._wait` holds
`_waitpid_lock` and re-checks `self.returncode` under it, with the comment
*"Another thread waited."* The evidence is in `subprocess.py` at
`Popen._wait`/`_try_wait`, and the guard is a test that races four waiters on
one real process and requires all four to agree. The floor here is 3.11, so
every supported interpreter has the lock.

### 12.5 Corrections index

| # | statement | what it now says |
|---|---|---|
| C1 | §4 Q2 tier order; §6.1 token list | sweep first; four tokens, the fourth being `leader only` |
| C2 | §4 Q3 pseudocode | strip the anchor before joining on a rooted-but-not-absolute path |
| C3 | §4 Q8(b) | the **signal** is the second argument, and a *variable* signal must be caught too; `killpg` is banned outright |
| C4 | AC-11 | the negative case is a `Popen` whose `wait` times out, not an injected clock; and verification covers the graceful tier |
| C5 | §9's grep | dropped `sched_setaffinity` from the substring search and asserted "no bare reference" over the AST instead |
| C6 | §4 Q5's table | `launch_failed:process_creation_failed: <e>` added; the table presented itself as complete |
