# Security Fix: Timing Attack on Shared Token Comparison (Issue #155)

## Overview

**Issue**: The `AuthService` uses the `==` operator for comparing the shared token in `validate_token()` and `verify_shared_token()`. This operator short-circuits on the first differing byte, leaking the length of the common prefix through response timing.

**Impact**: Full authentication bypass once the shared token is recovered; the token then mints arbitrary JWTs.

**Fix**: Use `hmac.compare_digest` (constant-time comparison) for all secret comparisons.

---

## Current Vulnerable Code

### `backend/services/auth_service.py`

**Line 38** in `validate_token()`:
```python
if self._shared_token and token == self._shared_token:
    return True
```

**Line 99** in `verify_shared_token()`:
```python
return token == self._shared_token
```

Both comparisons use `==`, which is vulnerable to timing attacks.

---

## User Stories

### US-1: Secure Shared Token Validation
**As a** system operator  
**I want** shared token validation to use constant-time comparison  
**So that** an attacker cannot recover the shared token through timing analysis

### US-2: Secure Direct Shared Token Verification
**As a** developer  
**I want** `verify_shared_token()` to use constant-time comparison  
**So that** direct token verification endpoints are not vulnerable to timing attacks

### US-3: Defense in Depth
**As a** security auditor  
**I want** all secret comparisons in the authentication path to use `hmac.compare_digest`  
**So that** there are no timing side-channels in the auth system

---

## Acceptance Criteria

### AC-1: `validate_token()` Uses Constant-Time Comparison
- **Given** a configured shared token
- **When** `validate_token()` is called with any token
- **Then** the comparison against the shared token uses `hmac.compare_digest`
- **And** the function returns `True` only for exact matches
- **And** the function returns `False` for all non-matches (including partial matches)

### AC-2: `verify_shared_token()` Uses Constant-Time Comparison
- **Given** a configured shared token
- **When** `verify_shared_token()` is called with any token
- **Then** the comparison uses `hmac.compare_digest`
- **And** the function returns `True` only for exact matches
- **And** the function returns `False` for all non-matches

### AC-3: Behavior Unchanged for Valid Tokens
- **Given** a valid shared token
- **When** `validate_token(valid_token)` is called
- **Then** it returns `True` (same as before fix)

### AC-4: Behavior Unchanged for Invalid Tokens
- **Given** an invalid shared token
- **When** `validate_token(invalid_token)` is called
- **Then** it returns `False` (same as before fix)

### AC-5: No Regressions in JWT Validation Path
- **Given** a valid JWT token (when shared token is not configured or doesn't match)
- **When** `validate_token(jwt_token)` is called
- **Then** it correctly validates the JWT and returns `True`
- **And** expired/malformed JWTs still return `False`

### AC-6: Edge Cases Handled
- **Given** `SHARED_TOKEN` is `None` or empty
- **When** `validate_token()` or `verify_shared_token()` is called
- **Then** both return `False` without error (same as before fix)

### AC-7: Type Safety
- **Given** the codebase uses mypy for type checking
- **When** `mypy .` is run from repository root
- **Then** no new type errors are introduced

### AC-8: Linting Passes
- **Given** the codebase uses ruff for linting and formatting
- **When** `ruff check . && ruff format --check .` is run from repository root
- **Then** no linting or formatting errors are reported

---

## Test Scenarios

### Unit Tests (add to `backend/tests/unit/test_auth.py`)

#### TC-1: Constant-Time Comparison for Valid Shared Token
```python
@pytest.mark.asyncio
async def test_validate_token_shared_token_constant_time(self, auth_service):
    """Test validate_token uses constant-time comparison for valid token."""
    with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
        assert await auth_service.validate_token("test-shared-token-123") is True
```

#### TC-2: Constant-Time Comparison for Invalid Shared Token
```python
@pytest.mark.asyncio
async def test_validate_token_shared_token_invalid_constant_time(self, auth_service):
    """Test validate_token uses constant-time comparison for invalid token."""
    with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
        assert await auth_service.validate_token("wrong-token") is False
```

#### TC-3: Verify Shared Token Uses Constant-Time Comparison
```python
@pytest.mark.asyncio
async def test_verify_shared_token_constant_time(self, auth_service):
    """Test verify_shared_token uses constant-time comparison."""
    with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
        assert await auth_service.verify_shared_token("test-shared-token-123") is True
        assert await auth_service.verify_shared_token("wrong-token") is False
```

#### TC-4: No Shared Token Configured
```python
@pytest.mark.asyncio
async def test_validate_token_no_shared_token_configured(self, auth_service):
    """Test validate_token returns False when no shared token configured."""
    with patch.object(settings, "SHARED_TOKEN", None):
        assert await auth_service.validate_token("any-token") is False
```

#### TC-5: Empty Shared Token
```python
@pytest.mark.asyncio
async def test_validate_token_empty_shared_token(self, auth_service):
    """Test validate_token returns False when shared token is empty string."""
    with patch.object(settings, "SHARED_TOKEN", ""):
        assert await auth_service.validate_token("any-token") is False
```

#### TC-6: JWT Path Still Works
```python
@pytest.mark.asyncio
async def test_validate_token_jwt_still_works(self, auth_service):
    """Test JWT validation path is unaffected by the fix."""
    with patch.object(settings, "SHARED_TOKEN", "different-token"):
        jwt_token = create_access_token({"sub": "test-user"})
        assert await auth_service.validate_token(jwt_token) is True
```

#### TC-7: Prefix Attack Attempt (Timing Attack Simulation)
```python
@pytest.mark.asyncio
async def test_validate_token_prefix_attack_fails(self, auth_service):
    """
    Test that tokens sharing prefix with shared token don't leak info.
    This is a functional test; actual timing test would be flaky in CI.
    """
    with patch.object(settings, "SHARED_TOKEN", "secret-token-12345"):
        # These share prefixes of various lengths
        assert await auth_service.validate_token("secret-token-12345") is True   # exact match
        assert await auth_service.validate_token("secret-token-12346") is False  # last char differs
        assert await auth_service.validate_token("secret-token-123") is False    # prefix only
        assert await auth_service.validate_token("secret") is False              # short prefix
        assert await auth_service.validate_token("wrong") is False               # no prefix match
```

#### TC-8: Verify Shared Token Prefix Attack
```python
@pytest.mark.asyncio
async def test_verify_shared_token_prefix_attack_fails(self, auth_service):
    """Test verify_shared_token doesn't leak prefix info."""
    with patch.object(settings, "SHARED_TOKEN", "secret-token-12345"):
        assert await auth_service.verify_shared_token("secret-token-12345") is True
        assert await auth_service.verify_shared_token("secret-token-12346") is False
        assert await auth_service.verify_shared_token("secret-token-123") is False
        assert await auth_service.verify_shared_token("secret") is False
        assert await auth_service.verify_shared_token("wrong") is False
```

### Integration Tests (add to `backend/tests/integration/test_auth_flow.py`)

#### TC-9: API Endpoint `/auth/validate` with Shared Token
```python
@pytest.mark.asyncio
async def test_validate_endpoint_shared_token_constant_time(self, async_client):
    """Test POST /auth/validate uses constant-time comparison."""
    with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
        response = await async_client.post(
            "/api/v1/auth/validate", json={"token": "test-shared-token-123"}
        )
        assert response.status_code == 200
        assert response.json()["valid"] is True

        response = await async_client.post(
            "/api/v1/auth/validate", json={"token": "wrong-token"}
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False
```

#### TC-10: API Endpoint `/auth/token` with Shared Token
```python
@pytest.mark.asyncio
async def test_create_token_endpoint_shared_token_constant_time(self, async_client):
    """Test POST /auth/token uses constant-time comparison for shared_token."""
    with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
        # Valid shared token -> 200 with JWT
        response = await async_client.post(
            "/api/v1/auth/token", json={"shared_token": "test-shared-token-123"}
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

        # Invalid shared token -> 401
        response = await async_client.post(
            "/api/v1/auth/token", json={"shared_token": "wrong-token"}
        )
        assert response.status_code == 401
```

#### TC-11: API Endpoint `/auth/verify` (GET) with Shared Token
```python
@pytest.mark.asyncio
async def test_verify_endpoint_shared_token_constant_time(self, async_client):
    """Test GET /auth/verify uses constant-time comparison."""
    with patch.object(settings, "SHARED_TOKEN", "test-shared-token-123"):
        response = await async_client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": "Bearer test-shared-token-123"},
        )
        assert response.status_code == 200
        assert response.json()["valid"] is True

        response = await async_client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False
```

### Security Test (New File: `backend/tests/security/test_timing_attack.py`)

#### TC-12: Timing Attack Resistance (Statistical)
```python
"""
Statistical timing test to verify constant-time comparison.
This test is marked as @pytest.mark.slow and may be skipped in CI.
Run manually to verify timing resistance.
"""
import statistics
import time
from unittest.mock import patch

@pytest.mark.slow
@pytest.mark.asyncio
async def test_constant_time_comparison_timing(self, auth_service):
    """
    Measure response times for valid vs invalid tokens.
    With constant-time comparison, the variance should be small
    and the means should be statistically indistinguishable.
    """
    with patch.object(settings, "SHARED_TOKEN", "x" * 32):  # 32-char token
        valid_times = []
        invalid_times = []

        for _ in range(1000):
            # Valid token
            start = time.perf_counter_ns()
            await auth_service.validate_token("x" * 32)
            valid_times.append(time.perf_counter_ns() - start)

            # Invalid token (differs at first char)
            start = time.perf_counter_ns()
            await auth_service.validate_token("y" + "x" * 31)
            invalid_times.append(time.perf_counter_ns() - start)

        # Statistical test: means should be close (within 2 standard deviations)
        valid_mean = statistics.mean(valid_times)
        invalid_mean = statistics.mean(invalid_times)
        valid_stdev = statistics.stdev(valid_times)
        invalid_stdev = statistics.stdev(invalid_times)

        # Difference should be less than 2 standard deviations
        diff = abs(valid_mean - invalid_mean)
        pooled_stdev = (valid_stdev + invalid_stdev) / 2
        assert diff < 2 * pooled_stdev, (
            f"Timing difference detected: valid={valid_mean}ns, "
            f"invalid={invalid_mean}ns, diff={diff}ns, "
            f"pooled_stdev={pooled_stdev}ns"
        )
```

---

## Implementation Plan

### Step 1: Import `hmac` Module
Add `import hmac` at the top of `backend/services/auth_service.py`.

### Step 2: Fix `validate_token()` (Line 38)
**Before:**
```python
if self._shared_token and token == self._shared_token:
    return True
```

**After:**
```python
if self._shared_token and hmac.compare_digest(token, self._shared_token):
    return True
```

### Step 3: Fix `verify_shared_token()` (Line 99)
**Before:**
```python
return token == self._shared_token
```

**After:**
```python
return hmac.compare_digest(token, self._shared_token)
```

### Step 4: Add Tests
Add the unit and integration tests specified above.

### Step 5: Run Gates
```bash
# From repository root
ruff check . && ruff format --check . && mypy .
pytest --cov=backend
```

---

## Migration Notes

### No Breaking Changes
- The fix is internal to the comparison logic
- API contracts remain identical
- No configuration changes required
- No database migrations needed

### Deployment
- Can be deployed independently
- No coordination with frontend required
- No downtime needed

### Rollback
- If issues arise, revert the two lines in `auth_service.py`
- No data migration to roll back

---

## Verification Checklist

- [ ] `hmac` imported in `auth_service.py`
- [ ] `validate_token()` uses `hmac.compare_digest` for shared token comparison
- [ ] `verify_shared_token()` uses `hmac.compare_digest` for shared token comparison
- [ ] All existing unit tests pass
- [ ] All new unit tests pass
- [ ] All existing integration tests pass
- [ ] All new integration tests pass
- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] `mypy .` passes
- [ ] `pytest --cov=backend` passes with >80% coverage
- [ ] Manual timing test (optional) shows no significant timing difference

---

## References

- **Issue**: #155
- **CWE**: [CWE-208: Observable Timing Discrepancy](https://cwe.mitre.org/data/definitions/208.html)
- **Python Docs**: [`hmac.compare_digest`](https://docs.python.org/3/library/hmac.html#hmac.compare_digest)
- **OWASP**: [Timing Attack](https://owasp.org/www-community/attacks/Timing_Attack)

---

## Related Issues

- **#31**: Localhost bypass authentication (auth flow)
- **#56**: Committed JWT signing keys (security)
- **#48**: Backend project setup (foundation)

---

*Spec created as part of agent-team workflow. Ready for SWE implementation.*