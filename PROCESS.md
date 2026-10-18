# Scientific Home Cluster Development Process

This document outlines the development workflow using the agent-team skill with specialized roles: Product Manager (PM), Software Engineer (SWE), Tester/QA, and On-Call Engineer.

## Current Phase Status

| Phase | Component | Status | GitHub Issue |
|-------|-----------|--------|--------------|
| 1 | Project Setup | Done | #1 |
| 1 | Shared Schemas & File Operations | Groomed | #2 |
| 1 | API Server Core | Ready for grooming | #3 |
| 1 | Syncthing Sync Service | Ready for grooming | #4 |
| **1** | **Frontend Dashboard** | **✅ Done** | **#5** |

**Active Phase**: Phase 1 - Backend Foundation (Issues #1-4)
**Completed**: Frontend Dashboard (Issue #5) — React + TypeScript + Vite + Tailwind dashboard with mock backend

## Backlog Management

We use GitHub Issues as our backtrack with the following labels:
- `ready-for-grooming`: Issue is ready for PM to create specification
- `groomed`: PM has created spec, ready for SWE to implement
- `in-progress`: SWE is implementing the feature
- `needs-review`: SWE has completed implementation, waiting for QA verification
- `done`: QA has verified, PM has accepted, ready to merge
- `frontend`: Frontend/UI work
- `phase-1`: Backend foundation (setup, schemas, API server, sync service)
- `phase-2`: Backend integration (agent, CLI, real backend connect)
- `phase-3`: Production hardening (Docker, CI/CD, monitoring)

## Role Workflow

### 1. Product Manager (PM)
- Grooms issues from `ready-for-grooming` to `groomed`
- Creates detailed specification with:
  - User stories (As a/I want/So that)
  - Acceptance criteria (Given/When/Then format)
  - Test scenarios (unit, integration, e2e)
  - Technical notes and API contracts
- Performs final acceptance review after QA verification
- Only PM can move issue to `done`

### 2. Software Engineer (SWE)
- Implements features based on PM's specification
- Writes unit and integration tests
- Works on `groomed` issues, moves to `in-progress` when starting
- Opens Pull Request when implementation is complete
- Addresses feedback from QA

### 3. Tester/QA
- Verifies implementation against acceptance criteria
- Runs all tests (unit, integration, e2e)
- Reports pass/fail status with evidence
- Moves issue to `needs-review` after verification
- If fails, returns to SWE with specific feedback

### 4. On-Call Engineer
- Monitors CI/CD pipelines after code is pushed to main
- Fixes any pipeline failures that occur
- Only active after code is merged to main

## Where the code lives

Backend, `shared/`, `agent/`, `cli/` and `docs/` live in **this** repository.

**`frontend/` is a git submodule** pointing at `https://github.com/AlZrSe/rendering-replicate.git`.
It is not vendored here and must not be.

Consequences for the workflow below:

- A pull request on a frontend issue **carries a gitlink bump only** — a one-line change to the SHA
  recorded for `frontend/`. It contains no frontend code, so reviewing it does not review the change.
- Frontend work is therefore two commits in two repositories:
  1. the code, in the submodule (`git -C frontend ...`), and
  2. the gitlink bump here, without which the change is invisible to anyone cloning this repository.
- Per the recorded team decision for issue #28, frontend commits go **straight to `main`** of
  `rendering-replicate` — no branch/PR dance in that repository. Branches and PRs still apply to
  everything in this one.
- `frontend/AGENTS.md` carries a Lovable sync notice: commits pushed to `main` of the submodule
  appear in the Lovable editor, so keep every one of them in a working state.
- Verify delivery with a clean recursive clone: `git clone --recurse-submodules
  https://github.com/AlZrSe/sci-home-cluster.git` must yield the new frontend code.

## Where the compose tests live

`backend/tests/compose/` holds an opt-in suite that runs the **checked-in**
`docker-compose.yaml` against a real Docker daemon (issue #134) — the layer
between "the image builds" and "the API answers", which
`backend/tests/integration/` (in-process `ASGITransport`) structurally cannot
reach: it never runs the Dockerfile, never publishes a port, never mounts a
volume, never bind-mounts the Syncthing folder, and never runs the `HEALTHCHECK`.

Three consequences for the workflow:

- **It is not part of `make test`.** The `compose` marker and
  `SHC_COMPOSE_TESTS=1` gate it; without that variable every test in the
  directory is SKIPPED. `make test-integration` is also unaffected — it is
  path-based (`backend/tests/integration`), so adding a fifth kind of test there
  would have silently made an existing developer command need Docker and publish
  a port.
- **It is destructive-adjacent and says so.** It creates and deletes volumes
  named `shc-e2e-*`; it never touches `shc-data` or `shc-state`. See
  `docker/README.md` § "Testing the compose file".
- **A PR that adds compose tests has to run them.** "Written" is not "verified";
  see the Definition of Done.

## Definition of Done

A task is considered done when:
- [ ] Code implements all acceptance criteria
- [ ] Unit tests pass (>80% coverage)
- [ ] Integration tests pass (where applicable)
- [ ] If the change touches `docker-compose.yaml`, `docker/Dockerfile` or anything under `docker/`, the
      opt-in compose suite has been **run against a real Docker daemon**, not just written:
      `make test-compose`. It is opt-in (`SHC_COMPOSE_TESTS=1`) and it is destructive-adjacent — it
      creates and deletes volumes named `shc-e2e-*` and never `shc-data`/`shc-state`, but it must
      only ever be pointed at your own checkout. See `docker/README.md` § "Testing the compose file"
      before the first run on a new machine. `make test` does **not** run it and does not need Docker.
- [ ] No backend lint/type errors (`ruff check . && ruff format --check . && mypy .`, from the repository root — `--check` because the bare formatter rewrites files and exits 0, and the root because `cd backend && mypy .` finds no `pyproject.toml`; see AGENTS.md § "Two spellings of the lint gate that are not the gate")
- [ ] No frontend lint/type errors (`cd frontend && npm run lint && npm run typecheck`) — and read `frontend/README.md#type-checking` first: `npm run typecheck` covers `src/` and the vitest suite but **not** `tests/e2e/**`, and `npm run build` does not check types at all
- [ ] Documentation updated (README, API docs)
- [ ] PM has performed final acceptance review
- [ ] Code is merged to main branch via Pull Request

## Branch Naming Convention

Use `task/<issue-number>-<short-description>` for feature branches.
Example: `task/123-add-user-authentication`

## Pull Request Requirements

All PRs must:
- Target the `main` branch
- Include tests for new functionality
- Pass all CI checks
- Be reviewed and approved by at least one team member
- Squash and merge when possible

## Issue Templates

We use GitHub's built-in issue templates located in `.github/ISSUE_TEMPLATE/`:

1. **Bug Report**: For reporting defects
2. **Feature Request**: For requesting new features
3. **Task**: For development tasks (used with agent-team workflow)

## Getting Started

1. Clone the repository (with the frontend submodule): `git clone --recurse-submodules https://github.com/AlZrSe/sci-home-cluster.git`
2. Create a feature branch: `git checkout -b task/123-feature-name`
3. Develop and test your changes — frontend code goes in the `frontend/` submodule, see "Where the code lives"
4. Push and open a Pull Request
5. Follow the workflow: PM grooms → SWE implements → QA verifies → PM accepts → merge

*Last updated: $(date)*