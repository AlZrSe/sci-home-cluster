# Syncthing Sync Service

## Description
Create a file system watcher that synchronizes YAML state files in the Syncthing folder with the SQLite database, ensuring the database reflects the source of truth.

## User Stories
- As a system operator, I want the database to reflect the current state of jobs so that I can monitor the system accurately
- As a developer, I want automatic synchronization so that I don't have to manually update the database
- As a user, I want reliable state tracking so that job status is always accurate
- As an administrator, I want to know when nodes go offline so that I can take corrective action

## Acceptance Criteria
- [ ] Watchdog observer monitoring /syncthing-shared/jobs/ and /syncthing-shared/nodes/ directories
- [ ] On startup: walk folders, read all state.yaml and node.yaml files → populate SQLite database
- [ ] On file change: debounced YAML parsing → update corresponding SQLite records
- [ ] Handle missing or corrupt YAML files gracefully (log error, skip file)
- [ ] Node heartbeat monitoring: mark node as OFFLINE after 90 seconds without heartbeat update
- [ ] Only update specific fields when YAML changes (don't overwrite unrelated data)
- [ ] Unit tests for sync logic and file parsing
- [ ] Integration tests: create YAML files → verify SQLite updated correctly
- [ ] No lint or type errors

## Technical Notes
- Use watchdog library with debouncing (e.g., 500ms delay) to avoid excessive updates
- Parse YAML files safely using yaml.safe_load
- Map YAML fields to SQLAlchemy model attributes
- Handle timezone-aware datetime objects properly
- Consider using SQLAlchemy's merge() or update() methods for efficient updates
- Log all sync operations for debugging
- Gracefully handle permission errors or missing directories