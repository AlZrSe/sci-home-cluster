# Backend Authentication System

## Description
Implement secure authentication with bearer tokens, password hashing, and localhost bypass for development.

## User Stories
- As a user, I want to authenticate with a bearer token so that I can securely access the API
- As a developer, I want localhost bypass so that I can develop without managing tokens
- As an administrator, I want secure token storage so that credentials are not exposed

## Acceptance Criteria
- [ ] Create `backend/backend/auth/` directory
- [ ] Implement password hashing using passlib with bcrypt
- [ ] Implement JWT token creation and validation using python-jose
- [ ] Create security module with:
  - hash_password() function
  - verify_password() function
  - create_access_token() function
  - decode_token() function
  - validate_token_format() function
- [ ] Create auth dependencies for FastAPI:
  - get_current_token() dependency
  - require_auth() dependency
- [ ] Implement localhost bypass: auto-accept "localhost-no-auth" for localhost, 127.0.0.1, .local, .lovable.app
- [ ] Implement POST /auth/validate endpoint that returns { valid: boolean }
- [ ] Token should be loaded from environment variable or auto-generated on first run
- [ ] Secret key should be configurable via environment
- [ ] Token expiration and refresh mechanism (optional but recommended)
- [ ] Write unit tests for all auth functions and dependencies

## Technical Notes
- Use HS256 algorithm for JWT tokens
- Token minimum length: 8 characters (as per OpenAPI)
- Localhost bypass should check request host against allowed patterns
- Store token in environment variable BACKEND_TOKEN or generate and save to file
- Follow OWASP recommendations for token security
- Ensure proper error handling for invalid/expired tokens
- Return appropriate HTTP status codes (401 for auth failures)