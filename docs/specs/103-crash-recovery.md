# Spec: Crash Recovery — Reconciling Orphaned RUNNING Jobs

**Issue**: #103 — "agent restart leaves job RUNNING, nothing repairs"
**Labels**: `agent`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-14

---

## 1. Verified Problem Statement

An agent that dies mid-job leaves the job in `RUNNING` forever. Nothing in the
system notices, and nothing repairs it.

Verified against the current code:

- `agent/loop.py::_hand_back_in_flight_job` is the shutdown seam for #103. It
  signals the execution task and clears `_current_job_id`, but it **does not**
  write a terminal state for the job. The job's `state.yaml` stays `RUNNING`
  with `node_id` still set, and the node file keeps `current_job_id` set.
  That is the *clean shutdown* case, and it is already only a half-answer.
- The *hard crash* case (`SIGKILL`, power loss, OOM) never runs that seam at
  all. The agent process is gone; the job file says `RUNNING`; the node
  heartbeat goes stale.
- `backend/services/syncthing_service.py::_heartbeat_monitor_iteration` marks a
  node `OFFLINE` once its heartbeat is older than `HEARTBEAT_TIMEOUT_SECONDS`
  (default 90 s, observed every 30 s), but it **never looks at
  `current_job_id` and never touches a job**. A node that dies mid-job leaves
  `node.status == OFFLINE` and `job.status == RUNNING` — a consistent-looking
  and entirely wrong pair of rows.
- `NodeModel.current_job_id` (`backend/store/database.py:79`) exists and is
  populated by the agent's registration tick (`agent/loop.py:253`), but #97 is
  the issue that makes it load-bearing at claim time. This issue cannot work
  without that.
- `JobState` carries no process identity (`shared/schemas/job_state.py`). A
  PID is not reliable across a reboot, and nothing in the schema carries one.
  So even a live agent cannot tell, on restart, whether the process it launched
  before it died is still running.

The issue's own framing, sharpened by the file-only decision (#92): under HTTP
the agent could poll. Under file-only the **backend** has to notice, because the
agent is the thing that died. Recovery becomes primarily a backend-side sweep,
not an agent behaviour. The agent's own startup path covers only the cases where
the agent comes back and can inspect its own leftovers.

### What is detectable, and what is not

Detectable by the backend:

- `nodes/<id>.yaml` heartbeat stale past the 90 s threshold → node `OFFLINE`
  (`syncthing_service.py:529-530`). That signal already exists and already works.
- `nodes.current_job_id` still set for an `OFFLINE` node → a job is very
  likely orphaned.
- A job `RUNNING` whose `node_id` names an `OFFLINE` (or missing) node.

Not detectable by the heartbeat monitor today:

- It never looks at `current_job_id`, never touches a job, and clears nothing.
- It cannot distinguish "node died mid-job" from "node was power-cycled between
  jobs". It does not have to: in both cases the right action is the same.

---

## 2. Root Cause

The recovery seam was declared but never filled. `#93` left two deliberate
no-ops in `agent/loop.py`:

- `_hand_back_in_flight_job` (line 641) — the shutdown hand-back seam, owned by
  #103.
- `_write_final_state` (line 669) — the terminal-state seam, owned by #100.

#100 filled the terminal-state write on the *execution* path (`agent/terminal.py`
and `agent/executor.py`), but neither issue filled the *reconciliation* path:
nothing sweeps orphaned `RUNNING` jobs, and nothing on the backend side ever
transitions one to a terminal state because of a dead node. The monotonicity
rule from #100 (`syncthing_service.py:327-352`) already makes such a transition
safe — a terminal write is final and cannot be overwritten — but nobody issues
it.

A second, independent gap: there is no durable process identity. A job can be
in `RUNNING` with no process attached (the very state #103 exists to detect),
and neither the agent nor the backend can tell whether a process is still
attached. `JobState` carries no PID, and a PID would not survive a reboot
anyway.

---

## 3. Open Questions — Resolved

The issue left several questions open. Each is decided here.

### Q1 — Lease or reconciliation?

**Decision: Reconciliation only. A lease is impossible under file-only.**

Nothing coordinates expiry under a file transport: there is no central clock,
no lock service, and no atomic compare-and-swap that all nodes share. A lease
would require either a lease-holder or a quorum, and neither exists in this
architecture. Reconciliation against the heartbeat signal is the only viable
option. The open question narrows to "how clean can reconciliation be made",
and the answer is: as clean as the heartbeat contract (#96) and `current_job_id`
(#97) allow.

### Q2 — Should the sweep run in `syncthing_service.py`?

**Decision: Yes — in `_heartbeat_monitor_iteration`, as a separate
`_recover_orphaned_jobs()` call. Do not move the monitor.**

The monitor is where the heartbeat signal lives and where `OFFLINE` is decided.
Adding recovery there is pragmatic; moving the monitor is scope creep (#96
open question 4 already deferred that). `syncthing_service.py` is excluded
from mypy (`pyproject.toml:70-80`), so the recovery code is type-checked only
by convention — but the service's existing methods are already the home of
this logic, and #54 tracks the broader lint/type situation.

### Q3 — Recovery action: fail or re-queue?

**Decision: Fail the job with an explanatory `error`. No automatic re-queue.**

Rationale:

- `README.md:55` describes "Manual retry workflow (user inspects logs before
  retry)".
- Re-queueing invites a crash loop on a job that reliably kills the node — a
  GPU job that OOMs the machine would be re-run forever.
- #100 removed automatic retry from the contract entirely (`spec.retry` is
  gone). There is no retry policy to honour.
- The operator retries explicitly via `POST /jobs/{id}/retry` once they have
  inspected the logs.

The recovery writes `status = FAILED`, `error = "orphaned:node_offline"`,
`completed_at = now`, leaves `exit_code = None` (no process exit to report),
and clears the node's `current_job_id`.

### Q4 — Does the backend delete the job directory on recovery?

**Decision: No. Failing a job is not deleting it.**

#105 D8 and #100 hazard 3 both depend on the delete rule staying narrow: only
`DELETE /jobs/{id}` removes a directory. Recovery leaves `state.yaml` in place
with its terminal state, so the agent's confirming write (if it ever arrives)
is refused by monotonicity rather than resurrecting a deleted job. Metrics and
logs survive.

### Q5 — Does recovery emit a command file?

**Decision: No. UI-only.**

A command file is a request to a *live* node (#106). An offline node cannot
ack, and an ack that never arrives would leave a dangling "requested" state
with no meaning. The orphaned state is surfaced through `GET /jobs/{id}` and
through the node's `current_job_id` being cleared. If the UI needs a
"recovered" badge, it derives it from `status == FAILED` and
`error.startswith("orphaned:")`.

### Q6 — Durable process identity: what scheme?

**Decision: A marker file written before launch, carrying a run token and,
once the process exists, its PID and start time.**

See §5.5. The marker is the only durable identity that survives an agent
restart on the same machine, and the only thing that lets the agent distinguish
"a process I launched is still running" from "nothing is running".

### Q7 — Agent startup path: adopt or fail?

**Decision: Fail by default; adopt only when a live process is provably the
same run.**

See §5.6. Adopting a process the agent did not supervise (no stdout/stderr
capture, no stop handle) would be worse than failing it: the job would appear
healthy while being uncontrollable. The default is to terminate any surviving
process and write a terminal `FAILED` state. Adoption is reserved for a future
issue and is not implemented here.

### Q8 — Clean shutdown vs hard crash: how are they distinguished?

**Decision: By the marker and by the node heartbeat, not by the job file.**

- Clean shutdown (#93): the agent ran `_hand_back_in_flight_job`, which sets
  the stop event and clears `_current_job_id` and the node's `current_job_id`,
  but leaves `state.yaml` as `RUNNING`. On restart the agent finds its own
  `RUNNING` job with a marker whose process is dead (the shutdown stopped it).
  It writes `FAILED` with `error = "orphaned:agent_restart"`? No — that would
  be wrong for a clean hand-back. Instead it writes nothing and leaves the job
  `RUNNING` for the backend? That is also wrong.

Correction: the clean-shutdown case is handled by #93's own decision, not by
this issue. #93's hand-back seam is a no-op *by design* in #93; #103 fills it
so that a clean shutdown writes the correct terminal state. See §5.6 and D5:
a stop driven by the shutdown path is **not** recorded as
`cancelled:by_operator`; the job's final state is decided by the execution
task's own terminal write (which happens when the stop event is set), exactly
as it does for an operator cancel. The difference is only the `error` code:
`stopped:shutdown` (from #125's vocabulary) rather than `cancelled:by_operator`.

So: clean shutdown → job ends `FAILED`/`CANCELLED`-equivalent with
`stopped:shutdown` via the normal execution path; hard crash → backend
recovery writes `orphaned:node_offline`. The two are distinguishable by
`error` and by whether a stop event was delivered.

---

## 4. Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| Backend sweep that fails orphaned `RUNNING` jobs when their node is `OFFLINE` | Automatic re-queue / retry |
| Clearing `nodes.current_job_id` on recovery | Moving the heartbeat monitor out of `syncthing_service.py` (#96 Q4) |
| Durable process-identity marker written before launch | Adopting a process the agent did not supervise (future issue) |
| Agent startup sweep of its own `RUNNING` jobs | HTTP transport for recovery |
| Documented recovery latency (~2 min) | Deleting the job directory on recovery |
| `error` vocabulary additions (`orphaned:*`) | Lease-based coordination |
| Idempotent recovery (running twice is a no-op) | #97's claim-time `current_job_id` write (owned by #97) |

### Coupling to #97 — recorded, not fixed here

#97 sets `current_job_id` at claim and clears it at completion. This issue's
detection depends on that: without it, the backend has no way to know which job
a dead node was running. This issue reads `current_job_id`; it does not write
it at claim time. The agent's registration tick already mirrors
`_current_job_id` into the node file (`agent/loop.py:253`); #97's claim path
is what makes that value correct.

### Coupling to #100 — recorded, not fixed here

#100's monotonicity rule (`syncthing_service.py:327-352`) is what makes
recovery safe: once the backend writes the terminal state, the agent's
confirming write (if it ever arrives) is refused. Recovery relies on that rule
and does not re-implement it. #100's `error` vocabulary is extended here with
the `orphaned:` family (see §5.3).

### Coupling to #93 — recorded, not fixed here

#93 left `_hand_back_in_flight_job` as a no-op seam. This issue fills it so
that a clean shutdown writes the correct terminal state via the execution
task's stop path, rather than leaving `RUNNING` behind. #93's seam signature is
unchanged; only its body grows.

### Coupling to #106 — recorded, not fixed here

Recovery does not emit a command file (#106). If the operator must be told a
job was orphaned, that surfaces through the job's terminal state and through
`current_job_id` being cleared, not through a file a dead node would have to
ack.

### Coupling to #96 — recorded, not fixed here

The 90 s timeout and 30 s observation interval are #96's contract. Recovery
inherits that latency floor and documents it as user-visible. No new threshold
is introduced.

---

## 5. Implementation Approach

### 5.1 Backend: recovery sweep in the heartbeat monitor

Add a method `SyncthingService._recover_orphaned_jobs()` and call it at the
end of `_heartbeat_monitor_iteration`, after the node status flips. It runs on
every iteration regardless of whether any node changed, because a node can be
`OFFLINE` from a previous iteration with an orphaned job nobody has handled
yet (for example, if the first recovery attempt's DB write failed).

```
async def _recover_orphaned_jobs(self, now):
    store = get_store()
    nodes = await store.list_nodes()
    for node in nodes:
        if node.status != "OFFLINE":
            continue
        if not node.current_job_id:
            continue
        job = await store.get_job(node.current_job_id)
        if job is None:
            # Job row is gone but node still references it. Clear the pointer.
            await store.update_node(node.node_id, current_job_id=None)
            continue
        if job.status in (COMPLETED, FAILED, CANCELLED):
            # Already terminal. Just clear the pointer.
            await store.update_node(node.node_id, current_job_id=None)
            continue
        if job.node_id != node.node_id:
            # Stale pointer: node references a job it does not own.
            logger.warning(...)
            await store.update_node(node.node_id, current_job_id=None)
            continue
        # Orphaned RUNNING job on an OFFLINE node.
        await store.update_job(
            job.job_id,
            status=JobStatus.FAILED,
            completed_at=now,
            exit_code=None,
            error="orphaned:node_offline",
        )
        # Also update state.yaml? No: the backend does not write state.yaml.
        # The agent's file, if it ever arrives, is refused by monotonicity.
        await store.update_node(node.node_id, current_job_id=None)
        logger.warning(
            "recovered orphaned job %s on offline node %s",
            job.job_id, node.node_id,
        )
```

Notes:

- The backend does **not** write `state.yaml`. Only the agent writes the
  folder (D2 of #106). The DB row is the backend's view; `state.yaml` is the
  agent's. If the agent is dead, `state.yaml` stays `RUNNING` on disk, and the
  next scan ingests it but monotonicity refuses the overwrite. That is the
  intended, documented divergence: the DB says `FAILED`, the folder says
  `RUNNING`, and the folder's value is stale. Operators reading the folder
  directly see the truth about the last process; operators reading the API see
  the truth about the cluster's decision.
- `now` is passed in for testability, matching `_heartbeat_monitor_iteration`'s
  existing `now` parameter.
- The sweep is idempotent by construction: a terminal job is skipped, and a
  cleared pointer is skipped.

### 5.2 Node eligibility after recovery

After recovery, the node is still `OFFLINE` until its heartbeat returns. Once
the agent (or a new agent) writes a fresh heartbeat, `_heartbeat_monitor_iteration`
flips it back to `ONLINE` with `current_job_id = None`, so it is eligible for
new work. AC-5 asserts this end-to-end: submit a new job after recovery and
observe it claimed.

### 5.3 Error vocabulary additions (#100 Q3)

Extend the documented vocabulary with the `orphaned:` family:

| Code Pattern | Meaning | Written By |
|---|---|---|
| `orphaned:node_offline` | Node went `OFFLINE` while the job was `RUNNING`; job failed by backend recovery | Backend (recovery sweep) |
| `orphaned:agent_restart` | Agent restarted and found its own `RUNNING` job with no live process | Agent (startup sweep) |
| `orphaned:process_lost` | Marker exists but the recorded process is gone and cannot be verified | Agent (startup sweep) |

The backend writes only `orphaned:node_offline`. The agent writes the other two.
`exit_code` is `None` for all three — there is no process exit to report.

### 5.4 Idempotency

Stated as one table, because the issue asks for it in those terms:

| situation | outcome |
|---|---|
| Sweep runs twice on the same offline node | First run fails the job and clears the pointer; second run sees a terminal job and a null pointer and does nothing |
| Sweep runs while the agent is still alive but slow | The job is still `RUNNING` with a live node; node is `ONLINE`, so the sweep skips it. No false positive |
| Sweep runs after the agent wrote `FAILED` on startup | Job is terminal; sweep skips it and clears the pointer |
| Agent restarts and re-reads its own `RUNNING` job | Startup sweep writes `orphaned:agent_restart` (or `orphaned:process_lost`); backend sweep later sees terminal and skips |
| Recovery runs on a node that has no `current_job_id` | Nothing to do; no error |
| Recovery runs on a node whose referenced job is already deleted | Pointer cleared; no error |

### 5.5 Durable process identity

**File**: `jobs/<job_id>/.process.<node_id>` (a dotfile, so both watchers
ignore it: `syncthing_service.py:_is_relevant_file` rejects dotfiles, and
`agent/watcher.py` does the same).

**Written before launch**, in `agent/executor.py`, immediately before
`subprocess.Popen`. The write is two-phase:

1. **Before launch** — create the marker with `node_id`, `job_id`, and
   `issued_at = datetime.now()` (naive local, matching the store convention).
   This is the durable record that a run was *about to start*. If the agent
   crashes here, the marker exists but has no PID; the startup sweep treats
   that as `orphaned:process_lost`.
2. **Immediately after launch** — update the marker with `pid`, `pgid` (where
   available) and `started_at = datetime.now()`. The update is atomic
   (`write_yaml` uses temp + `os.replace`).

The marker is **never deleted by the agent on completion**. It is removed only
when the job directory is removed (`DELETE /jobs/{id}`), which bounds its
lifetime (#105 D14). Keeping it after completion is deliberate: a restarted
agent reading a terminal job's marker can verify that the process it recorded
is gone, which is how it distinguishes "I crashed and the job died with me"
from "I crashed and the job is still running somewhere".

**Liveness check** (agent startup, §5.6):

- Read the marker. If absent, treat as `orphaned:process_lost`.
- If `pid` is present, probe with `os.kill(pid, 0)`:
  - `ProcessLookupError` → process is gone → `orphaned:agent_restart`.
  - Success → process may be alive. Verify the start time matches by
    comparing the marker's `started_at` against the process's actual start
    time (platform-specific: `/proc/<pid>/stat` field 22 on Linux,
    `GetProcessTimes` on Windows). If they match, the process is the same run
    → leave it running and write nothing (the job is still `RUNNING`; the
    backend will not recover it because the node is still `ONLINE` via a fresh
    heartbeat). If they do not match, a new process reused the PID →
    `orphaned:process_lost`.

The cross-platform start-time verification is the part that is hardest to get
right; if it cannot be implemented reliably on a platform, that platform falls
back to treating any surviving PID as `orphaned:process_lost` (fail rather
than adopt). Adoption is not attempted in this issue (Q7).

### 5.6 Agent: startup path

On boot, after the folder becomes usable and before the watcher starts (i.e.
inside `_ensure_watcher_started` or immediately after it), the agent runs a
one-shot `_recover_own_jobs()`:

```
async def _recover_own_jobs(self):
    jobs_dir = self._paths.jobs_dir
    if not jobs_dir.exists():
        return
    for job_dir in jobs_dir.iterdir():
        if not (job_dir.is_dir() and job_dir.name.startswith("job-")):
            continue
        state_file = job_dir / "state.yaml"
        if not state_file.exists():
            continue
        try:
            state = read_yaml(str(state_file), JobState)
        except Exception:
            continue
        if state.status != JobStatus.RUNNING:
            continue
        if state.node_id != self._settings.NODE_ID:
            continue
        # This is our own orphaned RUNNING job.
        marker = self._read_process_marker(job_dir, self._settings.NODE_ID)
        live = self._process_is_live(marker)
        if live:
            # Cannot supervise it. Terminate and fail.
            if marker.pid is not None:
                try:
                    os.kill(marker.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            await write_terminal_state(
                paths=self._paths,
                job_id=job_dir.name,
                status=JobStatus.FAILED,
                exit_code=None,
                error="orphaned:process_lost",
                watcher=self._watcher,
            )
        else:
            await write_terminal_state(
                paths=self._paths,
                job_id=job_dir.name,
                status=JobStatus.FAILED,
                exit_code=None,
                error="orphaned:agent_restart",
                watcher=self._watcher,
            )
        # Clear local state.
        self._current_job_id = None
```

Also, `_hand_back_in_flight_job` (the #93 seam) is filled so that a clean
shutdown sets the job's stop event and lets the execution task write its own
terminal state with `stopped:shutdown`. It does not write a terminal state
itself — that is #100's seam, and the execution task already does it when the
stop event fires. #103's contribution is only to ensure the stop event is set
and `_current_job_id` is cleared, which it already does (the `pass` at the
old line 550 was fixed by #125). What #103 adds is the node-file write that
clears `current_job_id` on disk, so the backend's recovery sweep does not
mistake a cleanly-shut-down job for an orphan. This is the one place where
#103 and #93 meet.

### 5.7 Timing and user-visible latency

The recovery latency floor is set by #96's contract, not by this issue:

- Heartbeat interval: ~22 s (agent), observed by the backend every 30 s.
- Offline threshold: 90 s.
- Worst case: a node dies immediately after a heartbeat. The backend notices
  at the next 30 s tick that the heartbeat is 30 s stale (still `ONLINE`),
  then at the 60 s tick 60 s stale (still `ONLINE`), then at the 90 s tick
  90 s stale — at which point `delta > 90` is false at exactly 90, so the flip
  happens at the 120 s tick. Then recovery runs on that same tick.

So the user-visible delay from node death to job `FAILED` is **up to ~2 minutes**.
This is stated in `docs/spec.md`'s backend-integration section and in the
README, so an operator is not surprised by a job that stays `RUNNING` for two
minutes after a node dies. AC-6 pins the documented number.

### 5.8 Concurrency and cost

- The sweep runs once per heartbeat tick (every 30 s) and touches only
  `OFFLINE` nodes with a non-null `current_job_id`. On a healthy cluster it
  does one `list_nodes()` and zero `get_job()` calls beyond what the monitor
  already makes.
- No locks. `file_lock`/`shared_lock` stay unused (#97 D5). The DB is the
  coordination point for recovery, and SQLAlchemy's own transaction semantics
  are sufficient for a single-writer backend.
- The marker file is written once per job launch and read once per agent
  startup. Negligible cost.

---

## 6. Decisions

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Reconciliation, not a lease.** Recovery is a backend sweep against the heartbeat signal. | A lease needs a coordinator that does not exist under file-only transport. #92 settled the transport; this issue accepts the consequence |
| **D2** | **The sweep runs in `_heartbeat_monitor_iteration`**, as `_recover_orphaned_jobs()`. | That is where `OFFLINE` is decided and where the 30 s cadence already lives. Moving it is scope creep |
| **D3** | **Recovery fails the job (`FAILED`, `error = "orphaned:node_offline"`). No auto-re-queue.** | Crash loops on node-killing jobs; README's manual retry workflow; #100 removed automatic retry |
| **D4** | **Recovery clears `current_job_id` but does not delete the job directory.** | Failing ≠ deleting (#100 hazard 3, #105 D8). Metrics and logs survive |
| **D5** | **`_hand_back_in_flight_job` sets the stop event and clears `current_job_id` on disk, but does not write a terminal state itself.** | The execution task writes the terminal state when the stop event fires (#100's seam). #103 only ensures the stop event is set and the node file is updated. Avoids two writers |
| **D6** | **No command file is emitted by recovery.** | A command needs a live node to ack. Orphaned state surfaces through the job row and `current_job_id` |
| **D7** | **Durable process identity is a dotfile marker written before launch.** | The only durable record that survives an agent restart on the same machine. A PID alone is not reliable; the marker carries the start time for verification |
| **D8** | **The marker is never deleted by the agent on completion.** | A restarted agent reading a terminal job's marker can verify the process is gone. Lifetime is bounded by `DELETE /jobs/{id}` |
| **D9** | **Agent startup defaults to fail, not adopt.** | An unsupervised process cannot be controlled (no stdout, no stop handle). Failing and terminating is safer than pretending to supervise |
| **D10** | **Recovery latency is ~2 minutes, documented as user-visible.** | Inherited from #96's 90 s + 30 s. Stating it prevents "why is my job still RUNNING" tickets |
| **D11** | **`exit_code` is `None` for every orphaned terminal state.** | There is no process exit to report. `-1` is the backend's cancel sentinel and must not be reused |
| **D12** | **No new threshold, no new settings, no new dependency, no DB migration.** | Recovery reuses #96's contract and #97's column. The marker is a plain YAML file written with the existing `write_yaml` |

---

## 7. Acceptance Criteria

The issue's revised acceptance criteria are the backbone. None is dropped and none
is left as a comment about the code — each is an assertion with a named test.

| Origin | Where it went |
|---|---|
| Issue AC (orphaned job detected within stated interval) | AC-1, AC-2 |
| Issue AC (`current_job_id` set at claim, cleared at completion) | AC-3 |
| Issue AC (recovery clears `current_job_id`; `GET /nodes/{id}` shows no phantom) | AC-4 |
| Issue AC (recovered node eligible for new work) | AC-5 |
| Issue AC (recovery idempotent) | AC-6 |
| Issue AC (recovery action documented and tested: status, error, no re-queue) | AC-7, AC-8 |
| Issue AC (clean restart vs hard crash distinguished) | AC-9, AC-10 |
| Issue AC (agent startup path defined and tested) | AC-11, AC-12 |
| Issue AC (durable process identity specified) | AC-13, AC-14 |
| Issue AC (recovery does not depend on `/health`) | AC-15 |
| Issue open question 1 (lease vs reconciliation) | D1 (decided) |
| Issue open question 2 (sweep in `syncthing_service`?) | D2 (decided) |
| Issue open question 3 (agent's file tells it if process alive?) | AC-14 |
| Issue open question 4 (backend deletes job dir on recovery?) | D4 (decided) |
| Issue open question 5 (recovery emits command file?) | D6 (decided) |
| — | AC-16, AC-17, AC-18 added by this analysis |

### Functional

- **AC-1 — A node that dies mid-job is detected and its job is failed by the
  backend.** A test drives the real `_heartbeat_monitor_iteration` with a fixed
  `now` past the threshold, with a node `ONLINE` whose `current_job_id` points
  at a `RUNNING` job, and asserts the job becomes `FAILED` with
  `error == "orphaned:node_offline"` and `exit_code is None`.
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_an_offline_node_fails_its_running_job`.

- **AC-2 — Recovery runs within the documented interval.** A test advances
  `now` in steps and asserts the job is still `RUNNING` at 90 s and `FAILED`
  by 120 s, matching §5.7. The two numbers are the assertion, not a comment.
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_recovery_latency_matches_the_documented_floor`.

- **AC-3 — `current_job_id` is set at claim and cleared at completion.** Two
  tests: (a) a successful claim leaves `node.current_job_id == job_id`; (b) a
  completed job leaves `node.current_job_id is None`. The claim side is #97's
  test; this issue asserts the cleared side, because recovery depends on it.
  *Proved by* `agent/tests/test_claim.py::test_claim_sets_current_job_id` and
  `agent/tests/test_loop.py::test_completion_clears_current_job_id`.

- **AC-4 — Recovery clears `current_job_id`.** After AC-1's recovery,
  `GET /nodes/{id}` (or the store read) reports `current_job_id is None`.
  Asserted on the same test fixture, so a recovery that fails the job but
  forgets the pointer fails here.
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_recovery_clears_current_job_id`.

- **AC-5 — The recovered node is eligible for new work.** After recovery and
  after a fresh heartbeat flips the node back to `ONLINE`, submit a new job and
  assert it is claimed by that node. Asserted end-to-end through the real claim
  path.
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_a_recovered_node_claims_new_work`.

- **AC-6 — Recovery is idempotent.** Run `_recover_orphaned_jobs()` twice on
  the same fixture and assert the second run produces no additional state
  change and no error. Also assert the job's `completed_at` is unchanged by
  the second run (so a double-run cannot reset the timestamp).
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_recovery_is_idempotent`.

- **AC-7 — Recovery action is exactly the documented one.** Assert
  `status == FAILED`, `error == "orphaned:node_offline"`,
  `exit_code is None`, `completed_at is not None`, and
  `retry_count` unchanged. Assert no command file and no ack file was written.
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_recovery_writes_the_documented_state`.

- **AC-8 — No automatic re-queue.** Assert `retry_count` is unchanged by
  recovery, and that `POST /jobs/{id}/retry` is still required to make the job
  `PENDING` again. A recovery that silently incremented `retry_count` or
  flipped the status to `PENDING` fails here.
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_recovery_does_not_requeue`.

- **AC-9 — Clean shutdown is distinguishable from hard crash.** A test runs a
  real agent to completion of a clean `SIGTERM` shutdown mid-job and asserts
  the job ends with `error == "stopped:shutdown"` (not `orphaned:*`). A
  separate test `SIGKILL`s the agent and asserts the backend writes
  `orphaned:node_offline`. The two `error` values are the distinguishing
  evidence.
  *Proved by* `agent/tests/test_crash_recovery.py::test_clean_shutdown_writes_stopped_shutdown`
  and `backend/tests/integration/test_crash_recovery.py::test_hard_crash_writes_orphaned_node_offline`.

- **AC-10 — A clean shutdown does not leave a phantom `current_job_id`.**
  After AC-9's clean case, the node file reports `current_job_id is None`,
  so the backend sweep never fires for a cleanly-shut-down job.
  *Proved by* `agent/tests/test_crash_recovery.py::test_clean_shutdown_clears_current_job_id`.

- **AC-11 — Agent startup fails its own orphaned `RUNNING` job.** A test
  creates a `RUNNING` job owned by this node with no live process (no
  marker, or a marker whose PID is dead), starts a real `Agent`, and asserts
  the job becomes `FAILED` with `error == "orphaned:agent_restart"` (or
  `orphaned:process_lost`).
  *Proved by* `agent/tests/test_crash_recovery.py::test_startup_fails_an_orphaned_running_job`.

- **AC-12 — Agent startup does not clobber a terminal job.** A test creates
  a `RUNNING` job, lets the agent write `FAILED` on startup, then re-runs the
  startup sweep and asserts the job stays `FAILED` with the same
  `completed_at`. Monotonicity is the guard; this test pins it from the
  agent side.
  *Proved by* `agent/tests/test_crash_recovery.py::test_startup_sweep_is_idempotent`.

- **AC-13 — The durable marker is written before launch.** A test
  monkeypatches `subprocess.Popen` to raise and asserts the marker file
  exists on disk afterwards (written in phase 1, before launch). A second
  assertion: the marker contains `node_id`, `job_id` and `issued_at` even when
  launch fails.
  *Proved by* `agent/tests/test_crash_recovery.py::test_marker_is_written_before_launch`.

- **AC-14 — The marker's liveness check is correct.** A table-driven test
  over `(marker_state, process_state)` pairs asserting the classification:
  no marker → `orphaned:process_lost`; marker with dead PID →
  `orphaned:agent_restart`; marker with live PID and matching start time →
  no write (job stays `RUNNING`); marker with live PID and mismatched start
  time → `orphaned:process_lost`.
  *Proved by* `agent/tests/test_crash_recovery.py::TestProcessLivenessClassification`.

- **AC-15 — Recovery does not depend on `/health`.** A test sets the
  backend's `/health` to report unhealthy (or does not start it) and asserts
  recovery still runs on the heartbeat tick. The recovery path never calls
  `/health` or any HTTP endpoint.
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_recovery_does_not_depend_on_health`.

- **AC-16 — The recovery sweep is idempotent on the pointer alone.** A test
  creates an `OFFLINE` node with `current_job_id` pointing at a job that no
  longer exists, runs the sweep, and asserts the pointer is cleared and no
  exception is raised. (The "dangling pointer" case from §5.1.)
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_dangling_current_job_id_is_cleared`.

- **AC-17 — A job whose node is `ONLINE` is never recovered.** A test
  creates a `RUNNING` job on an `ONLINE` node and runs the sweep repeatedly;
  the job stays `RUNNING`. This is the guard against false positives on slow
  jobs.
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_online_node_is_never_recovered`.

- **AC-18 — The recovery sweep does not write `state.yaml`.** An AST /
  filesystem assertion: after recovery, the job's `state.yaml` on disk is
  byte-identical to before (still `RUNNING`). The backend's only write is the
  DB row; the folder is the agent's.
  *Proved by* `backend/tests/integration/test_crash_recovery.py::test_recovery_does_not_touch_state_yaml`.

### Quality

- **AC-19 — `pytest -q` green for the modules this issue touches.** Baseline
  for `backend/tests/integration/test_crash_recovery.py`,
  `agent/tests/test_crash_recovery.py`, plus the existing
  `test_syncthing`, `test_job_folder_paths`, `test_api`, `test_api_fixes`,
  `test_store`, `test_shared_schemas` suites. Count must not drop below
  today's baseline.
- **AC-20 — `ruff check . && ruff format --check . && mypy .` exit 0.** No
  new `type: ignore`. `syncthing_service.py` remains excluded from mypy
  (#54); the recovery code there is checked by the existing tests instead.
- **AC-21 — `TestNoPrivateStoreAccess` still passes.** The recovery sweep
  uses only public store methods (`list_nodes`, `get_job`, `update_node`,
  `update_job`).
- **AC-22 — Coverage does not fall below `fail_under = 80`**, with
  `_recover_orphaned_jobs` and `_recover_own_jobs` measured explicitly rather
  than in the aggregate. Every branch of the liveness classification (AC-14)
  is covered by a named test.

---

## 8. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **False positive recovery** — a slow job on a node whose heartbeat slipped past 90 s is failed | Medium | High | Recovery only fires for `OFFLINE` nodes, and `ONLINE` → `OFFLINE` requires a genuine heartbeat gap (AC-17). The 22 s cadence leaves ~4× margin |
| **A process outlives recovery** — backend fails the job but the process keeps running on the node | Medium | Medium | The marker + agent startup sweep terminates it on the next agent boot (AC-11, AC-14). The process holds no cluster lease; it is just an orphaned OS process |
| **Double failure** — agent writes `orphaned:agent_restart` and backend writes `orphaned:node_offline` | Low | Low | Whichever lands first wins via monotonicity; the second is refused and logged at DEBUG (D11 of #106). The first writer's code is the one the operator sees |
| **Marker drift** — two conventions for the marker path (backend vs agent) | Low | High | The marker is agent-owned; the backend never reads it. Only `agent/paths.py` knows the path. AC-21's analogue is not needed because there is no backend counterpart |
| **Recovery touches a job the operator is mid-retry on** | Low | Medium | Retry writes `PENDING` first; a `PENDING` job is not `RUNNING`, so recovery skips it (AC-7 checks `retry_count` unchanged) |
| **The sweep masks a real bug** — a job that legitimately stays `RUNNING` for >2 min is killed | Low | High | The sweep keys on node `OFFLINE`, not on job age. A long-running job on a live node is never touched |
| **Startup sweep races the backend sweep** | Medium | Low | Both are idempotent and monotonicity resolves any conflict. The first writer wins; the second is a no-op |
| **Marker written but launch never happens** (crash between phase 1 and 2) | Low | Medium | Phase-1-only marker is classified as `orphaned:process_lost` (AC-14). Fail, don't adopt |
| **PID reuse** — a new process gets the dead process's PID | Very low | High | Start-time verification (§5.5) catches it; fallback is fail (AC-14). Never adopt on PID alone |
| **Platform-specific start-time lookup is unavailable** | Medium | Medium | Fall back to fail (AC-14's last row). Adoption is not attempted in this issue |

---

## 9. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Implementation complete (SWE)
- [ ] AC-1 … AC-22 verified with evidence (QA)
- [ ] `pytest -q` green for the touched modules (AC-19)
- [ ] `ruff` / `mypy` clean (AC-20)
- [ ] Recovery latency documented in `docs/spec.md` and README (AC-2)
- [ ] #93, #97, #100 cross-referenced in their bodies, so neither claims this
  issue's work
- [ ] PM acceptance

---

## 10. Related

- **#92** — the transport decision. Recovery inherits file-only: no central
  coordinator, no lease, no HTTP. The backend sweep is the only mechanism
  available, and that is the decision's intended consequence.
- **#93** — graceful shutdown. Owns `_hand_back_in_flight_job`'s seam; this
  issue fills its body (D5). Owns `start_new_session=True`
  (`agent/executor.py`), without which the SIGTERM in §5.6 orphans every
  rank. Owns the exit-code convention `stopped:shutdown`.
- **#96** — heartbeat contract. Owns the 90 s / 30 s numbers this issue's
  latency (§5.7) is built on. The sweep lives in its monitor. No new
  threshold is introduced (D10).
- **#97** — discover and claim. Owns setting `current_job_id` at claim and
  clearing it at completion (AC-3). This issue reads it; it does not write it
  at claim time. #97's advisory marker (`.claim.<node_id>`) is a dotfile and
  stays invisible to both watchers, which is why §5.5's marker does not
  have to exclude it.
- **#98** — job execution. Owns `start_new_session=True` and the process
  group. Recovery's "terminate the surviving process" step depends on the
  group existing.
- **#99** — cancellation propagation. Blocked on #106; this issue's clean-
  shutdown path must not be confused with an operator cancel. Distinguished by
  `error` code (`stopped:shutdown` vs `cancelled:by_operator`).
- **#100** — terminal state. Its monotonicity rule is what makes recovery
  safe (§5.1). Its `error` vocabulary is extended with the `orphaned:`
  family (§5.3). Its #99 coupling note ("#103 handles jobs stuck in
  RUNNING") is now fulfilled.
- **#101 / #102** — logs and metrics. Untouched here. Recovery leaves the
  job directory intact, so their data survives.
- **#105** — job submission folder. Its `remove_job_dir` bounds the marker's
  lifetime (D8). Its DB-first/file-second ordering and compensation paths
  reference #103 as the reconciler (e.g. `job_service.py:125`, `:173`).
- **#106** — command files. Recovery deliberately does not emit a command
  file (D6). The monotonicity refinement from #106 D11 (agreement DEBUG,
  disagreement WARNING) is what keeps an agent's confirming write from
  becoming noise.
- **#104** — docs. Receives the recovery latency (§5.7) and the
  "how to tell a recovered job from a cancelled one by reading `error`"
  section.
- **#125** — Windows job execution. Owns `stopped:shutdown` in the error
  vocabulary and the `cancel_reason` plumbing this issue's clean-shutdown path
  reuses. The `stop_event`/`cancel_reason` seam it introduced is what makes
  D5 possible.
- `shared/schemas/job_state.py` — no schema change. `error` is already
  `Optional[str]`; the `orphaned:` family is a vocabulary addition, not a
  schema change.
- `backend/store/database.py` — `NodeModel.current_job_id` (line 79) is the
  column recovery reads and clears. No migration needed.
- `agent/paths.py` — `job_commands_dir`/`job_acks_dir` are the model for the
  marker's location; the marker itself is a dotfile beside them.