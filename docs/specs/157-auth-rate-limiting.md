# Specification: Auth Endpoint Rate Limiting (Issue #157)

## Overview

Add per-IP rate limiting with exponential backoff and lockout to all authentication endpoints to prevent brute-force attacks on the shared token. The current implementation allows unlimited attempts at network speed, enabling full cluster takeover.

---

## User Stories

### US-1: Cluster Operator — Protection from Online Brute Force
> As a **cluster operator**, I want **authentication endpoints to enforce per-IP rate limits with exponential backoff** so that **an attacker cannot guess the shared token by submitting candidates at network speed**.

**Acceptance Criteria:**
- POST `/api/v1/auth/token` (create token from shared token) — rate limited
- POST `/api/v1/auth/validate` (validate token) — rate limited
- GET `/api/v1/auth/verify` (validate via Bearer header) — rate limited
- POST `/api/v1/auth/refresh` (refresh JWT) — rate limited
- Limits apply per client IP address (X-Forwarded-For aware)
- First 5 failures in a rolling 15-minute window: allow (baseline)
- 6–10 failures: exponential backoff (2s, 4s, 8s, 16s, 32s)
- 11+ failures: hard lockout for 1 hour, then reset to baseline
- Successful auth resets the failure counter for that IP

### US-2: Security Auditor — Audit Trail of Failed Attempts
> As a **security auditor**, I want **all failed authentication attempts logged with IP, timestamp, endpoint, and failure reason** so that **I can detect and investigate brute-force campaigns**.

**Acceptance Criteria:**
- Structured log entry on every failed auth attempt (JSON format)
- Fields: `timestamp`, `client_ip`, `endpoint`, `failure_reason`, `attempt_number`, `user_agent`
- Log level: `WARNING` for failures, `ERROR` for lockout events
- No sensitive data (tokens, credentials) in logs

### US-3: Developer — Localhost Bypass Unaffected
> As a **developer**, I want **localhost bypass to continue working without rate limiting interference** so that **my local development workflow is not disrupted**.

**Acceptance Criteria:**
- Requests from `localhost`, `127.0.0.1`, `::1`, `.local`, `.lovable.app` hosts (per `shared/auth/localhost_hosts.json`) are exempt from rate limiting
- Exemption applies regardless of `X-Forwarded-For` header
- No lockout or backoff for exempt hosts

### US-4: Platform Operator — Configurable Limits
> As a **platform operator**, I want **rate limit parameters configurable via environment variables** so that **I can tune thresholds for my deployment without code changes**.

**Acceptance Criteria:**
- `AUTH_RATE_LIMIT_BASE` (default: 5) — baseline allowed failures per window
- `AUTH_RATE_LIMIT_WINDOW_SECONDS` (default: 900) — rolling window duration
- `AUTH_RATE_LIMIT_BACKOFF_BASE_SECONDS` (default: 2) — initial backoff
- `AUTH_RATE_LIMIT_MAX_BACKOFF_SECONDS` (default: 3600) — cap at 1 hour
- `AUTH_RATE_LIMIT_LOCKOUT_THRESHOLD` (default: 11) — failures before hard lockout
- `AUTH_RATE_LIMIT_LOCKOUT_SECONDS` (default: 3600) — lockout duration

---

## Functional Requirements

### FR-1: Rate Limiting Middleware
- Implement as FastAPI middleware or dependency (not global middleware — only auth endpoints)
- Use in-memory store (sufficient for single-instance; documented limitation)
- Key: client IP (respect `X-Forwarded-For` first entry, fallback to `request.client.host`)
- Track: failure count, last failure time, lockout expiry
- Cleanup: periodic eviction of expired entries (TTL-based)

### FR-2: Exponential Backoff Algorithm
```
attempts = failure_count - BASE_LIMIT
if attempts <= 0: delay = 0
elif attempts <= BACKOFF_STEPS: delay = BASE_BACKOFF * 2^(attempts-1)
else: delay = LOCKOUT_DURATION (hard lockout)
```
Where `BACKOFF_STEPS = LOCKOUT_THRESHOLD - BASE_LIMIT - 1` (default: 5 steps)

### FR-3: Response Headers
On rate-limited endpoints, include:
- `X-RateLimit-Limit`: baseline limit (e.g., 5)
- `X-RateLimit-Remaining`: remaining attempts before backoff
- `X-RateLimit-Reset`: Unix timestamp when window resets
- `Retry-After`: seconds until next allowed attempt (when backed off/locked out)

### FR-4: Error Responses
- **429 Too Many Requests** when backed off or locked out
- Body: standard `ErrorResponse` with `error_code = "AUTH_RATE_LIMITED"`
- `detail` explains state: "Rate limited. Try again in N seconds." or "Locked out due to repeated failures. Try again in N seconds."

### FR-5: Audit Logging
```json
{
  "event": "auth_failure",
  "timestamp": "2026-10-24T12:34:56.789Z",
  "client_ip": "192.0.2.1",
  "endpoint": "/api/v1/auth/token",
  "failure_reason": "invalid_shared_token",
  "attempt_number": 7,
  "user_agent": "curl/8.5.0",
  "backoff_seconds": 8,
  "locked_out": false
}
```
Lockout event:
```json
{
  "event": "auth_lockout",
  "timestamp": "2026-10-24T12:35:00.000Z",
  "client_ip": "192.0.2.1",
  "endpoint": "/api/v1/auth/token",
  "lockout_duration_seconds": 3600,
  "total_failures": 11
}
```

---

## Non-Functional Requirements

### NFR-1: Performance
- Rate limit check: < 1 ms overhead per request (in-memory dict lookup)
- Memory: < 1 MB for 10,000 tracked IPs
- No external dependencies (Redis, etc.) for Phase 1

### NFR-2: Correctness
- Thread-safe / async-safe: use `asyncio.Lock` or `threading.Lock` for in-memory state
- Constant-time token comparison preserved (existing `hmac.compare_digest`)
- No timing side-channels in rate limit logic itself

### NFR-3: Observability
- Metrics: `auth_rate_limit_checks_total`, `auth_rate_limit_rejections_total`, `auth_lockouts_total`
- Labels: `endpoint`, `result` (allowed/backoff/lockout)

---

## API Changes

### New Error Code
- `AUTH_RATE_LIMITED` (HTTP 429)

### New Response Headers (on auth endpoints)
| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Baseline failure allowance per window |
| `X-RateLimit-Remaining` | Remaining attempts before backoff |
| `X-RateLimit-Reset` | Unix timestamp when window resets |
| `Retry-After` | Seconds until next allowed request (when limited) |

### Configuration (Environment Variables)
| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_RATE_LIMIT_BASE` | 5 | Allowed failures per window before backoff |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | 900 | Rolling window (15 min) |
| `AUTH_RATE_LIMIT_BACKOFF_BASE_SECONDS` | 2 | Initial backoff delay |
| `AUTH_RATE_LIMIT_MAX_BACKOFF_SECONDS` | 3600 | Max backoff (1 hour) |
| `AUTH_RATE_LIMIT_LOCKOUT_THRESHOLD` | 11 | Failures before hard lockout |
| `AUTH_RATE_LIMIT_LOCKOUT_SECONDS` | 3600 | Hard lockout duration |

---

## Migration Notes

- **No breaking changes** to existing API contracts
- New headers are additive; clients ignore unknown headers
- 429 is a new possible response on auth endpoints — document in OpenAPI
- Default settings are conservative; operators can tune via env vars
- Localhost bypass unchanged (uses existing `shared/auth/localhost_hosts.json`)

---

## Test Scenarios

### Unit Tests (`backend/tests/unit/test_auth_rate_limit.py`)

| Test ID | Description |
|---------|-------------|
| UT-RL-01 | Rate limiter allows up to `BASE` failures per window |
| UT-RL-02 | Rate limiter applies exponential backoff (2, 4, 8, 16, 32s) for attempts 6–10 |
| UT-RL-03 | Rate limiter hard-locks at `LOCKOUT_THRESHOLD` (11) for `LOCKOUT_SECONDS` |
| UT-RL-04 | Successful auth resets failure counter for that IP |
| UT-RL-05 | Window expiry resets failure counter |
| UT-RL-06 | Localhost IPs (from `localhost_hosts.json`) are exempt |
| UT-RL-07 | `X-Forwarded-For` header used when present (first entry) |
| UT-RL-08 | Rate limit headers present on all auth endpoint responses |
| UT-RL-09 | 429 response body matches `ErrorResponse` with `AUTH_RATE_LIMITED` |
| UT-RL-10 | Concurrent requests from same IP handled correctly (async safety) |
| UT-RL-11 | Expired entries evicted from memory store |
| UT-RL-12 | Configuration via env vars works (override defaults) |

### Integration Tests (`backend/tests/integration/test_auth_rate_limit.py`)

| Test ID | Description |
|---------|-------------|
| IT-RL-01 | POST `/auth/token` — 5 invalid shared tokens allowed, 6th gets 2s backoff |
| IT-RL-02 | POST `/auth/token` — 11th invalid shared token gets 1h lockout (429) |
| IT-RL-03 | POST `/auth/validate` — same rate limit bucket as `/auth/token` (per IP) |
| IT-RL-04 | GET `/auth/verify` — same rate limit bucket |
| IT-RL-05 | POST `/auth/refresh` — same rate limit bucket |
| IT-RL-06 | Valid shared token resets counter; subsequent invalid starts fresh |
| IT-RL-07 | Localhost bypass host header exempt from rate limiting |
| IT-RL-08 | `X-Forwarded-For` respected — different IPs have separate buckets |
| IT-RL-09 | Rate limit headers present and accurate on all responses |
| IT-RL-10 | Audit log entries emitted for failures, backoff, and lockout |
| IT-RL-11 | Metrics incremented: checks, rejections, lockouts |

### E2E / Manual Verification

| Scenario | Steps | Expected |
|----------|-------|----------|
| E2E-RL-01 | `curl -X POST /auth/token -d '{"shared_token":"wrong"}'` × 20 rapid | First 5: 401; 6–10: 429 with increasing `Retry-After`; 11+: 429 with 3600s |
| E2E-RL-02 | Wait 15 min, retry | Counter reset; 5 more allowed |
| E2E-RL-03 | Valid token, then 6 invalid | Counter reset by valid; 6th invalid gets 2s backoff |
| E2E-RL-04 | Request from `Host: localhost:8000` × 100 invalid | All 401 (no rate limit), no lockout |

---

## Implementation Plan

### Phase 1: Core Rate Limiter (Backend)
1. Create `backend/core/rate_limit.py` with:
   - `AuthRateLimiter` class (in-memory, async-safe)
   - `get_client_ip(request)` helper (X-Forwarded-For aware)
   - `is_exempt_host(hostname)` using `shared/auth/localhost_hosts.json`
2. Add config settings to `backend/core/config.py`
3. Create `backend/api/deps.py` (or extend `deps.py`) with `rate_limit_dependency` for auth endpoints
4. Apply dependency to all 4 auth endpoints in `backend/api/v1/auth.py`

### Phase 2: Audit Logging & Metrics
1. Add structured logging in rate limiter on failure/backoff/lockout
2. Add Prometheus metrics counters
3. Ensure log format matches FR-5

### Phase 3: Tests
1. Unit tests for rate limiter logic
2. Integration tests for all 4 endpoints
3. Verify localhost bypass exemption

### Phase 4: Documentation
1. Update `openapi.yaml` with 429 response and headers
2. Update README / deployment docs with new env vars

---

## Open Questions

1. **Distributed deployment**: In-memory store doesn't work across multiple API replicas. Document as known limitation; future work can add Redis backend.
2. **IPv6 privacy extensions**: Client IP may rotate. Acceptable for Phase 1.
3. **Shared token rotation**: If shared token changes, should existing lockouts clear? **Decision**: No — lockout is per IP, not per token.
4. **Health check endpoint**: Should `/health` be rate limited? **Decision**: No — only auth endpoints.

---

## Definition of Done

- [ ] Spec reviewed and approved by PM
- [ ] Implementation complete (Phases 1–2)
- [ ] All unit tests pass (`pytest backend/tests/unit/test_auth_rate_limit.py`)
- [ ] All integration tests pass (`pytest backend/tests/integration/test_auth_rate_limit.py`)
- [ ] Backend gates pass: `ruff check . && ruff format --check . && mypy .`
- [ ] Frontend gates unaffected (no frontend changes)
- [ ] CI passes on `main` branch
- [ ] PM accepts the work

---

## References

- Issue: #157
- Related: #31 (localhost bypass), #56 (JWT key generation)
- Shared config: `shared/auth/localhost_hosts.json`
- OWASP: Authentication Cheat Sheet — Rate Limiting
- RFC 6585: HTTP 429 Too Many Requests