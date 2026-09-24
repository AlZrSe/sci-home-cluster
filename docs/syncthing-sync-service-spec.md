# Scientific Home Cluster — Syncthing Sync Service Specification

## Overview

**Purpose**: Create a file system watcher service that synchronizes YAML state files in the Syncthing folder with the SQLite database, ensuring the database reflects the source of truth.

**Scope**: This specification covers the creation of a background synchronization service that:
- Monitors the Syncthing shared folder for job and node state changes
- Watches for file creation, modification, and deletion events
- Parses YAML state files and updates the corresponding SQLite database records
- Implements debouncing to prevent excessive updates during rapid file changes
- Handles node heartbeat monitoring to detect offline nodes
- Provides graceful error handling for corrupt or missing files
- Logs synchronization activities for monitoring and debugging

It does not cover:
- The Syncthing application itself (assumed to be running externally)
- The API server implementation (issue #3)
- The worker agent implementation (covered in agent/README.md)
- Initial database population (handled by API server startup)

## User Stories

- As a system operator, I want the database to reflect the current state of jobs so that I can monitor the system accurately
- As a developer, I want automatic synchronization so that I don't have to manually update the database
- As a user, I want reliable state tracking so that job status is always accurate
- As an administrator, I want to know when nodes go offline so that I can take corrective action

## Acceptance Criteria

### File System Monitoring
- [ ] Implement Watchdog observer monitoring:
  - `/syncthing-shared/jobs/` directory for job state files
  - `/syncthing-shared/nodes/` directory for node state files
- [ ] On service startup:
  - Walk both directories to find existing state files
  - Read all `state.yaml` and `node.yaml` files
  - Parse YAML content and populate corresponding SQLite database records
- [ ] On file system changes:
  - Implement debounced processing (500ms delay) to avoid excessive updates during rapid changes
  - On file creation/modification: parse YAML and update/create database record
  - On file deletion: mark corresponding database record as inactive or remove based on policy
- [ ] Handle missing or corrupt YAML files gracefully:
  - Log error with file path and error details
  - Skip problematic file without crashing the service
  - Continue monitoring other files

### Data Synchronization
- [ ] Only update specific fields when YAML changes (don't overwrite unrelated data):
  - For jobs: update status, timestamps, exit_code, error, retry_count, node_id, etc.
  - For nodes: update status, last_heartbeat, current_job_id, etc.
  - Preserve manually set fields that aren't in the YAML (if any)
- [ ] Node heartbeat monitoring:
  - Mark node as OFFLINE after 90 seconds without heartbeat update
  - Reset to ONLINE when new heartbeat received
  - Consider making heartbeat timeout configurable
- [ ] Timestamp handling:
  - Properly parse ISO format datetime strings from YAML
  - Store as timezone-aware UTC datetime objects in database
  - Convert to local time only for display purposes

### Reliability and Error Handling
- [ ] Graceful error handling:
  - Continue operation despite individual file errors
  - Log all errors with sufficient context for debugging
  - Implement retry logic for transient database errors
  - Handle permission errors when accessing Syncthing folder
- [ ] Logging:
  - Log file creation, modification, and deletion events
  - Log successful database updates
  - Log errors with traceback information
  - Use standard Python logging module
- [ ] Shutdown handling:
  - Gracefully stop watching when service terminates
  - Wait for pending operations to complete
  - Clean up observer resources

### Testing & Quality
- [ ] Unit tests for sync logic:
  - Test YAML parsing and field mapping
  - Test debouncing behavior
  - Test error handling scenarios
  - Test heartbeat timeout logic
- [ ] Integration tests:
  - Create sample YAML files and verify database updates correctly
  - Simulate file changes and verify proper synchronization
  - Test startup scan of existing files
  - Test edge cases like missing fields, extra fields
- [ ] No lint errors (ruff check) and no type errors (mypy strict mode)
- [ ] Code follows project formatting standards (ruff format)

## Technical Notes

### Implementation Approach
- Use watchdog library with `Observer` and `EventHandler` classes
- Implement custom `FileSystemEventHandler` for YAML file events
- Apply debouncing using `time.time()` tracking or `threading.Timer`
- Use SQLAlchemy ORM for database operations
- Leverage existing shared modules for path handling and YAML parsing

### Event Handling Details
- Process only `.yaml` files (case-insensitive)
- Ignore temporary files (those starting with `.` or ending with `.tmp`)
- Use file paths to extract job_id or node_id:
  - Jobs: `{syncthing_root}/jobs/{job_id}/state.yaml`
  - Nodes: `{syncthing_root}/nodes/{node_id}.yaml`
- Validate extracted IDs against expected patterns:
  - Job IDs: match `^job-\d+$`
  - Node IDs: validate against node naming conventions

### Database Update Strategy
- Use SQLAlchemy's `merge()` method for efficient upserts:
  - Load existing record or create new one
  - Update only the fields present in the YAML
  - Preserve database-only fields (like internal IDs)
- For node heartbeat tracking:
  - Store last_heartbeat timestamp from YAML
  - Calculate offline status based on time threshold
  - Update status field in database accordingly

### Concurrency and Performance
- Debounce file system events to prevent thrashing:
  - Wait 500ms after last event before processing
  - Reset timer on each new event within the window
  - Process batch of changes when debounce period expires
- Use asynchronous database operations where possible
- Batch multiple file updates into single database transaction when feasible
- Limit memory usage by processing files individually rather than loading all

### Logging and Monitoring
- Use standard logging hierarchy:
  - `scientific_home_cluster.sync` as base logger
  - Different levels for different event types:
    - DEBUG: File system events received
    - INFO: Successful synchronization operations
    - WARNING: Recoverable errors (corrupt YAML, etc.)
    - ERROR: Critical failures (database connection issues)
- Include contextual information in logs:
  - File path involved
  - Job/node ID being processed
  - Type of operation (create, update, delete)
  - Success or failure status

## Dependencies

### Runtime Dependencies
- `watchdog`: ^4.0.0 (for file system monitoring)
- Existing dependencies:
  - `sqlalchemy`^2.0.0 (for database operations)
  - `pyyaml`^5.3.1 (for YAML parsing - already required)
  - `shared` module (for path utilities and schemas)

### Development Dependencies
- No additional development dependencies beyond what's already specified
- Existing pytest and related tools will be used for testing

## Integration Considerations

### API Server Integration
- The sync service should complement, not conflict with, the API server
- Both should be able to read/write the same database
- Consider implementing database-level locking or relying on atomic operations
- Sync service should handle cases where API server has already updated records

### Worker Agent Compatibility
- Worker agents write state files to Syncthing folder
- Sync service reads these files and updates database
- Ensures database reflects the most recent state from workers
- Handle timing differences between worker writes and sync reads

### Deployment and Operation
- Service should be designed to run as a long-lived background process
- Consider implementing as a systemd service or similar for production
- Provide clear startup/shutdown logging
- Handle SIGTERM and SIGINT signals gracefully
- Work correctly in containerized environments (Docker, etc.)

## Directory Structure

After implementation, the sync service will be located at:
```
scientific-home-cluster/
├── sync/                     # New directory for sync service
│   ├── __init__.py
│   ├── main.py              # Service entry point
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py        # Service configuration
│   │   └── database.py      # Database setup (shared with API server)
│   ├── handlers/
│   │   ├── __init__.py
│   │   └── file_handler.py  # Watchdog event handler
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── debouncer.py     # Debouncing utility
│   │   └── yaml_parser.py   # YAML parsing and validation
│   └── models/
│       ├── __init__.py
│       └── shared.py        # Import shared schemas or define local equivalents
```

## Implementation Notes

### Startup Procedure
1. Service initialization begins
2. Load configuration (Syncthing root path from environment variable)
3. Validate Syncthing folder structure exists
4. Initialize database connection (shared with API server)
5. Perform initial scan:
   - Walk jobs and nodes directories
   - Process all existing state files
   - Populate database with initial state
6. Start file system observer
7. Service enters main loop waiting for events

### Event Processing Flow
1. File system event received (created, modified, deleted)
2. Check if event is relevant (YAML file in correct location)
3. Extract job_id or node_id from file path
4. Apply debouncing: restart timer if another event received within 500ms
5. When debounce period expires:
   - For create/modify events:
     - Read and parse YAML file
     - Validate YAML structure
     - Update/create database record
   - For delete events:
     - Mark record as inactive or remove based on policy
6. Log operation result
7. Return to waiting state

### Heartbeat Monitoring
- Separate periodic check (e.g., every 30 seconds):
  - Query all nodes from database
  - For each node, check if last_heartbeat is older than threshold
  - If offline and currently marked ONLINE: update status to OFFLINE
  - If online and currently marked OFFLINE: update status to ONLINE
  - Log status changes
- Alternative: update status directly when heartbeat file is modified

### Error Recovery
- If database connection fails:
  - Log error and retry after backoff period
  - Continue processing file events in memory if possible
  - Flush pending updates when database recovers
- If Syncthing folder becomes inaccessible:
  - Log error and stop processing new events
  - Preserve last known state
  - Resume when folder becomes accessible again

## References

- [PROCESS.md](./PROCESS.md) - Development workflow
- [openapi.yaml](./openapi.yaml) - API contract
- [AGENTS.md](./AGENTS.md) - Agent instructions and project structure
- [shared-schemas-file-ops-spec.md](./shared-schemas-file-ops-spec.md) - Shared schemas specification
- [api-server-core-spec.md](./api-server-core-spec.md) - API server specification
- [in-memory-store-spec.md](./in-memory-store-spec.md) - In-memory store specification
- [004-syncthing-sync-service.md](./.github/ISSUE_TEMPLATE/004-syncthing-sync-service.md) - Original issue template

---
*Specification ready for grooming. Move issue #4 to `groomed` once this document is created and reviewed.*