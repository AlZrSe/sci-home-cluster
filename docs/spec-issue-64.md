# Spec: SHARED_TOKEN generation (Issue #64)

**Issue**: #64 — "SHARED_TOKEN is never generated"
**Author**: PM
**Date**: 2026-11-05

## 1. Status
Generation already implemented in `backend/core/config.py::_resolve_shared_token`
and wired into `Settings.model_post_init`. This cycle adds tests and doc updates.

## 2. Acceptance Criteria
- AC-1: stock install can mint a token (already works).
- AC-2: generated token survives restart; restart is silent.
- AC-3: explicit SHARED_TOKEN wins, not persisted, not rotated (settings layer).
- AC-4: token never logged, never on /health.
- AC-5: tests added (`backend/tests/unit/test_shared_token.py`).
- AC-6: README updated.

## 3. Definition of Done
Gates green.