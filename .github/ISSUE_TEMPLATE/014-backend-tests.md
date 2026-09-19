# Backend Tests

## Description
Write comprehensive unit and integration tests for all backend components to ensure correctness and prevent regressions.

## User Stories
- As a developer, I want a comprehensive test suite so that I can refactor with confidence
- As a developer, I want tests that validate API contract compliance so that I know the backend matches OpenAPI
- As a developer, I want tests that cover edge cases and error conditions so that the system is robust

## Acceptance Criteria
- [ ] Create `backend/tests/` directory with proper structure
- [ ] Create `conftest.py` with fixtures:
  - TestClient fixture for FastAPI app
  - Authenticated client fixture with valid token
  - Sample job and node data fixtures
  - Mock store fixtures for isolation testing
- [ ] Write tests for authentication module:
  - Token creation and validation
  - Password hashing and verification
  - Localhost bypass functionality
  - Dependency injection and error cases
- [ ] Write tests for in-memory store:
  - All CRUD operations for jobs and nodes
  - Filtering and pagination logic
  - Metrics generation and caching
  - Log generation and history
  - Thread safety under concurrent access
- [ ] Write tests for jobs router:
  - All endpoints with various input scenarios
  - Authentication requirements
  - Validation and error handling
  - Job creation from YAML
  - Job retry and cancel logic
  - Metrics and logs endpoints
  - WebSocket log streaming
- [ ] Write tests for nodes router:
  - Listing and detail endpoints
  - Authentication requirements
  - Error handling for missing nodes
- [ ] Write tests for main application:
  - Startup and shutdown events
  - Basic endpoints and health check
  - CORS configuration
  - OpenAPI schema validation
- [ ] Write integration tests that test full API flows
- [ ] Achieve >80% code coverage as per Definition of Done
- [ ] Configure pytest to run with coverage reporting
- [ ] Add test commands to Makefile or scripts if applicable

## Technical Notes
- Use pytest as testing framework
- Use pytest-asyncio for async tests
- Use httpx.TestClient for FastAPI testing (better than TestClient for async)
- Mock external dependencies where appropriate
- Test both positive and negative cases
- Test boundary conditions for all validations
- Test WebSocket connections and message flows
- Use factory-boy or similar for test data generation if needed
- Follow Arrange-Act-Assert pattern in tests
- Ensure tests are independent and can run in any order
- Configure coverage reporting to exclude test files
- Add test script to pyproject.toml: `pytest --cov=backend --cov-report=term-missing`