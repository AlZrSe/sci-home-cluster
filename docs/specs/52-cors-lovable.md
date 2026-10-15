# Spec: CORS Allowlist for Lovable Preview Origins

**Issue**: #52  
**Status**: Groomed  
**Author**: PM  
**Date**: 2026-10-11

---

## Problem Statement

### What fails
A developer using the Lovable preview (`https://<preview>.lovableproject.com`) cannot reach the local backend API. The browser makes a **CORS preflight request** (OPTIONS) that is rejected before authentication is ever evaluated.

### Exact reproduction
1. Start the backend: `uvicorn backend.main:app --reload`
2. From a browser (or curl simulating the browser), send a preflight from a Lovable preview origin:

```bash
curl -i -X OPTIONS \
  -H "Origin: https://preview.lovableproject.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type,authorization" \
  http://localhost:8000/api/v1/jobs
```

### Current response (broken)
```
HTTP/1.1 400 Bad Request
content-length: 0
```
No CORS headers are returned. The browser blocks the request.

### Expected response (after fix)
```
HTTP/1.1 200 OK
access-control-allow-origin: https://preview.lovableproject.com
access-control-allow-methods: POST, OPTIONS
access-control-allow-headers: content-type,authorization
access-control-allow-credentials: true
```

---

## Root Cause

`backend/core/config.py:311-315` hardcodes the CORS allowlist:

```python
BACKEND_CORS_ORIGINS: List[Union[str, AnyHttpUrl]] = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
]
```

The `CORSMiddleware` in `backend/main.py:270-277` uses this list verbatim. No entry matches `https://*.lovableproject.com`, so the middleware rejects the preflight.

**This is distinct from the authentication bypass** (`is_localhost()` in `backend/core/utils.py`). That function checks the `Host` header and allows `.lovable.app` but deliberately excludes `.lovableproject.com`. The CORS check happens earlier and uses the `Origin` header, which is a separate security boundary.

---

## Scope Boundaries

| In Scope | Out of Scope |
|----------|--------------|
| Add Lovable preview origins to `BACKEND_CORS_ORIGINS` | Change `is_localhost()` / auth bypass logic |
| Make the allowlist configurable per-deployment | Frontend code changes |
| Document the CORS ↔ auth interaction | Wildcard `*` in production |
| Add test coverage for the new origins | Modify `shared/auth/localhost_hosts.json` |

---

## Acceptance Criteria

All checkboxes must be verified before the issue can move to `done`.

### AC-1: Preflight succeeds for a concrete Lovable preview origin
```bash
curl -i -X OPTIONS \
  -H "Origin: https://preview.lovableproject.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type,authorization" \
  http://localhost:8000/api/v1/jobs
```
- [ ] Returns `200 OK`
- [ ] Includes `access-control-allow-origin: https://preview.lovableproject.com`
- [ ] Includes `access-control-allow-credentials: true`
- [ ] Includes `access-control-allow-methods` containing `POST`
- [ ] Includes `access-control-allow-headers` containing `content-type,authorization`

### AC-2: Preflight succeeds for any `*.lovableproject.com` subdomain
```bash
curl -i -X OPTIONS \
  -H "Origin: https://abc123.lovableproject.com" \
  -H "Access-Control-Request-Method: GET" \
  http://localhost:8000/api/v1/health
```
- [ ] Returns `200 OK`
- [ ] `access-control-allow-origin` echoes the request origin exactly

### AC-3: Actual request (not preflight) works with credentials
```bash
curl -i -X GET \
  -H "Origin: https://preview.lovableproject.com" \
  -H "Authorization: Bearer localhost-no-auth" \
  http://localhost:8000/api/v1/jobs
```
- [ ] Returns `200 OK` (or `401` if token invalid, but **not** a CORS error)
- [ ] Includes `access-control-allow-origin: https://preview.lovableproject.com`
- [ ] Includes `access-control-allow-credentials: true`

### AC-4: Existing localhost origins still work
```bash
for origin in "http://localhost:3000" "http://localhost:5173" "http://localhost:8080"; do
  curl -i -X OPTIONS -H "Origin: $origin" -H "Access-Control-Request-Method: GET" http://localhost:8000/api/v1/health
done
```
- [ ] All three return `200 OK` with correct CORS headers

### AC-5: Non-allowed origin is rejected
```bash
curl -i -X OPTIONS \
  -H "Origin: https://evil.com" \
  -H "Access-Control-Request-Method: POST" \
  http://localhost:8000/api/v1/jobs
```
- [ ] Returns `400 Bad Request` (or similar rejection)
- [ ] No `access-control-allow-origin` header present

### AC-6: Configuration via environment variable
- [ ] `BACKEND_CORS_ORIGINS` can be overridden via `.env` or environment variable
- [ ] Comma-separated string is parsed correctly (per existing validator at `config.py:317-326`)
- [ ] Example: `BACKEND_CORS_ORIGINS="http://localhost:3000,https://preview.lovableproject.com"`

### AC-7: Unit test added
- [ ] New test in `backend/tests/unit/test_cors.py` (or similar) verifying AC-1 through AC-5
- [ ] Test runs in CI and passes

---

## Test Scenarios

| Scenario | Origin Header | Expected | Notes |
|----------|---------------|----------|-------|
| Lovable preview (exact) | `https://preview.lovableproject.com` | 200 + CORS | Primary use case |
| Lovable preview (random sub) | `https://abc123.lovableproject.com` | 200 + CORS | Wildcard behavior |
| Lovable preview (HTTPS, port) | `https://preview.lovableproject.com:443` | 200 + CORS | Explicit port |
| Local dev (Vite) | `http://localhost:5173` | 200 + CORS | Existing, must not regress |
| Local dev (Create React App) | `http://localhost:3000` | 200 + CORS | Existing, must not regress |
| Local dev (other) | `http://localhost:8080` | 200 + CORS | Existing, must not regress |
| Malicious origin | `https://evil.com` | 400, no CORS | Security boundary |
| Null origin (file://) | `null` | 400, no CORS | Browser sends `null` for file:// |
| Missing origin | (omitted) | 200, no CORS | Same-origin request, no CORS needed |

---

## Risks & Mitigations

### Risk 1: Wildcard origin allows any Lovable preview to read authenticated responses
**Impact**: High. A malicious Lovable preview could exfiltrate data from a developer's local backend if the developer visits it while the backend is running.

**Mitigation**:
- The CORS allowlist is **not** a security boundary for the API itself — authentication (Bearer token) still applies.
- However, with `allow_credentials=true`, the browser **will** send cookies/tokens and **will** expose the response to the calling origin if the origin is in the allowlist.
- **Recommendation**: Only enable `*.lovableproject.com` in development. In production, use an explicit, fixed list of known preview URLs, or require the developer to opt-in via `BACKEND_CORS_ORIGINS` env var.
- The default `BACKEND_CORS_ORIGINS` should remain localhost-only. The Lovable origins should be added **only when explicitly configured** (e.g., via `ENABLE_LOVABLE_PREVIEW=true` or by setting `BACKEND_CORS_ORIGINS` directly).

### Risk 2: Subdomain wildcard (`*.lovableproject.com`) not directly supported by CORSMiddleware
**Impact**: Medium. Starlette's `CORSMiddleware` does not support wildcard subdomains in `allow_origins` — it requires exact matches or `["*"]` (which disables credentials).

**Mitigation**:
- Option A: Accept the risk and add the specific preview URL(s) developers use (e.g., `https://preview.lovableproject.com`) as explicit entries.
- Option B: Use a custom middleware that checks `origin.endswith(".lovableproject.com")` before delegating to CORSMiddleware.
- Option C: Allow all origins in dev mode (`SEED_DEMO_DATA=true`) via `allow_origins=["*"]` with `allow_credentials=False`, but this breaks authenticated requests.
- **Recommended**: Option A — explicit entries, configurable via env var. Document that developers must add their specific preview URL.

### Risk 3: Drift between frontend and backend localhost lists
**Impact**: Low for this issue. The CORS allowlist is independent of the `is_localhost()` lists. But developers may conflate them.

**Mitigation**: Document clearly in the spec and README that:
- `BACKEND_CORS_ORIGINS` controls **browser preflight** (Origin header)
- `is_localhost()` controls **auth bypass** (Host header)
- They are separate lists for separate threat models

---

## Implementation Notes

### Files to modify
1. `backend/core/config.py` — add Lovable preview origins to default `BACKEND_CORS_ORIGINS` **or** add a new setting `ENABLE_LOVABLE_CORS` that extends the list conditionally.
2. `backend/tests/unit/test_cors.py` — new test file for CORS behavior.

### Suggested approach
Add a new setting `ENABLE_LOVABLE_PREVIEW_CORS: bool = False` that, when true, appends known Lovable preview origins to `BACKEND_CORS_ORIGINS`. This keeps the default secure (localhost only) while letting developers opt in.

```python
# In Settings class
ENABLE_LOVABLE_PREVIEW_CORS: bool = False


@property
def effective_cors_origins(self) -> List[str]:
    origins = list(self.BACKEND_CORS_ORIGINS)
    if self.ENABLE_LOVABLE_PREVIEW_CORS:
        origins.extend(
            [
                "https://preview.lovableproject.com",
                # Add more as needed; could also read from a comma-separated env var
            ]
        )
    return origins
```

Then in `main.py`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(o) for o in settings.effective_cors_origins],
    ...
)
```

---

## Corrections to Issue Text

The issue states:
> "The Lovable-preview user story US-1 in #31 was **misdiagnosed in the original issue body**"

**Correction**: The original issue #31 *did* correctly identify a CORS problem, but it was scoped out of #31's remediation. This issue (#52) is the follow-up to address it.

The issue states:
> "Host: preview.lovableproject.com on the backend returns 401 (not in the bypass list)"

**Clarification**: The `Host` header check (`is_localhost()`) is for **auth bypass**. The CORS check uses the `Origin` header. They are independent. A request from `https://preview.lovableproject.com`:
- Fails CORS preflight (Origin not in allowlist) → **this issue**
- Would fail auth if CORS passed (Host not in bypass list) → **separate, deliberate decision per #31**

Both must be addressed for the full Lovable preview flow to work, but they are separate fixes with separate security trade-offs.

---

## Definition of Done

- [ ] Spec written and reviewed (this document)
- [ ] Implementation complete (SWE)
- [ ] All ACs verified (QA)
- [ ] Unit tests pass
- [ ] No lint/type errors (`ruff check . && ruff format . && mypy .`)
- [ ] PM accepts