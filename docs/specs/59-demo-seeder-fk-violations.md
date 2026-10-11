# Issue #59: Demo Seeder Foreign Key Violations

**Status: ✅ IMPLEMENTED — groomed, ready for SWE (work already complete)**

## Problem Statement

The demo data seeder in `backend/store/database_store.py::_seed_data()` violates foreign key constraints in two ways:

1. **Insertion order violation**: Nodes are inserted before jobs, but `NodeModel.current_job_id` has a foreign key to `JobModel.job_id`. When nodes are inserted with `current_job_id` values, those jobs don't exist yet.

2. **Invalid reference**: `node-beta` references `current_job_id: "job-1039"`, but the seeder only creates jobs `job-1050` through `job-1041` (10 jobs). Job `job-1039` is never created.

## Root Cause

In `_seed_data()` (lines 60-241):
- Lines 73-128: Insert 4 nodes with `current_job_id` values
- Lines 179-236: Insert 10 jobs with IDs `job-1050` down to `job-1041`

The circular foreign key relationship:
- `JobModel.node_id` → `NodeModel.node_id` (FK)
- `NodeModel.current_job_id` → `JobModel.job_id` (FK)

SQLite doesn't enforce FKs by default, so this doesn't raise errors but leaves the database in an inconsistent state.

## Solution

**Change the insertion order** and **fix the invalid reference**:

1. Insert jobs FIRST (with `node_id = None`)
2. Insert nodes SECOND (with valid `current_job_id` references)
3. Update jobs with their `node_id` references

**Fix node-beta's `current_job_id`**: Change from `"job-1039"` to `"job-1040"` (a job that actually gets created).

## Acceptance Criteria

| AC | Description |
|----|-------------|
| AC-1 | Seeder runs without foreign key constraint violations when FK enforcement is enabled (`PRAGMA foreign_keys=ON`) |
| AC-2 | All `NodeModel.current_job_id` values reference existing `JobModel.job_id` values |
| AC-3 | All `JobModel.node_id` values reference existing `NodeModel.node_id` values |
| AC-4 | `node-beta.current_job_id` references a job that exists in the seeded dataset |
| AC-5 | Existing tests continue to pass (no regression in demo data shape) |
| AC-6 | The 10 demo jobs and 4 demo nodes are still created with the same IDs and attributes |

## Implementation Plan

### Modified `_seed_data()` flow:

```python
async def _seed_data(self, session: AsyncSession) -> None:
    # 1. Check if already seeded (existing logic)
    
    # 2. Create JobModel objects FIRST (node_id=None initially)
    jobs_data = [...]  # 10 jobs with job_ids job-1050 through job-1041
    job_models = [JobModel(**job_dict, node_id=None) for job_dict in jobs_data]
    for job in job_models:
        session.add(job)
    await session.flush()  # Assign PKs, but don't commit yet
    
    # 3. Create NodeModel objects with VALID current_job_id references
    nodes_data = [
        {"node_id": "node-alpha", ..., "current_job_id": "job-1041"},
        {"node_id": "node-beta",  ..., "current_job_id": "job-1040"},  # FIXED: was job-1039
        {"node_id": "node-gamma", ..., "current_job_id": None},
        {"node_id": "node-delta", ..., "current_job_id": None},
    ]
    for node_dict in nodes_data:
        session.add(NodeModel(**node_dict))
    await session.flush()
    
    # 4. Update jobs with node_id references
    job_node_mapping = {
        "job-1050": "node-alpha",
        "job-1049": "node-beta",
        "job-1048": "node-gamma",
        # ... etc per existing random assignment logic
    }
    for job in job_models:
        if job.node_id is not None:  # Only for RUNNING jobs
            job.node_id = job_node_mapping.get(job.job_id)
    
    # 5. Commit once
    await session.commit()
    
    # 6. Update _job_counter (existing logic)
```

### Key Changes:

1. **Reorder insertion**: Jobs → flush → Nodes → flush → Update jobs → commit
2. **Fix node-beta**: `current_job_id: "job-1039"` → `"job-1040"`
3. **Use `session.flush()`** instead of multiple commits to maintain atomicity
4. **Preserve deterministic job-to-node assignment** using the same random seed logic

## Test Scenarios

| Scenario | Expected Result |
|----------|-----------------|
| Fresh DB with `SEED_DEMO_DATA=true` | 10 jobs, 4 nodes created; all FKs valid |
| Fresh DB with `SEED_DEMO_DATA=false` | Empty tables; no seeding |
| Enable FK enforcement (`PRAGMA foreign_keys=ON`) | No constraint violations |
| Query `node-beta.current_job_id` | Returns `"job-1040"` (valid job) |
| Query `job-1040.node_id` | Returns `"node-beta"` (bidirectional consistency) |
| Run existing test suite | All tests pass |

## Files to Modify

- `backend/store/database_store.py` - `_seed_data()` method only

## Out of Scope

- Frontend changes (submodule untouched)
- Schema changes (FKs are correct, only seeding order is wrong)
- Production data migration (demo data only)

## Implementation Status

✅ **COMPLETE** — The fix has been applied in `backend/store/database_store.py:_seed_data()`:
- Jobs inserted FIRST with `node_id=None` (lines 123-191)
- `await session.flush()` after jobs (line 193)
- Nodes inserted SECOND with valid `current_job_id` references (lines 196-252):
  - `node-alpha.current_job_id = "job-1041"` (was already correct)
  - `node-beta.current_job_id = "job-1049"` (was `"job-1039"` — **FIXED**; `job-1049` exists and is assigned to `node-beta`)
  - `node-gamma` and `node-delta` have `current_job_id = None`
- `await session.flush()` after nodes (line 254)
- Jobs updated with `node_id` references for RUNNING jobs (lines 257-259)
- Single `await session.commit()` (line 262)

**Note**: The spec predicted `node-beta` should reference `"job-1040"`, but the actual implementation correctly uses `"job-1049"` (which is the RUNNING job assigned to `node-beta` via the deterministic random seed). This maintains bidirectional consistency: `job-1049.node_id = "node-beta"` and `node-beta.current_job_id = "job-1049"`.

All acceptance criteria (AC-1 through AC-6) verified by test suite with FK enforcement enabled.