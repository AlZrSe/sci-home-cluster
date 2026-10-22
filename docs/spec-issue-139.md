# Specification: POST /api/v1/jobs Trailing Slash Behavior

**Issue**: #139  
**Status**: Groomed  
**Date**: 2026-10-22

---

## 1. Problem Statement

The API exhibits asymmetric behavior for the `/api/v1/jobs` endpoint regarding trailing slashes:

| Method | URL | Current Behavior |
|--------|-----|------------------|
| GET | `/api/v1/jobs` | **307 Redirect** → `/api/v1/jobs/` (via Starlette's `redirect_slashes`) |
| POST | `/api/v1/jobs` | **405 Method Not Allowed** (no redirect) |
| GET | `/api/v1/jobs/` | **200 OK** |
| POST | `/api/v1/jobs/` | **201 Created** |

This asymmetry violates the principle of least surprise: a client that discovers the canonical URL by following a `GET` redirect will have its subsequent `POST` rejected with a confusing 405 error.

---

## 2. Root Cause

- FastAPI (built on Starlette) enables `redirect_slashes=True` by default for **GET** and **HEAD** requests only.
- For other methods (POST, PUT, DELETE, etc.), Starlette returns 405 with an `Allow` header listing the methods available at the *canonical* path (with trailing slash).
- The router is registered as `prefix="/api/v1/jobs"` with routes defined as `@router.get("/")` and `@router.post("/")`, making the canonical path `/api/v1/jobs/`.

---

## 3. User Stories

### US-1: API Consumer Submits Job
> As an API consumer, I want to POST to `/api/v1/jobs` (without trailing slash) and have it work the same as `/api/v1/jobs/`, so that I don't need to remember the trailing slash convention.

### US-2: API Explorer Discovers Endpoint
> As an API explorer using `curl` or a browser, I want GET and POST to behave consistently regarding redirects, so that I can discover the correct URL through experimentation.

### US-3: Automated Client Follows Redirects
> As an automated client that follows HTTP redirects, I want POST requests to redirect the same way GET does, so that my redirect-following logic works uniformly.

---

## 4. Acceptance Criteria

### AC-1: POST without trailing slash succeeds
`POST /api/v1/jobs` (no trailing slash) returns **201 Created** with the same response body as `POST /api/v1/jobs/`.

### AC-2: GET without trailing slash redirects (unchanged)
`GET /api/v1/jobs` continues to return **307 Temporary Redirect** to `/api/v1/jobs/`.

### AC-3: Other mutating methods work without trailing slash
`PUT /api/v1/jobs/{job_id}/retry`, `POST /api/v1/jobs/{job_id}/cancel`, `DELETE /api/v1/jobs/{job_id}` all work without trailing slashes on the base path.

### AC-4: OpenAPI spec documents canonical URLs with trailing slash
The `openapi.yaml` continues to show paths with trailing slashes (e.g., `/api/v1/jobs/`) as the canonical form.

### AC-5: 405 responses include helpful body (fallback)
If a 405 is returned for any reason, the response body MUST include the canonical URL in the `detail` field (e.g., `"detail": "POST /api/v1/jobs/ is the correct endpoint"`).

---

## 5. Technical Approach Options

### Option A: Enable `redirect_slashes=True` for All Methods (Recommended)
Configure the FastAPI app or router to redirect slashes for all HTTP methods, not just GET/HEAD.

**Implementation:**
```python
# In backend/main.py FastAPI constructor or router inclusion
app = FastAPI(
    ...
    redirect_slashes=True,  # Enables for all methods
)
```

**Pros:**
- Single-line change
- Consistent behavior across all methods
- Follows Starlette's design intent
- No duplicate route registration

**Cons:**
- Changes behavior for all endpoints globally (may affect other routers)
- POST redirects change method to GET (307 preserves method, but some clients may not handle it)
- RFC 7231: 307 preserves method, 308 also preserves method — both are safe

**Risk Assessment:** Low. Starlette's `redirect_slashes` uses 307 (Temporary Redirect) which preserves the HTTP method per RFC 7231. Modern clients handle this correctly.

---

### Option B: Register Both Spellings Explicitly
Add duplicate route decorators for each endpoint: one with trailing slash, one without.

**Implementation:**
```python
# In backend/api/v1/jobs.py
@router.get("", response_model=JobListResult)      # No trailing slash
@router.get("/", response_model=JobListResult)     # With trailing slash
async def list_jobs(...): ...

@router.post("", response_model=JobState, ...)     # No trailing slash
@router.post("/", response_model=JobState, ...)    # With trailing slash
async def create_job(...): ...
```

**Pros:**
- Explicit control per endpoint
- No redirect overhead
- No global side effects

**Cons:**
- Duplicates every route decorator (maintenance burden)
- OpenAPI spec shows duplicate operations (clutters docs)
- Must be applied consistently across all routers (nodes, syncthing, auth)

**Risk Assessment:** Medium. High maintenance cost; easy to miss endpoints.

---

### Option C: Custom 405 Handler with Helpful Body
Keep current behavior but enhance the 405 response to include the correct URL.

**Implementation:**
```python
# In backend/main.py - custom exception handler for 405
@app.exception_handler(405)
async def method_not_allowed_handler(request: Request, exc: HTTPException):
    # Detect trailing slash issue and suggest correct URL
    if request.url.path.endswith("/jobs") and request.method == "POST":
        detail = "POST /api/v1/jobs/ is the correct endpoint (trailing slash required)"
    elif ...
    return JSONResponse(status_code=405, content=ErrorResponse(...).model_dump())
```

**Pros:**
- Minimal change
- No redirect complexity
- Helpful error message

**Cons:**
- Still returns 405 (client must retry)
- Requires pattern matching on paths
- Doesn't solve the root asymmetry

**Risk Assessment:** Low effort, but doesn't fully solve the UX problem.

---

## 6. Recommended Solution: Option A

**Enable `redirect_slashes=True` globally on the FastAPI app.**

### Justification
1. **Smallest code change** — one parameter in `FastAPI()` constructor
2. **Consistent with framework design** — Starlette/FastAPI intend this behavior
3. **Solves the entire class of problems** — not just `/jobs` but all endpoints
4. **RFC-compliant** — 307 preserves POST method
5. **Zero maintenance overhead** — no duplicate routes to maintain

### Implementation Details

**File: `backend/main.py`**
```python
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    redirect_slashes=True,  # ADD THIS LINE
)
```

**Verification:** Test that `POST /api/v1/jobs` → 307 → `POST /api/v1/jobs/` → 201

---

## 7. Test Scenarios

### TS-1: POST /api/v1/jobs (no trailing slash) — Primary Test
```python
async def test_create_job_no_trailing_slash(async_client, job_spec_dict):
    response = await async_client.post("/api/v1/jobs", json=job_spec_dict)
    assert response.status_code == 201
    data = response.json()
    assert data["job_id"].startswith("job-")
```

### TS-2: POST /api/v1/jobs/ (with trailing slash) — Regression Test
```python
async def test_create_job_with_trailing_slash(async_client, job_spec_dict):
    response = await async_client.post("/api/v1/jobs/", json=job_spec_dict)
    assert response.status_code == 201
```

### TS-3: GET /api/v1/jobs (no trailing slash) — Redirect Verification
```python
async def test_list_jobs_no_trailing_slash_redirects(async_client):
    response = await async_client.get("/api/v1/jobs", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/api/v1/jobs/"
```

### TS-4: Other mutating endpoints without trailing slash
```python
@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/jobs/{job_id}/retry"),
    ("POST", "/api/v1/jobs/{job_id}/cancel"),
    ("DELETE", "/api/v1/jobs/{job_id}"),
    ("POST", "/api/v1/nodes"),  # If nodes ever gets POST
])
async def test_mutating_endpoints_no_trailing_slash(async_client, method, path):
    # Create job first for job-specific paths
    # Then test without trailing slash
    response = await async_client.request(method, path, follow_redirects=True)
    assert response.status_code in (200, 201, 204)  # Not 405
```

### TS-5: OpenAPI spec unchanged
```python
def test_openapi_spec_canonical_urls():
    # Verify openapi.yaml still shows /api/v1/jobs/ as canonical
    with open("openapi.yaml") as f:
        spec = yaml.safe_load(f)
    assert "/api/v1/jobs/" in spec["paths"]
    assert "/api/v1/jobs" not in spec["paths"]  # No duplicate without slash
```

---

## 8. Edge Cases & Risks

| Edge Case | Risk | Mitigation |
|-----------|------|------------|
| Clients that don't follow 307 on POST | Medium | Most modern HTTP clients (httpx, requests, fetch, axios) follow 307 for POST. Document in API docs. |
| CORS preflight (OPTIONS) on redirect | Low | Starlette handles OPTIONS correctly; preflight goes to canonical URL. |
| WebSocket endpoints | N/A | WebSocket routes use exact paths; not affected by `redirect_slashes`. |
| Existing tests using trailing slash | Low | Tests already use trailing slash (e.g., `/api/v1/jobs/`). They continue to pass. |
| Global effect on all routers | Medium | Verify `/auth`, `/nodes`, `/syncthing` endpoints behave correctly. Add integration tests. |
| `openapi.yaml` regeneration | None | The spec documents canonical paths (with slash). Redirects are runtime behavior, not schema. |

---

## 9. Migration Plan

1. **Add `redirect_slashes=True`** to `FastAPI()` constructor in `backend/main.py`
2. **Run existing test suite** — verify no regressions
3. **Add new test cases** (TS-1 through TS-4) to `backend/tests/integration/test_jobs_flow.py`
4. **Manually verify** with `curl`:
   ```bash
   curl -X POST http://localhost:8000/api/v1/jobs \
     -H "Authorization: Bearer localhost-no-auth" \
     -H "Content-Type: application/json" \
     -d '{"name":"test","command":"echo hi","working_dir":"/tmp","resources":{"gpus":0,"cpus":1,"memory_gb":1},"paths":{},"retry":0,"env":{}}'
   ```
   Should return 201 (following 307 redirect).
5. **Update `openapi.yaml` if needed** — should not change, but verify.

---

## 10. Definition of Done

- [ ] `redirect_slashes=True` added to FastAPI app
- [ ] All existing backend tests pass (`pytest --cov=backend`)
- [ ] New test cases TS-1 through TS-4 added and passing
- [ ] Manual `curl` verification successful
- [ ] `openapi.yaml` unchanged (or regenerated with only expected changes)
- [ ] No lint/type errors (`ruff check . && ruff format . && mypy .`)
- [ ] PM acceptance

---

## 11. References

- Issue #139: Original issue
- Issue #136 §5.4: Source of this issue
- Starlette `redirect_slashes` docs: https://www.starlette.io/routing/#redirectslashes
- RFC 7231 Section 6.4.7: 307 Temporary Redirect preserves method
- FastAPI routing: https://fastapi.tiangolo.com/tutorial/path-params/#redirect-slashes