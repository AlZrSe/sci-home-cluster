# Spec: Fix broken Makefile targets and Windows support (Issue #26)

**Issue**: #26 — "Backlog: fix broken Makefile targets, Windows support, and add CI"
**Labels**: linux, ready-for-grooming, windows
**Author**: PM
**Date**: 2026-11-05

## 1. Scope note
CI (`.github/workflows/ci.yml`) already exists and runs the four required gates
(backend-gates, frontend-gates, compose-suite, e2e) — added under #136. This spec
covers the remaining work: broken Makefile targets and Windows-friendly commands.

## 2. Broken targets to fix
- `install` / `install-dev`: `pip install -e .[dev]` from repo root (pyproject is at root).
- `backend-dev`: `uvicorn backend.main:app --reload` from repo root.
- `typecheck`: `mypy .` from repo root (pyproject config).
- `lint` / `format`: run from repo root.
- `backend-build`: remove from `.PHONY`/help (no recipe) or implement; remove.
- `clean`: cross-platform (Python shutil) instead of `rm -rf`/`find`.
- `.PHONY`: add `check`, `frontend-build-dev`, `dev`, `dev-full`.
- `run-agent`: default `SYNCTHING_ROOT` to `./syncthing` when unset.

## 3. Acceptance Criteria
- AC-1: `make install-dev`, `make lint`, `make typecheck`, `make backend-dev` invocations match documented commands.
- AC-2: `make clean` works on Windows (Git Bash / PowerShell via python).
- AC-3: All documented targets are in `.PHONY`.
- AC-4: CI workflow already present (no change).

## 4. Definition of Done
Gates green.