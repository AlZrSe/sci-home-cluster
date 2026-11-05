# Spec: Global QueryClient defaults (Issue #49)

**Issue**: #49 — "Global QueryClient defaults: settle the dedupe window, or delete createQueryErrorHandler"
**Author**: PM
**Date**: 2026-11-05

## 1. Decision
Delete the dead `createQueryErrorHandler` (zero call sites; its 5s de-dupe window was
shorter than the 7s poll interval). Keep error handling per-route via `useErrorHandler`.
No global `QueryClient` onError is configured; rationale documented in `router.tsx`.

## 2. Acceptance Criteria
- AC-1: `createQueryErrorHandler` removed; unused import cleaned up.
- AC-2: `router.tsx` documents the decision.
- AC-3: Frontend typecheck + lint clean.

## 3. Definition of Done
Gates green.