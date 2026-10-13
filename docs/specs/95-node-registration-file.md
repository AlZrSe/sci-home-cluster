# Spec: Node Registration File (`nodes/<node_id>.yaml`)

**Issue**: #95 — "Nothing writes `nodes/<node_id>.yaml`, so no node can ever appear in the dashboard"
**Labels**: `agent`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-13

---

## 1. Verified Problem Statement

No process in the repository writes `nodes/<node_id>.yaml`. That file is the only way a node
enters the system: `backend/services/syncthing_service.py:280-308` reads it into a `NodeSpec` and
writes it to the store. Until an agent produces one, the cluster has zero nodes, the heartbeat
monitor has nothing to act on, and the dashboard's node pages stay empty.

Every field of `NodeSpec` except `current_job_id` is mandatory, and the whole handler is wrapped in
a bare `except Exception` (`:307-308`) that **logs and drops**. A malformed file therefore produces
no node and no visible failure — the cluster looks empty, which is indistinguishable from "the
agent is not running".

Constraints that reject a file outright (`shared/schemas/node_spec.py:15-28`):

| Field | Constraint | Consequence of violating it |
|---|---|---|
| `cpus` | `ge=1` | node rejected, silently |
| `memory_gb` | `ge=1` | node rejected, silently |
| `status` | `^(ONLINE\|OFFLINE)$` | `BUSY`/`DRAINING`/`UNKNOWN` all rejected |
| `gpus` | required `List[GPUInfo]` | must be `[]`, **not** omitted |
| `last_heartbeat` | `NOT NULL` in DB (`backend/store/database.py:75`) | create fails |

Two things the backend explicitly does **not** do, so the agent owns them outright:

- **Nothing sets `current_job_id`.** Not the watcher, not any route, not any service.
- **Nothing writes `status` from the agent side.** The heartbeat monitor flips `ONLINE`↔`OFFLINE`
  in the DB only (`syncthing_service.py:341-349`), so anything the agent writes other than
  `ONLINE` gets fought.

---

## 2. Root Cause

The agent skeleton landed in #93 with the *seams* in place — `agent/paths.py` already exposes
`AgentPaths.node_file`, and the loop already has a `_write_final_state` no-op reserved for #100 —
but nothing was ever wired into them. This is missing implementation, not a defect in existing
code.

---

## 3. Open Questions — Resolved

The issue left four questions open. Each is decided here, with rationale.

### Q1 — Which hardware-detection library?

**Decision: `psutil` for CPU and memory; `gputil` for GPUs, imported lazily and tolerating
absence; no `nvidia-smi` subprocess.**

- `psutil` is already a hard dependency (`pyproject.toml:23`) and is installed.
- `gputil` is already declared (`pyproject.toml:24`) — **but is not currently installed** in the
  development virtualenv, and calling `gputil.getGPUs()` raises `ValueError` on a machine with no
  NVIDIA driver. A declared dependency that raises on a CPU-only box is unusable as-is, so the
  import and the call must both be guarded.
- `pynvml` is **not** added. It is a new dependency with an NVIDIA-only assumption, and the issue
  explicitly asks whether the agent is expected to work without vendor tooling. It must.
- An `nvidia-smi` subprocess is rejected: it puts a binary on `PATH` in the critical path of
  registration, adds a timeout that must be defended against, and buys nothing `gputil` does not.

**Degradation contract — a node with no GPU is normal, not an error:**

| Situation | Result |
|---|---|
| `gputil` importable, `getGPUs()` returns devices | one `GPUInfo` per device |
| `gputil` importable, raises / returns `[]` | `gpus: []` |
| `gputil` **not installed** (today's venv) | `gpus: []` |
| individual GPU lookup raises | that device is skipped; the rest are kept |

`gpus: []` must be **written as an empty list**, never omitted — `gpus` is a required field and an
omitted key fails validation, which the backend drops silently.

### Q2 — What goes in `os`?

**Decision: `platform.system().lower()`, i.e. `"linux"`, `"darwin"`, `"windows"`.**

It is a free-form `str` with no constraint, so anything passes and any value is equally
inconsistent. Pinning it to the normalised platform name makes the field comparable and
sortable across a fleet. The kernel version is deliberately **not** included: it would make every
patch bump a diff in a replicated file for no operational gain. The full kernel string belongs in
a log line, which the agent already emits.

### Q3 — Should the agent clamp `GPUInfo.memory_gb`?

**Decision: yes — clamp to `>= 0`, and drop a GPU entry whose name is empty.**

`GPUInfo` has **no** validation constraint on `memory_gb` (`shared/schemas/gpu_metric.py:10-14`),
unlike `cpus`/`memory_gb` on `NodeSpec`. A bogus `-1` therefore validates and reaches the store
unchallenged. Clamping is a two-line guard against writing a value we already know is wrong. The
schema itself is **not** changed here — tightening it would be a cross-cutting change to every
reader and writer of GPU data, which belongs to the metrics issue (#102), not this one.

### Q4 — File write or HTTP transport?

**Decision: file write, recorded as a scope boundary that a later transport decision can
invalidate.**

The transport question is not settled anywhere in the repo. This spec assumes the file write
because `AgentPaths.node_file` already exists and the backend watcher already consumes it. If a
later decision moves registration to HTTP, this issue becomes an API call and **only the probe
logic survives**. To keep that migration cheap, the probe is specified as a **pure function** with
no I/O, so it is reusable either way.

---

## 4. Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| Hardware probe producing a `NodeSpec` (pure function, no I/O) | Changing the `NodeSpec`/`GPUInfo` schemas |
| Writing `nodes/<node_id>.yaml` atomically via `write_yaml` | Any HTTP transport for registration |
| Ensuring `nodes/` exists before the first write | Setting `current_job_id` (belongs to #97) |
| Periodic rewrite as a heartbeat | Metrics writes (#102) |
| Re-registration: create-then-update | Fixing the hardcoded 90s threshold (#96) |

### Coupling to #96 — recorded, not fixed

`backend/services/syncthing_service.py:341` hardcodes `delta > 90`. The heartbeat monitor wakes
every 30s. For a healthy node to never be flipped `OFFLINE`, the agent's rewrite cadence must be
comfortably below 90s. This spec picks **30s** (§6), which leaves room for two missed writes plus
watcher latency. #96 makes that constant configurable; that is a separate change and is **not**
made here.

---

## 5. Implementation Approach

### New file: `agent/hardware.py`

A **pure** function plus its narrow helpers — no I/O beyond the probe itself, so it is testable
with fakes and survives an HTTP transport:

```python
def probe_hardware(*, hostname: str, cpus: int, memory_bytes: int,
                   gpus: list[GPUInfo], os_name: str) -> NodeSpec:
    """Assemble a NodeSpec from already-collected facts. Pure: no syscalls."""

def collect_node_spec(node_id: str, *, now: datetime | None = None) -> NodeSpec:
    """Probe this machine and return a NodeSpec. The only impure entry point."""
```

Field-by-field detection strategy (**this table is the documentation the issue asks for**):

| Field | Source | Notes |
|---|---|---|
| `node_id` | `settings.NODE_ID` | never probed; the operator names the node |
| `hostname` | `platform.node()` | same answer as `socket.gethostname()`; `socket` stays out of the agent's pinned import surface (issue #93 AC-15) |
| `cpus` | `psutil.cpu_count()` | **logical** count, floored at 1 (see below) |
| `memory_gb` | `psutil.virtual_memory().total // 1024**3` | floored at 1 |
| `gpus` | `gputil.getGPUs()`, guarded | `[]` when absent/unavailable |
| `os` | `platform.system().lower()` | see Q2 |
| `status` | literal `"ONLINE"` | see §6 |
| `last_heartbeat` | `datetime.now()` | **naive local** — see the correction below |
| `current_job_id` | `None` | not set here (#97) |

**The floors are load-bearing, not defensive noise.** `memory_gb` truncates: a machine reporting
3.7 GiB yields `3`, and a machine with under 1 GiB yields `0`, which `ge=1` rejects — the node is
then dropped silently by the backend's bare `except`. On this development box the raw figure is
`3.70` and truncates to `3`. Any value below 1 is raised to 1 so a real, small machine registers
instead of vanishing.

### Correction — `last_heartbeat` is naive local, not tz-aware UTC (found in implementation)

The first draft of this spec specified `datetime.now(timezone.utc)` on the reasoning that "the
monitor compares tz-aware". That is wrong about the value the monitor actually receives, and the
error is expensive:

- `last_heartbeat` is a **naive** `DateTime` column (`backend/store/database.py:75`). SQLite has no
  timezone, so the offset is dropped on the way in and the value comes back naive.
- `_heartbeat_monitor` then subtracts it from a naive `datetime.now()` — **local** time.
- Every other writer in this repository already persists naive local `datetime.now()`:
  `backend/services/job_service.py:82`, `backend/store/database_store.py:341,1055,1077`, and the
  demo seeder's `iso()` helper.

So the agent writes naive `datetime.now()`. Measured on the development box (offset `+0300`): an
aware-UTC heartbeat round-trips as naive UTC, the monitor computes a delta of `10800` s instead of
`~30`, and a node that is rewriting its file every 30 s is marked `OFFLINE` on every tick and can
never return `ONLINE`. #121 tracks the backend's inconsistent timezone handling as the proper
repair; matching the store here is what makes the monitor's arithmetic come out right in the
meantime.

### Where the write happens

In `agent/loop.py`, as a new supervised task alongside the existing ones, using the existing
`TaskSupervisor` seam:

- Write once at startup, immediately after `_ensure_folder()` succeeds (the folder is where the
  file goes, so writing before it exists is meaningless).
- Then rewrite on a **30s** interval, matching the backend's 30s monitor tick.
- **Best-effort: a failed write logs a warning and is retried on the next tick.** Registration must
  not be able to take the agent down — the agent's job is running jobs (#97+), and a transient
  `OSError` from a Syncthing-conflicted directory should cost a heartbeat, not the process.

### The re-registration race

The backend's handler is already idempotent from *its* side: it calls `get_node` and then either
`update_node` or `create_node` (`syncthing_service.py:288-302`). So create-then-update needs no
special handling in the agent — the agent simply rewrites the same file, and whichever branch the
watcher takes is correct.

The risk is **write frequency vs. watcher reaction time**: the watcher adds processed files to a
`_processed_files` set and reacts to filesystem events. A 30s rewrite of an unchanged-mtime file
is fine because `write_yaml` does `os.replace`, which always produces a fresh modify event.

What the agent must **not** do is rebuild the path by hand. Use `AgentPaths.node_file`, which
`resolve_paths` already computes (`agent/paths.py:70`) and which mirrors
`get_node_state_file`.

---

## 6. Decisions on Cadence and Status

- **Cadence: 30s.** Below the 90s offline threshold with room for two missed writes plus watcher
  latency (§4).
- **`status` is always written as `"ONLINE"`.** The heartbeat monitor owns the other direction
  (`syncthing_service.py:341-349`) and will overwrite anything else. Writing `OFFLINE` on shutdown
  would be fought by the monitor within 30s of the agent being gone anyway — the monitor already
  does that job, correctly, based on heartbeat staleness.

---

## 7. Acceptance Criteria

### Functional

- **AC-1** — `collect_node_spec()` returns a `NodeSpec` whose every field is populated from a real
  probe; no field is a hardcoded literal except `status` (`"ONLINE"`) and `current_job_id`
  (`None`).
  ```bash
  python -c "
  from agent.hardware import collect_node_spec
  s = collect_node_spec('node-01')
  print(s.model_dump_json(indent=2))
  assert s.cpus >= 1 and s.memory_gb >= 1
  assert s.status == 'ONLINE' and s.current_job_id is None
  assert s.last_heartbeat.tzinfo is None
  "
  ```

- **AC-2** — The **no-GPU** case yields `gpus: []` and the key is **present**, not omitted. This is
  the case that fails silently on a CPU-only machine.
  ```bash
  python -c "
  from agent.hardware import collect_node_spec
  s = collect_node_spec('node-01')
  assert 'gpus' in s.model_dump(), 'gpus key must be present'
  assert s.gpus == [], 'expected no GPUs on this machine'
  "
  ```

- **AC-3** — A unit test drives the probe against **faked** detection functions and asserts the
  resulting `NodeSpec` exactly, covering: a normal multi-GPU node, a CPU-only node, and a
  `gputil` that raises.

- **AC-4** — The written file **round-trips** through `read_yaml(path, NodeSpec)` without raising.
  This single check catches every schema constraint in §1 at once.
  ```bash
  # NOTE: `export` on its own line. `SYNCTHING_ROOT=... python -c "...$SYNCTHING_ROOT..."`
  # does NOT work -- the shell expands $SYNCTHING_ROOT from the PRE-EXISTING value
  # while building the argument, so resolve_paths() gets '' and writes nodes/ into
  # the current directory, i.e. the repo root.
  export SYNCTHING_ROOT="$(mktemp -d)/root"
  python -c "
  import os
  from agent.hardware import collect_node_spec, write_node_spec
  from agent.paths import resolve_paths
  from shared.file_ops.yaml_utils import read_yaml
  from shared.schemas.node_spec import NodeSpec
  p = resolve_paths(os.environ['SYNCTHING_ROOT'], 'node-01')
  write_node_spec(p, collect_node_spec('node-01'))
  print(read_yaml(str(p.node_file), NodeSpec).model_dump_json())
  "
  ```

- **AC-5** — The write is **atomic**: a reader polling during a write always sees either the
  previous file or the complete new one, never a truncated file. Asserted by polling from a second
  thread during repeated writes.

- **AC-6** — The path comes from `AgentPaths.node_file` (i.e. `resolve_paths`), never from a
  hand-built string. A test asserts the written path equals
  `get_node_state_file(root, node_id)` so the two conventions cannot drift.

- **AC-7** — The agent creates `nodes/` when it does not exist: start the agent against an
  **empty** root, before the backend has ever run, and assert the file appears.

- **AC-8** — **Re-registration**: writing twice in a row yields exactly one node, no `ValueError`,
  no duplicate. Driven end-to-end through the real `_process_node_file` handler.

- **AC-9** — `status` is `"ONLINE"` in the file, **and** a test drives the real
  `_heartbeat_monitor` against a node whose `last_heartbeat` is 10s old and asserts it is **not**
  flipped to `OFFLINE` — proving the cadence satisfies the threshold of §4. The heartbeat under
  test must be the value `collect_node_spec` actually produces, seeded **through the store** (so
  the `DateTime` column's loss of the offset is reproduced). A test that hand-writes
  `datetime.now() - 10s` assumes the convention under test and passes whether or not the agent
  honours it; the control case (a 120s heartbeat **is** flipped) is what stops the assertion from
  passing for the wrong reason.

- **AC-10** — **Failure isolation**: when the write raises `OSError`, the agent logs a warning,
  stays alive, and retries on the next tick.

### Quality

- **AC-11** — `pytest -q` → all green; the full-suite count does not drop below the current
  baseline and `.shc/secret_key` is unchanged by the run (the `guard_signing_key` fixture from
  #65 must not fire).
- **AC-12** — `ruff check . && ruff format --check . && mypy .` all exit 0. No new
  `type: ignore`, and no widening of the existing narrow overrides in `pyproject.toml`.
- **AC-13** — The detection-strategy table in §5 is reflected in the `agent/hardware.py` module
  docstring, including the no-GPU and missing-`gputil` cases.

---

## 8. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The backend's bare `except` drops a malformed file, so registration "silently fails" | Medium | High | AC-4 round-trip test; agent logs the resolved path at INFO on every successful write so an operator can compare against the watcher |
| `gputil` raises on a CPU-only machine | **Certain today** | High | Guarded import and guarded call; `[]` on any failure (Q1) |
| `memory_gb` truncates to `0` on a small machine and the node is dropped | Low | High | Floor at 1 (§5); AC-1 asserts the floor |
| Write cadence drifts above 90s and the monitor flips a healthy node offline | Medium | Medium | 30s cadence (AC-9); #96 will make the threshold configurable |
| A later HTTP transport decision invalidates the write | Medium | Medium | Probe is pure (§5, Q4); only `write_node_spec` is transport-specific |
| `gputil` is declared but not installed — CI installs it, a dev box may not | **High** | Medium | Guarded import makes absence a supported state, not a crash |
| A tz-aware `last_heartbeat` is silently reinterpreted as local by the store and the monitor, so a healthy node is flipped `OFFLINE` forever | **Was certain** (the first draft of this spec said tz-aware) | **High** | The agent writes naive local `datetime.now()` to match every other writer (§5 correction); a direct convention test and an AC-9 that seeds the agent's own value make a regression visible. **#121** owns the backend repair |

---

## 9. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Implementation complete (SWE)
- [ ] AC-1 … AC-13 verified with evidence (QA)
- [ ] `pytest -q` green; `ruff`/`mypy` clean
- [ ] PM acceptance

---

## 10. Related

- #93 — agent skeleton (`AgentPaths.node_file`, the `_write_final_state` seam)
- #96 — 90s offline threshold hardcoded (coupling recorded in §4, **not** fixed here)
- #97 — agent claims a job (owns `current_job_id`)
- #100 — terminal job status on shutdown
- #102 — node metrics writer
- #65 — `get_node_state_file`/`ensure_directory` now use `os.path.expanduser` (#108)
- #121 — inconsistent timezone handling in the store/monitor; the backend repair this issue
  works around by writing naive local (§5 correction)