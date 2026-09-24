# Nodes Router Specification

## Overview

This document specifies the implementation of the Nodes Router for the Scientific Home Cluster backend API. The router provides endpoints for listing and retrieving cluster node information, following the API contract defined in `openapi.yaml`.

## API Contract Reference

**Source**: `openapi.yaml` - paths `/nodes` and `/nodes/{id}`

### Endpoints

| Endpoint | Method | Operation ID | Description |
|----------|--------|--------------|-------------|
| `/api/v1/nodes` | GET | `listNodes` | List all cluster nodes |
| `/api/v1/nodes/{id}` | GET | `getNode` | Get node details |

### Authentication

- **Scheme**: Bearer token (`Authorization: Bearer <token>`)
- **Localhost bypass**: On `localhost`, `127.0.0.1`, `.local`, `.lovable.app` → token optional, auto-set to `"localhost-no-auth"`
- **Token validation**: Returns 401 Unauthorized for missing/invalid token

### Response Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 401 | Unauthorized (missing/invalid token) |
| 404 | Not Found (node not found for GET /nodes/{id}) |

---

## Data Models

### NodeSpec (from `openapi.yaml` components/schemas/NodeSpec)

```python
class GPUInfo(BaseModel):
    name: str
    memory_gb: int

class NodeSpec(BaseModel):
    node_id: str
    hostname: str
    gpus: List[GPUInfo]
    cpus: int  # >= 1
    memory_gb: int  # >= 1
    os: str
    status: Literal["ONLINE", "OFFLINE"]
    last_heartbeat: datetime  # ISO 8601 format
    current_job_id: Optional[str] = None
```

**Required fields**: `node_id`, `hostname`, `gpus`, `cpus`, `memory_gb`, `os`, `status`, `last_heartbeat`

**Enum values for status**: `ONLINE`, `OFFLINE`

---

## Implementation Requirements

### 1. GET /nodes - List All Nodes

**File**: `backend/api/v1/nodes.py`

**Behavior**:
- Returns array of `NodeSpec` objects
- No pagination (per OpenAPI spec - returns all nodes)
- Requires authentication
- Data sourced from in-memory store (`InMemoryStore.list_nodes()`)

**Response Example**:
```json
[
  {
    "node_id": "node-alpha",
    "hostname": "alpha.lan",
    "gpus": [{"name": "NVIDIA RTX 4090", "memory_gb": 24}],
    "cpus": 16,
    "memory_gb": 64,
    "os": "Ubuntu 24.04",
    "status": "ONLINE",
    "last_heartbeat": "2026-09-19T11:59:56",
    "current_job_id": "job-1041"
  },
  {
    "node_id": "node-delta",
    "hostname": "delta.lan",
    "gpus": [],
    "cpus": 8,
    "memory_gb": 32,
    "os": "Debian 12",
    "status": "OFFLINE",
    "last_heartbeat": "2026-09-18T10:46:40",
    "current_job_id": null
  }
]
```

### 2. GET /nodes/{id} - Get Node Details

**File**: `backend/api/v1/nodes.py`

**Behavior**:
- Returns single `NodeSpec` object
- Requires authentication
- Returns 404 if node not found
- Data sourced from in-memory store (`InMemoryStore.get_node(node_id)`)

**Path Parameter**:
- `id` (string): Node ID (e.g., `node-alpha`)

**Response Example** (200):
```json
{
  "node_id": "node-beta",
  "hostname": "beta.lan",
  "gpus": [
    {"name": "NVIDIA RTX 3090", "memory_gb": 24},
    {"name": "NVIDIA RTX 3090", "memory_gb": 24}
  ],
  "cpus": 24,
  "memory_gb": 128,
  "os": "Ubuntu 22.04",
  "status": "ONLINE",
  "last_heartbeat": "2026-09-19T11:59:49",
  "current_job_id": "job-1039"
}
```

**Error Response** (404):
```json
{
  "status": 404,
  "title": "Not Found",
  "detail": "Node node-unknown not found",
  "instance": "/api/v1/nodes/node-unknown"
}
```

---

## Architecture & Code Patterns

### Existing Files to Leverage

| File | Purpose |
|------|---------|
| `backend/api/v1/nodes.py` | Router endpoints (already exists with stubs) |
| `backend/services/node_service.py` | Service layer (already exists with stubs) |
| `backend/store/memory.py` | In-memory store with `list_nodes()`, `get_node()`, `update_node()` |
| `backend/models/node_spec.py` | Pydantic models (`NodeSpec`, `GPUInfo`) |
| `backend/core/deps.py` | Auth dependency `get_current_token_payload` |
| `backend/routers/jobs.py` | Reference implementation pattern |

### Implementation Pattern (from jobs router)

```python
@router.get("/{node_id}", response_model=NodeSpec)
async def get_node(
    node_id: str,
    payload: dict = Depends(get_current_token_payload)
):
    """
    Get node details.
    """
    node_service = NodeService()
    node = await node_service.get_node(node_id)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node {node_id} not found"
        )
    return node
```

---

## Acceptance Criteria

### Functional Requirements

| ID | Criterion | Test Case |
|----|-----------|-----------|
| **AC-01** | GET /nodes returns 200 with array of NodeSpec objects | `GET /api/v1/nodes` → 200, array length >= 4 (seed nodes) |
| **AC-02** | GET /nodes requires authentication | `GET /api/v1/nodes` without token → 401 (except localhost) |
| **AC-03** | GET /nodes/{id} returns 200 with NodeSpec for valid node | `GET /api/v1/nodes/node-alpha` → 200, matches seed data |
| **AC-04** | GET /nodes/{id} returns 404 for non-existent node | `GET /api/v1/nodes/unknown` → 404, ErrorResponse format |
| **AC-05** | GET /nodes/{id} requires authentication | `GET /api/v1/nodes/node-alpha` without token → 401 |
| **AC-06** | Response includes all NodeSpec fields | Verify all 9 fields present in response |
| **AC-07** | GPU array properly structured with name + memory_gb | Each GPU object has `name` (string) and `memory_gb` (int) |
| **AC-08** | Status enum is ONLINE or OFFLINE | Validate status value in response |
| **AC-09** | Timestamps are ISO 8601 format | `last_heartbeat` matches `datetime` format |
| **AC-10** | current_job_id is string or null | Validate type and nullability |

### Data Integrity Requirements

| ID | Criterion | Test Case |
|----|-----------|-----------|
| **AC-11** | Seed nodes match mock server data | Verify 4 seed nodes: node-alpha, node-beta, node-gamma, node-delta |
| **AC-12** | Node specs match frontend expectations | Compare with `src/lib/types.ts` NodeSpec type |
| **AC-13** | GPU info matches expected hardware | node-alpha: 1x RTX 4090 24GB; node-beta: 2x RTX 3090 24GB; node-gamma: 1x M3 Max 36GB; node-delta: 0 GPUs |

### Error Handling Requirements

| ID | Criterion | Test Case |
|----|-----------|-----------|
| **AC-14** | 401 response matches ErrorResponse schema | Verify `status`, `title`, `detail`, `instance` fields |
| **AC-15** | 404 response matches ErrorResponse schema | Verify `status`, `title`, `detail`, `instance` fields |
| **AC-16** | Localhost bypass works | Request from localhost without token → 200 |

### Testing Requirements

| ID | Criterion | Test Case |
|----|-----------|-----------|
| **AC-17** | Unit tests for list_nodes endpoint | Test success, auth, response structure |
| **AC-18** | Unit tests for get_node endpoint | Test success, 404, auth, response structure |
| **AC-19** | Integration tests with TestClient | End-to-end API calls |
| **AC-20** | Edge cases covered | Empty gpus array, OFFLINE status, null current_job_id |

---

## Test Scenarios

### Test Case: TC-NODES-001 - List Nodes Success
**Given**: Backend is running with seed data
**When**: `GET /api/v1/nodes` with valid auth (or localhost)
**Then**: 
- Status 200
- Response is array with ≥4 nodes
- Each node has all required fields
- At least one node has status "ONLINE"
- At least one node has status "OFFLINE"

### Test Case: TC-NODES-002 - List Nodes Unauthorized
**Given**: Backend is running, not localhost
**When**: `GET /api/v1/nodes` without Authorization header
**Then**: 
- Status 401
- Response matches ErrorResponse schema
- Detail: "Not authenticated" or "Could not validate credentials"

### Test Case: TC-NODES-003 - Get Node Success
**Given**: Backend is running with seed data
**When**: `GET /api/v1/nodes/node-beta` with valid auth
**Then**: 
- Status 200
- Response matches NodeSpec
- `node_id` = "node-beta"
- `gpus` array length = 2
- `status` = "ONLINE"
- `current_job_id` = "job-1039"

### Test Case: TC-NODES-004 - Get Node Not Found
**Given**: Backend is running
**When**: `GET /api/v1/nodes/non-existent-node` with valid auth
**Then**: 
- Status 404
- Response matches ErrorResponse schema
- Detail contains "not found"

### Test Case: TC-NODES-005 - Get Node Unauthorized
**Given**: Backend is running, not localhost
**When**: `GET /api/v1/nodes/node-alpha` without Authorization header
**Then**: 
- Status 401
- Response matches ErrorResponse schema

### Test Case: TC-NODES-006 - Node Data Structure Validation
**Given**: Backend is running with seed data
**When**: `GET /api/v1/nodes`
**Then**: 
- `node-gamma` has GPU with name "Apple M3 Max (MPS)" and memory_gb = 36
- `node-delta` has `gpus` = [] (empty array)
- `node-delta` has `status` = "OFFLINE"
- `node-delta` has `current_job_id` = null
- All `last_heartbeat` values are valid ISO 8601 datetime strings

### Test Case: TC-NODES-007 - Localhost Bypass
**Given**: Backend running on localhost:8000
**When**: `GET /api/v1/nodes` without Authorization header from localhost
**Then**: 
- Status 200
- Returns node list (auth bypassed)

---

## Implementation Checklist

### Code Changes Required

- [ ] **backend/api/v1/nodes.py**: Complete implementation of `list_nodes` and `get_node` endpoints
  - [ ] Import `HTTPException`, `status` from fastapi
  - [ ] Implement `list_nodes` with proper error handling
  - [ ] Implement `get_node` with 404 handling
  - [ ] Follow jobs router pattern exactly

- [ ] **backend/services/node_service.py**: Already has stubs, verify they work with store
  - [ ] `list_nodes()` → calls `store.list_nodes()`
  - [ ] `get_node(node_id)` → calls `store.get_node(node_id)`
  - [ ] No changes needed if store methods work

- [ ] **backend/tests/unit/test_api.py**: Add integration tests
  - [ ] `test_nodes_list_success()`
  - [ ] `test_nodes_list_unauthorized()`
  - [ ] `test_nodes_get_success()`
  - [ ] `test_nodes_get_not_found()`
  - [ ] `test_nodes_get_unauthorized()`

- [ ] **backend/tests/unit/test_store.py**: Add node-specific tests (already partially covered)
  - [ ] Verify seed node data matches expectations
  - [ ] Test `list_nodes()` returns all seed nodes
  - [ ] Test `get_node()` for each seed node

### Verification Steps

1. Run unit tests: `pytest backend/tests/unit/ -v`
2. Run integration tests: `pytest backend/tests/integration/ -v` (if any)
3. Manual test with `curl` or TestClient:
   ```bash
   # Localhost (no auth needed)
   curl http://localhost:8000/api/v1/nodes
   curl http://localhost:8000/api/v1/nodes/node-alpha
   
   # With auth (if testing remote)
   curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/nodes
   ```
4. Verify OpenAPI docs at `http://localhost:8000/api/v1/openapi.json`
5. Verify Swagger UI at `http://localhost:8000/docs`

---

## Frontend Integration Notes

From `docs/spec.md`:

- **Frontend route**: `/nodes` → `NodesPage` component
- **Service call**: `services.nodes.listNodes()`
- **Hook**: `useQuery(['nodes'])`
- **Polling**: 7000ms default (configurable)
- **Node detail route**: `/nodes/$nodeId` → `NodeDetailPage`
- **Service call**: `services.nodes.getNode(id)`

The frontend expects the exact `NodeSpec` structure from the API. Any deviation will cause TypeScript/runtime errors.

---

## Related Issues / Future Work

- **Issue #14**: Node registration/heartbeat endpoints (POST /nodes, PUT /nodes/{id}/heartbeat)
- **Issue #15**: Node metrics endpoint (GET /nodes/{id}/metrics)
- **WebSocket**: Real-time node status updates (not in current scope)

---

## Definition of Done

- [ ] All acceptance criteria (AC-01 through AC-20) verified
- [ ] Unit tests pass (>80% coverage for new code)
- [ ] No lint/type errors (`ruff check . && ruff format . && mypy .`)
- [ ] Integration tests pass
- [ ] PM acceptance (manual verification against spec)
- [ ] Issue label updated to `groomed`

---

*Document created during grooming of issue #13 "Backend Nodes Router"*
*Following agent-team workflow: PM → SWE → QA → PM acceptance*