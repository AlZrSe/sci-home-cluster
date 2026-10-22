# Specification: Node Detail Route Error Handling for Metrics Query Failure

## Issue Summary
The node detail page (`/nodes/$nodeId`) fails to handle errors when the metrics query (`getNodeMetrics`) fails. Currently:
- `nodeQuery` has proper error handling showing "Node {nodeId} could not be loaded."
- `metricsQuery` has **no error path** — when it fails, `gpuData`, `cpuData`, and `heartbeats` become empty arrays `[]`
- The page renders empty charts and shows "Last 0 heartbeats" misleadingly implying no data rather than a failure

---

## 1. User Stories

### US-1: As a cluster operator, I want to see an error message when node metrics fail to load
So that I know the metrics are unavailable rather than assuming the node has no activity.

### US-2: As a cluster operator, I want the heartbeat timeline to show an error state when metrics fail
So that I don't see misleading "Last 0 heartbeats" when the request actually failed.

### US-3: As a cluster operator, I want GPU and CPU charts to show an error state when metrics fail
So that I can distinguish between "no data yet" and "data unavailable due to error."

---

## 2. Acceptance Criteria

| ID | Criterion | Priority |
|----|-----------|----------|
| AC-1 | When `metricsQuery.isError` is true, display an error message in place of the GPU chart: "Failed to load GPU metrics" | P0 |
| AC-2 | When `metricsQuery.isError` is true, display an error message in place of the CPU chart: "Failed to load CPU metrics" | P0 |
| AC-3 | When `metricsQuery.isError` is true, display an error message in the heartbeat timeline section: "Failed to load heartbeat data" | P0 |
| AC-4 | The "Last N heartbeats" text should **not** render when `metricsQuery.isError` is true | P0 |
| AC-5 | When `metricsQuery.isPending` is true, continue showing skeletons (current behavior preserved) | P0 |
| AC-6 | When `metricsQuery.isSuccess` but data arrays are empty, show "No metrics available" (not an error) | P1 |
| AC-7 | Error messages should be dismissible or auto-clear when a retry succeeds | P1 |
| AC-8 | The error state should be visually distinct from empty state and loading state | P0 |

---

## 3. Test Scenarios

### TS-1: Metrics query fails (network error / 5xx / 4xx)
**Given** the node detail page is loaded and `getNodeMetrics` returns an error
**When** the metrics query completes with error
**Then**
- GPU chart area shows "Failed to load GPU metrics" error message
- CPU chart area shows "Failed to load CPU metrics" error message
- Heartbeat timeline shows "Failed to load heartbeat data"
- "Last N heartbeats" text is NOT rendered
- Node info (from `nodeQuery`) still displays correctly

### TS-2: Metrics query pending
**Given** the node detail page is loaded and `getNodeMetrics` is in-flight
**When** `metricsQuery.isPending` is true
**Then**
- GPU chart area shows skeleton loader
- CPU chart area shows skeleton loader
- Heartbeat timeline is hidden or shows skeleton

### TS-3: Metrics query succeeds with data
**Given** the node detail page is loaded and `getNodeMetrics` returns valid data
**When** `metricsQuery.isSuccess` with non-empty arrays
**Then**
- GPU chart renders with data
- CPU chart renders with data
- Heartbeat timeline shows "Last N heartbeats" with correct count
- Heartbeat bars render correctly

### TS-4: Metrics query succeeds but returns empty arrays
**Given** the node detail page is loaded and `getNodeMetrics` returns empty `gpu_metrics` and `cpu_metrics`
**When** `metricsQuery.isSuccess` with empty arrays
**Then**
- GPU chart shows "No metrics available" (not error)
- CPU chart shows "No metrics available" (not error)
- Heartbeat timeline shows "Last 0 heartbeats" (accurate)

### TS-5: Node query fails but metrics query succeeds
**Given** `nodeQuery.isError` but `metricsQuery.isSuccess`
**When** page renders
**Then**
- Node error message displays: "Node {nodeId} could not be loaded."
- Metrics charts still render if metrics succeeded (graceful degradation)

### TS-6: Both queries fail
**Given** both `nodeQuery.isError` and `metricsQuery.isError`
**When** page renders
**Then**
- Node error message displays
- Metrics error messages display in chart areas

### TS-7: Retry after error
**Given** `metricsQuery.isError` is showing error messages
**When** user triggers refetch (e.g., via poll interval or manual refresh)
**And** the subsequent request succeeds
**Then**
- Error messages disappear
- Charts render with new data
- Heartbeat timeline renders correctly

---

## 4. Technical Approach

### 4.1 Modify `nodes.$nodeId.tsx`

Add error handling for `metricsQuery` similar to `nodeQuery`:

```tsx
// Current (lines 43-61):
const metricsQuery = useQuery({
  queryKey: ["node-metrics", nodeId],
  queryFn: () => getNodeMetrics(nodeId),
});

const gpuData = (metricsQuery.data?.gpu_metrics ?? []).map(...);
const cpuData = (metricsQuery.data?.cpu_metrics ?? []).map(...);
const heartbeats = (metricsQuery.data?.cpu_metrics ?? []).slice(-24);

// Proposed:
const metricsQuery = useQuery({
  queryKey: ["node-metrics", nodeId],
  queryFn: () => getNodeMetrics(nodeId),
  retry: 1, // Allow one retry before showing error
});

// Conditional rendering based on metricsQuery state
```

In the render section (after line 130), replace the unconditional chart rendering with conditional logic:

```tsx
// GPU Chart section (lines 132-155)
<div className="panel p-4">
  <h2 className="mb-2 text-sm font-semibold">GPU history</h2>
  {metricsQuery.isPending ? (
    <Skeleton className="h-52 w-full" />
  ) : metricsQuery.isError ? (
    <div className="h-52 flex items-center justify-center text-muted-foreground text-sm">
      Failed to load GPU metrics
    </div>
  ) : gpuData.length === 0 ? (
    <div className="h-52 flex items-center justify-center text-muted-foreground text-sm">
      No GPU metrics available
    </div>
  ) : (
    <MetricChart ... />
  )}
</div>

// CPU Chart section (lines 156-181) - similar pattern

// Heartbeat timeline section (lines 184-202)
<div className="panel mt-4 p-4">
  <h2 className="text-sm font-semibold">Heartbeat timeline</h2>
  {metricsQuery.isError ? (
    <div className="mt-3 text-sm text-destructive">
      Failed to load heartbeat data
    </div>
  ) : metricsQuery.isPending ? (
    <Skeleton className="h-8 w-full" />
  ) : (
    <>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {heartbeats.map((h, i) => (...))}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Last {heartbeats.length} heartbeats, newest on the right.
      </p>
    </>
  )}
</div>
```

### 4.2 Enhance `MetricChart.tsx` (Optional but Recommended)

Add empty data and error state handling to the component itself for reusability:

```tsx
export function MetricChart({
  data,
  series,
  height = 220,
  domain,
  error,
  emptyMessage = "No data available",
}: {
  data: Array<Record<string, number | string>>;
  series: Series[];
  height?: number;
  domain?: [number, number];
  error?: Error | null;
  emptyMessage?: string;
}) {
  if (error) {
    return (
      <div style={{ height }} className="w-full flex items-center justify-center text-muted-foreground text-sm">
        {error.message}
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div style={{ height }} className="w-full flex items-center justify-center text-muted-foreground text-sm">
        {emptyMessage}
      </div>
    );
  }

  // ... existing chart rendering
}
```

Then update `nodes.$nodeId.tsx` to pass error state:
```tsx
<MetricChart
  data={gpuData}
  series={...}
  error={metricsQuery.error}
  emptyMessage="No GPU metrics available"
/>
```

### 4.3 Shared Error Display Component (Future Enhancement)

Consider creating a reusable `QueryError` component for consistent error display across the app.

---

## 5. Edge Cases & Risks

| Risk | Mitigation |
|------|------------|
| **Race condition**: Node query succeeds, metrics query fails, then node refetches and fails | Each query is independent; handle each error state separately. The spec handles this in TS-5/TS-6. |
| **Polling during error**: `refetchInterval` on `metricsQuery` could spam failed requests | Set `retry: 1` or `retry: false` on `metricsQuery`; rely on `nodeQuery`'s poll interval to trigger refetch when node data refreshes. |
| **Partial data**: GPU metrics succeed but CPU metrics fail (or vice versa) | Backend returns both in one response (`JobMetrics`), so they succeed/fail together. If backend changes, handle per-series. |
| **Empty vs Error confusion**: "No metrics" vs "Failed to load" | Distinct visual states: error = destructive color + "Failed" text; empty = muted color + "No data" text. |
| **Accessibility**: Error messages must be announced to screen readers | Use `role="alert"` or `aria-live="polite"` on error messages. |
| **TypeScript**: `metricsQuery.error` type is `Error | null` | Ensure proper typing when passing to `MetricChart`. |

---

## 6. Implementation Checklist

- [ ] Add `metricsQuery.isError` handling in GPU chart section
- [ ] Add `metricsQuery.isError` handling in CPU chart section
- [ ] Add `metricsQuery.isError` handling in heartbeat timeline section
- [ ] Suppress "Last N heartbeats" text when `metricsQuery.isError`
- [ ] Add empty data handling (`gpuData.length === 0`, `cpuData.length === 0`)
- [ ] (Optional) Enhance `MetricChart` with `error` and `emptyMessage` props
- [ ] Add unit tests for error states in `nodes.$nodeId.tsx`
- [ ] Add unit tests for `MetricChart` error/empty props
- [ ] Run `npm run typecheck` and `npm run lint` in frontend
- [ ] Run `npm test` in frontend

---

## 7. Files to Modify

| File | Changes |
|------|---------|
| `frontend/src/routes/nodes.$nodeId.tsx` | Primary: Add error/empty conditional rendering for metrics |
| `frontend/src/components/MetricChart.tsx` | Optional: Add `error` and `emptyMessage` props for reusable error/empty states |

---

## 8. Definition of Done

- [ ] All acceptance criteria (AC-1 through AC-8) verified by QA
- [ ] Test scenarios TS-1 through TS-7 pass
- [ ] No TypeScript errors (`npm run typecheck` passes)
- [ ] No lint errors (`npm run lint` passes)
- [ ] All unit tests pass (`npm test` passes)
- [ ] PM acceptance review complete