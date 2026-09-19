# Backend Main Application and Integration

## Description
Create the main FastAPI application, configure middleware, include routers, and set up application lifespan events.

## User Stories
- As a developer, I want a properly configured FastAPI app so that I can run and test the backend
- As a developer, I want automatic documentation so that I can easily explore and test the API
- As an operator, I want clean startup and shutdown so that resources are managed properly

## Acceptance Criteria
- [ ] Create `backend/backend/main.py` with FastAPI application instance
- [ ] Configure CORS middleware to allow frontend development
- [ ] Include all routers: auth, jobs, nodes
- [ ] Implement lifespan events for startup/shutdown tasks:
  - Startup: Initialize in-memory store, load seed data
  - Shutdown: Cleanup resources if needed
- [ ] Configure OpenAPI/Swagger documentation at /docs and /redoc
- [ ] Add custom exception handlers for consistent error responses
- [ ] Add request ID middleware for tracing (optional)
- [ ] Add basic health check endpoint at /health
- [ ] Root endpoint should return API information
- [ ] Write unit tests for application startup and basic endpoints
- [ ] Ensure all routes are properly prefixed with /api/v1 as per OpenAPI servers
- [ ] Verify that the API server starts correctly and serves documentation

## Technical Notes
- Use FastAPI v0.100+ features
- Lifespan events using `asynccontextmanager` or `Startup/Shutdown` dependencies
- Configure CORS to allow localhost origins for development
- OpenAPI metadata should match info from openapi.yaml
- Consider adding version information from package metadata
- Health check should return 200 OK with minimal payload
- Exception handlers should return ErrorResponse model format
- Ensure proper async/await usage in lifespan functions
- Test that docs are accessible at http://localhost:8000/docs when running