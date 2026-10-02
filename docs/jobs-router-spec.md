# Scientific Home Cluster - Backend Jobs Router Specification

## Overview

**Purpose**: Implement all job-related API endpoints as defined in the OpenAPI specification.

**Scope**: This specification covers the creation of the jobs router with all endpoints for job lifecycle management including creation, listing, retrieval, deletion, retry, cancellation, metrics, and log streaming. It does not cover the underlying service layer or data storage implementations (those are covered in other issues).

## User Stories

- As a user, I can submit jobs via API so that I can automate job submission
- As a user, I can check job status via API so that I can monitor long-running computations
- As a user, I can retrieve job logs and metrics so that I can debug and optimize performance
- As a user, I can retry failed jobs and cancel running jobs so that I can manage job lifecycle
- As a user, I can delete jobs so that I can clean up completed or unwanted jobs

## Acceptance Criteria

### Router Creation
- [ ] Create `backend/backend/routers/jobs.py` implementing all endpoints:
  - GET /jobs - list jobs with filtering (status, node, search, limit, offset)
  - POST /jobs - create job from YAML (multipart/form-data with job.yaml field)
  - GET /jobs/{job_id} - get job details
  - DELETE /jobs/{job_id} - delete job
  - GET /jobs/{job_id}/metrics - get job metrics with summary
  - GET /jobs/{job_id}/logs - get job log history (HTTP fallback)
  - GET /jobs/{job_id}/logs/stream - WebSocket endpoint for real-time logs
  - POST /jobs/{job_id}/retry - retry failed or cancelled job
  - POST /jobs/{job_id}/cancel - cancel running or pending job

### Authentication
- [ ] All endpoints require authentication (Bearer token) except as noted in OpenAPI
- [ ] Use `get_current_token_payload` dependency for authentication
- [ ] Return 401 Unauthorized for missing/invalid token

### Error Handling
- [ ] Proper error handling with correct HTTP status codes:
  - 401 Unauthorized for missing/invalid token
  - 404 Not Found for missing jobs
  - 409 Conflict for invalid state transitions (retry/cancel)
  - 400 Bad Request for validation errors
  - 422 Unprocessable Entity for invalid YAML or request format
- [ ] Return error responses in standard format: `{ "detail": "error message" }`

### Job Creation
- [ ] Job creation accepts multipart/form-data with job.yaml field containing YAML-encoded JobSpec
- [ ] Use python-multipart for handling file uploads
- [ ] Use PyYAML for parsing job.yaml content
- [ ] Validate job YAML against JobSpec model from shared.schemas
- [ ] Implement proper job ID generation matching pattern ^job-\d+$
- [ ] Ensure all datetime fields are properly formatted ISO strings

### Job Listing and Filtering
- [ ] Implement filtering logic for status, node, and search parameters
- [ ] Implement pagination with limit and offset parameters (default limit=10, max=100)
- [ ] Support filtering by status (exact match)
- [ ] Support filtering by node_id (exact match)
- [ ] Support filtering by search (case-insensitive partial match on job name)
- [ ] Combine multiple filters with AND logic
- [ ] Return paginated results with total count

### Job Lifecycle Management
- [ ] Retry endpoint only allowed for jobs in FAILED or CANCELLED status
- [ ] Retry resets job to PENDING status, increments retry count, clears error and completion timestamps
- [ ] Cancel endpoint only allowed for jobs in PENDING or RUNNING status
- [ ] Cancel sets job status to CANCELLED and updates completion timestamp

### Metrics and Logs
- [ ] Metrics endpoint returns job GPU/CPU metrics with summary
- [ ] Logs endpoint returns job log history as array of strings
- [ ] WebSocket endpoint streams real-time logs with proper connection handling
- [ ] WebSocket implementation should mirror mock-server behavior
- [ ] WebSocket endpoint should support subprotocol for structured messages (log/status)

> **Note (issue #35):** metrics and logs are read from the database and are
> never invented. With `SEED_DEMO_DATA=false` (the default) an existing job
> with no stored samples returns `200` with empty arrays and an all-zero
> `summary`, an unknown job returns `404` `METRICS_NOT_FOUND` /
> `LOGS_NOT_FOUND`, and the log WebSocket closes with code `1008` rather than
> emitting generated lines. The items below that mention *generated* or
> *mock-server* behaviour only apply with `SEED_DEMO_DATA=true`, and are the
> spec as originally written rather than the intended production behaviour.

### WebSocket Log Streaming
- [ ] Immediately send connecting status upon connection
- [ ] After short delay, send open status and begin log streaming
- [ ] On disconnect or job completion/failure/cancellation, send closed status
- [ ] Log streaming should mimic mock-server behavior (**`SEED_DEMO_DATA=true` only**):
  - Start with initial log lines (job accepted, syncing, environment ready, starting command)
  - Then stream generated log lines using same templates
  - Use same timing mechanisms as mock server
- [ ] Handle job not found (404) and authentication errors (401) before WebSocket acceptance

### Documentation and Testing
- [ ] OpenAPI/Swagger documentation automatically generated at /docs and /redoc
- [ ] Include accurate descriptions, parameters, and response examples
- [ ] Mark authentication requirements clearly in documentation
- [ ] Write comprehensive unit tests for all endpoints and edge cases
- [ ] Test both positive and negative cases
- [ ] Test boundary conditions for all validations
- [ ] Test WebSocket connections and message flows

## Technical Notes

### Dependencies
- Use python-multipart for handling file uploads (add to requirements if not present)
- Use PyYAML for parsing job.yaml content (add to requirements if not present)
- Use shared.schemas.job_spec.JobSpec for validation
- Use shared.schemas.job_state.JobState for response models
- Use shared.schemas.job_metrics.JobMetrics for metrics responses
- Use shared.schemas.job_status.JobStatus for status validation

### Implementation Approach
- Follow existing code patterns from auth router
- Use async/await throughout for consistency with FastAPI
- Leverage the JobService for business logic layer
- Ensure proper error handling with try/catch blocks
- Validate all inputs before processing
- Use Pydantic models for request/response validation and serialization

### Router Structure
- Prefix all routes with `/jobs` (applied in main.py)
- Use appropriate HTTP methods and status codes
- Return Pydantic models for all successful responses
- Use Query and Path parameters for endpoint parameters
- Use Depends for authentication injection
- Use UploadFile and File for multipart form handling

### WebSocket Implementation
- Use FastAPI's WebSocket support
- Implement proper connection lifecycle management
- Use asyncio tasks for background log streaming
- Implement proper cleanup on disconnect or job completion
- Use callbacks or queues for communication between background task and WebSocket
- Handle exceptions gracefully to prevent connection crashes

## Integration Considerations

### Shared Modules Integration
- Import and use `shared.schemas` models for data validation and serialization
- Ensure job ID generation matches existing patterns in the store
- Ensure datetime formatting matches ISO 8601 standard
- Ensure error response format matches existing patterns in auth router

### Service Layer Integration
- The jobs router should delegate to JobService for all business logic
- JobService should handle interactions with the store (in-memory or persistent)
- Router should focus on HTTP concerns: request validation, response formatting, error handling

### Worker Agent Compatibility
- Ensure job state format matches what worker agents expect
- Use same field names and data types as defined in shared schemas
- Maintain backward compatibility where possible

## References

- [PROCESS.md](./PROCESS.md) - Development workflow
- [openapi.yaml](./openapi.yaml) - API contract that this implementation must satisfy
- [AGENTS.md](./AGENTS.md) - Agent instructions and project structure
- [shared-schemas-file-ops-spec.md](./shared-schemas-file-ops-spec.md) - Shared schemas specification
- [in-memory-store-spec.md](./in-memory-store-spec.md) - In-memory store specification
- [010-backend-jobs-router.md](./.github/ISSUE_TEMPLATE/010-backend-jobs-router.md) - Original issue template