# Backend Jobs Router

## Description
Implement all job-related API endpoints as defined in the OpenAPI specification.

## User Stories
- As a user, I can submit jobs via API so that I can automate job submission
- As a user, I can check job status via API so that I can monitor long-running computations
- As a user, I can retrieve job logs and metrics so that I can debug and optimize performance
- As a user, I can retry failed jobs and cancel running jobs so that I can manage job lifecycle
- As a user, I can delete jobs so that I can clean up completed or unwanted jobs

## Acceptance Criteria
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
- [ ] All endpoints require authentication (Bearer token) except as noted
- [ ] Proper error handling with correct HTTP status codes:
  - 401 Unauthorized for missing/invalid token
  - 404 Not Found for missing jobs
  - 409 Conflict for invalid state transitions (retry/cancel)
  - 400 Bad Request for validation errors
  - 422 Unprocessable Entity for invalid YAML or request format
- [ ] Job creation accepts multipart/form-data with job.yaml field containing YAML-encoded JobSpec
- [ ] Implement proper job ID generation matching pattern ^job-\d+$
- [ ] Ensure all datetime fields are properly formatted ISO strings
- [ ] Implement filtering logic for status, node, and search parameters
- [ ] Implement pagination with limit and offset parameters
- [ ] WebSocket endpoint should support subprotocol for structured messages (log/status)
- [ ] Write comprehensive unit tests for all endpoints and edge cases

## Technical Notes
- Use python-multipart for handling file uploads
- Use PyYAML for parsing job.yaml content
- Validate job YAML against JobSpec model
- WebSocket implementation should mirror mock-server behavior
- Consider background tasks for job state updates (though not implemented in this phase)
- Follow existing error response format from OpenAPI
- Ensure proper async/await usage throughout