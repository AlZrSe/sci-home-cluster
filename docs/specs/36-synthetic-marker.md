# Spec: Synthetic Marker for Metrics and Log Rows

## Issue
**#36** — Fabricated metrics and log rows are indistinguishable from real agent output.

**Status**: Groomed  
**Labels**: `enhancement`, `phase-2`  
**Prerequisite for**: #101 (logs), #102 (metrics) — #92 sequences #36 *before* both.

---

## 1. Verified Problem Statement

Every row in `gpu_metrics`, `cpu_metrics`, and `log_entries` is currently a plain record with no indication of its origin. The demo generators write ~306 rows (121 GPU + 121 CPU + 64 log) into a real job's tables (`job-1`), and there is no column, flag, or query that can separate them from genuine agent output.

**Reproduction** (run against a fresh DB with `SEED_DEMO_DATA=True`):

```bash
$ sqlite3 data/scientific_home_cluster.db \
  "SELECT COUNT(*) FROM gpu_metrics; SELECT COUNT(*) FROM cpu_metrics; SELECT COUNT(*) FROM log_entries;"
121
121
64

$ sqlite3 data/scientific_home_cluster.db \
  "SELECT job_id, COUNT(*) FROM gpu_metrics GROUP BY job_id;"
job-1|121
```

All 306 rows belong to `job-1` — a real `PENDING` job. If an agent later reports real metrics for `job-1`, they would be interleaved with the fabricated rows and **indistinguishable**.

**Five write paths produce synthetic data** (all gated by `SEED_DEMO_DATA` except `_stream_worker`, which has its own per-tick gate):

| Path | Function | Lines | What it writes |
|------|----------|-------|----------------|
| 1 | `_generate_job_metrics` | 945–1008 | 121 `GPUMetricModel` + 121 `CPUMetricModel` per job |
| 2 | `_generate_node_metrics` | 874–939 | same, under `job_id = "node:<id>"` |
| 3 | `_generate_job_logs` | 1065–1101 | 64 `LogEntryModel` per job |
| 4 | `_store_job_metrics` | 689–713 | persists #1/#2 |
| 5 | `_store_node_metrics` | 788–812 | persists #2 |
| 6 | `_store_job_logs` | 1052–1062 | persists #3 |
| 7 | `_stream_worker` | 1154–1219 | **writes one `LogEntryModel` every 1.4 s** while a job is `RUNNING` and `SEED_DEMO_DATA` is on |

All seven paths eventually call `session.add(model)` + `commit()` on the plain SQLAlchemy models (`GPUMetricModel`, `CPUMetricModel`, `LogEntryModel`), which today have no `origin` column.

---

## 2. Root Cause

The three tables were created in the initial migration (`backend/alembic/versions/9e9891b90d08_initial_migration.py`) without any discriminator. Issue #35 stopped *new* fabrication at read time, but the rows already written remain, and the generators still write when `SEED_DEMO_DATA=True` — they just do it at read time (lazy seeding) rather than eagerly.

The `_stream_worker` is the highest-volume writer (one row / 1.4 s) and the easiest to forget because it lives inside `start_log_stream`, not in an obvious "generator" function.

---

## 3. Open Questions — Resolved

| # | Question | Resolution |
|---|----------|------------|
| 1 | **Boolean or source string?** | **String literal `origin` with values `"synthetic"` / `"real"`**. Extensible (a third origin like `"migrated"` is possible), and a `CHECK` constraint is as cheap as a boolean. |
| 2 | **Should the API expose the marker?** | **Yes — the Pydantic schemas (`GPUMetric`, `CPUMetric`, a new `LogEntry`) must include `origin`** so the dashboard can distinguish "demo chart" from "real chart". This is a contract change; the schema change and `openapi.yaml` regeneration are in scope. |
| 3 | **Is a schema change acceptable this early?** | **Yes.** #92 requires #36 before #101/#102 precisely so real agent data is never written without the marker. Retrofitting later means re-deciding what to do with rows already written. |

---

## 4. Scope Boundaries

### In scope
- Add `origin` column (`VARCHAR(16)`, NOT NULL, `CHECK (origin IN ('synthetic','real'))`, default `"synthetic"`) to `gpu_metrics`, `cpu_metrics`, `log_entries`.
- Alembic migration that adds the column, backfills existing rows to `"synthetic"`, and is reversible.
- Update SQLAlchemy models (`GPUMetricModel`, `CPUMetricModel`, `LogEntryModel` in `backend/store/database.py`).
- Update Pydantic schemas (`GPUMetric`, `CPUMetric` in `shared/schemas/`, and a **new** `LogEntry` in `shared/schemas/log_entry.py`).
- All seven write paths set `origin="synthetic"` (the three generators, the three store helpers, and `_stream_worker`).
- `openapi.yaml` regenerated and committed.
- Tests asserting the marker on write and read.

### Out of scope
- **Agent writers** — #101 (logs) and #102 (metrics) will write `origin="real"`. This issue only ensures the *backend's own* fabricators tag their rows.
- **Dashboard filtering** — whether the UI hides synthetic series when `SEED_DEMO_DATA=False` is a #101/#102 concern.
- **Cleanup queries** — the documented manual procedure for pre-marker rows is a documentation task, not code.

---

## 5. Implementation Approach

### 5.1 Alembic migration
Create a new migration `backend/alembic/versions/<rev>_add_origin_to_metrics_and_logs.py`:

```python
def upgrade() -> None:
    for table in ("gpu_metrics", "cpu_metrics", "log_entries"):
        op.add_column(table, sa.Column("origin", sa.String(16), nullable=False,
            server_default="synthetic"))
        op.create_check_constraint(
            f"ck_{table}_origin", table, "origin IN ('synthetic','real')"
        )
        op.create_index(f"ix_{table}_origin", table, ["origin"])

def downgrade() -> None:
    for table in ("gpu_metrics", "cpu_metrics", "log_entries"):
        op.drop_index(f"ix_{table}_origin", table_name=table)
        op.drop_constraint(f"ck_{table}_origin", table, type_="check")
        op.drop_column(table, "origin")
```

- `server_default="synthetic"` backfills existing ~306 rows automatically on PostgreSQL/SQLite. SQLite applies the default to existing rows when the column is added (verified).
- Index on `origin` supports the manual cleanup query.

### 5.2 SQLAlchemy models (`backend/store/database.py`)

Add to each model:
```python
origin = Column(String(16), nullable=False, default="synthetic", server_default="synthetic")
```
The Python `default` covers programmatic inserts; `server_default` covers the migration backfill.

### 5.3 Pydantic schemas

**`shared/schemas/gpu_metric.py`** — add to `GPUMetric`:
```python
origin: Literal["synthetic", "real"] = Field(default="synthetic")
```

**`shared/schemas/cpu_metric.py`** — add to `CPUMetric`:
```python
origin: Literal["synthetic", "real"] = Field(default="synthetic")
```

**`shared/schemas/log_entry.py`** — **new file**:
```python
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal

class LogEntry(BaseModel):
    """A single log line with its origin marker."""
    timestamp: datetime
    line: str
    origin: Literal["synthetic", "real"] = Field(default="synthetic")

    model_config = {"from_attributes": True}
```

Update `shared/schemas/__init__.py` to export `LogEntry`.

### 5.4 Backend writers — all set `origin="synthetic"`

| Write site | Change |
|------------|--------|
| `_store_job_metrics` (689) | `GPUMetricModel(..., origin="synthetic")`, same for `CPUMetricModel` |
| `_store_node_metrics` (788) | same |
| `_store_job_logs` (1052) | `LogEntryModel(..., origin="synthetic")` |
| `_stream_worker` (1198) | `LogEntryModel(job_id=..., timestamp=..., line=..., origin="synthetic")` |

The three generators (`_generate_job_metrics`, `_generate_node_metrics`, `_generate_job_logs`) return domain objects (`JobMetrics`, `List[str]`) — they don't create DB models directly. The store helpers are the single choke point, so the fix is localised to 4 functions.

### 5.5 Readers

`get_job_metrics`, `get_node_metrics`, `get_job_logs` already select the model and convert to Pydantic. With `origin` on the model and the schema, the field flows through automatically — no reader changes needed.

### 5.6 `openapi.yaml` regeneration

Run `python -c "from backend.main import app; import yaml; print(yaml.dump(app.openapi(), sort_keys=False))" > openapi.yaml` and commit the updated file. A test asserts the three schemas contain `origin`.

---

## 6. Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | `origin` is a `VARCHAR(16)` with `CHECK IN ('synthetic','real')` | Extensible, cheap constraint, matches Pydantic `Literal`. |
| D2 | Default `"synthetic"` for existing rows | Safe: never marks real data as fabricated. Pre-marker rows stay "real" by default if we ever change the default; here we *want* them marked synthetic because we know they are. |
| D3 | Index on `origin` | Enables the manual cleanup query `WHERE origin='synthetic'`. |
| D4 | All seven synthetic write paths set `origin="synthetic"` | Single choke point in the four store helpers; `_stream_worker` is the fifth writer but it constructs `LogEntryModel` directly. |
| D5 | Pydantic schemas include `origin` with default `"synthetic"` | API exposes the marker; real agent writers (#101/#102) will override to `"real"`. |
| D6 | New `LogEntry` schema in `shared/schemas/log_entry.py` | Logs currently return `List[str]`; a structured schema is needed for the marker. This is the contract change. |
| D7 | Migration is reversible (downgrade drops column) | Standard Alembic practice. |

---

## 7. Acceptance Criteria

| AC | Description | Test |
|----|-------------|------|
| AC-1 | `gpu_metrics`, `cpu_metrics`, `log_entries` each have NOT NULL `origin` column with `CHECK IN ('synthetic','real')`, default `"synthetic"`. | Migration test + `PRAGMA table_info` / `sqlite_master` check. |
| AC-2 | Existing rows (the ~306 from demo seeding) have `origin="synthetic"` after migration. | Seed demo data, run migration, `SELECT origin FROM gpu_metrics WHERE job_id='job-1' GROUP BY origin`. |
| AC-3 | All four store helpers (`_store_job_metrics`, `_store_node_metrics`, `_store_job_logs`, `_stream_worker`) write `origin="synthetic"`. | Insert via each path, read back, assert `origin == "synthetic"`. |
| AC-4 | `GPUMetric`, `CPUMetric`, `LogEntry` schemas include `origin: Literal["synthetic","real"] = "synthetic"`. | Schema import test + `model_fields["origin"]` assertion. |
| AC-5 | `openapi.yaml` regenerated; `GPUMetric`, `CPUMetric`, `LogEntry` schemas contain `origin`. | Compare committed `openapi.yaml` with live `app.openapi()`; assert field present. |
| AC-6 | `SEED_DEMO_DATA=True` still works — demo endpoint returns rows with `origin="synthetic"`. | Integration test with flag on, assert response includes `origin`. |
| AC-7 | `SEED_DEMO_DATA=False` produces no synthetic rows (existing `test_no_fabrication.py` passes). | Run existing suite; no new synthetic rows appear. |
| AC-8 | Migration downgrade drops the column cleanly. | `alembic downgrade -1` then `upgrade head` round-trip. |
| AC-9 | All gates green: `ruff`, `mypy`, `pytest` (≥ baseline). | CI gates. |

---

## 8. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Migration backfill behaves differently on SQLite vs PostgreSQL | Low | Medium | Test migration on both; `server_default` works on SQLite for existing rows. |
| `_stream_worker` forgotten because it's inside a nested function | Medium | High | Explicit AC-3 test covers it; code comment at line 1198. |
| `LogEntry` schema breaks existing `List[str]` log responses | Medium | Medium | API returns `List[LogEntry]`; frontend adapts in #101. This is the intended contract change. |
| Demo data flag flipped mid-stream leaves worker running | Low | Low | `_stream_worker` already checks `log_stream_available()` per tick (line 1168). |

---

## 9. Definition of Done

- [ ] Migration created, `alembic upgrade head` succeeds on fresh and seeded DB.
- [ ] All seven synthetic write paths verified to write `origin="synthetic"`.
- [ ] Pydantic schemas updated, `openapi.yaml` regenerated and committed.
- [ ] All AC-1…AC-9 verified by named tests.
- [ ] `ruff check . && ruff format --check . && mypy .` exit 0.
- [ ] `pytest -q` green, count ≥ baseline.
- [ ] Spec linked from #36; issue comment posted; `ready-for-grooming` → `groomed`.

---

## 10. Related

- **#92** — transport decision; sequences #36 before #101/#102.
- **#35** — stopped new fabrication at read time; #36 makes old fabrication identifiable.
- **#101** — log capture; writes `origin="real"`.
- **#102** — metric sampling; writes `origin="real"`.
- **#100** — terminal state; agent writes `origin="real"` for final metrics/logs.
- **#27** — manual cleanup procedure; #36 enables it.
- **backend/alembic/** — migration infrastructure.
- **shared/schemas/** — contract for API responses.