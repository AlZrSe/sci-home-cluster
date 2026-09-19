# Scientific Home Cluster — Backend In-Memory Store Specification

## Overview

**Purpose**: Implement a thread-safe in-memory data store for the Scientific Home Cluster backend that matches the behavior of the frontend mock server, providing seed data and enabling API functionality without a persistent database during development.

**Scope**: This specification covers the design and implementation of an `InMemoryStore` class that stores jobs, nodes, job metrics, and job logs in memory with asyncio-based thread safety. It includes seed data matching the mock server, CRUD operations, filtering and pagination, metrics caching, log history, and WebSocket log streaming simulation. It does not cover persistent storage (e.g., SQLite) which will be implemented in a separate issue.

## User Stories

- As a developer, I want a reliable data store so that the API can serve and manipulate data
- As a developer, I want seed data that matches the mock server so that frontend works without changes
- As a developer, I want thread-safe operations so that concurrent requests don't cause race conditions

## Acceptance Criteria

- [ ] Create `backend/backend/store/` directory
- [ ] Implement `InMemoryStore` class with asyncio locks for thread safety
- [ ] Implement CRUD operations for jobs and nodes
- [ ] Implement filtering and pagination for job listings (status, node, search, limit, offset)
- [ ] Port seed data from frontend/mock-server.ts:
  - 4 nodes (node-alpha, node-beta, node-gamma, node-delta)
  - 10 jobs with various statuses matching the mock data
  - Job metrics generator that produces realistic time-series data
  - Job log generator that produces realistic log lines
- [ ] Ensure all ID generation matches patterns (job-\\d+ for jobs, string IDs for nodes)
- [ ] Implement metrics caching similar to mock server
- [ ] Implement log history storage and retrieval
- [ ] Add methods for WebSocket log streaming simulation
- [ ] Write unit tests for all store operations

## Test Scenarios

### Unit Tests

1. **Store Initialization**
   - Verify that the store initializes with empty data structures
   - Verify that asyncio locks are properly initialized

2. **Seed Data Loading**
   - Verify that after initialization (or explicit seed loading), the store contains exactly 4 nodes with correct IDs and properties
   - Verify that the store contains exactly 10 jobs with correct IDs, statuses, and associations to nodes
   - Verify that job IDs follow the pattern `job-\\d+` and node IDs match the mock (node-alpha, etc.)

3. **Job CRUD Operations**
   - Create a new job and verify it is stored and retrievable by ID
   - Update an existing job's status and verify the change is persisted
   - Delete a job and verify it is no longer retrievable
   - Attempt to retrieve a non-existent job and verify it returns None/not found

4. **Node CRUD Operations**
   - Create a new node and verify it is stored and retrievable by ID
   - Update an existing node's status and heartbeat
   - Delete a node and verify it is no longer retrievable

5. **Job Listing with Filtering and Pagination**
   - List all jobs and verify the total count is correct (initially 10 from seed)
   - Filter jobs by status (e.g., RUNNING) and verify only matching jobs are returned
   - Filter jobs by node ID and verify only jobs assigned to that node are returned
   - Filter jobs by search term (matching job name) and verify correct subset
   - Verify pagination (limit and offset) returns correct subset and total count
   - Combine multiple filters and verify correct results

6. **Metrics Caching**
   - Verify that calling `get_job_metrics` for a job ID returns the same object on subsequent calls (caching)
   - Verify that the metrics data structure matches the expected format (GPUMetric, CPUMetric arrays, summary)
   - Verify that metrics for different jobs are cached separately

7. **Log History Storage and Retrieval**
   - Verify that calling `get_job_logs` for a job ID returns a list of strings
   - Verify that the log lines include expected prefixes (timestamps, log levels)
   - Verify that the log history is stored per job and can be retrieved multiple times
   - Verify that initial log lines are generated (job accepted, syncing input, environment ready, starting command)

8. **WebSocket Log Streaming Simulation**
   - Verify that the store provides a method to simulate log streaming (similar to mock's `subscribeLogs`)
   - Verify that the simulation can be started and stopped
   - Verify that new log lines are appended to the job's log history during streaming
   - Verify that the streaming respects the job's status (only emits lines while job is RUNNING)

9. **Thread Safety**
   - Verify that concurrent operations on the store do not cause data corruption or race conditions
   - (This may be tested with asyncio tasks that perform simultaneous reads/writes)

### Integration Tests

1. **API Integration**
   - Verify that the backend API endpoints correctly use the in-memory store
   - Verify that job creation via API results in the job being stored and retrievable
   - Verify that job listing API returns data from the store with correct filtering
   - Verify that node API endpoints return data from the store
   - Verify that job metrics and logs endpoints return data from the store

2. **Seed Data Consistency**
   - Verify that the seed data in the store matches exactly the output of the frontend mock server (for the same seed)
   - Verify that job metrics generation algorithm produces identical results to the mock
   - Verify that log generation uses the same templates and stamping mechanism

## Technical Notes

- Use `asyncio.Lock()` or `asyncio.RLock()` for thread safety on all public methods that modify state
- Seed data should be identical to mock-server.ts output (use the same seed value: 1337)
- Metrics generation algorithm should match the mock exactly (same loop, same calculations)
- Log generation should use same templates and stamping mechanism (ISO timestamp format without milliseconds)
- Consider using dataclasses or simple dicts for internal storage (but ensure compatibility with Pydantic models used in API)
- Ensure all methods are async where appropriate for FastAPI integration (but note that pure in-memory operations may not need to be async; however, we use async to simulate I/O and allow for locking)
- The store should be designed as a singleton or dependency-injected service that can be used by the service layer (job_service.py, node_service.py)
- ID generation for new jobs should follow the pattern `job-\\d+` with the next available number (based on existing jobs or a counter)
- Node IDs should be strings as provided in the seed data (node-alpha, etc.)
- The store should provide methods to clear/reset data for testing purposes

## Dependencies

- No additional runtime dependencies beyond what is already in the backend (asyncio is part of Python standard library)
- For testing: pytest, pytest-asyncio

## Implementation Notes

The store should be implemented in `backend/backend/store/__init__.py` and/or `backend/backend/store/memory.py`.

The service layer (job_service.py, node_service.py) should be updated to use the in-memory store instead of the current placeholder implementations.

Once the persistent store (SQLite) is implemented, the service layer can be switched to use the persistent store or a combination (e.g., write-through cache).

## References

- [PROCESS.md](./PROCESS.md) - Development workflow
- [openapi.yaml](./openapi.yaml) - API contract that the backend must implement
- [AGENTS.md](./AGENTS.md) - Agent instructions and project structure
- Frontend mock server: `frontend/src/lib/mock-server.ts`
- Issue #10: [Backend In-Memory Store](https://github.com/AlZrSe/sci-home-cluster/issues/10)

--- 
*Specification ready for grooming. Move issue #10 to `groomed` once this document is created and reviewed.*