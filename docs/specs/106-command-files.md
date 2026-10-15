# Spec: Command Files for Operator Intent (`jobs/<job_id>/commands/` + `acks/`)

**Issue**: #106 — "No path for operator intent to reach a file-only agent: cancel and retry write only to the private database"
**Labels**: `agent`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-15

---

## 1. Verified Problem Statement

`cancel_job` is the whole of the cancel path, in
`backend/services/job_service.py:326-341`:

```python
async def cancel_job(self, job_id: str) -> Optional[JobState]:
    """Cancel a running/pending job."""
    job: Optional[JobState] = await self._store.get_job(job_id)
    if not job:
        return None
    if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
        return None
    # Cancel the job
    now = datetime.now()
    updated_job: Optional[JobState] = await self._store.update_job(
        job_id,
        status=JobStatus.CANCELLED,
        completed_at=now,
        exit_code=-1,  # Indicates cancelled
    )
    return updated_job
```

No filesystem call, no import of anything from `shared.file_ops`, nothing outside the
backend process. Reproduced against the real API and the real folder:

```text
B: cancel -> 200 CANCELLED -1
B: folder before cancel: ['jobs', 'jobs/job-1051', 'jobs/job-1051/state.yaml', 'nodes']
B: folder after  cancel: ['jobs', 'jobs/job-1051', 'jobs/job-1051/state.yaml', 'nodes']
B: state.yaml on disk after cancel -> PENDING None None
```

The database says `CANCELLED`/`-1`. The folder says `PENDING`, with `exit_code: null`.
Nothing else exists. The job's process — if one is running — is never signalled, and
the folder now **permanently contradicts the database**, which is the failure mode #105
was written to eliminate.

### The second half of the problem: a command file would be invisible

Even with a command file, `_process_file`
(`backend/services/syncthing_service.py:134-150`) routes two shapes and drops
everything else without a word:

```python
141:            if relative_path.parts[0] == "jobs" and len(relative_path.parts) >= 3:
142:                job_id = relative_path.parts[1]
143:                if relative_path.parts[2] == "state.yaml":
144:                    await self.service._process_job_file(job_id, file_path)
145:            elif relative_path.parts[0] == "nodes" and len(relative_path.parts) >= 2:
146:                node_id = relative_path.parts[1].replace(".yaml", "")
147:                await self.service._process_node_file(node_id, file_path)
```

Measured, over every shape a command protocol could take:

| path | `_process_file` | `_process_delete` |
|---|---|---|
| `jobs/job-1/state.yaml` | routed to `_process_job_file` | routed to `_handle_job_deleted` |
| `jobs/job-1/commands/cancel-abc.yaml` | **silently ignored** | **silently ignored** |
| `jobs/job-1/acks/cancel-abc.yaml` | **silently ignored** | **silently ignored** |
| `jobs/job-1/stray.yaml` | **silently ignored** | **silently ignored** |
| `jobs/job-1/logs/stdout-2026.yaml` | **silently ignored** | **silently ignored** |
| `nodes/node-01.yaml` | routed to `_process_node_file` | routed to `_handle_node_deleted` |

`_is_relevant_file` (`:79-88`) admits all of them — `.yaml`, not a dotfile, not `.tmp` —
so they *are* observed. They are then discarded by the router. This is the trap #92
recorded as "Ingest needs new handlers".

### What is already delivered by the nested layout — and the one thing that is not

The operator's recorded reason for nesting under `jobs/<job_id>/` was that `jobs_dir` is
already scheduled recursive, so neither `observer.schedule` (`:203-204`) nor
`_initial_scan` (`:243-267`) needs a **new top-level scan root**. That is verified, and
it is the right call:

```text
backend SyncthingEventHandler._process_file saw: ['jobs/job-1/commands/cancel-abc123.yaml']
agent FolderWatcher saw: [('created', 'jobs/job-1/commands/cancel-abc123.yaml')]
```

A real `SyncthingService` and a real `agent.watcher.FolderWatcher`, both observing, no
change to either. The command file arrives, twice over in some runs (§1, below).

**The refinement the recorded reason does not cover.** `start()` calls `_initial_scan()`
at `:196` and `observer.start()` at `:205`; inotify only reports changes *after* a watch
is established. So a command file that already exists when the watcher starts is seen by
nobody:

```text
A: _initial_scan handed to _process_job_file -> ['jobs/job-1/state.yaml']
A: on disk -> ['jobs/job-1/commands/cancel-abc.yaml', 'jobs/job-1/state.yaml']
agent FolderWatcher events after start: []
```

`_initial_scan` (`:250-256`) reads `job_dir / "state.yaml"` and nothing else; the agent's
`FolderWatcher.start()` (`agent/watcher.py:207-227`) performs no scan at all. **A cancel
issued while the backend was down, or while the agent was down, is never seen by anyone.**
This is not a reason to revisit the layout: adding `commands/` and `acks/` to a scan that
already walks every job directory is a different and much smaller change than adding a
`control/` scan root. It is, however, load-bearing and the operator's summary of it was
one step short. Recorded as D1.

### Duplicate delivery is real, measured

The agent's `FolderWatcher`, watching a live folder, and a command file written the way
`write_yaml` writes one (temp file, then `os.replace`):

```text
1 delivery(ies) in 3/5 runs -> [('created', 'jobs/job-1/commands/cancel-abc123.yaml')]
2 delivery(ies) in 2/5 runs -> [('created', 'jobs/job-1/commands/cancel-abc123.yaml'),
                                ('created', 'jobs/job-1/commands/cancel-abc123.yaml')]
```

Two of five runs. The mechanism is the same one #105 §7 recorded for `state.yaml`: a
directory created around the file makes watchdog synthesise an extra event. A protocol
that acts on every event without an idempotency check will double-terminate. §5.8 is the
answer, and this measurement is its evidence.

### The agent cannot act on a cancel even if one appeared

Three independent gaps, all on the agent side:

1. **The routing.** `_is_job_state_file` (`agent/loop.py:417-424`) matches
   `state.yaml` only, and `_drain_events` (`:426-441`) routes only that. A command file
   reaches `logger.info("observed %s %s", ...)` and nothing else. Verified:
   `_is_job_state_file(jobs/job-1/commands/cancel-abc.yaml)` → `False`.
2. **The stop handle does not exist.** `agent/loop.py:508` creates
   `stop_event = asyncio.Event()` as a **local** inside `_spawn_execution_task`, captured
   only by `execution_wrapper`. Grepping `agent/` for `stop_event` finds no attribute
   anywhere that holds it. `_hand_back_in_flight_job` (`:542-562`) reaches
   `:550-552`, where the comment says *"The execution task checks the agent's stop
   event"* and the body is a bare `pass`. `Agent._stop` (`:66`) is a *different* event:
   `TaskSupervisor` consumes it (`:97`) and `execute_job` never sees it.
   **Consequence, verified by inspection: sending `SIGTERM` to the agent does not
   terminate the job it is running.** `_shutdown_sequence` (`:611-617`) awaits
   `self._execution_task` with a timeout and then gives up, leaving the process group
   alive in its own session. That is the defect #106 must not inherit, and the plumbing
   it needs is the same plumbing a command uses.
3. **`execute_job` cannot produce a `CANCELLED`.** `CANCELLED` does not appear in
   `agent/executor.py` at all (only in `agent/terminal.py`'s docstring). The status
   ladder at `:412-421` is `COMPLETED` / `FAILED` / `process_failed:exit_code=<N>`, and
   the stop path at `:379-404` sends `SIGTERM` to the group, waits `timeout=5.0`
   (`:391`), escalates to `SIGKILL`, and falls through to that ladder.

### Corrections to the issue body's line references

The substance is right. Every `file:line` citation in the body is stale, and one factual
claim is stale in a way that matters.

| Issue body | Actual |
|---|---|
| `job_service.py:73-88` (`cancel_job`) | `:326-341` |
| `job_service.py:78-79` (repeat cancel → 409) | `:331-332` returns `None`; the route raises `job_not_cancellable` at `backend/api/v1/jobs.py:284`. Verified `409` + `error_code: JOB_NOT_CANCELLABLE` |
| `job_service.py:53-71` (`retry_job`) | `:243-292` |
| `syncthing_service.py:53-62` (`_is_relevant_file`) | `:79-88` |
| `syncthing_service.py:115-120` (`_process_file` routing) | `:134-150` |
| `syncthing_service.py:176-177` (`observer.schedule`) | `:203-204` |
| `syncthing_service.py:217-241` (`_initial_scan`) | `:243-267` |
| `syncthing_service.py:260-269` (`update_job` from the file) | `:316-325` |
| `syncthing_service.py:310-314` (`_handle_job_deleted`) | `:366-370` |
| `syncthing_service.py:53-62`, `:132-137`, `:388-395` (#92's list of the two recognised shapes) | `:79-88`, `:141-147`, `:444-464` |
| `backend/core/config.py:31-34,178` (the DB is not replicated) | `backend/core/config.py` has no `DATABASE_URL`; the canonical path is resolved by `backend/core/database.py:resolve_database_url()` and `AGENTS.md` documents it. The claim is right, the citation is not |
| `README.md:223-224` (the CLI cancel is a commented-out stub) | `README.md:283`. The agent section now occupies `:218-231`, and the CLI cancel is still a stub |
| *"the backend's watcher reacts only to `state.yaml` created/modified/deleted events (`:41-49`)"* (#99) | `on_moved` exists and **routes to `_schedule_process`** (`:55-77`, added by #105). Without it every atomically published file is invisible — measured in #105 as "no event at all in 7 of 20 runs". A command file delivered by Syncthing arrives as a rename-in, so it needs `on_moved`, which it already has |
| `job_service.py:86` (`exit_code = -1`) | `:339` |
| `shared/file_ops/locking.py:41,126` (#97's `file_lock`) | `:169` (`file_lock`), `:190` (`shared_lock`). Unchanged; still host-local, still unused in production code |

### Already solved by issues that have landed — do not re-open

**Monotonic terminal state (#100) exists and is sufficient.** The rule is at
`syncthing_service.py:284-313`. Driven against the real handler:

```text
C: after ingest -> RUNNING
C: after cancel -> CANCELLED -1
C: after the agent wrote FAILED -> CANCELLED -1 None
WARNING backend.services.syncthing_service: Job job-1 is already CANCELLED; ignoring agent write attempting to set FAILED
```

#106's last acceptance criterion — *"monotonic terminal state is enforced, so the agent's
completion write cannot overwrite a recorded cancel"* — **is met by existing code**. It
becomes an AC only in the form *"still holds after this change"*, plus the one refinement
in §5.9, which is new.

**Retry no longer needs a command (#105).** `retry_job` (`job_service.py:243-292`)
re-emits `jobs/<job_id>/state.yaml` with `status: PENDING`, `node_id: null` and
`retry_count: n+1`, and reclaims the job through the discovery path #97 already
implements. Verified by `backend/tests/integration/test_job_submission_folder.py` AC-5.
**Retry is not carried as a command** — see Q5.

---

## 2. Root Cause

The transport decision on #92 settled that the Syncthing folder is the only channel in
both directions, and its BLOCKER 2 said operator intent has no path to the node. #105
fixed BLOCKER 1 by making the backend *write into* the folder. This is BLOCKER 2, and it
is a different shape of problem from #105: #105 reuses an existing file shape that both
watchers already route, whereas operator intent needs a **file shape that neither watcher
routes**, in **both** directions, plus a stop handle the agent does not have. Nothing here
was ever written and then broken; it was specified in the transport decision and never
implemented.

---

## 3. Open Questions — Resolved

The issue left five. Three are settled by the operator and recorded as data (Q1, Q2, and
the resource model in Q5's preamble). The analysis added seven more.

### Q1 — Where do command files live? **Nested: `jobs/<job_id>/commands/`. Operator decision, not re-opened.**

The recorded reason is verified above: `observer.schedule(jobs_dir, recursive=True)`
(`:203-204`) and the agent's equivalent (`agent/watcher.py:222-223`) already deliver a
file nested three levels down, with no change to either schedule. A top-level `control/`
would require extending `observer.schedule` **and** `_initial_scan` **and** the agent's
`FolderWatcher.start()` (`agent/watcher.py:207-213` raises if the directory is missing) —
three changes whose failure mode is silent, because a watcher that never saw the directory
reports nothing.

**The one thing the recorded reason missed, made explicit here:** nesting removes the new
top-level scan root, not the need for a scan. §1 measured it — a command file already on
disk at start-up is invisible. D1 carries the correction. This is a refinement of the
rationale, not a change of the decision.

### Q2 — Is an ack needed? **Yes. Operator decision — and it carries a second job.**

The recorded reason is that without an ack the UI cannot distinguish *requested* from
*delivered*. Correct, and there is a second reason this spec adds: **the ack is the
idempotency record.** §3/Q8 and §5.8 make the ack's mere existence the thing that tells a
restarted agent "this command was already honoured". A separate `.consumed` marker would
be a second file per command carrying a subset of the ack's information. The ack is
therefore load-bearing in two independent places, and skipping it would cost both.

### Q3 — What does the agent do with a command for a job it does not own? **Acknowledge and ignore. Never act.**

Three distinct refusals, each with its own ack `result`, each with a test:

| situation | agent action | ack `result` |
|---|---|---|
| `state.yaml` says another `node_id` | act on nothing | `ignored_not_owner` |
| no `state.yaml` at all (job deleted here, or never replicated) | act on nothing | `ignored_no_job` |
| the job is terminal here, or nothing of it is running | act on nothing | `already_terminal` |

The first is a safety property, not a nicety: a stale cancel must never kill something
else. The third is the common case for a repeat delivery — and the common case for a
**restarted** agent, which is why it is a positive acknowledgement rather than silence.

### Q4 — How long do command files live? **Until `DELETE` removes the directory. No retention policy in Phase 2.**

The bound is structural: `remove_job_dir` (`backend/services/job_folder.py:132-152`)
`rmtree`s `jobs/<job_id>/`, so commands and acks leave with the job. The accumulation rate
is one file per operator action per job — for a home cluster that is single digits.
`jobs/<job_id>/logs/` (#101) will dwarf it.

Two consequences recorded rather than designed around:

- **A `DELETE` between the command and its delivery destroys the command silently**, and
  no ack will ever arrive. The UI must therefore render "requested, no ack" as a real
  state, not as "still working". AC-14 pins it.
- **A command can outlive its job on one node and not another.** Syncthing replicates
  `state.yaml` and `commands/` at the same time, but a node that receives the command
  before the delete reaches it sees a command for a job whose directory is being removed.
  Q3's `ignored_no_job` is what makes that harmless.

### Q5 — Does the protocol also carry `retry`, "run now", priority, or drain-node? **Scope is `cancel`, and the schema is closed.**

- **`retry` is explicitly out.** #105 made `POST /jobs/{id}/retry` re-emit `state.yaml`,
  which is the entire mechanism: the job becomes `PENDING` again and #97's discovery path
  claims it. A `retry` command would be a second way to do the same thing, with its own
  acknowledgement states, for no operator-visible benefit. Including it would also mean
  two writers of `state.yaml` acting on the same job in the same protocol, which is
  precisely the shared-mutable-document hazard this design exists to avoid.
- **"run now" / priority / drain-node are not added, and the schema is closed against
  them.** The issue is right that deciding scope once beats versioning later — but the way
  to decide scope once is to make the extension *cost* an explicit change, not to
  speculatively carry fields nobody has asked for. `command` is a `Literal["cancel"]` in
  Phase 2. Adding `drain` later is one new `Literal` member, one new directory convention
  if it needs one, and one new `CommandResult` value. The version is the directory, not
  the schema.
- **The resource model is the operator's**: direct process, best-effort resources, no
  container, no `systemd-run` (#98, implemented at `agent/executor.py:300-310`). A
  command does not change it — a cancel is a signal, not an execution context.

### Q6 — Where do the schemas live, and how strict are they? **New pair under `shared/schemas/`, both closed.**

One model per direction, exported from `shared/schemas/__init__.py` like every other
contract there. Full definitions in §5.2 and §5.3.

### Q7 — Does the agent need a *new watcher task*? **No. The event already arrives.**

Measured: the agent's `FolderWatcher` enqueues the command file as a `created` event
without any change (`agent/watcher.py:207-227` observes `jobs/` recursively, and
`_is_relevant_file` at `:102-108` admits it). `_drain_events` (`agent/loop.py:426-441`) is
already the drain point at a 0.5 s cadence. The change is a routing branch and a new
`agent/commands.py` of pure functions. A second watcher would be a second observer over
the same tree for the same events — the duplication #93's module docstring already
criticises.

The one thing `_drain_events` cannot do is cover a command that appeared while the agent
was down (§1). That is a **one-shot sweep**, not a task: it belongs in
`_ensure_watcher_started` (`agent/loop.py:319-333`), which runs exactly once per process
and is also the seam a late-appearing folder goes through (`_check_folder`, `:400-415`).

### Q8 — How is a repeated or already-honoured command prevented from re-firing?

Three independent mechanisms, because they defend against three different things:

| threat | mechanism | evidence |
|---|---|---|
| the same file delivered twice within one process lifetime | the ack check, before any action | 2-of-5 duplicate delivery, §1 |
| a restarted agent re-reading a command it already honoured | the ack check again — the ack is **on disk** and replicated | "no supervisor" per the #92 correction comment |
| a crash between killing the process and writing the ack | re-execution is a **no-op**: there is no longer a live process to kill, so the agent takes the `already_terminal` path and acks it | the target of the action is gone |

The ack check is therefore not a cache — it is the durable idempotency record (Q2). A
command with no ack has never been acted on; a command with an ack has. There is no third
state and no time-based expiry, which means the rule cannot expire into a double-kill.

**The agent never deletes the command file.** Command files are backend-owned and
append-only (§5.1); an agent deleting one is #105 D9's hazard class, and the delete would
also race the backend's own watcher.

### Q9 — Who writes the terminal state after a cancel, and what happens to the warning? **The agent confirms; the backend's monotonicity check must stop calling that an overwrite.**

Today the monotonicity branch (`syncthing_service.py:284-300`) fires whenever the existing
row is terminal, *including when the incoming write agrees with it*:

```text
F: the agent AGREED with the recorded terminal state, and the backend
F:   logged at WARNING: ['Job job-1 is already CANCELLED; ignoring agent write attempting to set CANCELLED']
F:   -> CANCELLED -1 None
```

Under this protocol that is not an edge case — it is **the normal path of every cancel**:
the operator's click records `CANCELLED`, the agent then writes `CANCELLED` to confirm,
and the backend logs a WARNING that says someone tried to overwrite a terminal state. An
operator learns to ignore that log line, and then the log line that matters stops being
readable. §5.9 specifies the fix: quiet on agreement, loud on disagreement, and the test
asserts both directions.

### Q10 — What is the latency budget? **Measured; two numbers; the replication hop is named and excluded.**

Measured on this machine, one filesystem, real watchers, `write_yaml` publication:

| hop | measured | n |
|---|---|---|
| `write_yaml` → `FolderEvent` in the agent's queue | median **0.008 s**, max **0.009 s** | 12 |
| the agent's drain cadence (`EVENT_DRAIN_INTERVAL_S`, `agent/loop.py:41`) | **0.5 s** worst case, 0.25 s median | — |
| **command written → agent enters the stop path** | **median 0.26 s, worst 0.51 s** | — |
| cooperative 5-process tree, `SIGTERM` to the group | **0.02 s**, leader exit `-15`, **0 survivors** | 1 |
| 5-process tree that traps `TERM` | **5.004 s** (the `timeout=5.0` at `agent/executor.py:391`), leader exit `-9`, **0 survivors** | 1 |

Recorded budget (AC-5, AC-6 assert these):

- **B1 — ≤ 1.0 s** from the command file being written to the agent entering the stop
  path. Measured worst 0.51 s; 1.0 s leaves ~2× headroom and the dominant local term is
  a single in-memory constant.
- **B2 — ≤ 10 s** from the command file being written to the process group being gone.
  Measured worst 5.5 s; the 5 s escalation dominates and 10 s says so out loud.

**The Syncthing replication hop is not asserted by this issue and is not measured here** —
it cannot be measured from one machine. It is stated as the operator's: the folder is the
channel, replication is Syncthing's, and the number belongs to the #102 measurement and
#104's troubleshooting section (both already own it). AC-5's assertion is explicitly
scoped to one node.

---

## 4. Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| `jobs/<job_id>/commands/<command_id>.yaml` — backend writes, append-only, one file per command | Any new top-level directory (`control/`); D1 records why |
| `jobs/<job_id>/acks/<command_id>.yaml` — agent writes | `retry` as a command (#105 re-emits `state.yaml`) |
| `JobCommand` and `JobCommandAck` Pydantic models under `shared/schemas/` | `run now`, priority, drain-node, or any other verb (Q5) |
| `command_dir` / `ack_dir` path helpers on **both** path modules | Changing `AgentPaths`' existing fields |
| `cancel_job` writes the command file, then updates the row (§5.4) | Any change to `cancel_job`'s existing database semantics |
| Backend watcher routing for `commands/` and `acks/` in `_process_file` and `_process_delete` | Changing `observer.schedule` (`:203-204`) — no new scan root (D1) |
| `_initial_scan` and `_scan_folder_structure` learn `commands/` and `acks/` per job dir | A backend startup sweep for *new* job directories — #103's reconciliation |
| A log line for an unrecognised `.yaml` under a job directory | An error for one; §5.5 explains the difference |
| Agent: routing in `_drain_events`, a startup sweep, `agent/commands.py` | A second watcher task (Q7) |
| Agent: plumb the per-job `stop_event` onto the `Agent` so a command can set it | Changing the shutdown sequence's ordering |
| Agent: `execute_job` gains a cancel path that writes `CANCELLED` | **#99 — process-tree termination mechanics** (§4.1) |
| The monotonicity check distinguishes agreement from disagreement | **#99 — the exit-code convention for "I killed this process"** |
| `GET /api/v1/jobs/{job_id}/commands` surfacing requested vs acked | Persisting commands in a table; the folder is the store |
| The 409-on-repeat-cancel contract, unchanged and pinned | The CLI `sci-run cancel` stub (`README.md:283`) — still a stub |

### 4.1 The #99 boundary, stated so neither issue can claim the other's work

| | **#106 (this issue)** | **#99** |
|---|---|---|
| **delivers** | the command file, its schema, the watcher routing, the ack, the plumbing that lets a command reach the agent's stop path | the signal: `SIGTERM` → grace → `SIGKILL`, the escalation policy, the exit-code/error convention for a killed process |
| **asserts** | a cancel command reaches the agent's stop path within B1, and the process group is gone within B2 | *what* the stop path does, in detail, for every edge (#99's own AC list) |
| **does not** | choose the grace period, change the signal sequence, or decide what a **shutdown** stop means — that is #103's hand-back, and §5.7's `cancel_reason` is what keeps the two apart | create, route, or acknowledge a command; own any schema |

**The overlap is deliberate and bounded to one function.** `execute_job`'s existing stop
path (`agent/executor.py:379-404`) already sends `SIGTERM` to the group, waits, and
escalates — it is what this issue wires a command to, and #99's measured evidence shows
it works (0 survivors in both the cooperative and the `TERM`-trapping case). #106's
contribution to that function is *two things and nothing else*: **a way for a cancel
command to set `stop_event`, and a `CANCELLED` outcome when it was set — identified as a
cancel, and not as a shutdown.** It does not change the signal sequence, the grace, or the
escalation. #99 refines all three and owns the tests that pin them.

The boundary is drawn in the other direction too: if #99 lands first, it changes
`execute_job`'s stop path and #106 must adapt to it, not overwrite it. Neither issue
restructures `execute_job`.

### 4.2 Explicitly out of scope, with the owner named

| Issue | Why it is not this one |
|---|---|
| **#101** (log capture and the WebSocket gate) | This issue writes no log file and reads none. It **does** add two new directory shapes under `jobs/<id>/`, and §5.5's "log the unrecognised" rule is what stops them from hiding #101's `logs/` files. The WebSocket gate (`backend/api/v1/jobs.py:183-255`) is untouched |
| **#102** (metrics sampling) | Nothing here samples. #102's 30 s placeholder cadence is the binding replication cost; a cancel is one file per operator action and is not in that class |
| **#103** (crash/restart reconciliation) | #103 owns "a job is `RUNNING` with no process attached". This issue makes the *command* idempotent across a restart (Q8) and adds the one-shot sweep (Q7), which is recovery of commands, not of jobs. **The residual gap this issue creates is handed to #103 explicitly**: an agent that dies between killing the process and writing the ack leaves `state.yaml: RUNNING` with no process — §5.10 |
| **#104** (docs; **no systemd** per the operator's correction) | No unit file, no install path, no service definition — #104 was rewritten to be a written procedure. This issue contributes one section to it: *how to tell a delivered cancel from a requested one by hand*, which is a folder `ls`, and therefore belongs in #104's troubleshooting list |
| **#36** (synthetic-marker) | No metrics or log rows are produced here, so there is nothing to distinguish as fabricated. It landed anyway |

---

## 5. Implementation Approach

### 5.1 File layout and ownership

```
<root>/jobs/<job_id>/
├── state.yaml                       # backend on create/retry; agent on claim/terminal (#105)
├── commands/                        # NEW — backend only
│   └── cancel-<epoch_ms>-<hex6>.yaml
├── acks/                            # NEW — agent only
│   └── cancel-<epoch_ms>-<hex6>.yaml
├── logs/                            # agent, #101 — not .yaml, not watched
└── .claim.<node_id>                 # agent, #97 — dotfile, filtered at _is_relevant_file
```

| Path | Written by | Mutability | May delete? |
|---|---|---|---|
| `commands/<command_id>.yaml` | **backend** (`job_service.cancel_job`) | **append-only** — one file per command, never rewritten, never shared | backend only, and only via `remove_job_dir` on `DELETE` |
| `acks/<command_id>.yaml` | **agent** (`agent/commands.py`) | one file per command; written exactly once, then never rewritten | nobody in Phase 2 — the ack outlives the command's usefulness and is the audit trail |

**"Append-only, one file per command" is the load-bearing part.** It is what removes the
lost-update race the issue names: there is no document two writers contend over, so
`os.replace`'s atomicity (`shared/file_ops/yaml_utils.py:107-121`) is the *only*
concurrency property needed, and no lock exists anywhere in this protocol. It also means a
command that has been written cannot be unsent — which is why Q8's idempotency lives on
the *consumption* side.

**Asymmetric deletion, inherited from #105 D9 and restated here:** the backend may delete
a job directory and therefore its commands and acks; **the agent never may**. An agent
that removed its own command file would destroy the audit trail and race the backend's
watcher. This belongs in the module docstring of `agent/commands.py` and is asserted by
AC-18, in the same shape as #105's AC-13.

### 5.2 Command schema — `shared/schemas/job_command.py` (new)

```python
class JobCommandType(str, Enum):
    CANCEL = "cancel"   # Phase 2's only verb. See Q5.


class JobCommand(BaseModel):
    """One operator instruction. Backend-owned, written once, never rewritten."""

    command_id: str = Field(..., pattern=COMMAND_ID_PATTERN)
    command: JobCommandType
    issued_at: datetime
    issued_by: str = Field(..., min_length=1, max_length=128)

    model_config = {"from_attributes": True}
```

`COMMAND_ID_PATTERN = r"^cancel-\d{10,}-[0-9a-f]{6}$"` — the verb, a millisecond
timestamp, and six hex characters of entropy. Load-bearing properties, each with a test:

- **Sortable**, so `ls commands/` reads chronologically and the newest command is last.
- **Readable without opening it**, so an operator staring at `acks/` in a terminal can
  see what was cancelled and when. This is the whole reason the verb is in the filename.
- **Filesystem-safe by construction**, and the pattern is the check — a `command_id` is
  interpolated into a path on both sides, exactly as `node_id` is
  (`agent/config.py:150-160` is the precedent).
- **The verb is repeated in the body on purpose.** The **path is authoritative for
  routing** (so a file whose body disagrees cannot be misrouted) and the body is
  authoritative for content. A mismatch is a protocol violation, refused and logged, not
  silently resolved (§5.5).

`issued_by` is `payload["sub"]` from `get_current_token_payload`
(`backend/core/deps.py:17-46`). **Caveat stated rather than glossed:** under a single
shared Bearer token every caller shares one identity, and on the localhost bypass it is
the literal `"localhost_user"` (`:31`). `issued_by` is an audit hint that says *which
session pressed the button*, not who the human was — the same cost #92 accepted for the
agent having no identity at all. It is worth having anyway, because the alternative is a
command file with no provenance whatsoever.

### 5.3 Ack schema — `shared/schemas/job_command_ack.py` (new)

```python
class JobCommandResult(str, Enum):
    TERMINATED = "terminated"                # the process group was signalled and is gone
    ALREADY_TERMINAL = "already_terminal"    # nothing of this job was running here
    IGNORED_NOT_OWNER = "ignored_not_owner"  # state.yaml names another node
    IGNORED_NO_JOB = "ignored_no_job"        # no state.yaml here at all
    FAILED = "failed"                        # the terminate attempt itself failed


class JobCommandAck(BaseModel):
    """The agent's receipt for one command. Agent-owned, written once."""

    command_id: str = Field(..., pattern=COMMAND_ID_PATTERN)
    command: JobCommandType
    result: JobCommandResult
    acknowledged_at: datetime
    acknowledged_by: str = Field(..., min_length=1, max_length=128)
    detail: Optional[str] = Field(default=None, max_length=512)

    model_config = {"from_attributes": True}
```

| Field | Why it is there |
|---|---|
| `command_id` | the join key, and the only field that must match `commands/<command_id>.yaml` |
| `command` | echoed so an ack can be read without the command beside it |
| `result` | **the whole point of the ack.** `TERMINATED` vs. the three refusals vs. `FAILED` is what makes *delivered* a distinguishable state, not one boolean |
| `acknowledged_at` | naive local `datetime.now()`, matching every other writer in the repository (#95 §5 correction, #100 §5.5) |
| `acknowledged_by` | the `node_id`, which is what makes `ignored_not_owner` readable: it says *who* declined it |
| `detail` | optional, ≤512 chars, free text. The wait status after the kill (`"exit -15"`), or the reason for a refusal. Never a stderr tail (#100 Q3) |

**The stale-command rule.** Before *any* action the agent resolves the job and reads
`state.yaml`. If it is absent → `IGNORED_NO_JOB`, ack, done. If `node_id` is set and is
not this node → `IGNORED_NOT_OWNER`, ack, done. Only a `state.yaml` naming this node, with
a non-terminal status, and with a live process for it, leads to a signal. **A command can
never cause a signal to a process the agent did not start for that job**, which is the
safety property the issue's open question 3 is really asking for.

### 5.4 Backend: the writer

Path helpers, one pair per path module so neither can drift into a second convention —
the same rule #105 enforced between `backend/services/job_folder.py` and
`shared/file_ops/path_utils.py`:

```python
# backend/services/job_folder.py — JOBS_DIR_NAME (:37) and JOB_STATE_FILE_NAME (:41)
# gain their siblings; the shapes mirror the existing functions exactly.
COMMANDS_DIR_NAME: Final[str] = "commands"
ACKS_DIR_NAME: Final[str] = "acks"

def commands_dir(root: str | Path, job_id: str) -> Path: ...
def command_file(root: str | Path, job_id: str, command_id: str) -> Path: ...
def acks_dir(root: str | Path, job_id: str) -> Path: ...
def ack_file(root: str | Path, job_id: str, command_id: str) -> Path: ...
```

```python
# agent/paths.py — AgentPaths (:23-31) gains NOTHING: commands/ and acks/ are
# per-job, not per-root, so they are functions of job_id, not fields of the
# frozen dataclass. Every existing construction site keeps working untouched.
# Four methods, mirroring the four backend helpers above exactly.
def job_commands_dir(self, job_id: str) -> Path: ...
def job_acks_dir(self, job_id: str) -> Path: ...
def job_command_file(self, job_id: str, command_id: str) -> Path: ...
def job_ack_file(self, job_id: str, command_id: str) -> Path: ...
```

That is the whole addition to `agent/paths.py`: **no new field on a frozen dataclass that
three modules construct**, and the same four names the backend side grows, so the drift
tripwire below compares like with like.

The cross-module drift tripwire is the direct analogue of #105's AC-10: a test asserts
`backend.job_folder.command_file(root, jid, cid) == agent.paths.command_file(root, jid,
cid)` for several ids, with `job_id` and `command_id` values that include hostile ones
(`job-1`, `job-1041`, and a `command_id` that does not match the pattern) — the last of
those so the equality cannot pass by accident.

**`cancel_job`, in full after the change** (`backend/services/job_service.py:326-341`):

```
1. job = get_job(job_id)                          -> None  : 404 JOB_NOT_FOUND  (route)
2. job.status not in (RUNNING, PENDING)           -> 409 JOB_NOT_CANCELLABLE   (route)
3. command = JobCommand(id=..., CANCEL, now, payload["sub"])
   try: write_yaml(str(command_file(root, job_id, command.command_id)), command)
   except OSError: raise syncthing_unavailable(...)     # the row is NOT touched
4. update_job(job_id, status=CANCELLED, completed_at=now, exit_code=-1)
5. return updated_job
```

**The command file is written BEFORE the row is updated. This is the opposite of #105's
D1 and the reason is specific, not a preference.**

- If step 3 fails, the operator gets a **503** and the job is **still `RUNNING`** — and it
  is really still running. The alternative ordering produces today's bug in a new form: a
  row that says `CANCELLED` with nothing delivered, which is the exact defect this issue
  exists to remove.
- If step 4 fails after step 3 succeeded, the command is out and will be honoured. The
  process dies, the agent writes `CANCELLED`, and `_process_job_file` accepts it: the
  existing row is `RUNNING`, which is neither terminal (`:287`) nor the rejected
  `RUNNING`+`PENDING` pair (`:304-313`), so it falls through to `update_job` at `:316`.
  **That partial state heals itself**, which is why it needs no compensation. The reverse
  order's failure is survivable but silent — the folder holds the command, the row stays
  `RUNNING`, and nothing complains until someone reads the folder. Both orders survive;
  only this one never lies to the operator at the moment they are looking.
- No compensation means no rollback path to get wrong, and `cancel_job` keeps its existing
  database semantics exactly (step 4 is unchanged). #99 and #100 depend on those semantics
  and are not disturbed.

`command_id` is minted once, at step 3, and is the same id the ack must use. It is
generated from `time.time()` plus six hex characters — **not** a UUID: the id lands in a
filename a human reads, and `<verb>-<ms>-<hex6>` sorts and scans. Uniqueness is not
load-bearing for correctness (a collision would mean two identical cancels, which Q8 makes
a no-op) but the entropy is there so the two files of one command are not confusable with
another's.

### 5.5 Backend: watcher routing — the exact changes

Three edits in `backend/services/syncthing_service.py`, all in `SyncthingEventHandler`
or `SyncthingService`, and **no change to `observer.schedule`**.

**(a) `_process_file` (`:134-150`)** — three branches instead of two, with a fourth arm
that is not a `return`:

```python
parts = relative_path.parts
if parts[0] == "jobs" and len(parts) >= 3:
    job_id = parts[1]
    if parts[2] == "state.yaml":
        await self.service._process_job_file(job_id, file_path)
    elif parts[2] in (COMMANDS_DIR_NAME, ACKS_DIR_NAME) and len(parts) == 4 \
            and parts[3].endswith(".yaml"):
        await self.service._process_command_file(job_id, parts[3][:-5], file_path)
    else:
        logger.debug("ignoring %s: not a recognised file under a job directory", file_path)
elif parts[0] == "nodes" and len(parts) >= 2:
    node_id = parts[1].replace(".yaml", "")
    await self.service._process_node_file(node_id, file_path)
else:
    logger.debug("ignoring %s: not under jobs/ or nodes/", file_path)
```

The `len(parts) == 4` is exact and load-bearing. `jobs/<id>/logs/x.yaml` is 4 parts with
`parts[2] == "logs"`, so it falls to the `else` and is logged rather than routed;
`jobs/<id>/commands/nested/deep.yaml` is 5 parts and likewise. Without the exact
arithmetic, `parts[2] in (...)` alone would accept both.

**(b) `_process_delete` (`:152-167`)** — the mirror, and the safety-critical one:

```python
if parts[0] == "jobs" and len(parts) >= 3:
    job_id = parts[1]
    if parts[2] == "state.yaml":
        await self.service._handle_job_deleted(job_id)
    elif parts[2] in (COMMANDS_DIR_NAME, ACKS_DIR_NAME):
        logger.debug("command/ack file removed: %s", file_path)
    else:
        logger.debug("ignoring deletion of %s", file_path)
```

**A deleted command or ack file must never reach `_handle_job_deleted`.** Measured today:
every non-`state.yaml` path under `jobs/<id>/` already takes no branch (§1's table), so
the current behaviour is already correct — the change must not regress it, and AC-9 pins
that with the real handler driven by a real delete event.

**(c) `_process_command_file` — new, on the service.** Four responsibilities, in order:

1. **Derive the verb from the path** and cross-check it against the body. A mismatch is
   logged at WARNING and refused: a file whose name says `cancel` and whose body says
   something else is either a bug or an attempt to route by content, and both are refused.
2. **`read_yaml(path, JobCommand)`** — raises on anything malformed, and the handler's
   bare `except` (`:149-150`) logs at ERROR. A malformed command is therefore *visible*,
   which is the one thing the silently-ignored table in §1 denies today.
3. **`command_id` must match the filename** — same reason as (1).
4. **Record the outcome in the store's in-memory command log** (§5.11) and return. It
   writes nothing else: the backend never acts on a command, and never writes a job field
   because of one.

**`_initial_scan` (`:250-256`) and `_scan_folder_structure` (`:452-457`)** — the D1
correction. Both already iterate every job directory; both gain a per-job-dir sweep of
`commands/` and `acks/`. No new top-level root, which is the whole point of the layout.

`_scan_folder_structure` returning command and ack paths has **no effect on job deletion**,
and that is measured rather than argued:

```text
E: _scan_folder_structure -> ['jobs/job-1/state.yaml']
E: manual_scan -> {'scanned': 0, 'total_processed': 1}
E: _processed_files after manual_scan -> ['jobs/job-1/state.yaml']
E: job still present: True
```

with `_processed_files` pre-seeded to contain the command and ack paths. `_process_deleted_files`
(`:466-484`) dispatches on `parts[2] == "state.yaml"` and takes no branch for anything else,
so a command file cannot delete a job. Note the other half of that measurement:
`manual_scan` **replaces** `_processed_files` wholesale (`:440`), so command and ack paths
added to it are dropped by every manual scan. That is harmless — the set is a
change-detection cache, and re-seeing a command is idempotent (Q8) — and it is recorded
here so a later reader does not treat it as a bug.

**The monotonicity refinement (Q9)** — one change in `_process_job_file` (`:287-300`): when
the existing row is terminal **and** the incoming write has the same status, log at
DEBUG and return without the WARNING; when they differ, keep the WARNING exactly as it is.
Two assertions, one per direction (AC-11).

### 5.6 Agent: paths, routing, and the startup sweep

New module `agent/commands.py`, in the shape #95 established — **pure functions plus
narrow I/O helpers**, so the decision logic is testable with fakes and the module is
reusable if the transport is ever revisited:

```python
def classify_command(
    *, command: JobCommand, job_state: JobState | None, node_id: str,
    has_live_process: bool, already_acked: bool,
) -> JobCommandResult:
    """Decide what to do with one command. Pure: no I/O, no signals."""


def load_command(path: Path) -> JobCommand:
    """read_yaml(path, JobCommand). Raises on malformed."""


def load_ack(path: Path) -> JobCommandAck | None:
    """read_yaml(path, JobCommandAck), or None when absent or unreadable."""


async def handle_command(path: Path, *, paths, node_id, current, watcher) -> JobCommandAck:
    """The impure half: classify, act if warranted, write the ack exactly once."""
```

`classify_command` is the whole decision, and it is ordered so that the cheapest and
safest checks come first:

| # | check | result |
|---|---|---|
| 1 | `already_acked` (the ack file exists) | `ALREADY_TERMINAL` — and **no action**, which is Q8's whole mechanism |
| 2 | `job_state is None` | `IGNORED_NO_JOB` |
| 3 | `job_state.node_id != node_id` | `IGNORED_NOT_OWNER` |
| 4 | `job_state.status` is terminal | `ALREADY_TERMINAL` |
| 5 | `not has_live_process` | `ALREADY_TERMINAL` |
| 6 | otherwise | `TERMINATED` — and only now is `stop_event.set()` called |

Check 1 is above everything else **including** the refusals, so a duplicate delivery of a
command that was already refused does not produce a second ack and a second log line. An
ack is written exactly once per command, always.

**Routing in `_drain_events` (`agent/loop.py:426-441`)** — one new branch beside the
existing one, before it:

```python
if event.kind == "created" and self._is_command_file(event.path):
    await self._maybe_handle_command(event.path)
elif event.kind == "created" and self._is_job_state_file(event.path):
    await self._maybe_claim_job(event.path)
```

`_is_command_file` is the agent-side twin of the backend's branch and asserts
`parent.parent.name.startswith("job-") and parent.parent.parent.name == "jobs"` and
`parent.name in ("commands", "acks")` and `name.endswith(".yaml")` — the same shape as
`_is_job_state_file` (`:417-424`).

**The agent ignores its own acks on the read path.** `_maybe_handle_command` returns
immediately for any path whose parent is `acks/`: the agent writes acks and records them
in `SelfWriteLedger` (`agent/watcher.py:56-85`), so the common case is already suppressed —
but the ledger is bounded to 256 entries and in-memory (`:66`), so a restart or a busy
minute can deliver an ack the agent itself wrote. Routing an ack as a command would be a
protocol violation; AC-13 pins the refusal.

**The startup sweep** goes in `_ensure_watcher_started` (`agent/loop.py:319-333`),
immediately after `self._watcher.start()` and before the method returns: walk
`jobs/*/commands/*.yaml` and hand each to `handle_command`. One shot per process, and it
runs again if the folder appears late, because `_check_folder` (`:400-415`) calls the same
method. AC-3 pins it: a command file placed on disk *before* the agent starts is honoured.

**Plumbing the stop handle (the §1 gap).** `_spawn_execution_task` (`:495-539`) stores the
event it creates at `:508`, and the reason alongside it:

```python
self._job_stop_event: asyncio.Event | None = None    # new, beside _current_job_id (:74)
self._job_stop_reason: str | None = None             # new; "cancel" only, in this issue
```

`_maybe_handle_command` sets both when the classification is `TERMINATED`, and
`execute_job`'s existing `stop_event.is_set()` check (`agent/executor.py:382`) does the
rest with no change to the signal sequence; `cancel_reason` (a one-line callable closing
over `_job_stop_reason`) is what distinguishes this from a shutdown stop (§5.7).
`_hand_back_in_flight_job` (`:542-562`) also sets the event — replacing the bare `pass` at
`:550-552` — **without** a reason, which is what keeps #103's hand-back semantics its own.
Fixing it here means #103 inherits a working seam rather than a placeholder.

### 5.7 Agent: the terminate path and what it writes

`execute_job` already sends `SIGTERM` to the group, waits, and escalates
(`agent/executor.py:379-404`), and that mechanism is **measured to work** — 0 survivors
across a cooperative 5-process tree and a `TERM`-trapping 5-process tree (§Q10). This
issue changes exactly one thing there:

```python
# agent/executor.py — the status ladder at :412-421 gains one branch, and
# execute_job gains one keyword-only parameter beside `stop_event` (:161).
async def execute_job(..., stop_event: asyncio.Event,
                      cancel_reason: Callable[[], str | None] | None = None, ...):
    ...
    # at :412, after `exit_code` is known:
    reason = cancel_reason() if cancel_reason is not None else None
    if reason == "cancel" and exit_code is not None:
        status = JobStatus.CANCELLED
        error_code = "cancelled:by_operator"   # #100 Q3's vocabulary; today nothing writes it
    elif exit_code == 0:
        ...
```

**Why a reason and not just `stop_event.is_set()`.** `stop_event` has two future setters,
and they must not produce the same answer:

| setter | correct terminal outcome |
|---|---|
| a **cancel command** (this issue) | `CANCELLED`, `error: cancelled:by_operator`, `exit_code` = the real wait status |
| **agent shutdown / hand-back** (#103) | the job must return to being claimable — `PENDING`, `node_id: null` — which is #103's decision to make |

Overloading `stop_event.is_set()` would report "the operator cancelled this" for a job the
operator never touched, which is precisely the provenance `cancelled:by_operator` exists to
carry. So this issue adds the *reason* and leaves the shutdown outcome to #103: a stop with
any other reason logs it and writes nothing, which is #103's seam and not this issue's
(§4.1).

`cancelled:by_operator` is in #100's documented vocabulary table with the attribution
**"Backend (cancel_job)"** — and nothing writes it, because `cancel_job` (`:335-340`)
sets only `status`, `completed_at` and `exit_code`. **Verified: a cancelled job today has
`error=None`.** This spec corrects the attribution: the **agent** writes
`cancelled:by_operator`, because `error` is agent-owned (#105 §5.5) and because only the
agent knows the process actually died. The backend continues to write only what it owns.

`exit_code` on that write is the **real** wait status — `-15` after `SIGTERM`, `-9` after
`SIGKILL` (both measured, §Q10). It is **not** forced to `-1`: `-1` is the backend's
cancel sentinel (`job_service.py:339`), the two are different numbers, and conflating them
would destroy the distinction #99's hazard analysis is built on. Because monotonicity
refuses the write anyway (§5.9), the operator continues to see `exit_code: -1` from the
backend's record; the agent's `-15`/`-9` lives in `state.yaml` for anyone reading the
folder, which is where the truth about the process belongs.

### 5.8 Idempotency

Stated as one table, because the issue asks for it in those terms:

| situation | API | protocol | outcome |
|---|---|---|---|
| cancel a `RUNNING`/`PENDING` job | `200`, `JobState` with `CANCELLED` | one new command file, one new `command_id` | act once |
| **cancel again** | **`409 JOB_NOT_CANCELLABLE`** — measured | **no command file written**: step 2 of §5.4 raises before step 3 | idempotent, and *provably* so: a repeat cancel leaves the folder byte-identical |
| retry | `200` | **no command file** — #105 re-emits `state.yaml` (§3/Q5) | the two verbs are distinguishable *because they use different mechanisms*, not because a command encodes the difference |
| the same command file delivered twice (2 of 5 runs, §1) | — | ack exists ⇒ `ALREADY_TERMINAL`, no signal | idempotent |
| a restarted agent re-reads a command it honoured | — | ack exists on disk ⇒ `ALREADY_TERMINAL` | idempotent |
| a restarted agent re-reads a command it *refused* | — | ack exists ⇒ `ALREADY_TERMINAL`, no second refusal ack | exactly one ack per command, ever |
| the agent died between the signal and the ack | — | no ack ⇒ act; there is no live process, so classification 5 gives `ALREADY_TERMINAL` and the ack is written | re-execution is a no-op by construction |

The row that carries the weight is the second: **"repeat cancel is idempotent" is true
because the 409 happens before the write**, not because the protocol tolerates a second
identical command. A command file, once written, cannot be unsent — so the guarantee is
established at the only point where it can be.

### 5.9 The monotonicity interaction — how the two channels stay separate

#100's rule exists and works (§1, reproduction C). What this issue adds is the discipline
that keeps them from interfering.

**They are two different files, so they cannot contend.** The backend's intent is a *new
file that did not exist*; the agent's report is a *rewrite of a file that already existed*.
They never share a path, so there is no lost-update race to lose, and neither
`write_yaml`'s `os.replace` atomicity nor Syncthing's conflict resolution is asked to
break a tie — because there is no tie.

**The backend never writes an agent-owned field.** Not as a shortcut, not in the command
file, not in `state.yaml`. `status`, `exit_code` and `error` are the agent's
(#105 §5.5), and `cancel_job`'s only database write is what it already was. AC-17 pins it
mechanically, the way #105 AC-13 pinned the delete rule: the only site that could violate
it carries a comment saying so, and a test asserts the comment is present, so an edit
that drops it fails CI rather than passing review by inertia.

**Order of events, and who wins.** The operator's click records `CANCELLED` in the
database *before* the process has died (§5.4 step 4). The agent then writes its own
terminal state. #100's check refuses it, because the row is already terminal — **and that
refusal is the mechanism, not a failure.** It is what makes "the operator asked" beat "the
process happened to exit with `-15`", which is the property #99 identified as
correctness-critical. The one thing that must change is the log level: today that refusal
emits

```text
Job job-1 is already CANCELLED; ignoring agent write attempting to set CANCELLED
```

at **WARNING**, for the single most common event in the whole protocol (§1, reproduction
F). Q9's fix makes agreement a DEBUG and disagreement a WARNING. AC-11 asserts both.

**The residual, stated.** `completed_at` in the operator's view is when the button was
pressed, not when the process died. That is #99's open question 3, and this is its
answer: the database timestamp is *intent*, the ack is *outcome*, and the two are
deliberately different records of different things. The ack's `acknowledged_at` is the
moment the agent finished, and it is the only field that means "the process is gone".

### 5.10 Failure matrix

**Cancel — no compensation, because the ordering in §5.4 makes every failure safe:**

| step | fails | client sees | left behind |
|---|---|---|---|
| 1 `get_job` → `None` | — | `404 JOB_NOT_FOUND` | nothing touched |
| 2 status not cancellable | — | `409 JOB_NOT_CANCELLABLE` | **no command file**; folder byte-identical |
| 3 `write_yaml` | `OSError` | **`503 SYNCTHING_UNAVAILABLE`** | row untouched — the job is genuinely still `RUNNING`, which is the truth |
| 4 `update_job` | DB error | `500` | command is out and will be honoured; the agent's confirming write is accepted by monotonicity (`RUNNING → CANCELLED`), so it heals |
| — | crash between 3 and 4 | *(no response)* | same as above: a command nobody acked until the agent answers it, then it heals |

**Agent side:**

| failure | behaviour |
|---|---|
| command file malformed | `read_yaml` raises; logged at WARNING with the path; **no ack**, because an ack would claim a decision that was never made |
| ack write fails (`OSError`) | logged at ERROR; the command is retried on the next duplicate delivery or the next startup sweep, and re-classifies to `ALREADY_TERMINAL` via the kill's own side effect. Self-healing |
| `os.killpg` raises `ProcessLookupError` | the group is already gone ⇒ `TERMINATED` |
| the job dies between classification and the signal | check 5 already passed; the kill is a no-op; `TERMINATED` is still the honest answer |
| **the agent dies between the signal and the ack** | **`state.yaml` stays `RUNNING` with no process. #103 owns this** — it is the "job `RUNNING` with no process attached" case, and this issue hands it over explicitly rather than half-solving it |

### 5.11 Surfacing — `requested` vs `delivered`

The ack must be visible through the API the UI already reads, or it is decoration. One new
read-only endpoint:

```
GET /api/v1/jobs/{job_id}/commands
-> { "items": [ { "command": {...}, "ack": {...}|null } ], "total": N }
```

Pairs are formed from the folder, backend-owned commands left-joined to agent-owned acks,
ordered by `command_id` (chronological by construction). The store gets one in-memory table
and **no database migration** — the folder is the store, and a command's life ends when
`DELETE` removes its directory.

**`openapi.yaml` changes this time, and that is deliberate.** D11 of #105 forbade
regenerating it because nothing about a request or response shape changed. Here a route
exists that was not there. Concretely:
`backend/tests/integration/test_job_submission_folder.py:1190-1199`
(`test_the_snapshot_and_the_live_schema_are_identical`) **will fail** until the snapshot is
regenerated, and that failure is the tripwire, not a regression. AC-16 says so in advance
so nobody "fixes" it by relaxing the test.

### 5.12 Concurrency and cost

- **Replication:** one small file per operator action, replicated once each way. Two
  orders of magnitude below #102's per-30 s metrics file, which is the cost #92 flagged as
  needing measurement. Nothing to tune here; the binding cost is metrics, not jobs.
- **No locks anywhere.** `file_lock`/`shared_lock` (`shared/file_ops/locking.py:169,190`)
  stay unused, exactly as #97 left them. The protocol has no shared mutable document, so
  it needs no mutual exclusion — which is the point of one-file-per-command.
- **Idempotency beats dedup.** The 2-of-5 duplicate delivery costs one extra `read_yaml`
  and one `classify_command`. It does not cost a second signal, and no debounce or
  seen-set is introduced.

---

## 6. Decisions

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Commands nest at `jobs/<job_id>/commands/`, acks at `jobs/<job_id>/acks/`.** `observer.schedule` is **not** extended. **`_initial_scan` and `_scan_folder_structure` *are*, per job directory.** | Operator decision on the layout, verified: recursive observation is free (§1). **The recorded reason is one step short** — a command file already on disk at start-up is invisible to both watchers, measured. Extending a scan that already walks every job directory is a small change; adding a `control/` scan root is three silent ones |
| **D2** | **A cancel is delivered only as a command. The backend never writes `CANCELLED`, `exit_code` or `error` into `state.yaml`.** | Operator decision. It would race the agent's write and destroy the provenance of `exit_code = -1` (#92's BLOCKER 2, verbatim) |
| **D3** | **One ack file per command, `acks/<command_id>.yaml`, agent-owned, written exactly once.** | Operator decision, and Q2's second reason: the ack *is* the durable idempotency record. A separate `.consumed` marker would be a second file carrying a subset of its information |
| **D4** | **`cancel_job` writes the command file *before* the row update, with no compensation.** | If the file cannot be written the operator gets a 503 and the job is genuinely still running. The reverse order reproduces this issue's own bug one layer in. The one partial state (file out, row not updated) heals through monotonicity |
| **D5** | **Commands are append-only: one file per command, never rewritten. Acks likewise. The agent never deletes a command file.** | No shared mutable document ⇒ no lost-update race ⇒ `os.replace` is the only concurrency property needed and no lock exists. An agent deleting a backend-owned file is #105 D9's hazard class |
| **D6** | **The ack's existence is the idempotency mechanism, checked before every classification including the refusals.** | Three threats, three checks (Q8): duplicate delivery, restart, crash-before-ack. All three are the same check in different states, and it cannot expire into a double-kill |
| **D7** | **`command` is a `Literal`-equivalent `Enum` with one member, `cancel`. `retry` is not a command; `run now`/priority/drain-node are not anticipated.** | #105 made retry a `state.yaml` re-emit. Q5's reasoning: a closed schema makes an extension cost an explicit change instead of a silent one |
| **D8** | **`command_id` is `cancel-<epoch_ms>-<hex6>`, with the verb repeated in the body and the path authoritative for routing.** | Sortable, readable in an `ls`, filesystem-safe by pattern, and the path/body cross-check turns a mismatch into a refusal instead of an ambiguity |
| **D9** | **No new watcher task on the agent. Routing goes in `_drain_events`; the startup sweep is one shot inside `_ensure_watcher_started`.** | Measured: the event already arrives (§1). A second observer over the same tree is the duplication `agent/watcher.py`'s own docstring criticises |
| **D10** | **An unrecognised `.yaml` under a job directory is logged at DEBUG, not routed and not dropped silently.** | The issue's requirement, and the only thing that stops this protocol's new directories from becoming the blind spot that hid #101's and #102's. DEBUG not WARNING: #101's `logs/` will be unrecognised until it lands, and a permanently-warning path is a path that gets ignored |
| **D11** | **The monotonicity check distinguishes agreement (DEBUG) from disagreement (WARNING).** | Q9. Under this protocol an agreeing write is the *normal* path of every cancel; leaving it at WARNING trains the operator to ignore the log line that matters. #100's rule itself is unchanged and its outcome is unchanged |
| **D12** | **The agent writes `error: cancelled:by_operator` and the real wait status as `exit_code`; the backend keeps writing `-1`. Only a stop with the reason `cancel` does so** — a shutdown stop writes nothing, which is #103's to decide | Corrects #100 Q3's "Written By: Backend (cancel_job)" — which writes nothing, verified. `error` is agent-owned (#105 §5.5) and only the agent knows the process died. The reason check exists so an operator's `Ctrl-C` is never recorded as their own cancel request |
| **D13** | **`GET /api/v1/jobs/{job_id}/commands` is added, and `openapi.yaml` is regenerated.** | "Surfaced" has to mean through the API the UI reads. Unlike #105's D11 a route genuinely exists now, so `test_the_snapshot_and_the_live_schema_are_identical` failing is the tripwire (AC-16) |
| **D14** | **No retention policy. Command and ack files live until `DELETE` removes the job directory.** | `remove_job_dir` (`job_folder.py:132-152`) already bounds it. One file per operator action per job is single digits on a home cluster |
| **D15** | **The propagation budget is B1 ≤ 1.0 s to the stop path and B2 ≤ 10 s to the process group being gone, asserted on one node. The Syncthing replication hop is named and not asserted here.** | Measured (§Q10): 0.51 s worst to the stop path, 5.5 s worst to the group gone. Replication cannot be measured from one machine and already has an owner (#102's measurement, #104's troubleshooting) |
| **D16** | **No new dependency, no database migration, no `AgentPaths` field.** | `write_yaml`, `read_yaml` and `ensure_directory` already do the work; the command log is in-memory because the folder is the store |

---

## 7. Acceptance Criteria

The issue's eleven checkboxes are the backbone. None is dropped and none is left as a
comment about the code — each is an assertion with a named test.

| Origin | Where it went |
|---|---|
| Issue AC 1 (cancel actually terminates the group, real child, no HTTP) | AC-1, AC-2 |
| Issue AC 2 (latency measured, budget recorded, asserted) | AC-5, AC-6 |
| Issue AC 3 (protocol written down before implementation) | this document, AC-20 |
| Issue AC 4 (command and ack paths routed; a missed `_initial_scan` means commands issued while the backend was down are never seen) | AC-3, AC-4, AC-8 |
| Issue AC 5 (a stray `.yaml` is neither routed nor silently swallowed) | AC-7, AC-10 |
| Issue AC 6 (an ack exists and is surfaced) | AC-12, AC-13, AC-16 |
| Issue AC 7 (repeat cancel idempotent; retry a distinct command) | AC-14, AC-15 |
| Issue AC 8 (a consumed command is marked; cross-reference recovery) | AC-17 |
| Issue AC 9 (the backend never writes agent-owned fields to command the agent) | AC-19 |
| Issue AC 10 (monotonic terminal state, cross-referenced to #99) | AC-11, AC-12 |
| Issue Q3 (a stale command for a job this node does not own must be ignored) | AC-18 |
| — | AC-9, AC-21, AC-22, AC-23 added by this analysis |

### Functional

- **AC-1 — A cancel issued while a job is running on another node actually terminates the
  process group, with no HTTP anywhere.** A test drives the **real** `Agent` loop against a
  real `SYNCTHING_ROOT`, submits through `JobService.cancel_job`, and asserts the process
  group is gone. **The "no HTTP" half is already enforced structurally and must stay
  enforced:** `agent/tests/test_no_cluster_required.py:125-128` ASTs every module in
  `SHIPPED_MODULES` (`:20-35`) and fails on any import from `NETWORK_MODULES` (`:37-51`,
  which already lists `httpx`, `requests`, `aiohttp` and `socket`). **Two consequences for
  this issue, both of which a test already catches if missed:** `agent/commands.py` must be
  added to `SHIPPED_MODULES`, because `:196` asserts `present == set(SHIPPED_MODULES)` —
  adding the module without listing it turns that test red. *Proved by*
  `agent/tests/test_loop.py::test_a_cancel_command_terminates_a_running_job_without_http`
  **and** the existing `test_no_cluster_required.py` suite, which this issue extends rather
  than replaces.

- **AC-2 — Every process in a multi-process tree is terminated.** The child's command is a
  real launcher that forks N real ranks and records their PIDs; after the cancel, **every
  recorded PID is asserted gone** (`os.kill(pid, 0)` raises `ProcessLookupError`). Two
  cases: a cooperative tree and a tree that traps `TERM`, the second proving the
  escalation to `SIGKILL` happens. The measurement in §Q10 (0 survivors in both cases) is
  the expected result; the test is what stops it rotting. The test must clean up any
  surviving PID itself — a leaked rank from a failing test is worse than the bug.
  *Proved by* `agent/tests/test_executor.py::test_cancel_kills_every_rank_of_a_real_tree`.

- **AC-3 — A command issued while the agent was down is still honoured.** The command file
  is written to disk **before** `Agent.run()` starts, with the watcher already able to
  start. This is the D1 sweep, and it is the case §1 measured as invisible today.
  *Proved by* `agent/tests/test_loop.py::test_a_command_present_before_startup_is_honoured`.

- **AC-4 — A command issued while the backend was down is seen by the backend.** The command
  file exists before `SyncthingService.start()`; the real `_initial_scan` must surface it
  to the command handler. Drives the real `_initial_scan` (not a spy) and asserts the
  recorded outcome. This is the case §1 measured as invisible today, and the one the
  operator's recorded rationale did not cover.
  *Proved by* `backend/tests/integration/test_command_files.py::test_a_preexisting_command_is_seen_by_the_initial_scan`.

- **AC-5 — Propagation latency, asserted against the recorded budget.** Two assertions in
  one test, timed from the moment the command file is written:
  `t(stop path entered) - t(command written) <= 1.0 s` (**B1**), and
  `t(process group gone) - t(command written) <= 10.0 s` (**B2**). Both measured with
  `time.perf_counter` against a real watcher and a real child, with the drain cadence at
  its real 0.5 s (`agent/loop.py:41`). The test is scoped to **one node and one
  filesystem** and says so in its docstring: the Syncthing replication hop is D15's
  exclusion, not an oversight. A generous slack multiplier is **not** used — B1 has ~2×
  headroom over the measured 0.51 s and B2 ~1.8× over 5.5 s, so a regression past the
  budget is a regression, not jitter.
  *Proved by* `agent/tests/test_commands.py::test_propagation_latency_is_within_the_recorded_budget`.

- **AC-6 — The budget is recorded where an operator will find it.** The two numbers appear
  in `docs/spec.md`'s backend-integration section alongside the #105 submission
  description, with the measured medians next to them and the replication hop named as
  excluded. A test asserts the two numbers are present in the document, so a later change
  to the budget cannot drift from the test that asserts it — the same technique #105's
  AC-13 used on `.env.example`.
  *Proved by* `backend/tests/integration/test_command_files.py::test_the_latency_budget_is_documented`.

- **AC-7 — A stray `.yaml` under a job directory is neither routed as state nor silently
  swallowed.** A table-driven test over the shapes from §1's measured table, asserting
  for each: (a) `_process_job_file` is **not** called, (b) `_process_node_file` is not
  called, (c) `_handle_job_deleted` is not called, and (d) **a log record was emitted**
  naming the path. (d) is the criterion the issue is actually about and the one a routing
  test would otherwise omit. Shapes: `jobs/job-1/stray.yaml`,
  `jobs/job-1/notes/other.yaml`, `jobs/job-1/logs/out.yaml`,
  `jobs/job-1/commands/nested/deep.yaml`, and `nodes/subdir/thing.yaml`.
  *Proved by* `backend/tests/unit/test_command_routing.py::TestUnrecognisedPathsAreLoggedNotSwallowed`.

- **AC-8 — Command and ack paths are routed, in both directions and both watchers.**
  Backend: the real `_process_file` routes `commands/` to the command handler and `acks/`
  to the ack handler, and `_process_delete` treats both as non-deletions (AC-9). Agent:
  the real `FolderWatcher` enqueues a command file and `_drain_events` reaches the command
  handler. Asserted against real watchers, not fakes.
  *Proved by* `backend/tests/unit/test_command_routing.py` and
  `agent/tests/test_loop.py::test_a_command_file_event_reaches_the_command_handler`.

- **AC-9 — A deleted command or ack file never deletes the job.** Drive the real
  `_process_delete` for each of `commands/<id>.yaml` and `acks/<id>.yaml` and assert the
  job is still present **and** that `store.delete_job` was not called. §1 measured today's
  behaviour as already correct, so this is a regression guard on the §5.5(b) change, not a
  fix — and it is the single most dangerous edit in this issue.
  *Proved by* `backend/tests/unit/test_command_routing.py::TestDeletedCommandFilesNeverDeleteTheJob`.

- **AC-10 — Routing is decided by path arithmetic, and the arithmetic is pinned.** The test
  enumerates `(path, expected_route)` pairs including the ones the exact
  `len(parts) == 4` exists for: `jobs/job-1/commands/x.yaml` routes, `jobs/job-1/commands/
  nested/deep.yaml` does not, `jobs/job-1/commands/x.yaml.bak` does not (suffix), and
  `jobs/job-1/commands/.x.yaml` does not (dotfile). A test that only asserts the happy
  path passes with an off-by-one in the guard.
  *Proved by* `backend/tests/unit/test_command_routing.py::TestRouteIsDecidedByPathArithmetic`.

- **AC-11 — Monotonicity still holds, and agreement is no longer an alarm.** Both
  directions, driven through the real `_process_job_file`: (a) the agent writes `FAILED`
  over a recorded `CANCELLED` ⇒ the row is unchanged **and** a WARNING is logged (§1's
  reproduction C); (b) the agent writes `CANCELLED` over a recorded `CANCELLED` ⇒ the row
  is unchanged **and no record at WARNING level was logged** (§1's reproduction F). (b)
  is the new behaviour, and a test that only checks "no exception" would not notice its
  absence.
  *Proved by* `backend/tests/unit/test_command_routing.py::TestMonotonicityAgreementIsQuiet`.

- **AC-12 — The agent's completion write after a cancel reports `CANCELLED`, and the
  operator's view of the job is `CANCELLED`.** A real child is cancelled; the assertion is
  made twice: `state.yaml` on disk says `CANCELLED` with `error == "cancelled:by_operator"`
  (D12), and the database row, read through the real handler, says `CANCELLED`. The first
  is new behaviour; the second is #100's and is asserted here only so a refactor that
  disturbed it would fail in this test rather than in #99's. **The negative half matters
  as much:** the same stop driven by the **shutdown** path (`_hand_back_in_flight_job`,
  no reason set) must **not** write `cancelled:by_operator` — otherwise an operator's
  `Ctrl-C` is recorded as their own cancel request, and the provenance that whole
  vocabulary exists to carry is destroyed. AC-12's control case is what stops that passing
  for the right reason (§5.7).
  *Proved by* `agent/tests/test_executor.py::test_a_cancelled_job_reports_cancelled_with_the_operator_code`
  and `::test_a_shutdown_stop_is_not_reported_as_an_operator_cancel`.

- **AC-13 — Exactly one ack per command, ever, and the agent never routes its own ack as a
  command.** Three assertions: (a) a duplicate delivery (forced, not awaited — the 2-of-5
  case) produces **one** ack file and **zero** additional signals; (b) `handle_command` on
  a path under `acks/` returns immediately with no action and no ack; (c) an ack written by
  the agent and then delivered back to it is refused.
  *Proved by* `agent/tests/test_commands.py::TestOneAckPerCommand` and
  `::test_the_agent_never_treats_its_own_ack_as_a_command`.

- **AC-14 — Repeat cancel is idempotent, and the assertion is on the filesystem.** Cancel
  once, snapshot the job directory recursively, cancel again, and assert **409
  `JOB_NOT_CANCELLABLE`** *and* a byte-identical directory listing. The listing is the
  assertion that matters: a 409 with a stray second command file would pass a
  status-code-only test and still double-terminate (§1's duplicate measurement).
  *Proved by* `backend/tests/integration/test_command_files.py::test_repeat_cancel_writes_no_second_command`.

- **AC-15 — Retry is not a command, and that is asserted rather than documented.** Assert
  (a) `POST /jobs/{id}/retry` on a `FAILED` job writes **no** command file and **no** ack,
  and `state.yaml` is re-emitted with `status: PENDING` (#105's mechanism); (b) the
  `JobCommand` schema rejects any `command` other than `cancel`. A negative assertion on
  the schema is what stops a later "while we are here, let us add retry" from being a
  one-line change nobody re-reviews (Q5).
  *Proved by* `backend/tests/integration/test_command_files.py::test_retry_is_not_a_command`
  and `backend/tests/unit/test_shared_schemas.py::TestJobCommandIsClosed`.

- **AC-16 — The ack is surfaced, and the contract tripwire is expected to fire.**
  `GET /api/v1/jobs/{id}/commands` returns `{items, total}` with each item pairing a
  command to its ack or `null`, ordered by `command_id`, and returns `404` for an unknown
  job. A **requested, unacked** command is asserted to come back with `ack: null` — the
  distinguishability the issue asks for, tested in the state that matters most. And
  `openapi.yaml` is regenerated, which means
  `test_the_snapshot_and_the_live_schema_are_identical` must now pass against the **new**
  snapshot; the test file's docstring is updated to say the regeneration was deliberate,
  so the next reader does not read it as a #105 regression.
  *Proved by* `backend/tests/integration/test_command_files.py::TestTheAckIsSurfaced`.

- **AC-17 — A consumed command is marked, and a restarted agent does not re-execute it.**
  Not a marker file: **the ack**. The test (a) runs a real agent, honours a cancel, asserts
  the ack file exists; (b) constructs a **second, fresh** `Agent` over the same root — a new
  process's worth of state, new `SelfWriteLedger`, no in-memory anything carried over — and
  hands it the same command path; (c) asserts **no second signal** and **no second ack**.
  The "fresh `Agent`" part is load-bearing: reusing the same instance would pass on a
  ledger that a real restart does not have.
  *Proved by* `agent/tests/test_commands.py::test_a_restarted_agent_does_not_re_execute_an_acked_command`.

- **AC-18 — A stale command for a job this node does not own is ignored, not acted on.**
  Table-driven over `classify_command`'s refusals, plus one end-to-end case: a job whose
  `state.yaml` names `node-02`, delivered to `node-01`'s agent with **a live process
  standing in for that job** — the assertion is that the process survives. An
  `IGNORED_NOT_OWNER` ack is written. Also asserted: a command for a job with no
  `state.yaml` gives `IGNORED_NO_JOB`, and a command for an already-terminal job gives
  `ALREADY_TERMINAL`.
  *Proved by* `agent/tests/test_commands.py::TestStaleCommandsAreIgnored`
  and `::test_a_stale_command_does_not_kill_another_nodes_process`.

- **AC-19 — The backend never writes an agent-owned field as a way of commanding the
  agent.** Two halves. (a) **Mechanical:** an AST assertion over
  `backend/services/job_service.py` that the cancel path contains no `write_yaml` whose
  argument is anything other than a `command_file(...)` path, and no assignment of
  `status`/`exit_code`/`error` into a folder-bound model — the same technique as
  `agent/tests/test_hardware.py:549-559`, which is "one write, one place to audit".
  (b) **Behavioural:** after a cancel, `state.yaml` on disk is **unchanged and still says
  `RUNNING`/`PENDING`** — reproduced in §1 as the defect, asserted here as its permanent
  absence. A cancel that touched `state.yaml` would fail (b) even if it passed (a), and (b)
  is the one an operator experiences.
  *Proved by* `backend/tests/unit/test_command_routing.py::TestTheBackendNeverCommandsThroughStateYaml`.

- **AC-20 — The protocol is written down before implementation.** This document, plus the
  `docs/spec.md` section required by AC-6, exists in the tree before any code lands. Checked
  by the orchestrator at merge time, not by a test — a test that asserts a file's prose
  exists is theatre. What a test *does* check is the one thing that can silently drift:
  that the schema's `COMMAND_ID_PATTERN` in `shared/schemas/job_command.py` and the pattern
  used by the two path modules are the same string.
  *Proved by* `backend/tests/unit/test_job_folder_paths.py::test_the_command_id_pattern_is_defined_once`.

- **AC-21 — The two path conventions cannot drift.** `backend.job_folder.command_file(root,
  jid, cid) == agent.paths.command_file(root, jid, cid)` and likewise for
  `ack_file`, over several ids including ones that do not match the pattern. The direct
  analogue of #105's AC-10, and the reason the helpers exist twice rather than once.
  *Proved by* `backend/tests/unit/test_job_folder_paths.py::TestBackendAndAgentCommandPathsAgree`.

- **AC-22 — `DELETE` removes commands and acks with the job, and a cancel between the
  command and its delivery is visible as "never delivered".** (a) After `DELETE`, neither
  `commands/` nor `acks/` exists. (b) With a command written and **no** ack, the endpoint
  from AC-16 reports `ack: null` — Q4's stated consequence, asserted in the state an
  operator will actually see. A backend-side ack on delete would be a lie about a node that
  never answered.
  *Proved by* `backend/tests/integration/test_command_files.py::TestDeleteRemovesCommandsAndAcks`.

- **AC-23 — Malformed input is refused visibly, on both sides.** (a) A command file whose
  body contradicts its filename (`commands/cancel-...yaml` containing `command: something-else`)
  is refused with a WARNING naming the path. (b) A command file missing `command_id`, or
  carrying one that does not match the filename, is refused with a WARNING. (c) A command
  file that is not valid YAML is logged at ERROR by the handler's existing bare `except`
  and produces **no** ack. None of these may be silent — the issue's §1 table is entirely
  silent drops, and an ack that claims a decision was never made is worse than no ack.
  *Proved by* `backend/tests/unit/test_command_routing.py::TestMalformedCommandsAreRefusedVisibly`
  and `agent/tests/test_commands.py::test_a_malformed_command_produces_no_ack`.

### Quality

- **AC-24 — `pytest -q` green, and not fewer than the baseline.** The modules this issue
  touches most directly are green **today** at **454 passed, 5 skipped**
  (`backend/tests/unit/{test_syncthing,test_job_folder_paths,test_api,test_api_fixes,test_store,test_shared_schemas,test_write_yaml_json_mode}.py`
  + `agent/tests`), and that count must not drop. `.shc/secret_key` is unchanged by the run
  (`guard_signing_key`, #65) and `assert_no_db_in_source_tree` (`conftest.py:230`) must not
  fire — no database migration is added (D16), so a test reaching the real
  `data/scientific_home_cluster.db` is a bug.

- **AC-25 — `ruff check . && ruff format --check . && mypy .` all exit 0.** No new
  `type: ignore`, and no widening of the narrow overrides in `pyproject.toml:71-141`.
  `syncthing_service.py` is **not** among them — #105 AC-17 established that it is type
  checked — so the routing changes are checked. The route file
  `backend/api/v1/jobs.py` **is** excluded (`:76-77`), so the cancel logic lives in
  `job_service.py`, as it already does.

- **AC-26 — `TestNoPrivateStoreAccess` (`backend/tests/unit/test_api_fixes.py:153-170`)
  still passes.** `job_service.py`, `syncthing_service.py` and `backend/api/v1/*.py` reach
  into no store internals. The in-memory command log (§5.11) is exposed through a public
  method, not a private attribute — the mechanical guard behind D4.

- **AC-27 — Coverage does not fall below `fail_under = 80`** (`pyproject.toml:162`), with
  `agent/commands.py` measured explicitly rather than in the aggregate: every branch of
  `classify_command` (§5.6's six rows) is covered by a named test.

---

## 8. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **A command is written and never seen** — backend down, agent down, or the `_initial_scan` gap of §1 | **Certain without D1** (measured) | **Critical** — the operator believes the job stopped, and it burns a GPU until it finishes on its own | D1's per-job-dir scan on both sides; AC-3 and AC-4 pin the two cases separately; §5.10's failure matrix shows the one partial state that heals |
| **The command is delivered twice and terminates twice** | **2 in 5 runs, measured** | Medium — `SIGTERM` to a dead group is `ProcessLookupError`, but a second `SIGTERM` to a *new* process that inherited the pid would be catastrophic | D6: the ack check precedes every action, including the refusals. AC-13 pins exactly-one-ack and zero-additional-signals |
| **A stale cancel kills another node's job** | Low (needs a replication race) | **Critical** — one node's cancel stopping another node's workload | The `IGNORED_NOT_OWNER` check is *before* any signal, and AC-18 asserts a live standing-in process survives. This is the one safety property that is not negotiable |
| **The agent's confirming write is logged as an overwrite**, and operators learn to ignore the monotonicity WARNING | **Certain** without D11 (measured) | High — the one log line that means "a terminal state was corrupted" becomes noise | D11 splits agreement from disagreement; AC-11 asserts both directions, so the reintroduction of the WARNING is a failing test and not a review decision |
| **The routing change breaks job deletion** — a command or ack file deletion reaches `_handle_job_deleted`, which purges metrics, logs and the job row | Low (a plausible copy-paste in §5.5(b)) | **Critical** — total loss of one job and its telemetry | AC-9 drives the real `_process_delete` for both paths and asserts `store.delete_job` was not called. Today's behaviour is already correct (§1's table), so this is purely a regression guard on this issue's own edit |
| **An unrecognised `.yaml` becomes a permanent WARNING**, and operators disable the log | Medium | Medium | D10: DEBUG, not WARNING. A path that warns forever is a path that gets ignored, and #101's `logs/` will be unrecognised until it lands |
| **Two conventions drift** — `backend.job_folder` and `agent.paths` compute the same path differently, and the backend's watcher stops seeing what the agent watches | Low | **Critical** — the protocol is invisible in one direction, with no error anywhere | AC-21, the direct analogue of #105's AC-10; and AC-20's pattern tripwire, so the filename grammar has one definition |
| **`openapi.yaml` is regenerated and someone reads the failing #105 test as a regression** | **Certain** | Low | D13 and AC-16 name the failure in advance, with the file and test to look at |
| **The agent deletes a command file**, destroying the audit trail and racing the backend's watcher | Low (nothing tells it to) | High | D5; AC-18's "the agent never treats its own ack as a command" plus the module docstring assertion pattern from #105 AC-13 |
| **The agent dies between the signal and the ack**, leaving `RUNNING` with no process | Low | Medium | Self-healing on the next duplicate delivery or startup sweep (§5.10); the residual `RUNNING`-with-no-process case is **#103's**, handed over explicitly in §4.2 |
| **A cancel is requested and the operator then `DELETE`s the job**, and no ack ever arrives | Medium | Low | The UI must render requested-without-ack as a real state; AC-16 pins `ack: null`, AC-22 pins the delete |
| **`exit_code` confusion**: the agent's `-15`/`-9` and the backend's `-1` are conflated | Medium | Medium | D12: the sentinel is the backend's `-1` and stays it; the agent's write is refused by monotonicity anyway, so the operator's view is unchanged and the folder keeps the process's truth. #99 owns refining this further |
| **Replication cost** — every command and ack fans out to every node | Certain | Low | One small file per operator action. Two orders of magnitude below #102's per-30 s metrics file, which is the cost #92 flagged for measurement. Nothing to tune |
| **The folder grows a command and ack directory per job** | Certain | Low | D14: `DELETE` bounds it; the rate is one file per operator action |
| **The idempotency check is only in-memory somewhere**, and a restart re-executes | Low if D6 is followed | High | D6 puts it on the **acked file on disk**, not in memory. AC-17 uses a *fresh* `Agent`, because a reused instance would pass on a ledger a restart does not have |
| **The full suite is already red on this branch** (`starlette.testclient.Client.__init__() got an unexpected keyword argument 'app'` — 19 failed, 28 errors across `test_cors.py`, `test_auth.py`, `test_main.py`, `test_websocket.py`, identical in both `.venv` and `venv`, and present before this issue touched anything) | **Certain today** | Medium — it will be mistaken for a #106 regression | **Pre-existing and unrelated; it is an On-Call matter, not this issue's.** AC-24 records a baseline for the modules this issue actually touches. PM flags it to the orchestrator rather than absorbing it here |

---

## 9. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Implementation complete (SWE)
- [ ] AC-1 … AC-27 verified with evidence (QA)
- [ ] `pytest -q` green **for the modules this issue touches** (AC-24's baseline), and the
      pre-existing `starlette`/`testclient` failures are raised separately, not fixed here
- [ ] `ruff` / `mypy` clean (AC-25)
- [ ] `openapi.yaml` regenerated, with
      `test_the_snapshot_and_the_live_schema_are_identical` updated to say the regeneration
      was deliberate (D13, AC-16)
- [ ] #103 and #99 cross-referenced in their bodies, so neither claims the other's work
- [ ] PM acceptance

---

## 10. Related

- **#92** — the transport decision. BLOCKER 2 of it. Its operator-decisions comment settles
  D1's layout, D3's ack, D2's "never write agent-owned fields", and the resource model
  (no container, no `systemd-run`) that this protocol inherits unchanged.
- **#105** — submission enters the folder. **Its retry re-emit is why `retry` is not a
  command** (Q5). Its §5.5 ownership table is extended by §5.1. Its `remove_job_dir` is
  what bounds command-file retention (D14). Its AC-13 comment-assertion pattern is reused
  in AC-19, and its AC-10 path-drift tripwire in AC-21.
- **#100** — terminal state. **Its monotonicity rule already exists and already satisfies
  this issue's last acceptance criterion** (§1, reproduction C), so AC-11 asserts it still
  holds rather than implementing it. Two corrections are recorded: its §5.2 branch treats an
  *agreeing* write as an overwrite (D11), and its §5 Q3 attributes `cancelled:by_operator`
  to a writer that writes nothing (D12).
- **#97** — discovery and claim. Consumes the `PENDING` retry re-emit that makes retry work
  without a command. Its advisory marker (`.claim.<node_id>`) is a dotfile and stays
  invisible to both watchers, which is why §5.1 does not have to exclude it.
- **#98** — execution. Owns `start_new_session=True` (`agent/executor.py:305`), without
  which the SIGTERM in §5.7 orphans every `mpiexec` rank. Its best-effort resource model is
  inherited unchanged (Q5).
- **#99** — cancellation propagation. **Blocked on this issue, and the boundary is §4.1.**
  This issue delivers the command; #99 owns the signal sequence, the grace period and the
  exit-code convention. The two meet at exactly one place: the `stop_event` this issue
  plumbs (`agent/loop.py:508` → an attribute) and the one `CANCELLED` branch this issue
  adds to `execute_job`'s status ladder.
- **#101 / #102** — logs and metrics. Each needs its own routing branch for the same reason
  (§1's table), and D10's "log the unrecognised" rule is what keeps their files visible in
  the meantime. Neither is touched here.
- **#103** — crash/restart recovery. Owns the `RUNNING`-with-no-process case that §5.10
  hands over, and inherits a working stop-event seam instead of the bare `pass` at
  `agent/loop.py:550-552`. Per the operator's #92 correction, **nothing restarts the agent**,
  so its reconciliation sweep is the only thing that tells an operator the agent died.
- **#104** — docs. **No systemd**: the agent is launched by hand and there is no unit file.
  It receives one section from this issue — how to tell a delivered cancel from a requested
  one by looking at the folder — plus the latency budget for its troubleshooting list.
- **#34 / #58** — the CWD-relative-root and `.env`-found-from-one-CWD bugs. The reason
  `backend/services/job_folder.py` exists at all, and the reason the command helpers are
  added there rather than to `shared/file_ops/path_utils.py` (defect C8, no injection
  point).
- **#36** — the synthetic marker. Landed. Nothing here writes metrics or log rows, so there
  is nothing to distinguish as fabricated.
- **#71** — `file_lock`/`shared_lock`'s dead `timeout`. Remains unused after this issue,
  deliberately: a protocol of one-file-per-command needs no lock (D5).
- **#121** — inconsistent timezone handling. Another reason `acknowledged_at` and
  `issued_at` are naive local `datetime.now()`, matching every other writer (#95 §5).
