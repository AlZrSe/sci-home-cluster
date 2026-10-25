# Specification: Interactive API Docs Disabled in Production (Issue #161)

## Overview

Disable Swagger UI (`/docs`) and ReDoc (`/redoc`) endpoints in production environments unless explicitly enabled via an environment flag (`ENABLE_API_DOCS`). These endpoints expose the full OpenAPI schema, parameter shapes, and example payloads, which aids reconnaissance and increases the attack surface for unauthenticated probing.

---

## User Stories

### US-1: Security Operator — Reduced Attack Surface in Production
> As a **security operator**, I want **interactive API documentation endpoints disabled by default in production** so that **the API schema and example payloads are not exposed to unauthenticated attackers**.

**Acceptance Criteria:**
- `/docs` (Swagger UI) returns 404 in production when `ENABLE_API_DOCS` is not set
- `/redoc` (ReDoc) returns 404 in production when `ENABLE_API_DOCS` is not set
- `/openapi.json` returns 404 in production when `ENABLE_API_DOCS` is not set
- No schema information is accessible without authentication in production

### US-2: Developer — Docs Enabled in Development
> As a **developer**, I want **interactive API documentation available during local development and testing** so that **I can explore and test the API without extra configuration**.

**Acceptance Criteria:**
- `/docs` returns Swagger UI (200) when `ENVIRONMENT=development` (default)
- `/redoc` returns ReDoc (200) when `ENVIRONMENT=development` (default)
- `/openapi.json` returns the OpenAPI schema (200) when `ENVIRONMENT=development` (default)
- No additional environment variables needed for local development

### US-3: Platform Operator — Explicit Opt-In for Production Docs
> As a **platform operator**, I want **an explicit environment flag to enable API docs in production** so that **I can consciously choose to expose them (e.g., for internal tooling) without accidentally doing so**.

**Acceptance Criteria:**
- `ENABLE_API_DOCS=true` enables `/docs`, `/redoc`, and `/openapi.json` in production
- `ENABLE_API_DOCS=false` (or unset) keeps them disabled in production
- Flag works independently of `ENVIRONMENT` setting
- Clear documentation of the security implications

### US-4: Test Engineer — Docs Available in Test Environment
> As a **test engineer**, I want **API docs available during automated testing** so that **contract tests and integration tests can verify the OpenAPI schema**.

**Acceptance Criteria:**
- Tests can enable docs via `ENABLE_API_DOCS=true` or `ENVIRONMENT=test`
- Existing test infrastructure continues to work without modification
- OpenAPI schema validation tests still pass

---

## Functional Requirements

### FR-1: Configuration Setting
- Add `ENABLE_API_DOCS: bool = False` to `Settings` in `backend/core/config.py`
- Environment variable: `ENABLE_API_DOCS` (accepts "true"/"false", case-insensitive)
- Default: `False` (disabled)

### FR-2: Environment-Aware FastAPI Initialization
- In `backend/main.py`, conditionally set `docs_url`, `redoc_url`, and `openapi_url` based on:
  - `settings.ENABLE_API_DOCS` (explicit override, highest priority)
  - `settings.ENVIRONMENT` (development/test → enabled, production → disabled)
- Logic:
  ```python
  enable_docs = settings.ENABLE_API_DOCS or settings.ENVIRONMENT in ("development", "test")
  docs_url = "/docs" if enable_docs else None
  redoc_url = "/redoc" if enable_docs else None
  openapi_url = f"{settings.API_V1_STR}/openapi.json" if enable_docs else None
  ```

### FR-3: Consistent 404 Behavior
- When disabled, accessing `/docs`, `/redoc`, or `/openapi.json` returns 404 (Not Found)
- No redirect, no error page, no information disclosure
- Behavior matches a route that was never registered

### FR-4: Startup Logging
- Log the docs state at startup (INFO level):
  - `API docs ENABLED (ENVIRONMENT=development)` or
  - `API docs ENABLED (ENABLE_API_DOCS=true)` or
  - `API docs DISABLED (production mode)`

---

## Non-Functional Requirements

### NFR-1: Security
- No schema information leaked when disabled
- No timing side-channels in the enable/disable logic
- `ENABLE_API_DOCS` must be explicitly set to `true` — no truthy string parsing ambiguity

### NFR-2: Observability
- Startup log line clearly indicates docs state and reason
- Health check (`/api/v1/health`) does NOT expose docs status (avoids information leakage)

### NFR-3: Backward Compatibility
- Default behavior in development unchanged (docs enabled)
- Existing deployments without `ENABLE_API_DOCS` get secure-by-default in production
- No breaking changes to API contracts

---

## API Changes

### Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_API_DOCS` | `false` | Explicitly enable `/docs`, `/redoc`, `/openapi.json` |
| `ENVIRONMENT` | `development` | Implicitly enables docs when `development` or `test` |

### Endpoint Behavior

| Environment | `ENABLE_API_DOCS` | `/docs` | `/redoc` | `/openapi.json` |
|-------------|-------------------|---------|----------|-----------------|
| development | unset/false | 200 | 200 | 200 |
| test | unset/false | 200 | 200 | 200 |
| production | unset/false | 404 | 404 | 404 |
| production | true | 200 | 200 | 200 |
| any | true | 200 | 200 | 200 |

---

## Migration Notes

- **No breaking changes** for development workflows
- **Production deployments**: Add `ENABLE_API_DOCS=true` to environment if docs are needed (not recommended)
- **Docker/container deployments**: Ensure `ENVIRONMENT=production` is set (or explicitly set `ENABLE_API_DOCS=false`)
- **CI/CD pipelines**: Tests that rely on `/openapi.json` should set `ENABLE_API_DOCS=true` or `ENVIRONMENT=test`

---

## Test Scenarios

### Unit Tests (`backend/tests/unit/test_api_docs_production.py`)

| Test ID | Description |
|---------|-------------|
| UT-AD-01 | `Settings.ENABLE_API_DOCS` defaults to `False` |
| UT-AD-02 | `ENABLE_API_DOCS=true` env var sets `settings.ENABLE_API_DOCS = True` |
| UT-AD-03 | `ENABLE_API_DOCS=false` env var sets `settings.ENABLE_API_DOCS = False` |
| UT-AD-04 | `ENVIRONMENT=development` → docs enabled (implicit) |
| UT-AD-05 | `ENVIRONMENT=test` → docs enabled (implicit) |
| UT-AD-06 | `ENVIRONMENT=production` → docs disabled (implicit) |
| UT-AD-07 | `ENABLE_API_DOCS=true` overrides `ENVIRONMENT=production` |
| UT-AD-08 | `ENABLE_API_DOCS=false` overrides `ENVIRONMENT=development` |
| UT-AD-09 | FastAPI `docs_url`/`redoc_url`/`openapi_url` correctly computed from settings |
| UT-AD-10 | Case-insensitive parsing of `ENABLE_API_DOCS` (true/True/TRUE) |

### Integration Tests (`backend/tests/integration/test_api_docs_production.py`)

| Test ID | Description |
|---------|-------------|
| IT-AD-01 | `ENVIRONMENT=development` (default) — `/docs` returns 200 with Swagger UI HTML |
| IT-AD-02 | `ENVIRONMENT=development` — `/redoc` returns 200 with ReDoc HTML |
| IT-AD-03 | `ENVIRONMENT=development` — `/openapi.json` returns 200 with valid OpenAPI schema |
| IT-AD-04 | `ENVIRONMENT=production`, no `ENABLE_API_DOCS` — `/docs` returns 404 |
| IT-AD-05 | `ENVIRONMENT=production`, no `ENABLE_API_DOCS` — `/redoc` returns 404 |
| IT-AD-06 | `ENVIRONMENT=production`, no `ENABLE_API_DOCS` — `/openapi.json` returns 404 |
| IT-AD-07 | `ENVIRONMENT=production`, `ENABLE_API_DOCS=true` — all three endpoints return 200 |
| IT-AD-08 | `ENVIRONMENT=production`, `ENABLE_API_DOCS=false` — all three endpoints return 404 |
| IT-AD-09 | `ENVIRONMENT=test` — all three endpoints return 200 |
| IT-AD-10 | Startup log contains "API docs ENABLED" or "API docs DISABLED" with reason |

### Security Tests (`backend/tests/security/test_api_docs_production.py`)

| Test ID | Description |
|---------|-------------|
| ST-AD-01 | No schema information in 404 response body when disabled |
| ST-AD-02 | No `Server` or framework version headers leaked on 404 |
| ST-AD-03 | `/openapi.json` 404 does not reveal path structure |
| ST-AD-04 | Rapid requests to disabled endpoints do not cause DoS (rate limiting not required, but no crash) |

### E2E / Manual Verification

| Scenario | Steps | Expected |
|----------|-------|----------|
| E2E-AD-01 | Start backend with default settings, visit `/docs` | Swagger UI loads |
| E2E-AD-02 | Start backend with `ENVIRONMENT=production`, visit `/docs` | 404 Not Found |
| E2E-AD-03 | Start backend with `ENVIRONMENT=production ENABLE_API_DOCS=true`, visit `/docs` | Swagger UI loads |
| E2E-AD-04 | Check startup logs in each mode | Clear "ENABLED/DISABLED" message with reason |
| E2E-AD-05 | Run `pytest backend/tests/unit/test_api_contract.py` with `ENVIRONMENT=test` | Schema validation tests pass |

---

## Implementation Plan

### Phase 1: Configuration
1. Add `ENABLE_API_DOCS: bool = False` field to `Settings` in `backend/core/config.py`
2. Add field validator for case-insensitive boolean parsing (like other bool fields)

### Phase 2: FastAPI Initialization
1. In `backend/main.py`, compute `enable_docs` before `FastAPI()` instantiation
2. Pass conditional `docs_url`, `redoc_url`, `openapi_url` to `FastAPI` constructor
3. Add startup log line for docs state

### Phase 3: Tests
1. Create `backend/tests/unit/test_api_docs_production.py` with unit tests
2. Create `backend/tests/integration/test_api_docs_production.py` with integration tests
3. Create `backend/tests/security/test_api_docs_production.py` with security tests
4. Verify existing tests still pass (especially `test_api_contract.py` which may use `/openapi.json`)

### Phase 4: Documentation
1. Update `docs/spec.md` or deployment docs with `ENABLE_API_DOCS` variable
2. Update `openapi.yaml` if needed (though the schema itself doesn't change)

---

## Open Questions

1. **Should `ENVIRONMENT=staging` enable or disable docs?**
   - **Decision**: Treat `staging` as production-like → disable unless `ENABLE_API_DOCS=true`. Only `development` and `test` enable implicitly.

2. **Should the OpenAPI schema be available at a different path when docs are disabled?**
   - **Decision**: No. `/openapi.json` is disabled alongside `/docs` and `/redoc`. If programmatic access is needed, use `ENABLE_API_DOCS=true`.

3. **Impact on frontend?**
   - **Decision**: None. Frontend does not consume `/docs` or `/openapi.json` at runtime.

4. **What about the `openapi_url` parameter in FastAPI?**
   - **Decision**: Set to `None` when disabled. This prevents the schema from being generated/served entirely.

---

## Definition of Done

- [ ] Spec reviewed and approved by PM
- [ ] Implementation complete (Phases 1–2)
- [ ] All unit tests pass (`pytest backend/tests/unit/test_api_docs_production.py`)
- [ ] All integration tests pass (`pytest backend/tests/integration/test_api_docs_production.py`)
- [ ] All security tests pass (`pytest backend/tests/security/test_api_docs_production.py`)
- [ ] Existing tests pass (no regressions in `test_api_contract.py`, `test_main.py`, etc.)
- [ ] Backend gates pass: `ruff check . && ruff format --check . && mypy .`
- [ ] Frontend gates unaffected (no frontend changes)
- [ ] CI passes on `main` branch
- [ ] PM accepts the work

---

## References

- Issue: #161
- Related: FastAPI docs on [disabling docs](https://fastapi.tiangolo.com/advanced/settings/#disable-the-automatic-documentation)
- OWASP: API Security Top 10 — API3:2023 Broken Object Property Level Authorization (schema exposure aids enumeration)
- Config pattern: `SERVE_FRONTEND`, `ENABLE_LOVABLE_PREVIEW_CORS` in `backend/core/config.py`
- Startup logging pattern: `SEED_DEMO_DATA` log in `backend/main.py:161-168`