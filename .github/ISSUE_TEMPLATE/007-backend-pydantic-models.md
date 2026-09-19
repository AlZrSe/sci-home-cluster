# Backend Pydantic Models

## Description
Implement Pydantic models that match the OpenAPI 3.1.0 schemas defined in openapi.yaml for all API request/response bodies.

## User Stories
- As a developer, I want type-safe models that match the API contract so that I can ensure data validity
- As a developer, I want models that can be used for both request validation and response serialization

## Acceptance Criteria
- [ ] Create `backend/backend/models/` directory
- [ ] Implement all schemas from openapi.yaml components/schemas:
  - JobStatus enum
  - JobSpec model
  - JobState model
  - JobListResult model
  - JobQuery model
  - NodeSpec model
  - GPUMetric model
  - CPUMetric model
  - JobMetrics model
  - TokenValidationRequest model
  - TokenValidationResponse model
  - ErrorResponse model
- [ ] Ensure all field validations match OpenAPI (patterns, min/max, etc.)
- [ ] Use proper Pydantic v2 syntax and field definitions
- [ ] Add docstrings and comments where appropriate
- [ ] Verify models can serialize/deserialize correctly
- [ ] Test that models reject invalid data appropriately

## Technical Notes
- Use Pydantic v2 with `BaseModel`
- Use `Field()` for constraints when needed
- Use `model_validator` for complex validations if needed
- Ensure datetime fields use proper string format handling
- Follow existing code style conventions