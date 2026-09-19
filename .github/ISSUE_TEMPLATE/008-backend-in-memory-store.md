# Backend In-Memory Store

## Description
Implement a thread-safe in-memory data store with seed data matching the frontend mock server behavior.

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
- [ ] Ensure all ID generation matches patterns (job-\d+ for jobs, string IDs for nodes)
- [ ] Implement metrics caching similar to mock server
- [ ] Implement log history storage and retrieval
- [ ] Add methods for WebSocket log streaming simulation
- [ ] Write unit tests for all store operations

## Technical Notes
- Use `asyncio.Lock()` or `asyncio.RLock()` for thread safety
- Seed data should be identical to mock-server.ts output
- Metrics generation algorithm should match the mock exactly
- Log generation should use same templates and stamping mechanism
- Consider using dataclasses or simple dicts for internal storage
- Ensure all methods are async where appropriate for FastAPI integration