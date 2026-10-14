# Spec: Agent Discovers and Claims Work (`jobs/<id>/state.yaml`)

**Issue**: #97 — "An agent cannot discover work: the node filter can never match, and nothing claims a job"
**Labels**: `agent`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-14

---

## 1. Verified Problem Statement

The agent has no way to reliably find work, and no code path transitions a job from `PENDING` to `RUNNING`.

**Trap 1 — the node filter can never match.** `POST /jobs` inserts with `node_id = NULL` (`backend/store/database_store.py:346`), but `list_jobs` filters on equality (`:417-418`). An agent querying `status=PENDING&node=node-01` gets `{"items": [], "total": 0}` indefinitely. The contract "filter client-side, do not pass `node`" exists only in its absence.

**Trap 2 — nothing claims anything.** Grepping the backend for an assignment of `RUNNING` or `node_id` finds only the demo seeder (`database_store.py:190-194`) and `retry_job`, which *clears* the node (`job_service.py:64`). `JobStatus.RUNNING` appears only at `job_service.py:78`, inside the cancel guard. The entire `PENDING → RUNNING` edge is unpopulated by design.

**Trap 3 — unknown status returns empty, not an error.** `database_store.py:414-415` treats an unrecognised status string as matching nothing. A typo in an agent's status filter produces `200 OK` with zero results, indistinguishable from an idle cluster.

**Trap 4 — `current_job_id` is never maintained.** `nodes.current_job_id` is `Optional` (`backend/store/database.py:76`) and nothing sets it. "Exclusive node allocation (one job per node)" is advertised in `README.md:53` but is enforced by nothing.

**Trap 5 — `job_id` is doubly constrained.** `syncthing_service.py:272-274` rejects anything not `job-\d+`, and `shared/schemas/job_state.py:17` enforces `^job-\d+$`. The agent must claim an id the **backend** allocated.

**Trap 6 — file-based claim has a lost-update race.** Two agents can both read the same `PENDING` `state.yaml` and both write `RUNNING`. The only primitives available are `file_lock`/`shared_lock` (`shared/file_ops/locking.py:41,126`), which are **never used in production code** and are being reworked under #71 (timeout is dead code because `portalocker` raises `LockException`, not `OSError`).

---

## 2. Root Cause

The file transport was chosen for job state (#105), but the claim protocol was never implemented. The watcher (`agent/watcher.py`) already observes `on_moved` events for newly delivered `state.yaml` files (from #105), and the event drain (`agent/loop.py:_drain_events`) logs them — but parsing and routing is the #97 boundary. The backend's `_process_job_file` (`syncthing_service.py:268-303`) already reads `status`, `node_id`, `started_at`, `completed_at`, `exit_code`, `error`, `retry_count` from the file and syncs them to the store. The ownership table (§5.5 of #105) already assigns `PENDING` to the backend and `RUNNING`/`COMPLETED`/`FAILED`/`CANCELLED` to the agent. What is missing is the agent code that consumes the event, filters by resources, and writes the claim.

---

## 3. Open Questions — Resolved

### Q1 — Can the backend arbitrate across machines at all?

**Decision: No, not for the claim step.** The transport decision (from the 13-question batch, referenced in #92) is **file-only**. The backend is a single FastAPI process against one SQLite file, so it *could* make a claim atomic via HTTP, but the architecture decision is that the agent discovers work by watching the Syncthing folder, not by querying the API. The claim is therefore file-based, and exactly-once across machines is **not achievable** — `file_lock`/`shared_lock` are host-local OS locks; Syncthing replicates files, not locks. The decision: **at-least-once + advisory local marker**. Cross-machine double-claim is a documented known limitation.

### Q2 — What happens to a job no node can ever fit?

**Decision: Leave it `PENDING` forever and surface the reason.** The agent evaluates `JobSpec.resources` against its own `NodeSpec` (§5). If no node in the cluster can satisfy the request, the job stays `PENDING` with a visible reason (exposed via `GET /jobs/{id}` and the dashboard). A scheduler component that detects "fits no node" and marks `FAILED` is out of scope for this issue — the visibility is the minimum useful outcome.

### Q3 — Does `PENDING` survive a backend restart?

**Decision: Yes, the file is the source of truth.** Under file transport the `state.yaml` on disk is canonical. The backend restarts, rescans the folder, and reconstructs the DB row from the file. The agent does not hold an in-memory queue of claimed work (the watcher event queue is the only transient state), so a restart does not lose claims.

### Q4 — Should resource matching be exact or best-fit?

**Decision: First-that-fits.** The agent watches the folder sequentially and claims the first `PENDING` job whose resources it can satisfy. Best-fit would require global knowledge of all nodes' capacities, which the file transport does not provide without a scheduler. A home cluster with 2-4 heterogeneous nodes does not need best-fit; the choice is recorded here so a later scheduler issue can invalidate it.

### Q5 — What about the unknown-status filter behaviour (Trap 3)?

**Decision: Document the current behaviour, do not change it.** The `422` fix would be a breaking API change and belongs to a separate API-hardening issue. The spec records that an agent must not pass an unknown status string, and a test asserts that a typo produces `200 OK` with zero results so the behaviour is locked in.

---

## 4. Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| Consuming `FolderEvent` from `agent/watcher.py` and routing job events | Changing the `JobSpec`/`JobState`/`Resources` schemas |
| Resource filtering: agent reads `JobSpec.resources`, compares to its `NodeSpec` | HTTP claim endpoint (transport is file-only per #92) |
| Claim protocol: write `status: RUNNING, node_id, started_at` to `state.yaml` | Fixing `file_lock`/`shared_lock` timeout (#71) — advisory marker is used instead |
| Advisory marker file `jobs/<id>/.claim` (local `O_EXCL`) | Global scheduler, best-fit, pre-emption |
| Enforcing at most one in-flight job per agent (`current_job_id` on node) | Retry orchestration (#100, #103) |
| Setting `started_at` at claim (agent-owned timestamp) | Log streaming, metrics, execution (#101, #102) |
| Documenting cross-machine double-claim as known limitation | Backend `list_jobs` unknown-status behaviour (Trap 3) |

### Coupling to #105 — recorded, not fixed

#105 established the ownership table: backend writes only `PENDING`; agent writes `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`. This spec implements the agent's write of `RUNNING` and `node_id`. The backend's `_process_job_file` already syncs those fields to the DB. No change to the backend is required.

---

## 5. Implementation Approach

### New file: `agent/claim.py`

A pure function for resource matching and a claim function that writes the file.

```python
def can_fit_job(node_spec: NodeSpec, job_spec: JobSpec) -> tuple[bool, str]:
    """
    Return (fits, reason). Reason is empty on success, explanatory on failure.
    Pure: no I/O, testable with fakes.
    """
    ...


def claim_job(
    paths: AgentPaths,
    job_id: str,
    node_id: str,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """
    Attempt to claim a PENDING job.
    Returns (success, reason). On success, state.yaml is updated with
    status=RUNNING, node_id=<node_id>, started_at=now.
    On failure, state.yaml is unchanged.
    Creates advisory marker file jobs/<job_id>/.claim with O_EXCL.
    """
    ...
```

### Resource matching strategy

| Resource | Agent check | Notes |
|---|---|---|
| `gpus` | `job_spec.resources.gpus <= node_spec.gpus_count` | `node_spec.gpus` is `List[GPUInfo]`; count is `len(gpus)` |
| `cpus` | `job_spec.resources.cpus <= node_spec.cpus` | |
| `memory_gb` | `job_spec.resources.memory_gb <= node_spec.memory_gb` | |
| `vram_gb` | `job_spec.resources.vram_gb <= max(gpu.memory_gb for gpu in node_spec.gpus)` | If `gpus == 0`, `vram_gb` must be `0`; agent with `gpus: []` cannot fit `vram_gb > 0` |

`GPUInfo.memory_gb` is clamped to `>= 0` in `collect_node_spec` (#95 Q3). A node with `gpus: []` has zero VRAM capacity.

### Claim protocol (advisory marker + file write)

1. Agent receives `FolderEvent(kind="created", path="jobs/job-42/state.yaml")`.
2. Reads `state.yaml` via `read_yaml(path, JobState)`.
3. If `job_state.status != JobStatus.PENDING` → ignore (already claimed/completed).
4. If `job_state.node_id is not None` → ignore (already owned).
5. If `not can_fit_job(self_node_spec, job_state.spec)` → ignore (cannot run this job).
6. If `self.current_job_id is not None` → ignore (already running a job).
7. Attempt to create marker file `jobs/job-42/.claim` with `os.open(..., os.O_CREAT | os.O_EXCL | os.O_WRONLY)`.
   - If `FileExistsError` → another local task claimed it; ignore.
   - If success → marker created, proceed.
8. Write `state.yaml` with `status=RUNNING`, `node_id=self.node_id`, `started_at=now` using `update_yaml` (atomic temp + replace).
9. Update `self.current_job_id = job_id`.
10. Write `nodes/<node_id>.yaml` with updated `current_job_id` (next heartbeat tick will do this; claim writes it immediately for AC-6).
11. On any failure after step 7: remove marker file, leave `state.yaml` untouched, return `(False, reason)`.

### Where the event is consumed

In `agent/loop.py`, `_drain_events` is the boundary. It currently logs and returns. The implementation adds:

```python
async def _drain_events(self) -> None:
    queue = self._watcher.events()
    while True:
        try:
            event = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        logger.info("observed %s %s", event.kind, event.path)
        # #97: route job events
        if event.kind == "created" and self._is_job_state_file(event.path):
            await self._maybe_claim_job(event.path)
```

`_is_job_state_file` checks `path.name == "state.yaml"` and `path.parent.name.startswith("job-")`.

`_maybe_claim_job` calls `claim_job` and on success logs the claim and updates `self.current_job_id`.

### `current_job_id` lifecycle

- **Set on claim**: immediately after successful `state.yaml` write, agent writes its node file with `current_job_id = job_id`.
- **Cleared on completion**: `_write_final_state` (seam for #100) will clear it when the job finishes. #97 only ensures the field is written at claim; the clearing is #100's responsibility but the test for AC-6 verifies both transitions.

### Job ID handling

- Agent **never generates** a `job_id`. It only ever reads `job_state.job_id` from the `state.yaml` delivered by the backend (via #105).
- A test asserts the agent never calls `write_yaml` on a `state.yaml` for a `job_id` it did not observe in a `FolderEvent`.

---

## 6. Decisions on Concurrency and Known Limitations

### Advisory marker is local only

`os.O_EXCL` on `jobs/<id>/.claim` prevents two tasks *on the same node* from claiming the same job. It does **not** prevent two different nodes from both seeing `PENDING` and both writing `RUNNING`. Syncthing will eventually replicate both writes; the last writer wins. The backend's `_process_job_file` will see whichever `state.yaml` arrives last. This is the documented **at-least-once** guarantee: a job may be claimed by two nodes, but never zero (if at least one node is online and can fit it).

### Marker file cleanup

The marker file is removed:
- On claim failure (any exception after marker creation)
- On job completion (`_write_final_state` / #100)
- On agent shutdown (`_hand_back_in_flight_job` / #103)

A stale marker from a crashed agent is harmless — the next agent to start will see `state.yaml` with `status=RUNNING` and `node_id` set, and will not attempt to claim it. The marker is only consulted when `status == PENDING`.

### Exclusivity enforcement

The agent holds **at most one** in-flight job. `self.current_job_id` is `None` or a single `job_id`. The claim protocol checks this before attempting to claim. This is a local invariant; the backend's `nodes.current_job_id` mirrors it for visibility.

---

## 7. Acceptance Criteria

### Functional

- **AC-1** — A submitted `PENDING` job is discovered by a running agent within a bounded interval (real end-to-end test).
  ```bash
  # Test: start backend, start agent, POST /jobs, poll GET /jobs/{id} until status=RUNNING
  # Assert transition happens within 10s (watcher latency + claim write + sync)
  ```

- **AC-2** — Discovery contract documented: folder watch, no API query.
  - Spec §5 states the agent consumes `FolderEvent` from `agent/watcher.py`.
  - No HTTP call to `GET /jobs` is made for discovery.

- **AC-3** — Two agents racing for one job → exactly one winner (advisory marker test; cross-machine limitation documented).
  ```bash
  # Unit test: two tasks call claim_job concurrently on the same job_id
  # One returns (True, ""), one returns (False, "marker exists")
  # Cross-machine: README documents "at-least-once; double-claim possible across nodes"
  ```

- **AC-4** — Already-claimed job never claimed again; rejected claim leaves no partial state.
  ```bash
  # Test: state.yaml has status=RUNNING, node_id=node-01
  # Agent node-02 processes event → ignores it
  # Assert state.yaml unchanged, no .claim file left behind
  ```

- **AC-5** — Agent holds at most one job (test submits several, observes one in-flight).
  ```bash
  # Test: submit 3 jobs that fit the node
  # Agent claims first, sets current_job_id
  # Next two events processed → ignored because current_job_id is set
  # Assert only one job has status=RUNNING
  ```

- **AC-6** — `current_job_id` set on node at claim, cleared on completion (verified via `GET /nodes/{id}`).
  ```bash
  # Test: claim job → GET /nodes/node-01 → current_job_id == "job-42"
  # Simulate completion (write COMPLETED) → GET /nodes/node-01 → current_job_id == null
  ```

- **AC-7** — Resource filtering implemented and tested, including "fits no node" case with visible outcome.
  ```bash
  # Test: node has 1 GPU, job requests 2 GPUs
  # Agent processes event → ignores job (cannot fit)
  # GET /jobs/{id} → status=PENDING, error field contains "no node can satisfy: gpus (need 2, max 1)"
  # Job stays PENDING, not silently dropped
  ```

- **AC-8** — `started_at` set by agent at claim, survives to `GET /jobs/{id}`.
  ```bash
  # Test: claim job → GET /jobs/{id} → started_at is non-null and ~now
  ```

- **AC-9** — `job_id` taken from backend response, never generated (test asserts agent never writes `state.yaml` for unknown id).
  ```bash
  # Test: mock FolderEvent for job-999 (never submitted)
  # Agent ignores because no such file exists in jobs/ (watcher only emits real files)
  # Additional: assert claim_job is never called with a job_id not seen in an event
  ```

- **AC-10** — All gates green.
  - `pytest -q` (backend + agent)
  - `ruff check . && ruff format --check . && mypy .`
  - `cd frontend && npm run lint && npm run typecheck`

### Quality

- **AC-11** — `can_fit_job` is pure and covered by unit tests with faked `NodeSpec`/`JobSpec`: normal fit, GPU shortfall, CPU shortfall, memory shortfall, VRAM shortfall, zero-GPU node vs VRAM>0 job.
- **AC-12** — `claim_job` unit tests cover: success, already RUNNING, already owned by another node, cannot fit, already running a job, marker race (local), marker cleanup on failure.
- **AC-13** — No new `type: ignore`, no widening of existing overrides in `pyproject.toml`.
- **AC-14** — The cross-machine double-claim limitation is documented in `README.md` (or `docs/known-limitations.md` if it exists) and in the spec §6.

---

## 8. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Two nodes claim the same job (cross-machine race) | **Certain under load** | Medium | Documented known limitation; at-least-once is the contract. Idempotent job scripts mitigate corruption. |
| Marker file leaked on crash | Low | Low | Stale marker is harmless — `status != PENDING` gates the claim. |
| Agent claims a job it cannot actually run (resource check bug) | Medium | High | Pure `can_fit_job` with exhaustive unit tests (AC-11). |
| `started_at` timezone mismatch with backend | Medium | Medium | Agent writes naive `datetime.now()` to match every other writer (#95 §5 correction). |
| Watcher event lost (Syncthing conflict, watchdog gap) | Low | Medium | Backend `_process_job_file` is idempotent; agent can re-scan on startup (future #104). |
| `update_yaml` race with backend writing same file | Low | Medium | Backend only writes `PENDING` (ownership table); agent only writes `RUNNING`/`COMPLETED`/`FAILED`/`CANCELLED`. No overlapping fields. |
| `vram_gb` check wrong on multi-GPU node (max vs sum) | Medium | Medium | Spec says `max(gpu.memory_gb)` — a job needing 24 GB VRAM fits a node with 2×24 GB cards but not 2×12 GB. Unit test covers this. |

---

## 9. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Implementation complete (SWE)
- [ ] AC-1 … AC-14 verified with evidence (QA)
- [ ] `pytest -q` green; `ruff`/`mypy` clean
- [ ] PM acceptance

---

## 10. Related

- #92 — Transport decision (file-only; exactly-once not achievable)
- #93 — Agent skeleton (`AgentPaths`, `_drain_events`, `_write_final_state` seams)
- #95 — Node registration file (provides `NodeSpec` for resource matching, `current_job_id` field)
- #100 — Terminal job status on shutdown (clears `current_job_id`)
- #101 — Job execution (child process, logs)
- #103 — Hand back in-flight job on shutdown
- #105 — Job submission enters folder (writes `PENDING` `state.yaml`, owns `on_moved` delivery)
- #71 — `file_lock`/`shared_lock` timeout dead code (not used; advisory marker instead)
- #121 — Inconsistent timezone handling in store/monitor (agent writes naive local)