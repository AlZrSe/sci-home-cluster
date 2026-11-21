# Specification: Automatic Rescan of Nodes in Syncthing Folder

**Issue**: #178 — Automatic rescan of nodes in Syncthing folder
**Type**: Enhancement
**Status**: Groomed
**Target**: Phase 1 — Backend Foundation

---

## 1. User Stories

| ID | Story |
|----|-------|
| US-1 | As a cluster operator, I want nodes to be automatically discovered and registered when their YAML files appear in the Syncthing `nodes/` folder, so that I don't need to manually invoke an API to register nodes. |
| US-2 | As a cluster operator, I want nodes to be automatically removed from the cluster when their YAML files are deleted from the Syncthing `nodes/` folder, so that decommissioned nodes don't linger in the dashboard. |
| US-3 | As a cluster operator, I want node information to be automatically updated when the YAML file is modified (e.g., GPU changes, status updates), so that the dashboard always reflects the current hardware state. |
| US-4 | As a cluster operator, I want the system to recover from missed file system events (e.g., watcher restart, Syncthing delay) by periodically rescanning the `nodes/` folder, so that no node registration is permanently lost. |
| US-5 | As a developer, I want the node registration to work purely through the Syncthing folder (file-based) with no HTTP API dependency, so that the architecture remains consistent with the file-only transport decision (issue #92). |

---

## 2. Acceptance Criteria

### AC-1: Startup Rescan (Already Implemented — Verify)
**Given** the Syncthing service starts  
**When** the `nodes/` directory contains `node-*.yaml` files  
**Then** each file is parsed as a `NodeSpec` and the node is created or updated in the store  
**And** nodes that exist in the store but have no corresponding YAML file are **not** deleted (deletion only happens on explicit file deletion event)

### AC-2: File Creation Event
**Given** the Syncthing service is running  
**When** a new `nodes/node-XX.yaml` file appears (created or moved in)  
**Then** the file is parsed within 500 ms (debounce)  
**And** the node is created in the store if new, or updated if it exists  
**And** the node's `status` from the file is respected (agent writes `ONLINE`; heartbeat monitor owns `OFFLINE` transition)

### AC-3: File Modification Event
**Given** a node YAML file exists and the service is running  
**When** the file is modified (e.g., agent writes updated heartbeat, GPU list changes)  
**Then** the file is re-parsed within 500 ms (debounce)  
**And** the node in the store is updated with all fields from the new YAML  
**And** no duplicate node entry is created

### AC-4: File Deletion Event
**Given** a node YAML file exists and the service is running  
**When** the file is deleted  
**Then** the corresponding node is deleted from the store within 500 ms  
**And** any jobs assigned to that node are handled by the existing orphaned-job recovery logic

### AC-5: Periodic Full Rescan
**Given** the Syncthing service is running  
**When** 60 seconds have elapsed since the last periodic rescan (configurable)  
**Then** a full scan of the `nodes/` directory is performed  
**And** any files not previously processed are handled per AC-2  
**And** any files previously processed but now missing are handled per AC-4  
**And** the rescan is idempotent: processing a file twice produces the same store state

### AC-6: Idempotency & Error Handling
**Given** any file event or periodic rescan  
**When** a YAML file is malformed, missing required fields, or fails validation  
**Then** the error is logged at `WARNING` level with the file path and error details  
**And** the file is **not** added to `_processed_files` so it will be retried on next rescan  
**And** other files in the same scan continue to be processed

### AC-7: No HTTP API Dependency
**Given** the system is running  
**When** a node agent starts and writes its YAML file  
**Then** the node appears in the cluster without any HTTP `POST /nodes/register` or similar call  
**And** the frontend node list (`GET /api/v1/nodes`) reflects the node solely via the file watcher path

### AC-8: Watcher Resilience
**Given** the file system watcher stops or restarts (e.g., observer thread dies, Syncthing folder remounts)  
**When** the watcher restarts (via existing `folder-watch` task logic)  
**Then** a full rescan is triggered automatically  
**And** no node registrations are lost

---

## 3. Test Scenarios

### 3.1 Unit Tests (`backend/tests/unit/test_syncthing_service_nodes.py`)

| Test Case | Description |
|-----------|-------------|
| `test_process_node_file_creates_new_node` | New YAML file → node created in store |
| `test_process_node_file_updates_existing_node` | Modified YAML file → node updated in store |
| `test_process_node_file_idempotent` | Processing same file twice → same store state |
| `test_process_node_file_malformed_yaml` | Invalid YAML → warning logged, not added to `_processed_files` |
| `test_process_node_file_validation_error` | Missing required field → warning logged, retried on rescan |
| `test_handle_node_deleted_removes_from_store` | Deletion event → node removed from store |
| `test_initial_scan_nodes_directory` | Startup scan processes all `nodes/*.yaml` files |
| `test_periodic_rescan_detects_new_files` | Periodic scan finds files added while watcher was down |
| `test_periodic_rescan_detects_deleted_files` | Periodic scan finds files deleted while watcher was down |
| `test_manual_scan_nodes` | `manual_scan()` processes nodes directory correctly |

### 3.2 Integration Tests (`backend/tests/integration/test_node_registration_flow.py`)

| Test Case | Description |
|-----------|-------------|
| `test_agent_write_detected_by_backend` | Agent writes `nodes/node-01.yaml` → backend picks it up via watcher |
| `test_agent_heartbeat_updates_node` | Agent rewrites file with new `last_heartbeat` → backend updates node |
| `test_agent_gpu_change_detected` | Agent rewrites file with different GPU list → backend updates `gpus` field |
| `test_node_file_deletion_removes_node` | Delete `nodes/node-01.yaml` → node removed from store and API |
| `test_watcher_restart_triggers_rescan` | Stop/start watcher → full rescan runs, no nodes lost |

### 3.3 E2E Tests (Manual / Compose)

| Test Case | Description |
|-----------|-------------|
| `test_multi_node_cluster_registration` | Start 2+ agents → all nodes appear in dashboard via file watcher only |
| `test_node_decommission` | Stop agent, delete its node file → node disappears from dashboard |
| `test_syncthing_delay_recovery` | Simulate Syncthing delay (write file, wait 10s, start backend) → node registered |

---

## 4. Technical Approach

### 4.1 Current State Analysis

The `SyncthingService` already implements:
- **Initial scan** (`_initial_scan`): Scans `nodes/` directory on startup, calls `_process_node_file` for each `*.yaml` file
- **File watcher** (`SyncthingEventHandler`): Handles `created`, `modified`, `moved`, `deleted` events for `nodes/*.yaml`
- **Node processing** (`_process_node_file`): Parses YAML → `NodeSpec` → `store.create_node` or `store.update_node`
- **Node deletion** (`_handle_node_deleted`): Calls `store.delete_node`
- **Manual scan** (`manual_scan`): Full rescan with deleted/new file detection
- **Processed files tracking** (`_processed_files`): Set of file paths already processed

**Gaps to address:**
1. **No periodic automatic rescan** — only manual scan exists
2. **Watcher restart doesn't trigger rescan** — `folder-watch` task in agent restarts watcher but backend doesn't rescan
3. **Error handling in `_process_node_file`** — catches `Exception`, logs error, but doesn't ensure retry (file stays in `_processed_files` on success only, but on error it's not added — this is actually correct; verify)
4. **Configuration** — rescan interval should be configurable

### 4.2 Changes Required

#### 4.2.1 Add Periodic Rescan Task to `SyncthingService`

```python
# In SyncthingService.__init__:
self._rescan_task: Optional[asyncio.Task] = None
self._rescan_interval_seconds: int = 60  # Configurable via settings

# In SyncthingService.start():
self._running = True
self._heartbeat_check_task = asyncio.create_task(self._heartbeat_monitor())
self._rescan_task = asyncio.create_task(self._periodic_rescan())  # NEW

# In SyncthingService.stop():
if self._rescan_task:
    self._rescan_task.cancel()
    try:
        await self._rescan_task
    except asyncio.CancelledError:
        pass

# New method:
async def _periodic_rescan(self):
    """Periodically rescan nodes/ and jobs/ directories for missed changes."""
    while self._running:
        try:
            await asyncio.sleep(self._rescan_interval_seconds)
            if not self._running:
                break
            logger.debug("Starting periodic rescan of Syncthing folder")
            await self.manual_scan()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic rescan: {e}")
```

#### 4.2.2 Make Rescan Interval Configurable

Add to `backend/core/config.py`:
```python
SYNCTHING_RESCAN_INTERVAL_SECONDS: int = Field(
    default=60,
    ge=10,
    le=3600,
    description="Interval in seconds for periodic full rescan of Syncthing folder"
)
```

Pass to `SyncthingService` constructor or read from settings in `_periodic_rescan`.

#### 4.2.3 Ensure Watcher Restart Triggers Rescan

The backend's `SyncthingService` doesn't currently have a "watcher restart" concept — the observer runs for the lifetime of the service. However, the **agent** has a `folder-watch` task that restarts the watcher. The backend should be resilient to:
- Observer thread dying (watchdog `Observer.is_alive()` becomes `False`)
- Syncthing folder remount (directory disappears/reappears)

**Option A**: Add observer health check to `_heartbeat_monitor_iteration` or a new task
**Option B**: Rely on periodic rescan to catch up (simpler, 60s max delay)

**Decision**: Option B — periodic rescan is sufficient. The 60s interval bounds the maximum time a missed event can go undetected. This is acceptable because:
- Agent writes node file every ~30s (heartbeat)
- Syncthing replication is typically <5s
- 60s rescan catches any gap

If stricter guarantees needed later, add observer health monitoring (separate issue).

#### 4.2.4 Verify Idempotency in `_process_node_file`

Current implementation:
```python
existing_node = await store.get_node(node_id)
if existing_node:
    await store.update_node(...)
else:
    await store.create_node(node_spec)
```

This is idempotent for create/update. **Verify** `store.create_node` raises on duplicate (it does: `ValueError` if node exists). The current code doesn't catch this — if a race creates the node between `get_node` and `create_node`, `create_node` raises. **Fix**: Catch `ValueError` and retry as update, or use upsert pattern.

**Proposed fix in `_process_node_file`:**
```python
try:
    await store.create_node(node_spec)
except ValueError as e:
    if "already exists" in str(e):
        await store.update_node(node_id, ...)  # Same fields
    else:
        raise
```

#### 4.2.5 Ensure Error Handling Doesn't Block Retry

Current code in `_process_node_file`:
```python
try:
    node_spec = read_yaml(...)
    # ... process ...
    self._processed_files.add(file_path)
except Exception as e:
    logger.error(f"Error processing node file {file_path}: {e}")
```

On error, `file_path` is **not** added to `_processed_files` — correct, it will be retried on next scan. **Verify** this behavior is preserved.

#### 4.2.6 Remove Any Stale API References

Search for and remove any references to `POST /nodes/register` or similar HTTP node registration endpoints. Based on code review:
- `backend/mcp/tools/nodes.py` has `nodes_create` tool (MCP, not HTTP API) — keep for testing
- No HTTP route for node registration exists in `backend/api/routes/` — confirmed
- Frontend spec mentions "Nodes appear after they register with POST /nodes/register" (open question #3 in spec.md) — this should be updated to reflect file-based registration

### 4.3 Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `SYNCTHING_RESCAN_INTERVAL_SECONDS` | 60 | Periodic full rescan interval |
| `HEARTBEAT_CHECK_INTERVAL_SECONDS` | 30 | Existing: heartbeat monitor interval |
| `HEARTBEAT_TIMEOUT_SECONDS` | 300 | Existing: node offline threshold |

The rescan interval (60s) is 2x the heartbeat interval (30s), ensuring at least 2 heartbeats per rescan cycle.

### 4.4 Files to Modify

| File | Changes |
|------|---------|
| `backend/services/syncthing_service.py` | Add `_periodic_rescan` task, start/stop in `start`/`stop`, use configurable interval |
| `backend/core/config.py` | Add `SYNCTHING_RESCAN_INTERVAL_SECONDS` setting |
| `backend/tests/unit/test_syncthing_service_nodes.py` | New unit tests for node rescan logic |
| `backend/tests/integration/test_node_registration_flow.py` | New integration tests for file-based node registration |
| `docs/spec.md` | Update "Open Questions" #3: node registration is file-based, not HTTP |

---

## 5. API Contract Impact

**No HTTP API changes.** Node registration remains file-based only.

**MCP Tools** (`backend/mcp/tools/nodes.py`): `nodes_create` remains for test seeding only.

**Frontend**: No changes needed — `GET /api/v1/nodes` already returns nodes from store.

---

## 6. Rollback Plan

If issues arise:
1. Disable periodic rescan by setting `SYNCTHING_RESCAN_INTERVAL_SECONDS=0` (add validator to allow 0 = disabled)
2. Revert `syncthing_service.py` to previous version (initial scan + watcher only)
3. The watcher-based approach alone is sufficient for normal operation; periodic rescan is a safety net

---

## 7. Definition of Done

- [ ] Periodic rescan task implemented and started/stopped with service
- [ ] Rescan interval configurable via `SYNCTHING_RESCAN_INTERVAL_SECONDS`
- [ ] Idempotency fix in `_process_node_file` (handle race on create)
- [ ] Unit tests pass (>80% coverage for new code)
- [ ] Integration tests pass (agent write → backend pickup)
- [ ] No backend lint/type errors (`ruff check . && ruff format --check . && mypy .` from repo root)
- [ ] Documentation updated (`docs/spec.md` open question #3)
- [ ] PM acceptance review

---

## 8. Notes

- The agent's `write_node_spec` (every ~30s with jitter) + Syncthing replication + backend watcher + periodic rescan creates a **defense in depth** registration pipeline
- No single point of failure: if watcher misses an event, periodic rescan catches it; if periodic rescan is delayed, next agent heartbeat rewrites the file triggering watcher
- The 60s rescan interval is a reasonable balance between promptness and resource usage; can be tuned via config
- This specification intentionally does **not** add observer health monitoring — that can be a separate enhancement if 60s bound proves insufficient in practice