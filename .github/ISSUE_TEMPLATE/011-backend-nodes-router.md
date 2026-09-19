# Backend Nodes Router

## Description
Implement node-related API endpoints for listing and retrieving cluster node information.

## User Stories
- As a user, I can list all cluster nodes so that I can see available resources
- As a user, I can get details about a specific node so that I can monitor its status and specs
- As an administrator, I can see which jobs are running on which nodes so that I can manage cluster load

## Acceptance Criteria
- [ ] Create `backend/backend/routers/nodes.py` implementing endpoints:
  - GET /nodes - list all nodes
  - GET /nodes/{node_id} - get node details
- [ ] Both endpoints require authentication (Bearer token)
- [ ] Proper error handling with correct HTTP status codes:
  - 401 Unauthorized for missing/invalid token
  - 404 Not Found for missing nodes
- [ ] Node data should include all fields from NodeSpec model:
  - node_id, hostname, gpus (array), cpus, memory_gb, os, status, last_heartbeat, current_job_id
- [ ] GPU information should be properly structured as array of objects with name and memory_gb
- [ ] Status should be either "ONLINE" or "OFFLINE" as per enum
- [ ] Timestamps should be properly formatted ISO strings
- [ ] Current job ID should reference existing job or be null/undefined
- [ ] Write unit tests for both endpoints including edge cases

## Technical Notes
- Data should come from the in-memory store
- Ensure proper serialization of nested objects (gpus array)
- Handle case sensitivity for status values
- Validate node_id parameter format (though OpenAPI doesn't specify pattern)
- Follow existing code patterns from jobs router
- Ensure consistent error response formatting