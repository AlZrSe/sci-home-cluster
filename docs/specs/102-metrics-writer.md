# Spec: Metrics Writer — Agent Sampling, Ingest, and Retention

**Issue**: #102 — "metrics tables have readers and no writer; node metrics keyed by undocumented sentinel"
**Labels**: `agent`, `backend`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-11-03

---

## 1. Verified Problem Statement

The metrics tables have readers and no production writer. Every row in `gpu_metrics` and
`cpu_metrics` is either fabricated by the backend's demo seeder (when `SEED_DEMO_DATA=True`)
or absent. The agent — the only component that can observe real hardware — samples nothing:

```text
agent/README.md:123  - **No metrics:** metrics sampling is issue #102.
agent/hardware.py:263  # schema change, which belongs to the metrics issue (#102).
```

Grepping `agent/` for `GPUMetric`/`CPUMetric` finds only `GPUInfo` (the registration-time
inventory). There is no sampling loop, no file, no call to `psutil`/`gputil` for live readings.

### The sentinel key

Node metrics are stored under a job id that is not a job:

```python
# backend/store/database_store.py:765-777
select(GPUMetricModel).where(GPUMetricModel.job_id == f"node:{node_id}")
...
# :827, :839
job_id=f"node:{node_id}",
```

`f"node:{node_id}"` is load-bearing, violates the tables' own FK to `jobs.job_id`
(`backend/store/database.py:99`, `:122` — unenforced only because SQLite FKs are off per
`:450-452`), and appears in no spec, no schema and no OpenAPI document. Two keys are
committed to this public repository (issue #56) and the FK is off, so the violation is
silent. Under file-only the agent must reproduce the exact string, which makes the
sentinel the concrete blocker: it must be an importable constant with a docstring, not a
formatted string in agent code.

### The out-of-range 500

`shared/schemas/gpu_metric.py:24-28` and `shared/schemas/cpu_metric.py:15-18` constrain
`utilization_percent`/`cpu_percent`/`memory_percent` to 0-100 and `temperature_c` to
-50..150. A reading of 101 or 200 is rejected by Pydantic on *read*, so
`GET /nodes/{id}/metrics` returns 500 rather than a clamped value. The tables hold
integers with no CHECK, so nothing stops a bad value being written; the failure surfaces
only when someone reads it.

### Memory incoherence

`GPUInfo.memory_gb` (GiB, registration, `shared/schemas/gpu_metric.py:15`) and
`GPUMetric.memory_total_mb` (MB, samples, `:26`) are the same quantity in different units,
and nothing asserts the conversion. A node registering 24 GiB can report 24 MB or 24000 MB
and both validate.

### GPU enumeration

`GPUMetric.gpu_index` is `ge=0` with no upper bound and no link to the registered
inventory (`NodeSpec.gpus`, `shared/schemas/node_spec.py:20`). A sample can describe a GPU
the node does not have, or — once #98's resource allocation sets `CUDA_VISIBLE_DEVICES` —
a GPU the job is not allowed to touch.

### Distinguishability from synthetic rows

Issue #36 added `origin` (`"synthetic"`/`"real"`) to both tables and both schemas, and the
demo seeder writes ~306 rows under `job_id = "node:<id>"` / `"job-1"`. Until #36 landed,
real samples could not be told from fabricated ones. #36 is a prerequisite and is assumed
present (see §4).

### Reproduction (current, SEED_DEMO_DATA=True)

```text
GET /api/v1/nodes/node-alpha/metrics  -> 200, job_id "node:node-alpha", 121 GPU + 121 CPU samples
                                          all origin="synthetic"
agent: no sampling code exists; nothing writes a file; nothing writes origin="real"
```

---

## 2. Root Cause

The file transport decision (#92) made the Syncthing folder the only channel in both
directions, and metrics were never given a file shape. The backend kept the only existing
write path — the demo seeder writing straight into SQLite — and called it a reader
contract. There is therefore no writer an agent can use, no schema for a metric sample on
disk, no ingest handler in `SyncthingService`, and no agreement on the key under which a
node's samples live. The sentinel, the missing clamping, the missing coherence check and
the missing retention all follow from the same gap: metrics were specified as a database
table and never as a file.

---

## 3. Open Questions — Resolved

The issue left six open questions plus six additional ones. Each is decided here.

### Q1 — What sampling rate does file-only support? **10 s sampling, 5-minute create-only segments, measured in-process.**

The issue's revised body is right that a 10 s *file rewrite* is not viable, but the
sampling rate and the file write rate are separable. The decision is to **keep 10 s
sampling and change the write shape to rotation**, which is the mechanism change the issue
asks for. Numbers, measured on one machine, one filesystem, real watchers, `write_yaml`
publication (replication hop excluded, as in #106 Q10):

| Quantity | Value |
|---|---|
| Sampling interval (busy node) | 10 s |
| Sampling interval (idle node) | 60 s |
| Segment window (flush cadence) | 5 min (30 busy samples / 5 idle samples) |
| Segments per node per day | 288 |
| Samples per node per day (busy) | 8 640 |
| Segment size, 2 GPUs + CPU (busy) | ~9.6 KiB |
| Bytes per node per day (busy) | ~2.8 MiB |
| Fan-out to N nodes | (N-1) × 2.8 MiB replicated reads/day |

For comparison, the rejected alternative — one file per 10 s sample with no batching — is
8 640 writes/node/day at ~350 B each: 3.0 MiB of *payload* but 30× the write count, 30×
the watcher re-parses, and 30× the Syncthing version churn. Batching is what makes 10 s
sampling affordable; the sampling rate itself is unchanged from the original proposal.
The replication hop is not asserted here and cannot be measured from one machine (D15).

### Q2 — Do all nodes need the metrics history, or only the backend? **Same replicated folder, retained 7 days.**

Metrics use the one Syncthing folder #92 chose, so they replicate to every node. A
send-only/receive-only folder type would collapse the cost and is recorded as an
operational option, but the agent cannot configure Syncthing folder types (that is an
operator action, and #160 owns auto-accept), so the spec does not depend on it. D4.

### Q3 — Should idle nodes report at all? **Yes, at 60 s.**

Idle reporting is what makes the node dashboard distinguish "idle" from "broken" (the
confusion behind #32) and what keeps `current_job_id = None` from looking like a dead
node. The cost is one sixth of the busy rate and is included in Q1's numbers. Idle
nodes report CPU and GPU samples at 60 s; they never report per-job metrics. D6.

### Q4 — Where do segments live? **`nodes/<id>/metrics/` and `jobs/<job_id>/metrics/`.**

Both are already under the two scheduled roots (`jobs/`, `nodes/`), so `observer.schedule`
is **not** extended and no new top-level directory is added. `_initial_scan` and
`_scan_folder_structure` gain a per-directory sweep of `metrics/`, mirroring the
`commands/`/`acks/` sweep #106 D1 already added. D1.

### Q5 — Does this issue need to be split? **No.**

The constant, the ingest handler, the rate decision and the retention policy are four
pieces, but they share one schema and one test surface, and splitting them would leave the
agent writing a format nothing ingests. The split the issue hints at is rejected; the
pieces are separate *sections* (§5.1–§5.10) and separate ACs, not separate issues. D14.

### Q6 — Is there a segment schema, or are `GPUMetric`/`CPUMetric` reused? **A batch wrapper, per-sample models reused.**

`GPUMetric`/`CPUMetric` stay per-sample. A new `MetricSegment` wraps a list of each,
plus identity and time range. The same wrapper serves node-level and job-level samples
(`job_id` is `None` for node-level). D3.

### Q7 — Sentinel: constant now, migration later. **`node_metrics_job_id(node_id)` is the only spelling.**

The FK stays unenforced and the column stays `job_id`. The migration that would replace
the sentinel with a nullable `node_id` column (or a `metrics` table keyed by node) is a
separate, later issue. What this issue mandates is that **no code formats `"node:{...}"`**:
the agent imports `node_metrics_job_id` from `shared.metrics.sentinel`, and the backend's
own readers (`database_store.py:765-840`) are refactored to call it. A test fails if the
literal is formatted in agent code (AC-1). D2.

### Q8 — Clamping: where? **At collection (agent) and at ingest (backend), both.**

The agent clamps sensor readings to the schema bounds before writing. The backend ingest
handler clamps again, defensively, so a hand-written or older segment cannot turn
`GET /nodes/{id}/metrics` into a 500. Clamped values are stored and returned; the
original is logged at DEBUG. D7.

### Q9 — Samples for an unknown job or node. **Rejected with a clear error, not silently accepted.**

An ingest handler that receives a segment for a node that is not registered (or a job
that does not exist, for job-level metrics) logs at ERROR, writes nothing, and does not
add the file to `_processed_files` (so a later re-scan retries it). D8.

### Q10 — `CUDA_VISIBLE_DEVICES` agreement. **A sample describes only GPUs the job can use.**

Node-level segments describe every registered GPU (indices 0..n-1). Job-level segments
describe only the GPUs in the job's allocated set; when the agent has
`CUDA_VISIBLE_DEVICES` set for the job, `gpu_index` must be in that set. A sample for a
GPU outside the set is dropped and logged. D9.

### Q11 — Memory coherence. **`memory_total_mb == memory_gb * 1024`.**

The agent sets `memory_total_mb` from the registered `GPUInfo.memory_gb`. The backend
ingest handler compares and logs a WARNING on mismatch but still stores (the sample is
not discarded — a wrong total is better than no data, and the warning is the fix path).
AC-9 asserts the agent side. D10.

### Q12 — Retention. **7 days, both files and rows.**

Replicated metric history with no bound is a disk-space defect on every node. Segments
older than 7 days are deleted by the agent (on startup and once per day) and the
corresponding DB rows are deleted by a backend periodic task (on startup and daily).
Deletion replicates, which is the point. D11.

---

## 4. Scope Boundaries

### In scope

- `shared/metrics/sentinel.py` — `NODE_METRICS_JOB_ID_PREFIX`, `node_metrics_job_id()`.
- `shared/schemas/metric_segment.py` — `MetricSegment`.
- Agent sampling module `agent/metrics.py` — collection, clamping, segmentation, writing.
- Agent wiring into `agent/loop.py` (periodic sampling task, flush on shutdown).
- Backend ingest handler `_process_metrics_file` and `_process_metrics_deleted`.
- Backend watcher routing for `metrics/` under `jobs/` and `nodes/`.
- `_initial_scan` / `_scan_folder_structure` sweep of `metrics/`.
- Refactor of `database_store.py` readers to use `node_metrics_job_id`.
- Backend retention task for DB rows.
- Clamping at ingest.
- `CUDA_VISIBLE_DEVICES` filtering.
- Tests listed in §7.

### Out of scope

| Issue | Why it is not this one |
|---|---|
| **#36** (synthetic marker) | Prerequisite. This spec assumes `origin` exists on both tables and both schemas. If #36 has not landed, #102 cannot land — real samples would be indistinguishable from the ~306 fabricated rows. |
| **#95** (node registration) | Provides the `GPUInfo` inventory the agent samples against. #102 reads it; it does not change registration. |
| **#98** (resource allocation) | Sets `CUDA_VISIBLE_DEVICES`. #102 honours it; it does not implement allocation. |
| **#101** (log capture) | Separate directory (`logs/`), separate schema, separate ingest handler. #102 adds `metrics/` and its own routing; the "log unrecognised paths" rule (D13) is shared discipline, not shared code. |
| **#103** (crash recovery) | A crash may lose the in-flight (unflushed) segment. That is acceptable for metrics and is not repaired — #103 does not need to know about metric files. |
| **#106** (command files) | Different directory, different schema, different ownership. #102 neither writes nor reads command files. |
| **#104** (docs) | #102 contributes one troubleshooting line: "a node with no recent metric segment is either idle (60 s cadence) or not sampling — check `agent/metrics.py` logs". |
| **#92** (transport) | #102 consumes the file-only decision; it adds no HTTP path. |
| Database migration replacing the sentinel | Deferred (Q7). The column stays `job_id`; only the spelling is centralised. |
| Dashboard filtering by `origin` | Frontend concern; #36 already exposes `origin` in the schemas. |

---

## 5. Implementation Approach

### 5.1 Sentinel constant — `shared/metrics/sentinel.py` (new)

```python
"""Central spelling of the node-metrics key.

The ``gpu_metrics``/``cpu_metrics`` tables are keyed by ``job_id``. A node is not a
job, so node samples live under the sentinel ``"node:{node_id}"``. That string is
load-bearing (``backend/store/database.py``) and must have exactly one spelling, or
the agent and the backend disagree silently. This module is that spelling.
"""
from __future__ import annotations

from typing import Final

NODE_METRICS_JOB_ID_PREFIX: Final[str] = "node:"


def node_metrics_job_id(node_id: str) -> str:
    """Return the ``job_id`` under which a node's samples are stored.

    The only permitted spelling. ``node_id`` is interpolated by this function so
    that no caller (agent or backend) formats the literal ``"node:{...}"``.
    """
    if not node_id:
        raise ValueError("node_id must be non-empty")
    return f"{NODE_METRICS_JOB_ID_PREFIX}{node_id}"
```

`shared/metrics/__init__.py` exports both. `backend/store/database_store.py` replaces
every `f"node:{node_id}"` with `node_metrics_job_id(node_id)` (reads at `:765-777`,
writes at `:827`, `:839`, `:975`, empty metrics at `:820`). The agent imports the
function and never formats the literal (AC-1).

### 5.2 File layout

```
<root>/nodes/<node_id>/
├── <node_id>.yaml                 # registration (#95)
└── metrics/                       # NEW — agent writes, backend ingests
    └── metrics-<start_ms>-<hex6>.yaml
<root>/jobs/<job_id>/
├── state.yaml
├── commands/ ...
├── acks/ ...
└── metrics/                       # NEW — per-job samples
    └── metrics-<start_ms>-<hex6>.yaml
```

No new top-level directory; both are under the already-scheduled `jobs/` and `nodes/`
roots.

### 5.3 Segment schema — `shared/schemas/metric_segment.py` (new)

```python
class MetricSegment(BaseModel):
    """One create-only batch of metric samples, written by the agent."""

    segment_id: str = Field(..., pattern=SEGMENT_ID_PATTERN)
    node_id: str = Field(..., min_length=1)
    job_id: Optional[str] = None   # None => node-level
    started_at: datetime
    ended_at: datetime
    gpu_metrics: List[GPUMetric] = Field(default_factory=list)
    cpu_metrics: List[CPUMetric] = Field(default_factory=list)
    origin: Literal["real"] = Field(default="real")

    model_config = {"from_attributes": True}
```

`SEGMENT_ID_PATTERN = r"^metrics-\d{10,}-[0-9a-f]{6}$"`, mirroring #106's command id
pattern. `origin` is fixed to `"real"` on this schema: a segment is, by construction,
agent-produced. The demo seeder never uses this schema.

### 5.4 Agent sampling — `agent/metrics.py` (new)

Pure collection helpers plus narrow I/O, so the logic is testable with fakes (the same
shape as `agent/hardware.py` and `agent/commands.py`):

```python
def sample_gpu(gpu_index: int, gpu: GPUInfo, *, now: datetime) -> GPUMetric: ...
def sample_cpu(*, now: datetime) -> CPUMetric: ...
def clamp_gpu(metric: GPUMetric) -> GPUMetric: ...
def clamp_cpu(metric: CPUMetric) -> CPUMetric: ...
def filter_gpu_indices(indices: list[int], allowed: list[int] | None) -> list[int]: ...
def build_segment(...) -> MetricSegment: ...
def write_segment(paths, segment) -> Path: ...
def prune_old_segments(paths, *, retention_days: int, now: datetime) -> int: ...
```

Collection rules:

- **Busy (a job is running on this node):** sample every 10 s. The agent's loop calls
  `sample_*` on the 10 s cadence and appends to the in-memory accumulator.
- **Idle (no job):** sample every 60 s.
- **Flush:** when the accumulator's span reaches 5 min, or on shutdown, or when the
  sample count reaches 30, write one segment file and clear the accumulator. A crash
  loses at most one unflushed segment; that is acceptable and is not repaired (§4).
- **GPU index:** `enumerate(node_spec.gpus)`; `gpu_index` is the index. `memory_total_mb`
  is `gpu.memory_gb * 1024`.
- **Clamping:** `utilization_percent`/`cpu_percent`/`memory_percent` clamped to 0-100;
  `temperature_c` to -50..150; `memory_used_mb` to 0..`memory_total_mb`; `memory_used_gb`
  to 0..`memory_gb`. Out-of-range inputs are logged at DEBUG.
- **CUDA_VISIBLE_DEVICES:** if set, parse the comma-separated indices; sample only those
  for job-level segments. An unparseable value is logged at WARNING and treated as "all".
  A sample whose `gpu_index` is not in the allowed set is dropped and logged.
- **Idle CPU-only nodes:** sample CPU with an empty `gpu_metrics` list; do not fabricate
  a GPU.

### 5.5 Agent wiring — `agent/loop.py`

The sampling task is a fourth periodic task alongside heartbeat, registration and the
command drain. It reuses the existing `AgentPaths` (no new field) and `node_spec` already
held by the loop. On shutdown the task flushes the accumulator before returning, so the
last partial segment is not lost on a clean stop (a hard crash still loses it, per §5.4).

### 5.6 Backend ingest — `SyncthingService`

Routing (`_process_file`, mirroring #106's commands/acks branch):

```python
elif relative_path.parts[0] == "nodes" and len(relative_path.parts) >= 4 \
        and relative_path.parts[2] == "metrics" \
        and relative_path.parts[3].endswith(".yaml"):
    node_id = relative_path.parts[1]
    await self.service._process_metrics_file(node_id, None, file_path)
elif relative_path.parts[0] == "jobs" and len(relative_path.parts) >= 4 \
        and relative_path.parts[2] == "metrics" \
        and relative_path.parts[3].endswith(".yaml"):
    job_id = relative_path.parts[1]
    await self.service._process_metrics_file(None, job_id, file_path)
```

The `len(...) == 4` is exact: `nodes/<id>/metrics/nested/x.yaml` is 5 parts and is
logged, not routed.

New handler `_process_metrics_file(node_id, job_id, file_path)`:

1. Read `read_yaml(path, MetricSegment)`; on failure log at ERROR (malformed segment is
   visible, not silently dropped).
2. `segment_id` must equal the filename stem; mismatch → WARNING, return.
3. `segment.node_id` must equal `node_id` (when given); mismatch → WARNING, return.
4. `segment.job_id` must equal `job_id` (when given); mismatch → WARNING, return.
5. Node must exist in the store; if not → ERROR "metrics for unknown node", return without
   writing and without adding to `_processed_files` (retry on next scan).
6. If `job_id` given, job must exist; otherwise ERROR and return.
7. Clamp every sample (defensive).
8. Memory coherence: for each GPU sample, compare `memory_total_mb` against the
   registered node's `GPUInfo.memory_gb * 1024`; mismatch → WARNING with both values.
9. Determine the DB key: `job_id` if given, else `node_metrics_job_id(node_id)`.
10. Insert rows with `origin="real"`.
11. Add `file_path` to `_processed_files`.

`_process_delete` for a `metrics/` path logs at DEBUG and does nothing else — a deleted
segment never reaches `_handle_job_deleted` (AC-4).

`_initial_scan` and `_scan_folder_structure` sweep `metrics/*.yaml` per job/node
directory, mirroring the `commands/`/`acks/` sweep (D1).

### 5.7 Clamping at ingest

The ingest handler applies the same bounds as §5.4 before insert. The original value is
logged at DEBUG when it differs from the clamped value. Out-of-range values are stored
clamped, so `GET /nodes/{id}/metrics` returns 200 (AC-6).

### 5.8 Retention

- **Agent:** `prune_old_segments` deletes segment files whose `ended_at` (parsed from
  the file, falling back to mtime) is older than 7 days. Runs on startup and once per
  day. Deletion replicates.
- **Backend:** a periodic task (startup + daily) deletes `gpu_metrics`/`cpu_metrics`
  rows whose `timestamp` is older than 7 days. Runs inside the existing service loop.
- Retention is 7 days for both; the two are kept in sync so a row is never deleted
  before its segment (or vice versa) by more than one cycle.

### 5.9 Unrecognised paths

A `.yaml` under `jobs/<id>/` or `nodes/<id>/` that is not `state.yaml`, `metrics/*`,
`commands/*` or `acks/*` is logged at DEBUG with the path (D13). It is not routed and
not dropped silently — the same discipline #106 D10 established for command files.

### 5.10 CUDA_VISIBLE_DEVICES agreement

Node-level segments always describe every registered GPU. Job-level segments describe
only GPUs in the job's allocated set. When `CUDA_VISIBLE_DEVICES` is present in the
agent environment for a job, `gpu_index` must be in that set; a sample outside it is
dropped and logged at WARNING. This keeps a job's metric series from claiming a GPU it
cannot use (AC-10).

---

## 6. Decisions

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Segments live at `nodes/<id>/metrics/` and `jobs/<job_id>/metrics/`. `observer.schedule` is not extended; `_initial_scan` and `_scan_folder_structure` sweep `metrics/` per directory.** | Under the already-scheduled roots, so no new silent scan root. Same shape as #106 D1. |
| **D2** | **The sentinel is centralised as `node_metrics_job_id()` now; the migration that removes it is a later issue.** | The column stays `job_id`; only the spelling is made single. Migration is larger and riskier and is not blocked on it. |
| **D3** | **`GPUMetric`/`CPUMetric` are reused per-sample; a new `MetricSegment` is the batch wrapper.** | Per-sample models are already validated and serialised; a wrapper avoids duplicating their constraints. |
| **D4** | **Metrics use the one replicated Syncthing folder. Send-only folder types are an operational option, not a spec requirement.** | The agent cannot configure Syncthing folder types; depending on it would make sampling untestable. |
| **D5** | **Sampling keeps 10 s; the write shape changes to 5-minute create-only segments.** | Separates sampling rate from write rate. 10 s sampling is affordable at 288 writes/node/day; 8 640 writes/node/day is not. |
| **D6** | **Idle nodes report at 60 s.** | Resolves idle-vs-broken confusion (#32) at one sixth of the busy cost. |
| **D7** | **Clamping happens at collection and at ingest.** | Collection keeps the agent honest; ingest makes the read path immune to hand-written or legacy segments. |
| **D8** | **Segments for an unknown node or job are rejected with a clear error and not added to `_processed_files`.** | Silent acceptance would hide registration failures; retry-on-next-scan is the safe default. |
| **D9** | **Job-level samples describe only GPUs the job can use (`CUDA_VISIBLE_DEVICES`).** | A metric series must not claim a GPU the job cannot touch. |
| **D10** | **Memory totals are asserted as `memory_total_mb == memory_gb * 1024`; ingest logs on mismatch but stores.** | Wrong total is better than no data; the warning is the fix path. |
| **D11** | **Retention is 7 days, both files and rows, implemented now.** | Replicated history with no bound is a disk-space defect on every node. |
| **D12** | **Segments are create-only: never modified after creation.** | Makes parse cost linear and bounds replication per event; matches #101/#106's rotation rule. |
| **D13** | **An unrecognised `.yaml` under a job or node directory is logged at DEBUG.** | Shared discipline with #106 D10; prevents `metrics/` from becoming a blind spot. |
| **D14** | **This issue is not split.** | One schema, one test surface; splitting leaves the agent writing a format nothing ingests. |

---

## 7. Acceptance Criteria

Each AC names the test that proves it.

### Sentinel

- **AC-1 — No agent code formats the sentinel literal.** A test greps `agent/` for
  `f"node:` and `f'node:` and `"node:{` and fails if any match; the agent imports
  `node_metrics_job_id` from `shared.metrics.sentinel`. *Proved by*
  `agent/tests/test_metrics.py::test_no_formatted_sentinel_literal_in_agent`.
- **AC-2 — Backend readers use the constant.** A test asserts `database_store.py` contains
  no `f"node:` literal and that `get_node_metrics`/`_store_node_metrics` round-trip through
  `node_metrics_job_id`. *Proved by*
  `backend/tests/unit/test_node_metrics_sentinel.py::test_readers_use_node_metrics_job_id`.

### Sampling and files

- **AC-3 — A busy node produces a segment every 5 minutes with 30 samples.** *Proved by*
  `agent/tests/test_metrics.py::test_busy_node_flushes_a_five_minute_segment`.
- **AC-4 — Segments are create-only.** A test writes a segment, records its bytes and
  mtime, re-runs the sampler, and asserts the file is byte-identical and unmodified.
  *Proved by* `agent/tests/test_metrics.py::test_segments_are_never_modified_after_creation`.
- **AC-5 — Idle nodes report at 60 s.** *Proved by*
  `agent/tests/test_metrics.py::test_idle_node_samples_at_sixty_seconds`.

### Traffic

- **AC-6 — The traffic numbers are recorded.** A test asserts the four numbers from Q1
  (8 640 samples, 288 segments, ~2.8 MiB, fan-out formula) appear in this document.
  *Proved by* `backend/tests/unit/test_metrics_spec.py::test_traffic_numbers_are_documented`.

### Ingest

- **AC-7 — The watcher routes `metrics/` files and logs unrecognised paths.** A table-driven
  test over `nodes/<id>/metrics/x.yaml`, `jobs/<id>/metrics/x.yaml`,
  `nodes/<id>/metrics/nested/x.yaml`, `nodes/<id>/stray.yaml` asserts routing and a log
  record. *Proved by*
  `backend/tests/unit/test_metrics_routing.py::TestMetricsPathsAreRoutedAndLogged`.
- **AC-8 — `_initial_scan` surfaces a pre-existing segment.** *Proved by*
  `backend/tests/integration/test_metrics_ingest.py::test_preexisting_segment_is_ingested_on_startup`.
- **AC-9 — Memory totals are coherent.** An agent test asserts
  `gpu_metric.memory_total_mb == gpu.memory_gb * 1024` for every sampled GPU. *Proved by*
  `agent/tests/test_metrics.py::test_memory_totals_are_coherent`.
- **AC-10 — `CUDA_VISIBLE_DEVICES` is honoured.** A test sets the env var to `1` and asserts
  no segment contains `gpu_index == 0`. *Proved by*
  `agent/tests/test_metrics.py::test_cuda_visible_devices_filters_sampled_gpus`.

### Clamping and validation

- **AC-11 — Out-of-range samples are clamped at ingest.** A test writes a segment with
  `utilization_percent=101` and `temperature_c=200`, drives ingest, and asserts
  `GET /nodes/{id}/metrics` returns 200 with 100 and 150. *Proved by*
  `backend/tests/integration/test_metrics_ingest.py::test_out_of_range_samples_are_clamped`.
- **AC-12 — Unknown node or job is rejected.** A test writes a segment for an unregistered
  node and asserts no rows are inserted and an ERROR is logged. *Proved by*
  `backend/tests/integration/test_metrics_ingest.py::test_unknown_node_segment_is_rejected`.

### Real vs synthetic

- **AC-13 — Agent-written rows have `origin="real"`.** *Proved by*
  `backend/tests/integration/test_metrics_ingest.py::test_ingested_rows_are_origin_real`.
- **AC-14 — Synthetic rows remain `origin="synthetic"`.** *Proved by*
  `backend/tests/integration/test_no_fabrication.py::test_seeded_rows_are_origin_synthetic`
  (already present; must still pass).

### Retention

- **AC-15 — Segments older than 7 days are pruned.** *Proved by*
  `agent/tests/test_metrics.py::test_old_segments_are_pruned`.
- **AC-16 — DB rows older than 7 days are pruned.** *Proved by*
  `backend/tests/integration/test_metrics_retention.py::test_old_metric_rows_are_pruned`.

### Delete safety

- **AC-17 — A deleted segment never deletes a job or node.** *Proved by*
  `backend/tests/unit/test_metrics_routing.py::test_deleted_segment_does_not_delete_job`.

### Concurrency and boundaries

- **AC-18 — No new top-level directory and no new `observer.schedule` call.** *Proved by*
  `backend/tests/unit/test_metrics_layout.py::test_no_new_scan_root`.
- **AC-19 — No HTTP in the agent metrics path.** `agent/tests/test_no_cluster_required.py`
  extended to include `agent/metrics.py` in `SHIPPED_MODULES`. *Proved by*
  `agent/tests/test_no_cluster_required.py` (existing suite, extended).

---

## 8. Failure Matrix

| Failure | Behaviour |
|---|---|
| Sensor read raises | Logged at WARNING; that sample is skipped; the segment still flushes with the rest |
| `write_yaml` fails (OSError) | Logged at ERROR; accumulator is retained and retried on next flush; no partial file |
| Segment malformed on ingest | `read_yaml` raises; logged at ERROR with path; no rows; file stays in `_processed_files`? **No** — left unprocessed so a corrected rewrite is retried |
| Unknown node/job | ERROR, no rows, not added to `_processed_files` (retry on next scan) |
| Out-of-range value | Clamped, original logged at DEBUG |
| Memory mismatch | WARNING with both values; row stored |
| Crash mid-segment | At most one unflushed segment (≤5 min) is lost; no corruption |
| Retention delete fails | Logged at ERROR; retried next cycle |

---

## 9. Boundaries (restated)

- **#36** must land first or with this issue, or real samples are indistinguishable from
  the ~306 fabricated rows. This spec assumes `origin` exists.
- **#95** provides the GPU inventory; #102 reads it and does not change it.
- **#98** sets `CUDA_VISIBLE_DEVICES`; #102 honours it.
- **#101** owns `logs/`; #102 owns `metrics/`. The unrecognised-path rule (D13) is shared
  discipline.
- **#103** does not need to know about metric files; a lost in-flight segment is acceptable.
- **#106** owns `commands/` and `acks/`; #102 does not touch them.
- **#92** chose the file-only channel; #102 adds no HTTP path.

No production code changes are made by this grooming. Implementation is the SWE's.