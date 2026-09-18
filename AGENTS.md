# Scientific Home Cluster — Agent Instructions

## Quick Reference
Distributed platform for running scientific workloads on a home GPU cluster.
**Phase 1**: Backend Foundation (FastAPI + SQLite + Syncthing sync). Frontend ✅ Complete.

| Area | Command |
|------|---------|
| Frontend dev | `cd frontend && npm run dev` |
| Frontend build | `cd frontend && npm run build` |
| Frontend lint | `cd frontend && npm run lint` |
| Frontend test | `cd frontend && npm run test` |
| Backend lint | `ruff check . && ruff format . && mypy .` |
| Backend test | `pytest --cov=backend --cov=agent --cov=cli` |
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
├── frontend/          # React dashboard (Vite + TanStack + Tailwind) — COMPLETE
├── backend/           # FastAPI + SQLite API server (planned)
├── shared/            # Shared schemas, types, utilities (planned)
├── agent/             # Worker node agent (planned)
├── cli/               # Typer CLI for job submission (planned)
├── tests/             # Integration/e2e tests (planned)
├── docker/            # Docker configs (planned)
├── docs/spec.md       # Frontend specification
├── openapi.yaml       # API contract
├── PROCESS.md         # Development workflow
└── AGENTS.md          # This file
```

---

## Code Conventions

### TypeScript (Frontend)
- **Imports**: `@/` alias for `src/`, relative for siblings
- **Naming**: PascalCase components, camelCase hooks/utils, kebab-case files
- **Types**: Zod schemas for validation, infer types from schemas
- **Services**: Use `ServiceFactory` (auto-selects mock on localhost)

### Python (Backend)
- **Style**: `ruff` (format + lint), `mypy` strict mode
- **Imports**: Absolute from package root (`from backend.api import routes`)
- **Naming**: snake_case functions/vars, PascalCase classes, UPPER_CASE constants
- **Types**: Full type hints, Pydantic models for API schemas
- **Async**: `async def` for I/O, `await` all async calls

---

## Testing
| Level | Target | Command |
|-------|--------|---------|
| Unit | >80% coverage | `pytest --cov` (backend), `vitest --coverage` (frontend) |
| Integration | API endpoints, service layer | `pytest tests/integration` |
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