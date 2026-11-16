# Spec: Nodes Dashboard — Manual and Automatic Refresh

**Issue**: #176  
**Status**: Groomed  
**Owner**: PM (grooming) → SWE (implementation) → QA (verification) → PM (acceptance)

---

## 1. User Stories

| ID | Story |
|----|-------|
| US-1 | As a cluster operator, I want a **manual Refresh button** on the Nodes dashboard so I can force an immediate data refresh without waiting for the 7-second poll interval. |
| US-2 | As a cluster operator, I want the **Refresh button to show a spinning icon** while the refresh is in progress, so I know the action was registered and is working. |
| US-3 | As a cluster operator, I want the **automatic polling to continue working** (as it does today) so the dashboard stays fresh even when I don't click Refresh. |
| US-4 | As a cluster operator, I want the **Retry button in the error state** to reuse the same refresh logic, so one click repairs the entire view. |

---

## 2. Acceptance Criteria

| ID | Criterion | Testable Check |
|----|-----------|----------------|
| AC-1 | A **Refresh button** appears in the `actions` slot of the `Shell` component on `/nodes/`. | Visual: button with `RefreshCw` icon + "Refresh" label is rendered top-right. |
| AC-2 | The Refresh button has `variant="outline" size="sm"` (matching Jobs dashboard). | Code: `Button` props match exactly. |
| AC-3 | Clicking Refresh triggers `nodesQuery.refetch()`. | Interaction: network request fires; `nodesQuery.isFetching` becomes `true`. |
| AC-4 | While any query on the page is fetching, the Refresh icon spins (`animate-spin`). | Visual: `className={refreshing ? "size-4 animate-spin" : "size-4"}` on `RefreshCw`. |
| AC-5 | The existing `refetchInterval: settings.pollIntervalMs` on `nodesQuery` remains unchanged. | Code: line 50 in `nodes.index.tsx` unmodified. |
| AC-6 | The **Retry button in the error branch** (line 88) calls the same refresh function, not a bare `nodesQuery.refetch()`. | Code: error branch `onClick` uses the shared `refreshAll` (or equivalent). |
| AC-7 | No regression: automatic polling still works; manual refresh works; error retry works. | E2E: wait 8s → data updates; click Refresh → data updates; disconnect backend → click Retry → data updates. |
| AC-8 | TypeScript compiles with zero errors (`npm run typecheck`). | CI gate passes. |
| AC-9 | Lint passes (`npm run lint`). | CI gate passes. |

---

## 3. Test Scenarios

| Scenario | Steps | Expected |
|----------|-------|----------|
| **T-1 Manual refresh (happy path)** | 1. Load `/nodes/`<br>2. Click Refresh button | Button shows spinning icon; `nodesQuery` refetches; icon stops spinning when done; node cards update. |
| **T-2 Manual refresh during poll** | 1. Load `/nodes/`<br>2. Wait for a poll tick (≈7s)<br>3. Click Refresh immediately after | Both poll and manual refresh fire; no double-spin glitch; data consistent. |
| **T-3 Error state → Retry** | 1. Stop backend<br>2. Load `/nodes/` → error view appears<br>3. Start backend<br>4. Click Retry | Retry uses shared refresh; error clears; nodes appear. |
| **T-4 Error state → auto-poll recovers** | 1. Stop backend<br>2. Load `/nodes/` → error view<br>3. Start backend<br>4. Wait for poll interval | Poll tick recovers view without user action (existing behavior preserved). |
| **T-5 Empty state** | 1. Ensure no nodes registered<br>2. Load `/nodes/`<br>3. Click Refresh | No error; empty state persists; button still works. |
| **T-6 Type/lint gates** | Run `npm run typecheck && npm run lint` | Exit code 0; no new diagnostics. |

---

## 4. Technical Notes

### Files to Change
- **Primary**: `frontend/src/routes/nodes.index.tsx`

### Pattern to Mirror (from `frontend/src/routes/index.tsx`)

```tsx
// 1. Compute a `refreshing` flag from all queries on the page
const refreshing = nodesQuery.isFetching;

// 2. Define a shared refresh function (even though there's only one query today)
const refreshAll = () => {
  void nodesQuery.refetch();
};

// 3. Add to Shell.actions:
actions={
  <>
    <Button variant="outline" size="sm" onClick={refreshAll}>
      <RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />
      Refresh
    </Button>
  </>
}

// 4. In the error branch, reuse the same function:
<Button variant="outline" onClick={refreshAll}>
  <RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} /> Retry
</Button>
```

### Imports Already Present
- `RefreshCw` from `lucide-react` (line 4)
- `Button` from `@/components/ui/button` (line 17)
- `useSettings` for `settings.pollIntervalMs` (line 20)
- `useQuery` from `@tanstack/react-query` (line 3)

### No New Dependencies
All required components and hooks are already imported.

---

## 5. Definition of Done

- [ ] Implementation complete in `frontend/src/routes/nodes.index.tsx`
- [ ] All acceptance criteria (AC-1 … AC-9) verified
- [ ] Test scenarios (T-1 … T-6) executed and passing
- [ ] `npm run typecheck` passes (zero errors)
- [ ] `npm run lint` passes
- [ ] `npm test` passes (unit + integration)
- [ ] PM reviews and accepts
- [ ] PR merged to `main` (squash merge, conventional commit: `feat: add manual refresh to nodes dashboard`)

---

## 6. Reference Links

- Jobs dashboard (reference implementation): `frontend/src/routes/index.tsx` (lines 111–124, 130–135, 228–230)
- Target file: `frontend/src/routes/nodes.index.tsx`
- Shared components: `Shell`, `Button`, `RefreshCw` (lucide-react)
- Settings: `useSettings()` → `settings.pollIntervalMs`