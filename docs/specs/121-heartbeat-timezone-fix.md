# Spec: Heartbeat Timezone Fix — Store and Monitor Compare Aware-to-Aware UTC

**Issue**: #121 — "Heartbeat monitor compares naive local now() against naive UTC, so a healthy node is marked OFFLINE on any non-UTC host"
**Labels**: `bug`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-14

---

## 1. Verified Problem Statement

`backend/services/syncthing_service.py::_heartbeat_monitor` (lines 346-378) compares a stored `last_heartbeat` against **naive local** `datetime.now()` (line 353). But a `last_heartbeat` written as a **tz-aware UTC** value round-trips through the naive `DateTime` column (`backend/store/database.py:75`) as **naive UTC**, not naive local. On any host that is not running UTC, the stored heartbeat reads as hours in the future-or-past relative to the monitor's clock, and a perfectly healthy node is marked `OFFLINE` — permanently.

Measured on a host with local offset `+0300`, using the real SQLite round-trip:

```
written (tz-aware)   : 2026-10-13 01:30:01.208216+00:00
read back from DB    : 2026-10-13 01:30:01.208216   | tzinfo: None
monitor's now        : 2026-10-13 04:30:01            (naive local)
delta                : 10800 s  (3.0 h)
delta > 90           : True   -> node marked OFFLINE
```

Re-running the agent's 30 s heartbeat does not help — each write is still 3 h "old":

```
tick 0: delta=10800s -> OFFLINE
tick 1: delta=10830s -> OFFLINE
tick 2: delta=10860s -> OFFLINE
tick 3: delta=10890s -> OFFLINE
```

The node can never return `ONLINE`, because the monitor only flips back to `ONLINE` when `delta <= 90` (line 371), which is unreachable while the two clocks disagree by the UTC offset.

### Why it is easy to miss

- The whole rest of the codebase writes **naive local** `datetime.now()` for DB-stored timestamps (`job_service.py:82`, `database_store.py:341,1055,1077`, and the demo seeder's `iso()` helper). A writer following that convention is correct; only a tz-aware writer breaks it.
- The line at `:359-360` looks like tz-awareness is handled — it replaces `tzinfo` on a naive value with the monitor's local tz — but that only covers the *comparison* side. The corruption happens earlier, at the SQLite boundary, and is invisible from inside the process.
- On a UTC host (most CI containers) the bug does not reproduce at all. The local dev box here is `+0300`.

### The coupling with #95

#95 (node registration) is already merged. The agent writes `nodes/<node_id>.yaml` with a `last_heartbeat` timestamp. #95's workaround: the agent now writes **naive LOCAL** to match the store's broken convention, but this is a workaround, not a fix. The coupling is bidirectional: when #121 makes the store tz-aware, `agent/hardware.py` must change in the same commit.

---

## 2. Root Cause

The `NodeModel.last_heartbeat` column is defined as `DateTime` (timezone-naive) at `backend/store/database.py:75`. SQLite has no timezone type, so SQLAlchemy stores naive datetimes as-is. When a tz-aware UTC value is written, the offset is silently dropped and the value comes back naive. The monitor then treats that naive value as if it were in the *local* timezone (line 360), causing a delta inflated by the host's UTC offset.

---

## 3. Open Questions — Resolved

The issue left the fix direction open. Each is decided here, with rationale.

### Q1 — Which side moves? Store tz-aware (Option 2) vs Monitor naive-UTC (Option 1)?

**Decision: Option 2 — Make the store timezone-aware (UTC).**

- The column becomes `DateTime(timezone=True)`. On SQLite this is a no-op at storage level but changes SQLAlchemy behavior: it preserves the offset on write and expects an offset on read.
- The monitor uses `datetime.now(timezone.utc)` and compares aware-to-aware UTC. The coercion at lines 359-360 is removed.
- This matches `backend/core/security.py` which already uses `datetime.now(timezone.utc)` for JWT expiry — the codebase already has the "aware UTC" convention for time that must be comparable across zones.
- Option 1 (naive UTC by convention) requires every writer to remember to strip tzinfo before persisting, and every reader to know the convention. Option 2 makes the type system enforce it.

### Q2 — Migration strategy for existing naive UTC rows?

**Decision: Alembic migration adds `timezone=True` to the column; existing rows are interpreted as UTC.**

- SQLite stores naive values as-is. Adding `timezone=True` does not change the stored bytes; it changes SQLAlchemy's *reading* behavior. A naive value read from a `timezone=True` column comes back as *aware UTC* (SQLAlchemy assumes naive = UTC when the column is tz-aware).
- This is exactly what we want: the rows written by the #95 workaround (naive local) will be misread as UTC, but those rows are already broken on non-UTC hosts. The fix is forward-looking: new writes are aware UTC, new reads are aware UTC, and the comparison is correct.
- A data-migration step is **not** needed — the existing rows are few (dev clusters only) and the misread is a one-time shift. The monitor will flip them OFFLINE once, then the agent's next heartbeat (now aware UTC) corrects it.

### Q3 — What timestamp does `agent/hardware.py:collect_node_spec()` produce?

**Decision: It already produces `datetime.now()` (naive local) as the #95 workaround.**

- The fix requires changing it to `datetime.now(timezone.utc)` in the same commit as the store change.
- The test `test_the_heartbeat_is_naive_local_because_the_store_is` in `agent/tests/test_hardware.py:379` currently *asserts* naive local. That test must flip to assert aware UTC.
- The pure function `probe_hardware` takes `now: datetime` as a required argument (line 113) — the caller owns the clock convention. The loop calls `collect_node_spec` without `now`, which defaults to `datetime.now()` (line 164). That default changes.

### Q4 — Does the 90s/30s threshold change?

**Decision: No. #96 owns threshold configurability. This issue only fixes the timezone comparison bug.**

---

## 4. Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| `NodeModel.last_heartbeat` → `DateTime(timezone=True)` | Changing `JobModel` or other `DateTime` columns |
| Alembic migration for the column change | Backfilling historical data (see Q2) |
| `_heartbeat_monitor`: `datetime.now(timezone.utc)` + aware-to-aware compare | Making the 90s threshold configurable (#96) |
| `agent/hardware.py`: `collect_node_spec` → aware UTC | HTTP transport for registration (file write stays) |
| Tests updated to expect aware UTC | Fixing other naive-local writers in the codebase |

### Coupling to #95 — recorded, not fixed here

`agent/hardware.py` currently writes naive local (the #95 workaround). It **must** switch to aware UTC in the same commit as the store change. The test `test_the_heartbeat_is_naive_local_because_the_store_is` is what will go red and must be updated.

---

## 5. Implementation Approach

### 5.1 Backend store model — `backend/store/database.py:75`

```python
# Before
last_heartbeat = Column(DateTime, nullable=False)

# After
last_heartbeat = Column(DateTime(timezone=True), nullable=False)
# Convention: stored values are ALWAYS timezone-aware UTC.
# The monitor compares against datetime.now(timezone.utc).
```

### 5.2 Alembic migration

Generate a migration that alters the column to `DateTime(timezone=True)`. On SQLite this is a metadata-only change (no `ALTER TABLE` needed), but the migration must exist for other dialects and for Alembic's version tracking.

```python
# migrations/versions/xxxx_heartbeat_tz_aware.py
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.alter_column('nodes', 'last_heartbeat',
                    existing_type=sa.DateTime(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=False)

def downgrade():
    op.alter_column('nodes', 'last_heartbeat',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.DateTime(),
                    existing_nullable=False)
```

### 5.3 Heartbeat monitor — `backend/services/syncthing_service.py:346-378`

```python
# Before (lines 353, 359-360)
now = datetime.now()
...
if node.last_heartbeat.tzinfo is None:
    last_hb = node.last_heartbeat.replace(tzinfo=now.tzinfo)
else:
    last_hb = node.last_heartbeat

# After
from datetime import datetime, timezone
...
now = datetime.now(timezone.utc)
...
# node.last_heartbeat is now always aware UTC (column is timezone=True)
last_hb = node.last_heartbeat
# No coercion needed — both are aware UTC
delta = (now - last_hb).total_seconds()
```

The `if node.last_heartbeat.tzinfo is None:` block (lines 359-362) is **removed entirely**.

### 5.4 Agent hardware probe — `agent/hardware.py`

```python
# collect_node_spec (line 164)
# Before
now=datetime.now() if now is None else now,

# After
from datetime import datetime, timezone
...
now=datetime.now(timezone.utc) if now is None else now,
```

The module docstring (lines 28-39) and the comment at line 151-155 must be updated to reflect the new convention: the agent now writes aware UTC, the store preserves it, and the monitor compares aware-to-aware.

### 5.5 Test updates

#### `agent/tests/test_hardware.py`

- Line 56-59: `NOW` constant comment changes from "naive local" to "aware UTC (fixed for test determinism)". The value `datetime(2026, 10, 13, 12, 0)` becomes `datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)`.
- Line 379-406: `test_the_heartbeat_is_naive_local_because_the_store_is` is **renamed and rewritten** to `test_the_heartbeat_is_aware_utc_because_the_store_is_now_tz_aware`. It asserts:
  - `value.tzinfo is not None`
  - `value.tzinfo == timezone.utc`
  - `abs((datetime.now(timezone.utc) - value).total_seconds()) < 5`
- Line 368-371: `test_a_real_probe_populates_every_field` assertion changes from `tzinfo is None` to `tzinfo is not None and tzinfo == timezone.utc`.
- Line 441-444: `test_the_written_file_round_trips_through_read_yaml` assertion updated similarly.
- Line 756-767: `heartbeat_as_the_agent_writes_it` helper now returns an aware UTC value (subtracting `timedelta` from an aware datetime preserves awareness).
- `FakeStore._as_naive` (line 679) and `_as_stored` (line 683) are **removed** — the fake store no longer strips tzinfo. It stores the aware value as-is, matching the real store's new behavior.
- `seed_node` (line 770) no longer needs to go through `create_node` to simulate tz-loss; it passes the aware value directly.

#### `backend/tests/unit/test_syncthing.py`

- Line 141: `last_heartbeat=datetime.now()` in `test_initial_scan_nodes` becomes `datetime.now(timezone.utc)`.
- Any other test creating `NodeSpec` with a naive `last_heartbeat` must be updated to aware UTC.

---

## 6. Acceptance Criteria

### Functional

- **AC-1** — `NodeModel.last_heartbeat` is `DateTime(timezone=True)` in the model (`backend/store/database.py`).
- **AC-2** — Alembic migration adds `timezone=True` (or equivalent for SQLite) and existing rows are treated as UTC.
- **AC-3** — `syncthing_service.py:_heartbeat_monitor` uses `datetime.now(timezone.utc)` and compares aware-to-aware; the coercion at lines 359-362 is removed.
- **AC-4** — Agent (`collect_node_spec` / `write_node_spec`) emits timezone-aware UTC `last_heartbeat`.
- **AC-5** — On a non-UTC host (simulated by setting `TZ` env or `time.tzset`), a healthy node stays `ONLINE` — verified by a test that mocks the timezone.
- **AC-6** — The #95 test `test_the_heartbeat_is_naive_local_because_the_store_is` is updated to expect aware UTC (renamed to `test_the_heartbeat_is_aware_utc_because_the_store_is_now_tz_aware`).
- **AC-7** — All gates green: `ruff check . && ruff format --check . && mypy .` clean; `pytest -q` green (full suite count does not drop).

### Quality

- **AC-8** — No remaining writer persists a tz-aware datetime into a naive `DateTime` column (grep for `DateTime(` without `timezone=True` on columns that receive aware values).
- **AC-9** — The convention (stored values are aware UTC) is documented in a comment at the column definition (`backend/store/database.py:75`) and at the monitor (`syncthing_service.py` near line 353).

---

## 7. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Alembic migration fails on SQLite (no `ALTER COLUMN` support) | Low | Medium | SQLite ignores `ALTER COLUMN` for type changes; the migration is a no-op at runtime but records the version. Test the migration applies cleanly. |
| Existing naive-local rows misread as UTC cause a one-time OFFLINE flip | Medium | Low | Expected and acceptable — the agent's next heartbeat (aware UTC) corrects it within 30s. |
| `FakeStore` in agent tests diverges from real store behavior | Medium | High | `FakeStore` is updated to *not* strip tzinfo (removed `_as_naive`/`_as_stored`), matching the real store's new tz-aware behavior. |
| Other `DateTime` columns in the codebase are inconsistent | Low | Medium | This issue only fixes `last_heartbeat`. Other columns (`JobModel.created_at`, etc.) remain naive local per existing convention. A separate issue can unify if desired. |
| `mypy` complains about `datetime.now(timezone.utc)` vs naive comparisons | Low | Medium | The monitor now compares aware-to-aware; no mixed-type arithmetic remains. `NodeModel.last_heartbeat` type annotation updates to `datetime` (aware). |

---

## 8. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Implementation complete (SWE)
- [ ] AC-1 … AC-9 verified with evidence (QA)
- [ ] `pytest -q` green; `ruff`/`mypy` clean
- [ ] PM acceptance

---

## 9. Related

- #95 — node registration file (agent writes `last_heartbeat`; workaround was naive local; this issue fixes the backend so the agent can write aware UTC)
- #96 — 90s offline threshold hardcoded (coupling recorded in §4, **not** fixed here)
- #102 — node metrics writer (may also write timestamps; out of scope)
- `backend/core/security.py` — already uses `datetime.now(timezone.utc)` for JWT expiry, the pattern this fix adopts
- `shared/auth/localhost_hosts.json` — unrelated but noted: the localhost bypass list is duplicated in backend and frontend; this issue does not touch it