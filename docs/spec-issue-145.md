# Specification: Fix Negative Control Grep in Compose Suite CI

## Issue Reference
- **Issue**: #145
- **Title**: compose-suite: negative control grep fails to find skip reason in pytest output
- **Labels**: bug, ready-for-grooming

---

## 1. User Stories

### As a CI/CD Engineer
I want the compose-suite negative control verification to reliably pass so that CI correctly validates the opt-in interlock without false negatives.

### As a Developer
I want the compose-suite CI job to be deterministic so that I can trust green builds and not waste time investigating flaky failures.

### As a Maintainer
I want the negative control to be a genuine proof that the compose suite is opt-in, not a fragile grep that passes or fails based on terminal formatting quirks.

---

## 2. Acceptance Criteria (from Issue #145)

| ID | Criterion | Status |
|----|-----------|--------|
| AC-1 | The negative control step must reliably demonstrate that tests are skipped without `SHC_COMPOSE_TESTS=1` | Required |
| AC-2 | The verification must not be flaky (no race conditions, no formatting-dependent failures) | Required |
| AC-3 | The skip reason must be visible in the log for debugging | Required |
| AC-4 | The grep pattern must match the skip reason even when wrapped or truncated by pytest output formatting | Required |
| AC-5 | The step must pass consistently across multiple runs on GitHub Actions ubuntu-latest runners | Required |

---

## 3. Test Scenarios

### Scenario 1: Negative Control Without Opt-In (Current Failure)
**Given**: CI runs `pytest tests/compose -m compose -v -x -r s --color=no` without `SHC_COMPOSE_TESTS=1`
**When**: The verification step greps for the opt-in skip reason
**Then**: The grep must find at least one occurrence of the opt-in skip reason
**And**: The step must exit 0 (success)

### Scenario 2: Negative Control With Opt-In (Should Not Match)
**Given**: CI runs the compose suite with `SHC_COMPOSE_TESTS=1 SHC_COMPOSE_ALLOW_DESTRUCTIVE=1`
**When**: The verification script checks for opt-in skip reasons
**Then**: No tests should be skipped for the opt-in reason
**And**: At least 48 tests should be collected
**And**: 15+ tests should pass or xfail (config tests + xfail)

### Scenario 3: Pytest Output Variations
**Given**: Different pytest versions, terminal widths, or output formats
**When**: The skip reason is printed by pytest
**Then**: The verification must match regardless of:
- Line wrapping at 80/120 columns
- Truncation with `...` 
- ANSI color codes (disabled via `--color=no`)
- pytest's `-v` vs default verbosity
- `-r s` (show skip reasons in short summary) formatting

### Scenario 4: Edge Case - Collection Errors
**Given**: pytest encounters collection errors before running tests
**When**: The `-x` flag stops at first failure
**Then**: The verification must still detect if any tests were skipped for opt-in reason
**And**: Must not falsely pass if collection failed entirely

---

## 4. Technical Approach

### Root Cause Analysis

The skip reason in `backend/tests/compose/conftest.py` (lines ~141-145):
```python
reason=(
    f"compose suite is opt-in and runs the real Docker daemon: set "
    f"{FORCE_ENV}=1 (issue #134). `make test` and a bare `pytest` are "
    "unaffected."
),
```

With pytest 8.0.0, `-v -r s` produces output where:
- Verbose lines: `test_file.py::test_name SKIPPED [reason]` — reason may be truncated/wrapped
- Short summary (`-r s`): Lists skip reasons but may split long reasons across lines

The current verification uses `grep -q 'opt-in' compose-not-opted-in.log` which should work as a substring match, but fails because:
1. pytest's verbose output truncates the reason on the SKIPPED line
2. The `-r s` summary may wrap the reason across multiple lines
3. The grep only matches if "opt-in" appears on a single line

### Solution Options

#### Option A: Use pytest's `--tb=no -q` with custom output parsing (Recommended)
Run pytest with minimal output and parse the JSON report:
```bash
pytest tests/compose -m compose --collect-only -q --co 2>/dev/null | head -20
```
Actually, better: use `--json-report` or parse JUnit XML.

#### Option B: Use pytest's `--no-header -r s` for cleaner skip report
```bash
pytest tests/compose -m compose -x --no-header -r s --color=no > compose-not-opted-in.log 2>&1; exit 0
```
Then grep the short summary section which has full skip reasons.

#### Option C: Python script verification (Most Robust)
Write a small Python script that:
1. Runs pytest programmatically or parses the log
2. Uses pytest's API to collect skip reasons directly
3. Checks for the opt-in reason programmatically

#### Option D: Improved grep with multiline support
Use `grep -z` (null-separated) or `pcregrep -M` for multiline matching, or read entire file into variable.

### Recommended Approach: Option B + Option D Hybrid

**Primary fix**: Use pytest's short summary (`-r s`) which prints full skip reasons, combined with a grep that reads the entire file (handles wrapping).

**Implementation**:
```bash
# Run pytest capturing full output including short summary
pytest tests/compose -m compose -x -r s --color=no --no-header > compose-not-opted-in.log 2>&1; exit 0

# Verify: read entire file, check for opt-in substring (handles line wrapping)
if ! grep -q 'opt-in' compose-not-opted-in.log; then
    # Fallback: also check with Python for multiline
    python3 -c "
import sys
text = open('compose-not-opted-in.log').read()
if 'opt-in' not in text:
    print('FAIL: opt-in skip reason not found')
    print(text)
    sys.exit(1)
print('OK: opt-in skip reason found')
"
fi
```

**Even better**: Use pytest's `--collect-only` to verify collection without running, then check skip markers:
```bash
pytest tests/compose -m compose --collect-only -q 2>&1 | grep -c "SKIPPED"  # Count skipped
```

Actually, the simplest robust fix: **use Python to parse pytest's output** since we already have Python in the CI environment.

### Detailed Implementation Plan

1. **Modify the negative control step** in `.github/workflows/ci.yml`:
   - Keep pytest command but ensure `--no-header` is used
   - Add explicit `-r s` for short summary with skip reasons

2. **Replace the verification step** with a Python script that:
   - Reads the entire log file as a single string
   - Searches for "opt-in" substring (handles any line wrapping)
   - Also verifies no tests passed (interlock working)
   - Exits with clear error messages

3. **Add debug output** on failure:
   - Print the relevant section of the log
   - Show what skip reasons were found

---

## 5. Edge Cases and Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Pytest version upgrade changes output format | Medium | High | Use substring match on full log, not line-based grep |
| Terminal width changes line wrapping | Low | Medium | Read entire file as single string |
| `--color=no` not respected in some environments | Low | Low | Already in command, but Python fallback ignores colors |
| No tests collected at all (collection error) | Low | High | Verify at least one test was skipped; fail if zero collected |
| Race condition between pytest write and grep read | Low | Medium | Sequential steps in same job, file on disk |
| `SHC_COMPOSE_TESTS` accidentally set in environment | Low | High | Explicitly unset in env block (already done) |
| Docker not available causing collection errors | Medium | Medium | The `-x` flag stops early; verify skip reason still appears |

### Additional Considerations

1. **The `-x` flag**: Stops at first failure. If there's a collection error, pytest exits before running all tests. The skip reason might not appear if the first test fails to collect. Consider removing `-x` for the negative control, or handling collection errors.

2. **Pytest exit code 5**: "No tests collected" — the `exit 0` after pytest handles this, but we should verify tests were actually collected and skipped.

3. **CI Log Limits**: GitHub Actions has log size limits. The compose suite log is uploaded as artifact, but the negative control log is not. Keep it small.

4. **Forward Compatibility**: The fix should work with pytest 8.x and future 9.x.

---

## 6. Implementation Checklist

- [ ] Update `.github/workflows/ci.yml` negative control step
- [ ] Replace grep verification with Python script
- [ ] Add debug output on failure showing skip reasons found
- [ ] Verify the fix works locally (if compose tests exist locally)
- [ ] Test by temporarily breaking the opt-in logic to ensure verification catches it
- [ ] Commit with message: `fix: make compose-suite negative control verification robust to pytest output formatting`

---

## 7. Files to Modify

1. **Primary**: `.github/workflows/ci.yml` — compose-suite job, negative control and verification steps
2. **Reference**: `backend/tests/compose/conftest.py` — skip reason definition (for context only, no change needed)

---

## 8. Verification Plan

1. **Local test** (if compose tests available):
   ```bash
   cd backend
   SHC_COMPOSE_TESTS="" SHC_COMPOSE_ALLOW_DESTRUCTIVE="" pytest tests/compose -m compose -v -x -r s --color=no --no-header > compose-not-opted-in.log 2>&1; exit 0
   python3 -c "
   text = open('compose-not-opted-in.log').read()
   assert 'opt-in' in text, 'opt-in not found'
   print('PASS: opt-in found')
   "
   ```

2. **CI test**: Push to a branch and verify compose-suite job passes negative control step

3. **Negative test**: Temporarily modify conftest.py to remove "opt-in" from skip reason, verify verification step fails

---

## 9. Related Issues

- **Issue #134**: Compose suite opt-in interlock (origin of the skip reason)
- **Issue #136**: CI workflow creation (where this job was added)
- **Issue #138**: E2E typecheck advisory workflow