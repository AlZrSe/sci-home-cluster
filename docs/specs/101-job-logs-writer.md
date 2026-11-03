# Spec: Job Logs Writer (Segment-Based Ingest)

**Issue**: #101 — "Job logs have no writer: the WebSocket closes 1008 with 'No agent writes job logs yet'"
**Labels**: `agent`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-11-03

**Sequencing**: #36 (synthetic marker) MUST land first or together. Real rows written here
carry `origin="real"`; without #36's discriminator they are indistinguishable from the ~64
fabricated rows per demo job. #92 sequences #36 before #101/#102.

---

## 1. Verified Problem Statement

`log_entries` is written only by demo fabricators (`_store_job_logs`, `_stream_worker` in
`backend/store/database_store.py`). In production (`SEED_DEMO_DATA=False`) `get_job_logs`
returns `[]` and the WebSocket closes `1008` with `LOG_STREAM_UNAVAILABLE_REASON`
(`backend/api/v1/jobs.py:39-45`), which literally says "No agent writes job logs yet."
The agent captures stdout/stderr to text files (`agent/executor.py` → `jobs/<id>/logs/stdout-*.log`)
but nothing ingests them into the DB or the WebSocket. There is no ingest route and no file
shape the backend watcher recognises beyond `state.yaml`/`commands/`/`acks/`.

## 2. Decisions (Open Questions Resolved)

| # | Question | Decision |
|---|----------|----------|
| D1 | Ingest channel | **Segment files under `jobs/<id>/logs/`**, create-only, atomic. HTTP batch endpoint rejected (file-only transport, #92). Append-to-one-file rejected: whole-file re-replication + O(n²) parse + partial-YAML failure. |
| D2 | Segment location | `jobs/<job_id>/logs/<seq:04d>.yaml` (inside the already-watched `jobs/` tree; no new scan root). Mixed with the agent's `.log` text files, which the watcher ignores (suffix filter). |
| D3 | Segment schema | **New `shared/schemas/log_segment.py`** exporting `LogSegment` (`job_id`, `stream: Literal["stdout","stderr"]`, `sequence: int`, `lines: List[LogEntry]`, `dropped: int = 0`, `created_at: datetime`). `LogEntry` (from #36) carries `origin`; real segments set `origin="real"`. |
| D4 | Segment rotation | Rotate when current segment reaches **100 lines OR 64 KiB**, whichever first. Segment count bounded: hard cap **1000 segments per job** (≈100k lines); beyond that the agent stops writing and sets `dropped` on the last segment with a warning. |
| D5 | Create-only | Each segment written once via `write_yaml` (temp + `os.replace`). Never modified after creation. A test asserts the file mtime/hash is stable. |
| D6 | Line length | **10 KiB max per line** (reuse `BoundedLineWriter.MAX_LINE_LENGTH = 10240`), truncate with `...[truncated N bytes]`. One pathological line cannot make a segment unparseable: it is stored as a single YAML scalar. |
| D7 | Timestamps | Agent emits **timezone-aware UTC** `datetime` in each `LogEntry.timestamp`. Backend stores as naive UTC (strip tzinfo on persist, matching the existing `timestamp` column); reads back as naive (interpreted UTC). Frontend `LogViewer` formats unchanged. |
| D8 | Ordering | **No cross-stream ordering guarantee.** stdout and stderr are separate segment sequences; within one stream, segments are ordered by `sequence`. Merged view orders by `(timestamp, segment sequence)` best-effort and the API documents "no defined order across streams." |
| D9 | Backpressure | Agent keeps an in-memory bounded buffer of **1000 lines** per stream. On overflow it drops the **oldest** lines, increments `dropped`, and logs a warning. Memory is bounded by `1000 × 10 KiB ≈ 10 MiB` per stream. |
| D10 | Batch/flush | Agent flushes a segment when buffer reaches 50 lines **or** 5 s elapses, whichever first. Replication rate: ≤ 1 segment / 5 s / stream / job in steady state; burst up to 1 segment / line-batch. |
| D11 | WebSocket gate | `log_stream_available(job_id=None)` no longer returns `settings.SEED_DEMO_DATA`. Returns `True` iff `SEED_DEMO_DATA` is on **or** (job_id given and at least one real `LogEntry` row with `origin="real"` exists for that job). Unavailable case remains testable. Close code stays `1008`. |
| D12 | `LOG_STREAM_UNAVAILABLE_REASON` | Updated to: `"Log streaming is unavailable: no log segments have been ingested for this job yet."` (no longer claims no agent writes logs). |
| D13 | `logs/history` | Remains an alias of `/logs` (identical), per existing route. Recorded, not changed. |
| D14 | Retention | No pruning in Phase 2. `delete_job` purges `log_entries` for the job and removes the job directory (segments included). Accumulation is unbounded but recorded; a future issue owns caps. |
| D15 | #36 sequencing | #36 lands first/together. This spec assumes the `origin` column and `LogEntry.origin` exist. Real rows here set `origin="real"`. |

## 3. Scope Boundaries

| In Scope | Out of Scope |
|----------|--------------|
| Agent segment writer (`agent/log_segment_writer.py`) batching stdout/stderr | Container/runtime resource enforcement (#98) |
| Backend watcher routing branch for `jobs/<id>/logs/*.yaml` segments | Frontend changes (separate issue) |
| Backend ingest handler → `log_entries` with `origin="real"` | Retention/caps beyond D14 |
| WebSocket live delivery from ingested segments (poller task) | Metrics sampling (#102) |
| `log_stream_available` gate flip + reason update | #36 schema (prerequisite) |
| `shared/schemas/log_segment.py` | CLI `sci-run` cancel stub |
| `tests/e2e`/websocket path fix note | systemd/install docs (#104) |

## 4. Implementation Approach

### 4.1 Agent side
- New `agent/log_segment_writer.py` with `SegmentWriter`:
  - `write_lines(stream, lines)` buffers; flushes per D10.
  - Rotates per D4; enforces D6 line length; tracks `dropped`.
  - Writes atomically via `shared.file_ops.write_yaml`.
  - Records self-write in `SelfWriteLedger` so the agent's own segments are not re-ingested by it (they are backend-owned ingest, but the agent must not loop on them).
- `agent/executor.py`: replace the `.log` text-file capture with `SegmentWriter` for stdout/stderr. `ExecutionResult.stdout_path`/`stderr_path` point to the first segment written (or `""` if none), preserving the existing "no output → empty string" convention. `BoundedLineWriter` still enforces D6 per line before segmenting.
- Agent never deletes segment files (append-only ownership: backend owns ingest, agent owns creation; deletion only via job dir removal).

### 4.2 Backend side
- `backend/services/syncthing_service.py` `_process_file`: add branch
  ```
  jobs/<id>/logs/<file>.yaml  (parts[2]=="logs", parts[3].endswith(".yaml"))
  ```
  → `_process_log_segment_file(job_id, file_path)` which `read_yaml`s a `LogSegment`, validates `segment.job_id == job_id` and `sequence >= 1`, and ingests each `LogEntry` into `log_entries` with `origin="real"` (idempotent: skip if that `(job_id, timestamp, line)` already present, or track ingested segment filenames in `_processed_files`).
- `_initial_scan` and `_scan_folder_structure`: sweep `jobs/<id>/logs/*.yaml`.
- `_process_delete`: log segment deletion at DEBUG; never triggers job deletion.
- Unrecognised `.yaml` under a job dir: already logged at DEBUG; keep and add a test.

### 4.3 Store / WebSocket
- `DatabaseStore.log_stream_available(job_id=None)`: implement D11.
- `start_log_stream`: in production (not demo), run a poller task that every 1 s queries new real rows (by `id`/`timestamp` cursor) and sends `line` to subscribers. Backpressure via bounded `asyncio.Queue` (maxsize=1000) per subscriber; on full, drop + count + warn.
- `LOG_STREAM_UNAVAILABLE_REASON` updated per D12.

### 4.4 Shared schema
- New `shared/schemas/log_segment.py` with `LogSegment`; export from `shared/schemas/__init__.py`.

## 5. Acceptance Criteria (named tests)

| AC | Description | Test |
|----|-------------|------|
| AC-1 | Agent captures stdout+stderr into segments; both retrievable via `GET /jobs/{id}/logs` with `origin="real"`. | `test_job_logs_real_ingest_end_to_end` |
| AC-2 | WebSocket delivers real lines with `SEED_DEMO_DATA=False`, test not skipped. | `test_websocket_real_stream` (path fixed to `/{job_id}/logs`) |
| AC-3 | `log_stream_available()` no longer keys solely on `SEED_DEMO_DATA`; available/unavailable cases tested. | `test_log_stream_available_available_and_unavailable` |
| AC-4 | `LOG_STREAM_UNAVAILABLE_REASON` updated; close code stays `1008`. | `test_websocket_close_1008_reason` |
| AC-5 | Real vs synthetic rows distinguishable (`origin` differs). | `test_real_vs_synthetic_origin` |
| AC-6 | Backpressure: 10k lines/s does not exhaust agent memory; dropped lines counted. | `test_backpressure_drop_count` |
| AC-7 | Batching: stated batch size 50 lines / 5 s; latency measured & recorded. | `test_segment_batch_flush` |
| AC-8 | Line length bounded at 10 KiB; pathological line cannot break a segment. | `test_segment_line_length_bound` |
| AC-9 | Timestamps timezone-aware UTC; stored value consistent with `LogViewer`. | `test_segment_timestamps_utc` |
| AC-10 | Ordering disclaimed: cross-stream order not guaranteed; within-stream by sequence. | `test_segment_ordering_disclaimer` |
| AC-11 | Segments create-only; file hash stable after creation. | `test_segment_create_only` |
| AC-12 | Segment cap 1000 per job; overflow sets `dropped`. | `test_segment_cap` |
| AC-13 | Watcher routing: unrecognised `.yaml` under job dir logged, not swallowed. | `test_unrecognised_yaml_logged` |
| AC-14 | #36 landed (or together); real rows `origin="real"`. | `test_origin_real_requires_36` |
| AC-15 | `pytest`/`ruff`/`mypy` green. | CI gates |

## 6. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Segment file count explosion | Cap 1000 segments/job (D4); Syncthing replication cost stated in D10 |
| Watcher re-ingests same segment | `_processed_files` set + idempotent ingest (skip duplicates) |
| Agent self-write echo | `SelfWriteLedger` records segment paths |
| Timestamp skew across streams | Best-effort ordering disclaimer (D8) |
| #36 not landed first | Hard sequencing constraint; spec blocks merge |

## 7. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] #36 landed or bundled
- [ ] Implementation complete (SWE)
- [ ] AC-1…AC-15 verified (QA)
- [ ] Gates green
- [ ] PM acceptance

## 8. Related

- #36 — synthetic marker (prerequisite; provides `origin`, `LogEntry`)
- #92 — transport decision (file-only; sequences #36 first)
- #98 — job execution (produces stdout/stderr)
- #99 — cancel (process tree termination)
- #100 — terminal state
- #105 — job submission folder (watcher routing precedent)
- #106 — command files (routing precedent; `commands/`/`acks/`)
- `agent/executor.py` — log capture site
- `backend/services/syncthing_service.py` — watcher routing
- `backend/store/database_store.py` — `log_stream_available`, `get_job_logs`
- `shared/schemas/log_entry.py` — `LogEntry`
- `agent/log_writer.py` — `BoundedLineWriter`
