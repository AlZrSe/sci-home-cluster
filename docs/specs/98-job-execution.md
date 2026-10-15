# Spec: Job Execution (Agent-Side)

**Issue**: #98 — "No agent launches a process: command, working_dir, env and resources are validated but never executed"
**Labels**: `agent`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-14

---

## 1. Verified Problem Statement

No process in the repository executes `JobSpec.command`. The fields that describe *how* a scientific job runs — `command`, `working_dir`, `env`, `resources`, `paths` — are validated on submission but nothing reads them. The agent skeleton (#93) and claim logic (#97) exist; the claim logic writes `RUNNING` + `node_id` + `started_at` to `state.yaml`, but then the agent does nothing with the job.

Evidence from the issue body:

- `shared/schemas/job_spec.py:14-25` — the entire contract validates but is never consumed
- `command` is a single opaque string; no shell is specified
- `working_dir` defaults to `/sync/projects/new-run` — an absolute path not derived from `SYNCTHING_ROOT`
- `paths.input` / `paths.output` are never read anywhere (zero hits in `backend/`, `cli/`, `agent/`)
- `env` has no allow/deny policy or inheritance rule
- `resources` has no defaults and nothing consumes them
- `is_within_syncthing_root` (`path_utils.py:138`) exists and is unused

---

## 2. Root Cause

The agent loop (`agent/loop.py`) has a `_write_final_state` seam reserved for #100, but no execution task is registered. The claim logic (`agent/claim.py`) successfully claims a job and writes `RUNNING`, but the agent never proceeds to launch the process. This is missing implementation, not a defect in existing code.

---

## 3. Open Questions — Resolved

The issue left five questions open. Each is decided here, with rationale.

### Q1 — Shell or direct exec?

**Decision: Shell — `/bin/sh -c` with `start_new_session=True`.**

Rationale:
- Scientific workloads require `mpiexec`, pipes, `&&`, `||`, environment-prefix idioms (`CUDA_VISIBLE_DEVICES=0,1 python train.py`). These are shell features.
- `shlex.split` cannot express `mpiexec -n 4 vasp_std` correctly if the command contains shell metacharacters the user intentionally wrote.
- Security: the cluster uses a single shared Bearer token. "Trusted submitter" means "anyone with the token". The execution model is documented explicitly (§5.1) with the security implication stated.

### Q2 — Container or `systemd-run` scope for resource enforcement?

**Decision: No container, no `systemd-run` in Phase 2. Best-effort resources only.**

Rationale:
- Requiring a container runtime on every node is Phase 3 territory.
- The decision constrains what can be promised: `gpus` → enforced via `CUDA_VISIBLE_DEVICES`; `cpus` → advisory (`os.sched_setaffinity` if available, else documented no-op); `memory_gb` / `vram_gb` → documented unenforced.
- An unenforced field that *looks* enforced is worse than one documented as advisory.

### Q3 — What does "job succeeded" mean?

**Decision: Exit code only. `paths.output` existence is NOT checked.**

Rationale:
- The schema has no notion of "expected artifacts".
- A job that exits 0 while writing nothing to `paths.output` is a *user logic* failure, not an execution failure.
- Checking `paths.output` would require the agent to understand the job's semantics, which it does not.

### Q4 — Is `working_dir` supposed to live under `SYNCTHING_ROOT`?

**Decision: Yes. The default `/sync/projects/new-run` is a documented convention, not a derived path.**

Policy:
- If `working_dir` is inside `SYNCTHING_ROOT` (checked via `is_within_syncthing_root`), the agent ensures it exists (creates parents).
- If `working_dir` is outside `SYNCTHING_ROOT`, the job fails with a clear error: `launch_failed:working_dir_not_in_syncthing_root`.
- The default `/sync/projects/new-run` is documented as "the conventional path under Syncthing root for new runs; the operator must ensure it exists or the job will fail".

### Q5 — Does `name` need to appear in the execution environment?

**Decision: No. `name` is metadata only. It is not injected as an environment variable or used in the process environment.**

---

## 4. Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| Launch `JobSpec.command` via `/bin/sh -c` in a new process group | Container runtime, `systemd-run`, cgroups |
| Validate and prepare `working_dir` | Deriving `working_dir` from `SYNCTHING_ROOT` automatically |
| Build child environment: parent + `spec.env`, strip internal vars | Secret injection, vault integration |
| Enforce `gpus` → `CUDA_VISIBLE_DEVICES` | Enforce `memory_gb`, `vram_gb` |
| Advisory `cpus` → `os.sched_setaffinity` | Hard CPU quotas |
| Capture stdout/stderr separately, line-bounded, with undecodable-byte policy | Log shipping to backend (#101) |
| Run execution in background task so agent continues heartbeat/log shipping | Job scheduling, priority, preemption |
| Validate `paths.input` exists BEFORE launch | Sync/verify input availability (Syncthing is eventual) |

### Coupling to #97 — recorded, not fixed

`agent/claim.py` writes `RUNNING` + `node_id` + `started_at` on successful claim. Execution starts **after** the claim succeeds and the `state.yaml` is visible to the backend. The execution task is a new supervised task spawned after claim.

### Coupling to #100 — recorded, not fixed

Terminal state (`COMPLETED`/`FAILED`/`CANCELLED` with `exit_code` and `error`) is written by the execution task on process exit. #100 specifies the vocabulary and monotonicity; this spec produces the values.

### Coupling to #101 — recorded, not fixed

Log lines are written to `jobs/<job_id>/logs/<timestamp>.log` (or similar) for #101 to ingest. This issue produces the files; #101 ships them.

---

## 5. Implementation Approach

### New file: `agent/executor.py`

A module with the execution logic, designed as a **pure function plus narrow helpers** where possible, so it is testable with fakes.

```python
async def execute_job(
    *,
    job_id: str,
    job_spec: JobSpec,
    paths: AgentPaths,
    node_spec: NodeSpec,
    stop_event: asyncio.Event,
) -> tuple[int, str, str]:
    """
    Execute a job and return (exit_code, stdout_path, stderr_path).

    The function:
    - Validates working_dir and paths.input
    - Builds the child environment
    - Sets up resource enforcement (GPU, CPU affinity)
    - Launches the process via /bin/sh -c in a new session
    - Captures stdout/stderr to log files under jobs/<job_id>/logs/
    - Returns when the process exits or stop_event is set
    """
```

### New file: `agent/log_writer.py`

Helper for bounded line capture with undecodable-byte policy.

```python
class BoundedLineWriter:
    """Write lines to a file with length cap and encoding policy."""
    MAX_LINE_LENGTH: int = 10240  # 10 KiB

    def __init__(self, file_path: Path):
        ...

    def write_line(self, line: bytes) -> None:
        """Decode with errors='replace', truncate to MAX_LINE_LENGTH, write."""
        ...

    async def flush(self) -> None:
        ...
```

### Integration into `agent/loop.py`

In `_maybe_claim_job`, on successful claim:
1. Read the claimed job's `JobSpec` from the `state.yaml` (already have `job_state.spec`)
2. Spawn a new supervised task `execute_job_task` that runs `execute_job`
3. On completion, the task writes terminal state (per #100) and clears `_current_job_id`

The execution task runs **concurrently** with heartbeat, log shipping, and event drain — it is a background task supervised by `TaskSupervisor`.

### Working directory validation

```python
def validate_and_prepare_working_dir(
    working_dir: str,
    syncthing_root: Path,
) -> tuple[Path, str | None]:
    """
    Returns (resolved_path, error_message).
    Error messages:
    - "launch_failed:working_dir_not_in_syncthing_root"
    - "launch_failed:working_dir_not_found" (if outside and doesn't exist)
    """
    resolved = normalize_path(Path(working_dir))
    if not is_within_syncthing_root(resolved):
        # Outside Syncthing root: must exist, we don't create it
        if not resolved.exists():
            return resolved, "launch_failed:working_dir_not_found"
        return resolved, None
    # Inside Syncthing root: create parents if needed
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return resolved, f"launch_failed:working_dir_create_failed: {e}"
    return resolved, None
```

### Child environment construction

```python
def build_child_env(
    parent_env: dict[str, str],
    spec_env: dict[str, str],
    gpu_indices: list[int] | None = None,
) -> dict[str, str]:
    """
    Returns the environment for the child process.

    - Starts with parent_env (os.environ)
    - Removes cluster-internal variables:
        SHARED_TOKEN, SECRET_KEY, AGENT_*, NODE_ID, SYNCTHING_ROOT
    - Applies spec_env (overrides)
    - If gpu_indices provided, sets CUDA_VISIBLE_DEVICES=comma-separated-indices
    - Logs variable NAMES only (never values) at INFO
    """
```

### Resource enforcement

| Field | Mechanism | Enforcement Level |
|---|---|---|
| `gpus` | `CUDA_VISIBLE_DEVICES` set to allocated indices | **Enforced** |
| `cpus` | `os.sched_setaffinity(0, cpu_set)` if available; else no-op | **Advisory** (documented) |
| `memory_gb` | None | **Unenforced** (documented) |
| `vram_gb` | None | **Unenforced** (documented) |

GPU allocation: the agent claims a job only if `node_spec.gpus` has enough devices. The first N GPUs are allocated (indices 0..N-1). This is a simple policy; a more sophisticated allocator is Phase 3.

### Log capture

- Stdout and stderr are captured **separately** to separate files under `jobs/<job_id>/logs/`
- File naming: `stdout-<timestamp>.log`, `stderr-<timestamp>.log` (timestamp = start time ISO8601, colon-safe)
- Line length bounded at 10 KiB; longer lines are truncated with a marker `...[truncated N bytes]`
- Undecodable bytes: `errors="replace"` (replacement character `�`)
- Files are opened in append mode; the agent may rotate if a single file grows too large (Phase 3)

### Process group and signal handling

> **Superseded by [`docs/specs/125-windows-job-execution.md`](125-windows-job-execution.md).**
> This subsection is the historical record of what #98 decided. The code no longer looks like
> this: the shell is `AGENT_SHELL` rather than `/bin/sh`, the platform half lives in
> `agent/process.py`, and the Windows implementation is a Job Object plus a `taskkill /T /F`
> sweep rather than a process group. Read §4 Q1 and Q2 of #125 for what runs and what is
> signalled today.

```python
proc = await asyncio.create_subprocess_shell(
    command,
    cwd=working_dir,
    env=child_env,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    start_new_session=True,  # New process group
)
```

- `start_new_session=True` creates a new session and process group (POSIX `setsid`).
- The entire process tree (including `mpiexec` children) is in this group.
- On agent shutdown or cancel (#99), the agent sends `SIGTERM` to the process group (`os.killpg(proc.pid, signal.SIGTERM)`), then `SIGKILL` after grace period.
- This satisfies AC-4: a test kills the launcher with a multi-process child still running and asserts the entire tree terminates.

### Error vocabulary (agent-side launch failures)

> **Extended and partially corrected by
> [`docs/specs/125-windows-job-execution.md`](125-windows-job-execution.md).** The table below
> is #98's; §4 Q5 of #125 is the vocabulary as shipped. It adds
> `launch_failed:shell_not_found`, `stopped:shutdown` and `stop_failed:tree_survived`, and it
> narrows `launch_failed:command_not_found` to POSIX -- `cmd.exe` and `pwsh` return 1 for a
> missing program, with a localized message, so there is nothing portable to match on.

These are written to `state.yaml` as `error` when the agent cannot launch the process at all:

| Code | Meaning |
|---|---|
| `launch_failed:working_dir_not_in_syncthing_root` | `working_dir` is outside `SYNCTHING_ROOT` |
| `launch_failed:working_dir_not_found` | `working_dir` outside Syncthing root and does not exist |
| `launch_failed:working_dir_create_failed` | Inside Syncthing root but `mkdir` failed |
| `launch_failed:input_missing` | `paths.input` is set and does not exist |
| `launch_failed:command_not_found` | Shell reports command not found (exit 127) |

Process failures (the command ran but exited non-zero) use: `process_failed:exit_code=<N>`.

---

## 6. Acceptance Criteria

### Functional

- **AC-1** — A submitted job with a known `command` actually executes, its exit code is captured, and the result is visible through `GET /jobs/{id}`.
- **AC-2** — The execution model (shell) is documented in `docs/spec.md` and in the agent's documentation, with the security implication stated.
- **AC-3** — A test executes a real child process with multi-line stdout **and** stderr and asserts both are captured, attributed and ordered.
- **AC-4** — A test asserts the child runs in its own process group and that the entire tree can be terminated — verified by killing a launcher with a multi-process child still running.
- **AC-5** — `working_dir` policy is enforced: a directory outside the permitted set fails the job with a clear `error`, and the test asserts the message rather than a traceback.
- **AC-6** — The default `working_dir` `/sync/projects/new-run` has a documented resolution rule, tested.
- **AC-7** — `paths.input` non-existence is detected **before** launch and reported as a job-level failure with an explanatory `error`.
- **AC-8** — A test asserts the child environment equals parent + `spec.env` exactly, and that agent-internal credentials are absent (test asserts variable names).
- **AC-9** — Resource enforcement is implemented per field: `gpus` enforced, `cpus` advisory, `memory_gb`/`vram_gb` unenforced — test for each enforced field.
- **AC-10** — Log lines have a bounded length and the child cannot exhaust agent memory through a single pathological line — tested.
- **AC-11** — Undecodable output bytes are handled by a stated policy (replace) and tested.
- **AC-12** — A long-running job does not block the agent's other duties (heartbeat, log shipping) — tested with a job that sleeps.

### Quality

- **AC-13** — `pytest -q` → all green; the full-suite count does not drop below the current baseline and `.shc/secret_key` is unchanged by the run.
- **AC-14** — `ruff check . && ruff format --check . && mypy .` all exit 0. No new `type: ignore`, and no widening of existing narrow overrides in `pyproject.toml`.
- **AC-15** — Unit tests for `execute_job`, `validate_and_prepare_working_dir`, `build_child_env`, `BoundedLineWriter` with faked subprocess and filesystem.

---

## 7. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Shell injection via `command` | Medium | High | Documented security boundary; shared token = trusted submitters only |
| `CUDA_VISIBLE_DEVICES` not respected by user code | Medium | Medium | Documented; user code must honour it (standard CUDA practice) |
| `os.sched_setaffinity` unavailable on macOS/Windows | High | Low | Advisory only; graceful no-op with debug log |
| Single pathological log line exhausts memory | Low | High | AC-10: bounded line length, streaming write |
| Agent blocked on long-running job | Medium | High | AC-12: execution in background supervised task |
| `paths.input` race: file appears after check | Low | Medium | Check is best-effort; Syncthing is eventual; documented |
| Process group signal delivery fails on some platforms | Low | Medium | Tested on Linux; documented limitation |

---

## 8. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Implementation complete (SWE)
- [ ] AC-1 … AC-15 verified with evidence (QA)
- [ ] `pytest -q` green; `ruff`/`mypy` clean
- [ ] PM acceptance

---

## 9. Related

- #97 — agent claims a job (writes `RUNNING`, `node_id`, `started_at`)
- #99 — cancel job (sends signal to process group)
- #100 — terminal job status (agent writes `COMPLETED`/`FAILED`/`CANCELLED` with real `exit_code` and `error`)
- #101 — log ingestion (consumes the log files this issue produces)
- #105 — job submission folder (the file watch that delivers `state.yaml`)
- `shared/schemas/job_spec.py` — the contract being executed
- `shared/file_ops/path_utils.py:is_within_syncthing_root` — the containment check