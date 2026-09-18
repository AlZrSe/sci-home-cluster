# Scientific Home Cluster Development Process

This document outlines the development workflow using the agent-team skill with specialized roles: Product Manager (PM), Software Engineer (SWE), Tester/QA, and On-Call Engineer.

## Current Phase Status

| Phase | Component | Status | GitHub Issue |
|-------|-----------|--------|--------------|
| 1 | Project Setup | Ready for grooming | #1 |
| 1 | Shared Schemas & File Operations | Ready for grooming | #2 |
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

## Definition of Done

A task is considered done when:
- [ ] Code implements all acceptance criteria
- [ ] Unit tests pass (>80% coverage)
- [ ] Integration tests pass (where applicable)
- [ ] No lint/type errors (ruff, mypy)
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

1. Clone the repository: `git clone https://github.com/AlZrSe/sci-home-cluster.git`
2. Create a feature branch: `git checkout -b task/123-feature-name`
3. Develop and test your changes
4. Push and open a Pull Request
5. Follow the workflow: PM grooms → SWE implements → QA verifies → PM accepts → merge

*Last updated: $(date)*