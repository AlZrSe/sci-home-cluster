# Specification: Fix DB_POOL Default for Async Engine Compatibility

**Issue**: #130  
**Status**: Groomed  
**Author**: PM (agent-team workflow)  
**Date**: 2026-10-22

---

## 1. Problem Statement

The `Settings.DB_POOL` configuration defaults to `"pooled"`, but `backend/core/database.py:108` passes `QueuePool` (a synchronous pool class) to `create_async_engine()`. SQLAlchemy 2.0 rejects this with:

```
ArgumentError: Pool class QueuePool cannot be used with asyncio engine
```

This bug is currently masked in two environments:
1. **Tests** (`backend/tests/conftest.py:181`) explicitly set `settings.DB_POOL = "null"`
2. **Docker** (documented/default deployment) sets `DB_POOL=null` via environment

A user running the application locally with default settings (`DB_POOL=pooled`) will experience a startup crash.

---

## 2. User Stories

### US-1: Local Developer Startup
**As a** developer running the backend locally  
**I want** the application to start with default configuration  
**So that** I can develop without manually setting `DB_POOL=null`

### US-2: Production Deployment Flexibility
**As a** platform operator  
**I want** to choose between pooled and null pool strategies via configuration  
**So that** I can optimize for my deployment topology (single-process vs multi-worker)

### US-3: Test Suite Integrity
**As a** QA engineer  
**I want** the test suite to continue using `NullPool` for hermetic isolation  
**So that** connections are never shared between event loops (preventing "Event loop is closed" errors)

---

## 3. Acceptance Criteria

| ID | Criterion | Verification |
|----|-----------|--------------|
| AC-1 | Application starts successfully with `DB_POOL=pooled` (default) | `uvicorn backend.main:app` starts without `ArgumentError` |
| AC-2 | `DB_POOL=pooled` uses `AsyncAdaptedQueuePool` for async engine | Code inspection: `poolclass=AsyncAdaptedQueuePool` when `"pooled"` |
| AC-3 | `DB_POOL=null` continues to use `NullPool` | Code inspection: `poolclass=NullPool` when `"null"` |
| AC-4 | Test suite continues to pass with `DB_POOL=null` override | `pytest backend/tests/` exits 0 |
| AC-5 | Invalid `DB_POOL` values are rejected at startup | Setting `DB_POOL=invalid` raises clear configuration error |
| AC-6 | Documentation reflects the two valid values | README.md table updated if needed |

---

## 4. Test Scenarios

### TS-1: Default Startup (Happy Path)
```bash
# No DB_POOL set → defaults to "pooled"
uvicorn backend.main:app --port 8001 &
curl -f http://localhost:8001/api/v1/health  # Should return 200
kill %1
```

### TS-2: Explicit Pooled Mode
```bash
DB_POOL=pooled uvicorn backend.main:app --port 8002 &
curl -f http://localhost:8002/api/v1/health
kill %1
```

### TS-3: Null Pool Mode (Test-Like)
```bash
DB_POOL=null uvicorn backend.main:app --port 8003 &
curl -f http://localhost:8003/api/v1/health
kill %1
```

### TS-4: Invalid Value Rejection
```bash
DB_POOL=invalid uvicorn backend.main:app --port 8004 2>&1 | grep -i "invalid\|error"
# Should fail fast with clear message
```

### TS-5: Full Test Suite
```bash
cd backend && pytest --cov=backend  # All tests pass
```

### TS-6: Alembic Migrations Work
```bash
DB_POOL=pooled alembic upgrade head  # No pool-related errors
```

---

## 5. Technical Approach

### 5.1 Root Cause
`QueuePool` from `sqlalchemy.pool` is designed for synchronous engines. For async engines, SQLAlchemy provides `AsyncAdaptedQueuePool` in `sqlalchemy.ext.asyncio`.

### 5.2 Changes Required

#### File: `backend/core/database.py`

**Import Addition:**
```python
from sqlalchemy.ext.asyncio import AsyncAdaptedQueuePool
```

**Logic Change (line ~108):**
```python
# Before:
poolclass=NullPool if settings.DB_POOL == "null" else QueuePool,

# After:
if settings.DB_POOL == "null":
    poolclass = NullPool
elif settings.DB_POOL == "pooled":
    poolclass = AsyncAdaptedQueuePool
else:
    raise ValueError(
        f"Invalid DB_POOL value: {settings.DB_POOL!r}. "
        f"Valid values: 'null', 'pooled'"
    )
```

#### File: `backend/core/config.py` (Optional Enhancement)

Add validation to `Settings` model for early failure:
```python
@field_validator("DB_POOL")
@classmethod
def validate_db_pool(cls, v: str) -> str:
    if v not in ("null", "pooled"):
        raise ValueError(f"DB_POOL must be 'null' or 'pooled', got {v!r}")
    return v
```

### 5.3 Why Not Other Pool Classes?

| Pool Class | Suitable for Async? | Notes |
|------------|---------------------|-------|
| `QueuePool` | ❌ | Sync-only, causes the reported error |
| `AsyncAdaptedQueuePool` | ✅ | **Correct choice** - wraps QueuePool for asyncio |
| `NullPool` | ✅ | Already used for tests, opens fresh connection per session |
| `StaticPool` | ⚠️ | Single connection, not suitable for concurrent requests |

---

## 6. Edge Cases & Risks

### Risk 1: Connection Leaks with Pooled Mode
**Mitigation**: `pool_pre_ping=True` already configured (line 103). The async engine's `dispose()` is called on shutdown (`close_database()`).

### Risk 2: SQLite + Pooled + Concurrency
**Context**: SQLite with WAL mode (configured in `_configure_sqlite`) supports concurrent readers + one writer. Pooled connections work correctly with aiosqlite's internal threading.

**Verification**: Integration tests with concurrent requests should be added if not present.

### Risk 3: Event Loop Affinity
**Context**: The test suite uses `NullPool` specifically because "a pooled connection created in one loop and reused in the next produces intermittent 'Event loop is closed' failures" (comment lines 104-107).

**Mitigation**: Production use with `pooled` assumes a single long-lived event loop (standard uvicorn worker). This is the normal deployment model.

### Risk 4: Migration Compatibility
**Check**: Alembic uses `resolve_database_url()` which creates a sync engine for migrations. The pool class is only used by the async application engine. **No migration impact.**

### Risk 5: Backward Compatibility
**Impact**: None. The default behavior changes from "crash" to "work". Existing deployments using `DB_POOL=null` continue working.

---

## 7. Implementation Checklist

- [ ] Add `AsyncAdaptedQueuePool` import to `backend/core/database.py`
- [ ] Replace ternary with explicit if/elif/else and validation
- [ ] Add `field_validator` for `DB_POOL` in `backend/core/config.py`
- [ ] Verify application starts with default config (`DB_POOL=pooled`)
- [ ] Run full test suite (`pytest backend/tests/`)
- [ ] Verify Alembic migrations still work
- [ ] Update README if any documentation gaps found

---

## 8. Related Files

| File | Role |
|------|------|
| `backend/core/database.py` | Primary fix location (engine creation) |
| `backend/core/config.py` | Settings definition + validation |
| `backend/tests/conftest.py` | Test fixture that masks the bug (sets `null`) |
| `README.md` | Documents `DB_POOL` env var (line 294) |
| `backend/alembic/env.py` | Uses `resolve_database_url()` — unaffected |

---

## 9. Definition of Done

- [ ] All acceptance criteria (AC-1 through AC-6) verified
- [ ] All test scenarios (TS-1 through TS-6) pass
- [ ] Code passes lint/type gates: `ruff check . && ruff format . && mypy .`
- [ ] Test coverage maintained (>80%)
- [ ] PM accepts the implementation

---

*Generated as part of agent-team workflow for issue #130*