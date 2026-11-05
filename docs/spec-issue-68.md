# Spec: Shared logging setup (Issue #68)

**Issue**: #68 — "Logging is configured only in backend/main.py"
**Author**: PM
**Date**: 2026-11-05

## 1. Problem
Only `backend/main.py` configured logging, so other entrypoints (alembic, CLI, tests subprocesses) emitted no INFO records.

## 2. Implementation
- Add `backend/core/logging.py` with `configure_logging(level=None)`.
- Wire `backend/main.py`, `backend/alembic/env.py`, `cli/command.py`, `backend/tests/conftest.py`.

## 3. Acceptance Criteria
- AC-1: `configure_logging()` callable from any entrypoint.
- AC-2: Gates green.

## 4. Definition of Done
Gates green.