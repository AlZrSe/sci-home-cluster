# Specification: E2E Typecheck Advisory Workflow

**Issue**: #138
**Status**: Groomed
**Author**: PM Agent
**Date**: 2026-10-22

---

## 1. User Stories

### Primary Story
> **As a** maintainer of the scientific-home-cluster project  
> **I want** the e2e TypeScript typecheck (`npm run typecheck:e2e`) to run automatically on a schedule  
> **So that** the 8 known type errors in `frontend/tests/e2e/**` remain measured and visible without blocking CI

### Secondary Story
> **As a** contributor  
> **I want** to manually trigger the e2e typecheck via `workflow_dispatch`  
> **So that** I can verify the current error count before/after my changes

### Tertiary Story
> **As a** project lead  
> **I want** the error count posted to the GitHub Actions step summary  
> **So that** regressions (e.g., 9 errors when it was 8) are immediately visible without digging into logs

---

## 2. Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|--------------|
| AC1 | Workflow file exists at `.github/workflows/advisory.yml` | `ls -la .github/workflows/advisory.yml` |
| AC2 | Workflow triggers on `schedule` (weekly) and `workflow_dispatch` only | Check `on:` section; no `pull_request`, no `push` |
| AC3 | Workflow runs `npm run typecheck:e2e` in the `frontend/` directory | Check job steps |
| AC4 | Workflow captures the printed error count and writes to `$GITHUB_STEP_SUMMARY` | Step summary shows "E2E Typecheck Errors: N" |
| AC5 | Workflow exits 0 regardless of error count (no `continue-on-error`, no `|| true`) | Workflow succeeds even with 8 errors |
| AC6 | Workflow is NOT added to `main` branch protection required status checks | Branch protection rules unchanged |
| AC7 | The 8 known errors (7×TS2345 in `job-lifecycle.spec.ts`, 1×TS7006 in `node-monitoring.spec.ts`) are still reported | Run workflow, verify output matches known baseline |

---

## 3. Test Scenarios

### Scenario 1: Scheduled Run (Happy Path)
- **Given**: It's the scheduled time (e.g., Monday 00:00 UTC)
- **When**: The workflow triggers automatically
- **Then**: 
  - Job runs `npm run typecheck:e2e` in `frontend/`
  - Output shows 8 errors with `file:line` references
  - Step summary records "E2E Typecheck Errors: 8"
  - Workflow completes with success (green check)

### Scenario 2: Manual Dispatch
- **Given**: A contributor clicks "Run workflow" in Actions tab
- **When**: Workflow executes on demand
- **Then**: Same as Scenario 1

### Scenario 3: Regression Detection
- **Given**: A change introduces a 9th type error in `tests/e2e/`
- **When**: Workflow runs (scheduled or manual)
- **Then**: 
  - Step summary shows "E2E Typecheck Errors: 9"
  - Maintainers see the increase without failed PR checks

### Scenario 4: No False Failures
- **Given**: The 8 baseline errors exist
- **When**: Workflow runs
- **Then**: Workflow status is **success** (not failure), because non-gating is structural

### Scenario 5: Zero-Error Future State
- **Given**: All 8 errors are fixed (future milestone)
- **When**: Workflow runs and reports 0 errors
- **Then**: 
  - Step summary shows "E2E Typecheck Errors: 0"
  - Team can then fold e2e into main `typecheck` and delete `tsconfig.e2e.json` (per issue second half)

---

## 4. Technical Approach

### 4.1 Workflow File Structure

```yaml
# .github/workflows/advisory.yml
name: Advisory — E2E Typecheck

on:
  schedule:
    - cron: '0 0 * * 1'  # Weekly on Monday 00:00 UTC
  workflow_dispatch: {}

permissions:
  contents: read
  actions: read  # needed for step summary

jobs:
  e2e-typecheck:
    name: E2E TypeScript Typecheck (Advisory)
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - name: Checkout repository (with submodules)
        uses: actions/checkout@v4
        with:
          submodules: recursive

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        run: npm ci

      - name: Run E2E typecheck
        id: typecheck
        run: |
          # npm run typecheck:e2e exits 0 always; captures stderr+stdout
          output=$(npm run typecheck:e2e 2>&1)
          echo "$output"
          # Extract error count from tsc output (last line typically has "Found X errors")
          error_count=$(echo "$output" | grep -oE 'Found [0-9]+ errors?' | grep -oE '[0-9]+' | tail -1)
          error_count=${error_count:-0}
          echo "error_count=$error_count" >> $GITHUB_OUTPUT

      - name: Write summary
        run: |
          echo "### E2E Typecheck Advisory Report" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "**Errors found:** ${{ steps.typecheck.outputs.error_count }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "**Baseline:** 8 known errors (7×TS2345 in job-lifecycle.spec.ts, 1×TS7006 in node-monitoring.spec.ts)" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "**Status:** ${ { steps.typecheck.outputs.error_count > 8 && '⚠️ REGRESSION' || '✅ Baseline maintained' } }" >> $GITHUB_STEP_SUMMARY
```

### 4.2 Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Separate workflow file (not in `ci.yml`) | Structural non-gating: no risk of accidental `required: true` in branch protection |
| `schedule` + `workflow_dispatch` only | No PR noise; runs on cadence and on demand |
| No `continue-on-error` | The workflow *succeeds* by design — `npm run typecheck:e2e` exits 0. `continue-on-error` would imply it *could* fail. |
| Submodule checkout with `submodules: recursive` | `frontend/` is a submodule; must fetch it to run the command |
| Error count extraction via regex | `tsc --noEmit` prints "Found N errors" on exit; we parse that for the summary |

### 4.3 Frontend Command Reference

From `frontend/package.json`:
```json
{
  "scripts": {
    "typecheck:e2e": "tsc --noEmit -p tsconfig.e2e.json"
  }
}
```

From `frontend/tsconfig.e2e.json`:
- Targets `tests/e2e/**`
- Runs in Node environment (Playwright)
- 8 known errors tolerated

---

## 5. Edge Cases & Risks

| Risk | Mitigation |
|------|------------|
| **Submodule not checked out** | `actions/checkout@v4` with `submodules: recursive` ensures `frontend/` exists |
| **Node version mismatch** | Pin to Node 20 (LTS) matching frontend `.nvmrc` or `package.json` engines |
| **`npm ci` fails** | Cache keyed on `frontend/package-lock.json`; fallback to `npm install` if cache miss |
| **Error count parsing fails** | Default to 0; summary still renders. Log full output for debugging. |
| **Workflow added to branch protection by mistake** | Document in PR description: "Do not add this workflow to required checks" |
| **Frontend submodule SHA drift** | This workflow reads the submodule at its current SHA in this repo. A gitlink bump here is a separate PR. |
| **Lovable sync** | Frontend commits go to `AlZrSe/rendering-replicate` main; this repo only bumps the gitlink. No conflict. |

---

## 6. Implementation Checklist

- [ ] Create `.github/workflows/advisory.yml` with above content
- [ ] Test locally: `cd frontend && npm run typecheck:e2e` → verify 8 errors, exit 0
- [ ] Push to a test branch, run workflow manually via `workflow_dispatch`
- [ ] Verify step summary renders correctly with error count
- [ ] Confirm workflow does NOT appear in branch protection settings
- [ ] Comment on issue #138 with link to this spec and mark as groomed

---

## 7. References

- **Issue**: #138 (this spec)
- **Parent Issue**: #136 (CI pipeline design)
- **Frontend Repo**: `AlZrSe/rendering-replicate` (submodule at `frontend/`)
- **Baseline Errors**: `frontend/scripts/typecheck-baseline.json` (for main typecheck), `tsconfig.e2e.json` (for e2e)
- **Frontend AGENTS.md**: Lovable sync notice
- **PROCESS.md**: Branch naming, PR conventions