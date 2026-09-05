# API Server Core

## Description
Create the FastAPI server with SQLite database, authentication, and core job/node management endpoints.

## User Stories
- As a user, I want to submit jobs via API so that I can automate job submission
- As a user, I want to check job status via API so that I can monitor long-running computations
- As an administrator, I want to register and monitor worker nodes so that I can manage cluster resources
- As a developer, I want secure API access so that only authorized users can submit jobs

## Acceptance Criteria
- [ ] FastAPI app with lifespan event for startup/shutdown tasks
- [ ] SQLAlchemy models: JobDB and NodeDB with appropriate indexes
- [ ] REST endpoints:
  - POST /jobs - create job (writes job.yaml + state.yaml to Syncthing)
  - GET /jobs - list jobs with filtering (status, user, node)
  - GET /jobs/{job_id} - get job details
  - GET /jobs/{job_id}/logs - stream logs via WebSocket
  - GET /jobs/{job_id}/metrics - get job metrics
  - POST /jobs/{job_id}/retry - retry failed job (manual confirmation)
  - POST /jobs/{job_id}/cancel - cancel running job
  - DELETE /jobs/{job_id} - delete job
  - GET /nodes - list nodes
  - GET /nodes/{node_id} - get node details
  - POST /nodes/register - register/update node
- [ ] Single Bearer token authentication (auto-generated on first run)
- [ ] SQLite database stored in Syncthing folder for persistence
- [ ] Proper error handling and validation
- [ ] Unit and integration tests for all endpoints (>80% coverage)
- [ ] OpenAPI/Swagger documentation available at /docs

## Technical Notes
- Use SQLAlchemy 2.0 style with async support
- Token should be stored in ~/.config/sci-run/token or environment variable
- Startup event should scan Syncthing folder and populate SQLite database
- Use Python-multipart for form data handling
- Consider using Pydantic models for request/response validation
- Implement proper HTTP status codes (200, 201, 400, 401, 404, 409, 500)