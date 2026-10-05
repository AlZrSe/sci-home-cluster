# Scientific Home Cluster — Agent Instructions

## MANDATORY: Use Agent Team Workflow
All development work MUST follow the agent-team workflow with PM, SWE, QA, and On-Call Engineer roles. Direct implementation without proper grooming, implementation, and verification cycles is prohibited. See the "Agent-Team Workflow" section below for details.

## Quick Reference
Distributed platform for running scientific workloads on a home GPU cluster.
**Phase 1**: Backend Foundation (FastAPI + SQLite + Syncthing sync). Frontend ✅ Complete.

| Area | Command |
|------|---------|
| Frontend dev | `cd frontend && npm run dev` |
| Frontend build | `cd frontend && npm run build` (type-blind; `vite build` does not check types) |
| Frontend typecheck | `cd frontend && npm run typecheck` (the gate) |
| Frontend e2e types | `cd frontend && npm run typecheck:e2e` (advisory report; prints 8 known errors, exits 0) |
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
| Backend unit | >80% coverage | `pytest --cov=backend` |
| Backend integration | API endpoints, service layer | `pytest backend/tests/integration` |
| Backend type | Pydantic + hints | `mypy .` (part of `ruff check . && ruff format . && mypy .`) |
| Frontend unit + integration | vitest in jsdom | `cd frontend && npm test` |
| Frontend type | `src/` + the vitest suite | `cd frontend && npm run typecheck` |
| Frontend lint | — | `cd frontend && npm run lint` |
| E2E | Critical user flows | `cd frontend && npm run test:e2e` (Playwright; needs a live backend + dev server) |

**Frontend types are checked — with one recorded gap.** `npm run typecheck` runs
`tsc --noEmit` over `frontend/tsconfig.json` and covers `src/**` *and the whole vitest suite*
(`src/**/*.test.ts` plus `tests/integration/**`) plus `vitest.config.ts` / `vite.config.ts`. It is
ratcheted: **1** known production error in `src/` is recorded in
`frontend/scripts/typecheck-baseline.json` and tolerated, and the gate goes red if that inventory
stops matching — so a new error in `src/` fails, and so does fixing one without shrinking the
baseline. That one entry is a real bug, not type debt: the `async` `streamJobLogs` wrapper in
`src/services/index.ts` makes `jobs.$jobId.tsx` invoke a Promise as its unmount cleanup and leak
the log WebSocket. It is deliberately unfixed — see the `$comment` in that baseline file. Test files
carry no baseline at all.

**`tests/e2e/**` is NOT type-checked.** It is a separate program (`frontend/tsconfig.e2e.json`,
`npm run typecheck:e2e`) with 8 known errors, deliberately excluded from the main program because
Playwright runs in Node against a live backend rather than in jsdom. `npm run typecheck:e2e` prints
all 8 with their `file:line` and **exits 0** — it is an advisory report, not a gate, because a
permanently red npm target gets routed around rather than fixed; `npm run typecheck` is the gate.
The 8 are unfixed, so the gap is recorded in `tsconfig.e2e.json` and nowhere else. So *"`npm run
typecheck` passes" does not mean "everything is checked"* — and `typecheck:e2e` exiting 0 does not
change that. It also does not mean the build is checked: `npm run build` is `vite build`, which
strips types without checking them.

**Definition of Done**: All tests pass + no lint/type errors (backend `ruff`/`mypy`, frontend
`npm run lint` + `npm run typecheck`) + PM acceptance. See:
`PROCESS.md#definition-of-done`, `docs/spec.md#testing-strategy`

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
- **Auth**: Single shared Bearer token. **Localhost bypass**: On `localhost`, `127.0.0.1`, `::1`, `.local`, `.lovable.app` → token auto-set to `"localhost-no-auth"` (case-insensitive, IPv6 brackets stripped; see `docs/spec.md#authentication`). The backend also accepts `0.0.0.0` and `testserver`; the frontend does not. The list is duplicated in `backend/core/utils.py` and `frontend/src/lib/settings.ts` — the shared entries live in `shared/auth/localhost_hosts.json` and a test fails if they drift, so **change both lists together**. It is a developer-convenience list keyed on the client-supplied `Host` header, not a security boundary.
- **Settings**: Frontend uses `localStorage` (`shc.settings`, `shc.profiles`)

---

## Important References
- **Frontend spec**: `docs/spec.md`
- **API contract**: `openapi.yaml`
- **Workflow**: `PROCESS.md`
- **Frontend AGENTS.md**: `frontend/AGENTS.md` (Lovable connection note)
- **Agent-team skill**: `.opencode/skills/agent-team/SKILL.md`