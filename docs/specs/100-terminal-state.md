# Spec: Terminal Job State (Agent + Backend)

**Issue**: #100 — "Only the seeder and cancel ever write a terminal job status; exit_code and error are unreachable for a real run"
**Labels**: `agent`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-14

---

## 1. Verified Problem Statement

Only two components in the repository ever write a terminal job status:

1. The demo seeder (`backend/store/database_store.py:143-154`) — writes `COMPLETED`/`FAILED` with invented exit codes and errors
2. `job_service.cancel_job` (`job_service.py:73-88` in current code, lines 326-341) — writes `CANCELLED` with `exit_code = -1`

Nothing else writes `COMPLETED` or `FAILED`. A job that runs to completion has no path to a terminal state, and `exit_code` and `error` are unreachable for any real run.

The fields exist and are nullable in the database (`backend/store/database.py:41-46`) and schema (`shared/schemas/job_state.py:14-28`):
```python
created_at   = Column(DateTime, nullable=False)
started_at   = Column(DateTime)
completed_at = Column(DateTime)
exit_code    = Column(Integer)
error        = Column(Text)
retry_count  = Column(Integer, nullable=False, default=0)
```

Three specific hazards identified in the issue:

**Hazard 1 — Retry on wrong side of the wire.** `retry_job` (`job_service.py:53-71`, now lines 243-292) is an operator action: validates `FAILED`/`CANCELLED`, resets to `PENDING`, increments `retry_count`, clears terminal fields. Meanwhile `JobSpec.retry` carries `max_retries: 0-10 (default 3)` and `retry_delay_seconds: 0-3600 (default 60)` (`shared/schemas/retry.py:8-14`). **Nothing reads `spec.retry`.** Two competing retry concepts exist — automatic (spec) and manual (endpoint) — and it's undefined whether the agent honours `max_retries`.

**Hazard 2 — `error` is bare `Text` with no structure.** No error code, no distinction between launch failure and process failure. `ErrorResponse` shape exists (`shared/schemas/error_response.py:8`) but is an HTTP envelope, not part of `JobState`.

**Hazard 3 — Cleaning up deletes the job.** `syncthing_service.py:310-314` routes deleted `state.yaml` to `store.delete_job`, which purges `gpu_metrics`, `cpu_metrics`, `log_entries` (`database_store.py:474-482`). An agent that removes its scratch directory on completion makes the finished job and its telemetry disappear.

---

## 2. Root Cause

The agent's `_write_final_state` seam (`agent/loop.py:503-519`) is a no-op. The claim logic (`agent/claim.py`) writes `RUNNING` but nothing writes the terminal transition. The backend's `_process_job_file` (`syncthing_service.py:268-303`) accepts whatever the agent writes but has no monotonicity enforcement — it would overwrite a terminal state if the agent wrote one.

---

## 3. Open Questions — Resolved

The issue left five questions open. Each is decided here.

### Q1 — Automatic or manual retry?

**Decision: Manual only. Remove `spec.retry.max_retries` and `retry_delay_seconds` from the contract.**

Rationale:
- `README.md:55` describes "Manual retry workflow (user inspects logs before retry)"
- `job_service.py:retry_job` implements manual retry and is the only wired retry path
- `spec.retry` says automatic with default 3 attempts — this is a lie the user can see in their job spec
- Automatic retry would require the agent to re-launch the same command, but after completion `working_dir` contains the *output* of the previous run. For a simulation that appends/overwrites its own files, re-running is unsafe.
- **Action**: Remove `RetryPolicy` from `JobSpec` (breaking change to schema, documented). The `retry` field in `JobSpec` is removed entirely. `JobState.retry_count` remains (owned by backend).

### Q2 — Does the agent retry, or does the backend re-queue?

**Decision: Backend re-queues via manual `retry_job` endpoint. Agent never retries.**

The agent executes once. On any terminal outcome, it writes terminal state and stops. The operator decides to retry via API.

### Q3 — What should `error` contain?

**Decision: A documented vocabulary of machine-readable codes distinguishing launch failure from process failure.**

| Code Pattern | Meaning | Written By |
|---|---|---|
| `launch_failed:working_dir_not_in_syncthing_root` | `working_dir` outside `SYNCTHING_ROOT` | Agent (pre-launch) |
| `launch_failed:working_dir_not_found` | `working_dir` outside Syncthing root, doesn't exist | Agent (pre-launch) |
| `launch_failed:working_dir_create_failed` | Inside Syncthing root, `mkdir` failed | Agent (pre-launch) |
| `launch_failed:input_missing` | `paths.input` set but doesn't exist | Agent (pre-launch) |
| `launch_failed:command_not_found` | Shell exit 127 | Agent (launch) |
| `process_failed:exit_code=<N>` | Process ran, exited with code N (N != 0) | Agent (post-process) |
| `cancelled:by_operator` | Cancelled via API (#99) | Backend (cancel_job) |

The `error` field holds exactly one code. No stderr tail, no free text. The code is machine-parseable and documented.

### Q4 — Should a job be re-runnable without the retry endpoint?

**Decision: No. Retry means the manual endpoint. Re-running the same command against previous output is unsafe and not supported.**

### Q5 — Who owns `started_at` if the agent dies before writing it?

**Decision: Agent owns `started_at`. It writes `started_at` on claim (already done in #97). If the agent dies after claim but before launch, the job stays `RUNNING` with a `started_at` but no terminal state. #103 (restart recovery) will handle reconciliation.**

---

## 4. Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| Agent writes terminal state to `state.yaml` on process exit | Automatic retry logic |
| Backend enforces monotonicity (terminal state never overwritten) | `ErrorResponse` shape in `JobState` |
| Documented error vocabulary (agent + backend) | Stderr tail in `error` field |
| `retry_count` owned by backend only | Agent deleting `state.yaml` on completion |
| Agent NEVER deletes `state.yaml` | Cleanup of per-job scratch dir (allowed) |
| Timezone handling for `completed_at` (naive local, matches store) | Changing `JobState` schema beyond removing `retry` |

### Coupling to #98 — recorded, not fixed

#98's execution task produces the `exit_code` and determines the terminal status. This spec defines what the agent writes and how the backend handles it.

### Coupling to #99 — recorded, not fixed

#99 (cancel) will set `cancelled:by_operator` via backend `cancel_job`. The agent's execution task must handle `SIGTERM` to the process group and write `CANCELLED` if it catches the signal.

### Coupling to #103 — recorded, not fixed

#103 (restart recovery) handles jobs stuck in `RUNNING` after agent crash. This spec does not solve it; the monotonicity rule here makes #103's job easier (terminal states are final).

---

## 5. Implementation Approach

### 5.1 Agent: Write Terminal State

In `agent/executor.py` (from #98), on process exit:

```python
async def execute_job(...) -> tuple[int, str, str]:
    ...
    # Process exited
    exit_code = proc.returncode
    if exit_code == 0:
        status = JobStatus.COMPLETED
        error = None
    else:
        status = JobStatus.FAILED
        error = f"process_failed:exit_code={exit_code}"
    
    # Write terminal state to state.yaml
    await write_terminal_state(
        paths=paths,
        job_id=job_id,
        status=status,
        exit_code=exit_code,
        error=error,
    )
    return exit_code, stdout_path, stderr_path
```

`write_terminal_state`:
```python
async def write_terminal_state(
    *,
    paths: AgentPaths,
    job_id: str,
    status: JobStatus,
    exit_code: int,
    error: str | None,
) -> None:
    """Write COMPLETED/FAILED/CANCELLED to state.yaml. Never deletes the file."""
    state_file = paths.jobs_dir / job_id / "state.yaml"
    now = datetime.now()  # naive local, matches store convention
    
    # Read current state to preserve spec, created_at, retry_count
    current = read_yaml(str(state_file), JobState)
    
    terminal_state = JobState(
        job_id=current.job_id,
        spec=current.spec,
        status=status,
        node_id=current.node_id,
        created_at=current.created_at,
        started_at=current.started_at,
        completed_at=now,
        exit_code=exit_code,
        error=error,
        retry_count=current.retry_count,  # NEVER incremented by agent
    )
    
    write_yaml(str(state_file), terminal_state)
    # Record in ledger so agent's own watcher ignores it
    if watcher:
        watcher.ledger.record(state_file)
```

**Critical**: The agent NEVER deletes `state.yaml`. It NEVER increments `retry_count`.

### 5.2 Backend: Monotonicity Enforcement

In `backend/services/syncthing_service.py:_process_job_file`, add monotonicity check:

```python
async def _process_job_file(self, job_id: str, file_path: str):
    ...
    job_state = read_yaml(str(Path(file_path)), JobState)
    
    store = get_store()
    existing_job = await store.get_job(job_id)
    
    if existing_job:
        # MONOTONICITY: If existing job is in a terminal state, reject any write
        # that would change status, exit_code, error, or completed_at.
        if existing_job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            # Terminal states are final. Log and ignore.
            logger.warning(
                f"Job {job_id} is already {existing_job.status.value}; "
                f"ignoring agent write attempting to set {job_state.status.value}"
            )
            return
        
        # Allow PENDING -> RUNNING, RUNNING -> terminal
        # But not RUNNING -> PENDING (that's retry_job's job)
        if existing_job.status == JobStatus.RUNNING and job_state.status == JobStatus.PENDING:
            logger.warning(
                f"Job {job_id} is RUNNING; ignoring agent write attempting to set PENDING"
            )
            return
    
    # ... proceed with update/create
```

This ensures:
- `COMPLETED`/`FAILED`/`CANCELLED` → never overwritten
- `RUNNING` → `PENDING` rejected (only `retry_job` does this)
- `PENDING` → `RUNNING` allowed (claim)
- `RUNNING` → terminal allowed (completion)

### 5.3 Backend: `retry_job` Ownership of `retry_count`

`job_service.py:retry_job` (lines 243-292) already increments `retry_count`. This remains the **sole owner**. The agent never touches `retry_count`.

### 5.4 Schema Change: Remove `RetryPolicy` from `JobSpec`

`shared/schemas/job_spec.py` — remove the `retry` field entirely:

```python
class JobSpec(BaseModel):
    name: str = Field(..., pattern="^[a-zA-Z0-9_-]+$", min_length=3)
    command: str = Field(..., min_length=3)
    working_dir: str = Field(default="/sync/projects/new-run")
    env: Dict[str, str] = Field(default_factory=dict)
    resources: Resources
    paths: Paths = Field(default_factory=Paths)
    # retry: RetryPolicy = Field(default_factory=RetryPolicy)  # REMOVED
```

`shared/schemas/retry.py` — keep the file (may be used elsewhere) but it is no longer referenced by `JobSpec`.

### 5.5 Timezone Handling

- `completed_at` written by agent: **naive local** `datetime.now()` (matches every other writer in the repo: `job_service.py:82`, `database_store.py:341,1055,1077`)
- Backend `_heartbeat_monitor` uses aware UTC but compares against naive local — the agent matching the store's convention is what makes the arithmetic work (see #95 §5 correction)
- `started_at` is already written by agent on claim (naive local)

---

## 6. Acceptance Criteria

### Functional

- **AC-1** — A job that runs and exits 0 reports `COMPLETED` with the real exit code, visible through `GET /jobs/{id}`, asserted against an actual child process.
- **AC-2** — A job whose command exits non-zero reports `FAILED` with the real exit code and a non-empty `error` — tested by running a command that deliberately fails.
- **AC-3** — A job the agent cannot launch at all (bad `working_dir`, missing input) reports `FAILED` with an `error` that distinguishes agent-side failure from process failure.
- **AC-4** — The `error` vocabulary is documented, and a test asserts the agent uses the documented values rather than free text.
- **AC-5** — The finished job and its metrics and logs survive, verified through the API after the agent has finished with it. A test asserts the agent does not remove `state.yaml` on completion.
- **AC-6** — `retry_count` has exactly one owner (backend), stated in the docs, with a test asserting the count after a manual retry is exactly 1 — not 2.
- **AC-7** — The decision on `spec.retry` is recorded: removed from the contract. `max_retries` and `retry_delay_seconds` are no longer in `JobSpec`.
- **AC-8** — Timezone handling is pinned end-to-end for `started_at` and `completed_at`, consistent with the coercion at `syncthing_service.py:335-336` and with issue #5.
- **AC-9** — Timestamps written by the agent are naive local (matching the store).
- **AC-10** — Backend monotonicity: terminal state never overwritten (test reproduces race where agent writes terminal state, then backend receives a stale `RUNNING` write and rejects it).
- **AC-11** — `cancelled:by_operator` error code used by backend `cancel_job` (tested in #99).

### Quality

- **AC-12** — `pytest -q` → all green; full-suite count does not drop; `.shc/secret_key` unchanged.
- **AC-13** — `ruff check . && ruff format --check . && mypy .` all exit 0. No new `type: ignore`.
- **AC-14** — Unit tests for `write_terminal_state`, monotonicity check in `_process_job_file`, error vocabulary.

---

## 7. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Agent crashes between process exit and state write | Medium | High | Job stays `RUNNING`; #103 (restart recovery) reconciles. Monotonicity prevents corruption. |
| Backend receives stale `RUNNING` after terminal state | Medium | Medium | Monotonicity check rejects it (AC-10). |
| `retry_count` drift if agent also increments | Low | High | Agent never touches it; test asserts (AC-6). |
| Operator expects automatic retry (from old `spec.retry`) | Medium | Medium | Schema change documented; README updated; migration guide. |
| `state.yaml` deleted by overzealous cleanup | Medium | High | Explicit rule + test (AC-5); #100 hazard 3 documented. |
| Timezone mismatch breaks `completed_at` semantics | Low | High | Agent writes naive local to match store; AC-8/9. |

---

## 8. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Implementation complete (SWE)
- [ ] AC-1 … AC-14 verified with evidence (QA)
- [ ] `pytest -q` green; `ruff`/`mypy` clean
- [ ] PM acceptance

---

## 9. Related

- #97 — agent claims a job (writes `RUNNING`, `node_id`, `started_at`)
- #98 — job execution (produces `exit_code`, determines terminal status)
- #99 — cancel job (sets `cancelled:by_operator` via backend)
- #101 — log ingestion (reads logs written by #98)
- #103 — restart recovery (reconciles jobs stuck in `RUNNING`)
- #105 — job submission folder (file watch delivers `state.yaml`)
- `shared/schemas/job_spec.py` — `retry` field removed
- `shared/schemas/job_state.py` — `retry_count` remains, `error` vocabulary documented
- `backend/services/job_service.py:retry_job` — sole owner of `retry_count`
- `backend/services/syncthing_service.py:_process_job_file` — monotonicity enforcement