# Scientific Home Cluster — Agent Instructions

## MANDATORY: Use Agent Team Workflow
All development work MUST follow the agent-team workflow with PM, SWE, QA, and On-Call Engineer roles. Direct implementation without proper grooming, implementation, and verification cycles is prohibited. See the "Agent-Team Workflow" section below for details.

## Quick Reference
Distributed platform for running scientific workloads on a home GPU cluster.
**Phase 1**: Backend Foundation (FastAPI + SQLite + Syncthing sync). Frontend ✅ Complete.

| Area | Command |
|------|---------|
| Frontend dev | `cd frontend && npm run dev` |
| Frontend build | `cd frontend && npm run build` |
| Frontend lint | `cd frontend && npm run lint` |
| Frontend test | `cd frontend && npm test` (vitest unit + integration; e2e is `npm run test:e2e`) |
| Backend lint | `ruff check . && ruff format . && mypy .` |
| Backend test | `pytest --cov=backend` |
| API server | `uvicorn backend.main:app --reload` |
| Worker agent | `python agent/run_agent.py --node-id node-01` |

---

## Agent-Team Workflow
This project uses the [agent-team skill](.opencode/skills/agent-team/SKILL.md) with four roles:

### When to Invoke Each Subagent
| Role | Trigger | Example Prompt |
|------|---------|----------------|
| **PM** | New issue in `ready-for-grooming`, or need spec/acceptance criteria | "Groom issue #3: create spec for API Server Core" |
| **SWE** | Issue is `groomed`, need implementation + tests | "Implement API Server Core per spec in docs/spec.md" |
| **QA** | PR opened, issue in `needs-review` | "Verify PR #42 against acceptance criteria for issue #3" |
| **On-Call** | CI/CD fails on `main` branch | "Fix failing pipeline after merge to main" |

### Issue Label Flow
`ready-for-grooming` → `groomed` (PM) → `in-progress` (SWE) → `needs-review` (QA) → `done` (PM accepts)

See: `PROCESS.md#role-workflow` and `.opencode/skills/agent-team/SKILL.md`

### Issue Management
All tasks and bugs should be managed exclusively through GitHub Issues, not local files. This ensures:
- Transparency across the team
- Proper tracking of work states
- Integration with the agent-team workflow
- Clear audit trail of decisions and changes
- Automatic linking of commits, branches, and PRs to issues

Never create or modify local task files (e.g., .todo.md, .groomed.md) as they will be ignored by the workflow.

### Code Commit Convention
After every agent team cycle (PM grooming, SWE implementation, QA verification), the orchestrator should commit any code changes to the repository with a descriptive commit message following conventional commits format. This ensures that progress is tracked and the main branch remains stable.

**Commit Message Format:**
- `feat: <description>` for new features
- `fix: <description>` for bug fixes
- `docs: <description>` for documentation changes
- `refactor: <description>` for code refactoring
- `test: <description>` for test additions/changes
- `chore: <description>` for maintenance tasks

**Example:** After completing work on issue #8 (Backend Project Setup):
```
feat: set up backend project structure with FastAPI and dependencies
```

---

## Project Structure
```
scientific-home-cluster/
├── frontend/          # git SUBMODULE — see "Frontend lives in another repo" below
├── backend/           # FastAPI + SQLite API server (COMPLETE)
├── shared/            # Shared schemas, types, utilities (COMPLETE)
├── agent/             # Worker node agent (stub)
├── cli/               # Typer CLI for job submission (stub)
├── backend/tests/     # Unit & integration tests
├── docker/            # Docker configs (planned)
├── docs/              # Architecture specs
├── openapi.yaml       # API contract
├── PROCESS.md         # Development workflow
└── AGENTS.md          # This file
```

---

## Frontend lives in another repo

`frontend/` is a **git submodule** pointing at `https://github.com/AlZrSe/rendering-replicate.git`.
It is not vendored into this repository and must not be.

**A pull request here carries a gitlink bump only** — a one-line change to the SHA recorded for
`frontend/`. It contains no frontend code. Frontend work has two steps:

1. Commit the code in the submodule (`git -C frontend ...`), per decision 4 straight to `main` of
   `rendering-replicate` unless a branch is called for.
2. Commit the resulting gitlink bump here, or the change is invisible to anyone cloning this
   repository.

Verify delivery with a clean recursive clone — `git clone --recurse-submodules` must yield the
new frontend code. `frontend/AGENTS.md` also carries a Lovable sync notice: commits pushed to
`main` of the submodule appear in the Lovable editor, so keep each one in a working state.

---

## Code Conventions

### TypeScript (Frontend)
- **Imports**: `@/` alias for `src/`, relative for siblings
- **Naming**: PascalCase components, camelCase hooks/utils, kebab-case files
- **Types**: Zod schemas for validation, infer types from schemas
- **Services**: Import the wrappers from `@/services` (`src/services/index.ts`), never an implementation. `VITE_CLUSTER_BACKEND=mock` — exactly that string — is the only way to reach `mockService`; unset or anything else resolves to `httpService`. There is no reachability probe. `mockService` / `httpService` are only importable from `src/services/testing.ts`, which ESLint restricts to tests.

### Python (Backend)
- **Style**: `ruff` (format + lint), `mypy` (lenient; not strict mode)
- **Imports**: Absolute from package root (`from backend.api import routes`)
- **Naming**: snake_case functions/vars, PascalCase classes, UPPER_CASE constants
- **Types**: Full type hints, Pydantic models for API schemas
- **Async**: `async def` for I/O, `await` all async calls

---

## Testing
| Level | Target | Command |
|-------|--------|---------|
| Unit | >80% coverage | `pytest --cov=backend` |
| Integration | API endpoints, service layer | `pytest backend/tests/integration` |
| E2E | Critical user flows | Planned: Playwright |

**Definition of Done**: All tests pass + no lint/type errors + PM acceptance. See: `PROCESS.md#definition-of-done`, `docs/spec.md#testing-strategy`

---

## Git & PR Conventions
- **Branches**: `task/<issue-number>-<short-desc>` (e.g., `task/3-api-server-core`)
- **PRs**: Target `main`, include tests, pass CI, 1 approval, squash merge
- **Commits**: Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`)

See: `PROCESS.md#branch-naming-convention`, `PROCESS.md#pull-request-requirements`

---

## Environment & Auth
- **Syncthing**: Required on all nodes. Set `SYNCTHING_ROOT` env var.
- **Python**: `python -m venv venv && pip install -e .`
- **Auth**: Single shared Bearer token. **Localhost bypass**: On `localhost`, `127.0.0.1`, `.local`, `.lovable.app` → token auto-set to `"localhost-no-auth"` (see `docs/spec.md#authentication`)
- **Settings**: Frontend uses `localStorage` (`shc.settings`, `shc.profiles`)

---

## Important References
- **Frontend spec**: `docs/spec.md`
- **API contract**: `openapi.yaml`
- **Workflow**: `PROCESS.md`
- **Frontend AGENTS.md**: `frontend/AGENTS.md` (Lovable connection note)
- **Agent-team skill**: `.opencode/skills/agent-team/SKILL.md`