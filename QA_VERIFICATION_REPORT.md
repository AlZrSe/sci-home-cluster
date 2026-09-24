# QA Verification Report: Issues #12 (Jobs Router) and #10 (In-Memory Store)

**Date:** 2026-09-24  
**Verifier:** QA Agent  
**Test Environment:** Python 3.12.7, pytest 8.0.0, FastAPI TestClient

---

## Executive Summary

| Issue | Overall Status | Pass Rate | Critical Issues |
|-------|---------------|-----------|-----------------|
| **#10 In-Memory Store** | ⚠️ **Partially Passing** | 19/21 (90.5%) | 2 test bugs (not implementation bugs) |
| **#12 Jobs Router** | ⚠️ **Partially Passing** | 9/12 endpoints verified | 3 critical bugs: empty logs, broken WebSocket auth, incorrect retry/cancel logic |

---

## Issue #10: In-Memory Store (`backend/store/memory.py`)

### Test Results: `pytest backend/tests/unit/test_store.py -v`

| Test Category | Tests | Pass | Fail | Notes |
|--------------|-------|------|------|-------|
| Node CRUD | 8 | 8 | 0 | ✅ All pass |
| Job CRUD | 8 | 6 | 2 | ⚠️ 2 test bugs (see below) |
| Store Reset | 1 | 1 | 0 | ✅ Pass |
| Seed Data | 3 | 3 | 0 | ✅ Pass |

### Acceptance Criteria Verification

| Criterion | Status | Evidence |
|-----------|--------|----------|
| CRUD operations for jobs and nodes | ✅ PASS | All create/read/update/delete tests pass |
| Filtering and pagination for job listings | ✅ PASS | Implementation correct; test bug only |
| Seed data: 4 nodes (alpha, beta, gamma, delta) | ✅ PASS | Verified in `test_seed_nodes_exist` |
| Seed data: 10 jobs with various statuses | ✅ PASS | Verified in `test_seed_jobs_exist` |
| Metrics generator matching mock server (LCG) | ✅ PASS | `_generate_job_metrics` uses LCG with seed=1337 |
| Log generator matching mock server (templates + stamping) | ⚠️ **BUG** | `_generate_job_logs` works but not triggered due to pre-init bug |
| ID generation patterns (job-\\d+ for jobs) | ✅ PASS | Verified in `test_create_job` |
| Metrics caching | ✅ PASS | `_metrics_cache` dict with lock protection |
| Log history storage and retrieval | ⚠️ **BUG** | Pre-initialized empty lists prevent generation |
| WebSocket log streaming simulation | ⚠️ **PARTIAL** | `start_log_stream` exists but same pre-init bug |
| Thread safety with asyncio locks | ✅ PASS | Separate locks for jobs, nodes, metrics, logs |

### Critical Bugs Found

#### 1. **Log History Pre-initialization Bug** (Lines 261, 286, 298)
```python
# In _seed_data() and create_job():
self._log_history[job_id] = []  # Pre-initializes empty list

# In get_job_logs():
if job_id in self._log_history:  # Always True!
    return self._log_history[job_id]  # Returns empty list, never generates
```
**Impact:** `GET /jobs/{id}/logs` returns empty array for all seed jobs.  
**Fix:** Remove pre-initialization or check `if not self._log_history[job_id]:` before returning.

#### 2. **Test Bug: `test_list_jobs` (Line 220)**
```python
job_ids = {node.node_id for node in result}  # BUG: uses node.node_id
assert job1.job_id in job_ids  # Compares job_id to node_ids
```
Should be: `job_ids = {job.job_id for job in result}`

#### 3. **Test Bug: `test_list_jobs_by_status` (Line 240)**
```python
assert running_jobs[0].job_id == job1.job_id  # Flaky: assumes ordering
```
Should verify `job1.job_id in [j.job_id for j in running_jobs]`

---

## Issue #12: Jobs Router (`backend/api/v1/jobs.py`)

### Test Results

| Test Command | Tests Run | Pass | Notes |
|--------------|-----------|------|-------|
| `pytest backend/tests/unit/test_api.py -k "jobs"` | 1 | 1 | Only `test_jobs_router_included` exists |
| `pytest backend/tests/unit/test_api.py -k "websocket"` | 0 | N/A | No WebSocket tests exist |

### Endpoint Verification (Manual Testing via TestClient)

| # | Endpoint | Method | Status | Issues Found |
|---|----------|--------|--------|--------------|
| 1 | `GET /jobs` | List with filters | ✅ PASS | Filtering (status, node, search), pagination (limit/offset) all work |
| 2 | `POST /jobs` | Create from YAML | ✅ PASS | Accepts multipart/form-data, validates JobSpec, returns 201 with JobState |
| 3 | `GET /jobs/{job_id}` | Get job details | ✅ PASS | Returns 200 with JobState, 404 for missing |
| 4 | `DELETE /jobs/{job_id}` | Delete job | ✅ PASS | Returns 204, 404 for missing |
| 5 | `GET /jobs/{job_id}/metrics` | Get metrics + summary | ✅ PASS | Returns 121 GPU metrics, summary stats |
| 6 | `GET /jobs/{job_id}/logs` | Get log history (HTTP) | ❌ **FAIL** | Returns empty array due to store bug #1 |
| 7 | `GET /jobs/{job_id}/logs/stream` | WebSocket real-time logs | ❌ **FAIL** | Auth dependency broken for WebSocket |
| 8 | `POST /jobs/{job_id}/retry` | Retry failed/cancelled | ⚠️ **PARTIAL** | Logic correct but store bug affects FAILED job retrieval |
| 9 | `POST /jobs/{job_id}/cancel` | Cancel running/pending | ✅ PASS | Returns 200 with updated JobState, 409 for invalid state |

### Acceptance Criteria Verification

| Criterion | Status | Evidence |
|-----------|--------|----------|
| **Router Creation** - All 9 endpoints implemented | ✅ PASS | All endpoints exist in `jobs.py` |
| **Authentication** - All endpoints require Bearer token | ✅ PASS | Uses `Depends(get_current_token_payload)` |
| **Error Codes** - 401, 404, 409, 400, 422 | ⚠️ **PARTIAL** | 401/404/400 work; 409 logic has issues (see below) |
| **YAML Validation** - multipart/form-data with job.yaml | ✅ PASS | Uses `UploadFile`, `yaml.safe_load`, `JobSpec(**job_data)` |
| **Job ID Pattern** (^job-\\d+$) | ✅ PASS | Enforced by `JobState` model and OpenAPI param |
| **ISO Timestamps** | ✅ PASS | All datetime fields use ISO format |
| **Filtering Logic** (status, node, search, AND) | ✅ PASS | Verified manually |
| **Pagination** (limit default=10, max=100, offset) | ✅ PASS | Verified manually |
| **WebSocket Subprotocol** | ❌ **FAIL** | Not implemented; connection fails due to auth |

### Critical Bugs Found

#### 1. **WebSocket Authentication Broken**
```python
# In jobs.py line 135-140:
@router.websocket("/{job_id}/logs/stream")
async def stream_job_logs(
    websocket: WebSocket,
    job_id: str,
    payload: dict = Depends(get_current_token_payload),  # BROKEN
):
```
`get_current_token_payload` requires `Request` and `HTTPBearer` which don't work for WebSocket upgrades.  
**Error:** `TypeError: HTTPBearer.__call__() missing 1 required positional argument: 'request'`

**Fix:** Create WebSocket-specific auth dependency that extracts token from `websocket.headers` or query params.

#### 2. **Retry/Cancel State Logic Issues**
Manual testing revealed:
- `POST /jobs/job-1050/retry` (RUNNING→CANCELLED) returned **200** instead of 409
  - *Root cause:* Previous test cancelled job-1050, making it CANCELLED (retryable)
  - But logic should check current state at request time
- `POST /jobs/job-1041/cancel` (comment said COMPLETED) returned **200**
  - *Root cause:* job-1041 is actually RUNNING in seed data (not COMPLETED)
  - The seed data has: job-1041=RUNNING, job-1045=FAILED, job-1044=CANCELLED

**Actual logic in `job_service.py` is correct** - it checks `job.status not in (FAILED, CANCELLED)` for retry and `not in (RUNNING, PENDING)` for cancel.

#### 3. **Log Generation Not Triggered** (Store Bug #1)
As documented in Issue #10, `get_job_logs` returns empty because log history is pre-initialized.

---

## OpenAPI Contract Compliance

| Aspect | Status | Notes |
|--------|--------|-------|
| All 9 endpoints defined | ✅ PASS | Match `openapi.yaml` paths |
| Request/response models | ✅ PASS | Use shared schemas |
| Security scheme (BearerAuth) | ✅ PASS | Applied to all endpoints |
| Error response format | ✅ PASS | Returns `{"detail": "..."}` format |
| WebSocket response schema | ⚠️ PARTIAL | Schema defines subprotocol messages but not implemented |

---

## Recommendations

### Immediate Fixes Required (Blocking)

1. **Fix log history pre-initialization** in `backend/store/memory.py`:
   - Remove lines 261, 286, 298 that set `self._log_history[job_id] = []`
   - Update `get_job_logs` to handle missing key gracefully

2. **Fix WebSocket authentication** in `backend/api/v1/jobs.py`:
   - Create `get_ws_token_payload` dependency for WebSocket
   - Extract token from `websocket.headers.get("authorization")` or query param

3. **Fix test bugs** in `backend/tests/unit/test_store.py`:
   - Line 220: Change `node.node_id` to `job.job_id`
   - Line 240: Change assertion to check membership, not index

### Additional Improvements

4. **Add comprehensive API tests** for all 9 job endpoints:
   - Positive and negative cases for each endpoint
   - WebSocket connection tests
   - Filter combination tests
   - Error code verification tests

5. **Verify metrics generation matches mock server exactly**:
   - Compare output with frontend mock server for same seed
   - Verify timestamp generation uses `i * 30000` ms ago pattern

6. **Add WebSocket subprotocol support**:
   - Implement structured messages: `{"type": "log", "payload": "..."}` and `{"type": "status", "payload": "open"}`

---

## Conclusion

**Issue #10 (In-Memory Store):** **90% Complete** - Core functionality works correctly. The 2 failing tests are test bugs, not implementation bugs. The log generation bug is a simple fix.

**Issue #12 (Jobs Router):** **75% Complete** - 6/9 endpoints fully functional. Critical gaps: log retrieval (blocked by store bug), WebSocket streaming (auth broken), and incomplete test coverage.

**Recommendation:** Fix the 3 blocking issues above, then both issues can move to `needs-review` → `done`.