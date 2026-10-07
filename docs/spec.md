# Scientific Home Cluster — Frontend Specification

## Overview

**Purpose**: Web dashboard for monitoring and managing distributed scientific compute jobs across a home GPU cluster.

**Tech Stack**:
- **Framework**: TanStack React Router (file-based routing) + React 19
- **State**: TanStack React Query (server state) + localStorage (client state)
- **Styling**: Tailwind CSS v4 + Radix UI primitives + shadcn/ui patterns
- **Charts**: Recharts
- **Forms**: React Hook Form + Zod validation
- **Real-time**: WebSocket (log streaming) + React Query polling (7s default)
- **Build**: Vite + TanStack Start (SSR capable)

---

## Architecture

```
src/
├── routes/              # File-based routes (TanStack Router)
│   ├── index.tsx        # Jobs dashboard (list + filters + pagination)
│   ├── jobs.new.tsx     # Job submission form + YAML preview
│   ├── jobs.$jobId.tsx  # Job detail (overview, logs, metrics)
│   ├── nodes.index.tsx  # Node list + status cards
│   ├── nodes.$nodeId.tsx# Node detail (specs, GPU/CPU history, heartbeats)
│   ├── profiles.index.tsx     # Profile library (builtin + custom)
│   ├── profiles.new.tsx       # Create profile
│   ├── profiles.$profileId.tsx# Edit profile
│   ├── settings.tsx     # Connection, polling, theme settings
│   └── login.tsx        # Token entry + validation
├── lib/
│   ├── api.ts           # DEPRECATED - 5-line shim, `export * from "@/services"`
│   ├── mock-server.ts   # In-memory mock backend (jobs, nodes, metrics, logs)
│   ├── settings.ts      # localStorage settings + React hook
│   ├── profiles.ts      # localStorage profiles + React hook
│   └── format.ts        # Date/duration/YAML formatting utilities
├── services/            # The only door to the backend
│   ├── index.ts         # Resolver + wrappers (listJobs, createJob, ...)
│   ├── types.ts         # ClusterService contract + ServiceError
│   ├── mock.ts          # In-memory implementation, backed by mock-server.ts
│   ├── http.ts          # fetch/WebSocket implementation against /api/v1
│   └── testing.ts       # Test-only re-export of mockService / httpService
├── components/
│   ├── layout/Shell.tsx # Page shell (title, subtitle, actions)
│   ├── ui/              # Radix-based UI primitives (40+ components)
│   ├── StatusBadge.tsx  # Job/Node status with color coding
│   ├── MetricCard.tsx   # KPI display card
│   ├── MetricChart.tsx  # Recharts wrapper for time-series
│   ├── LogViewer.tsx    # Virtualized log stream with connection status
│   ├── YamlBlock.tsx    # Syntax-highlighted YAML with copy/download
│   ├── ProfileForm.tsx  # Full profile editor with YAML preview
│   └── ...
└── hooks/               # Custom hooks (useMobile, useSettings, useProfiles)
```

---

## Routes & Pages

| Route | Component | Purpose | Key Data Hooks |
|-------|-----------|---------|----------------|
| `/` | `JobsPage` | Job dashboard with filters, pagination, stats cards | `useQuery(['jobs', filters])`, `useQuery(['nodes'])` |
| `/jobs/new` | `SubmitJobPage` | Form + YAML preview for job submission | `useMutation(createJob)`, `useProfiles()` |
| `/jobs/$jobId` | `JobDetailPage` | Live logs, metrics charts, job controls (retry/cancel/delete) | `useQuery(['job', id])`, `useQuery(['job-metrics', id])`, WebSocket stream |
| `/nodes` | `NodesPage` | Node grid with status, specs, current job | `useQuery(['nodes'])` |
| `/nodes/$nodeId` | `NodeDetailPage` | Hardware specs, GPU/CPU history, heartbeat timeline | `useQuery(['node', id])`, `useQuery(['node-metrics', id])` |
| `/profiles` | `ProfilesPage` | Browse builtin/custom profiles, edit/create/delete | `useProfiles()` (localStorage only) |
| `/profiles/new` | `NewProfilePage` | Create new profile | `useProfiles()` |
| `/profiles/$profileId` | `EditProfilePage` | Edit existing profile | `useProfiles()` |
| `/settings` | `SettingsPage` | API URL, token, polling, theme | `useSettings()` (localStorage) |
| `/login` | `LoginPage` | Token entry + validation | `validateToken()` → `setSettings()` |

---

## Data Models (TypeScript)

All defined in `src/lib/types.ts` — mirrored in OpenAPI `components/schemas`:

- `JobStatus`: `"PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"`
- `JobSpec`: Job definition (name, command, resources, paths, retry, env)
- `JobState`: Runtime job with status, timestamps, node assignment, exit code
- `NodeSpec`: Cluster node (GPUs, CPUs, memory, OS, heartbeat, current job)
- `GPUMetric` / `CPUMetric`: Time-series metric points
- `JobMetrics`: Aggregated metrics with summary statistics
- `JobListResult`: Paginated response `{ items: JobState[], total: number }`

---

## API Contract

**Reference**: `openapi.yaml` (project root)

### Endpoint → UI Mapping

| Frontend Feature | API Endpoint | Method | Service Call |
|------------------|--------------|--------|--------------|
| Job list + filters | `/jobs` | GET | `services.jobs.listJobs(q)` |
| Job detail | `/jobs/{id}` | GET | `services.jobs.getJob(id)` |
| Job metrics | `/jobs/{id}/metrics` | GET | `services.jobs.getJobMetrics(id)` |
| Job logs (HTTP) | `/jobs/{id}/logs/history` | GET | `services.jobs.getJobLogs(id)` |
| Job logs (WS) | `/jobs/{id}/logs` | WS | `services.jobs.streamJobLogs(id, onLine, onStatus)` |
| Create job | `/jobs` | POST (JSON) | `services.jobs.createJob(spec)` |
| Retry job | `/jobs/{id}/retry` | POST | `services.jobs.retryJob(id)` |
| Cancel job | `/jobs/{id}/cancel` | POST | `services.jobs.cancelJob(id)` |
| Delete job | `/jobs/{id}` | DELETE | `services.jobs.deleteJob(id)` |
| Node list | `/nodes` | GET | `services.nodes.listNodes()` |
| Node detail | `/nodes/{id}` | GET | `services.nodes.getNode(id)` |
| Node metrics | `/nodes/{id}/metrics` | GET | `services.nodes.getNodeMetrics(id)` |
| Validate token | `/auth/verify` | GET | `services.auth.validateToken(token)` |

### Authentication

- **Scheme**: Bearer token in `Authorization: Bearer <token>`
- **Storage**: localStorage (`shc.settings.token`)
- **Localhost bypass**: On `localhost`, `127.0.0.1`, `::1`, `.local`, `.lovable.app` → token optional, auto-set to `"localhost-no-auth"`. Matching is case-insensitive and strips one surrounding `[` `]` pair, so IPv6 loopback matches in both the browser's `[::1]` form and the backend's `::1` form. The backend additionally accepts `0.0.0.0` (wildcard bind) and `testserver` (test-client default); a browser reports neither.
- **Validation**: `GET /auth/verify` with Bearer header → `{ valid: boolean }`
- **Token min length**: 8 characters
- **Signing key**: `SECRET_KEY` is **locally generated**, not shipped. On first
  use it is written to `SHC_STATE_DIR/secret_key` (default
  `<repo-root>/.shc`, mode `0600` on POSIX) so tokens survive a restart; that
  directory is gitignored at any depth. An explicit `SECRET_KEY` wins and is
  never persisted. Two signing keys were committed to the public repository
  before the ignore rule existed, so they are **revoked**: their SHA-256
  digests are in `backend/core/config.py::PUBLISHED_KEY_SHA256` (digests only,
  never key values) and the first start on current code replaces any key on
  that list with a `WARNING`, invalidating every token issued before it. No key
  or key path is exposed over `GET /health`, which has no auth dependency
  (issue #56).

The bypass reads the client-supplied `Host` header, so **it is a developer-convenience list, not a
security boundary**: any client that can reach the backend and set an arbitrary `Host` can already
claim `localhost` and get the bypass. Adding a loopback-shaped entry therefore grants no capability
`localhost` does not already grant. Behind a real deployment, pin the allowed hosts at the proxy.

The list is duplicated by necessity — `backend/core/utils.py` and
`frontend/src/lib/settings.ts` each keep a literal copy, because the frontend bundler cannot import
from outside its own repository. The entries they share live in
`shared/auth/localhost_hosts.json`, and `backend/tests/unit/test_frontend_alignment.py` fails if the
two lists drift. Change both together.

`.lovableproject.com` was on the frontend's list here and is on neither side's now. It is the one
entry in the whole list that a third party could plausibly present in a `Host` header, so it was
removed from the frontend rather than mirrored into the backend (issue #31). Do not re-add it: the
Lovable preview flow is blocked by CORS, which is a separate issue, not by this list.

### Real-time: WebSocket Log Streaming

- **Endpoint**: `GET /jobs/{id}/logs` (WebSocket upgrade)
- **Messages**: Plain text log lines (one line per message)
- **Status values**: Provided via `onStatus` callback: `"connecting" | "open" | "closed"`
- **Reconnect**: Exponential backoff (configurable via `wsReconnectMs`, default 3000ms)
- **Fallback**: HTTP `GET /jobs/{id}/logs/history` for initial load

### Polling

- **Default interval**: 7000ms (configurable via `pollIntervalMs` in settings)
- **Applied to**: Job list, job detail, node list, node detail
- **Library**: TanStack React Query `refetchInterval`

---

## Profiles System (localStorage Only)

**No backend calls** — entirely client-side.

- **Storage key**: `shc.profiles`
- **Structure**: `{ custom: Profile[], overrides: Record<id, Profile>, hidden: string[] }`
- **Built-in profiles** (9): VASP (std/GPU), LAMMPS, RMCProfile, GROMACS, Quantum ESPRESSO, CP2K, PyTorch
- **Profile fields**: name, software, description, command, working_dir, resources (gpus/cpus/memory), paths (input/output), retry policy, env vars
- **YAML preview**: Live preview in job submission (`jobs.new.tsx`) and profile editor
- **Usage**: Profiles pre-fill job submission form via `?profile=<id>` search param

---

## Settings (localStorage)

**Storage key**: `shc.settings`

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `token` | string | `""` | Bearer token (auto-filled on localhost) |
| `apiBaseUrl` | string | `http://localhost:8000/api/v1` | Backend API base URL |
| `theme` | `"light" \| "dark"` | `"dark"` | UI theme (applies to `<html>`) |
| `wsReconnectMs` | number | `3000` | WebSocket reconnect delay |
| `pollIntervalMs` | number | `7000` | React Query refetch interval |

---

## UI Component Library

| Component | Purpose | Key Props |
|-----------|---------|-----------|
| `Shell` | Page wrapper with title, subtitle, actions | `title`, `subtitle`, `actions` |
| `StatusBadge` | Job/Node status pill | `status`, `lastHeartbeat?` |
| `MetricCard` | KPI display | `label`, `value`, `hint?`, `tone?` |
| `MetricChart` | Time-series chart (Recharts) | `data`, `series[]`, `domain?` |
| `LogViewer` | Virtualized log stream | `lines[]`, `connection`, `jobId` |
| `YamlBlock` | Syntax-highlighted YAML | `yaml`, `filename?` |
| `ProfileForm` | Full profile editor | `initial`, `mode` |
| `ConfirmModal` | Destructive action confirmation | `open`, `onConfirm`, `destructive?` |
| `EmptyState` | Empty list placeholder | `icon`, `title`, `description`, `action?` |

---

## Error Handling

- **ApiError class**: `{ status, message }` thrown by service layer
- **Toast notifications**: `sonner` for success/error (top-right)
- **React Query**: `isError` states with retry buttons
- **Error boundaries**: Not yet implemented (TODO)
- **Validation**: Zod schemas on forms (job submission, profile edit)

---

## Testing Strategy

### Unit Tests (Vitest)
- `src/lib/services/__tests__/*.test.ts` — Mock service implementations
- `src/lib/__tests__/*.test.ts` — Utilities (format, settings, profiles)

### Integration Tests (Vitest + RTL)
- `src/hooks/__tests__/*.test.tsx` — React Query hooks with mocked services
- `src/components/__tests__/*.test.tsx` — Component behavior

### E2E Tests (Planned)
- `e2e/jobs.spec.ts` — Login → list → create → detail → retry/cancel/delete
- `e2e/nodes.spec.ts` — Login → list → detail
- `e2e/profiles.spec.ts` — Login → list → create → edit → delete
- `e2e/settings.spec.ts` — Login → settings → save

### Coverage Targets
- Lines: 70%
- Functions: 70%
- Branches: 60%
- Statements: 70%

---

## TypeScript Generation from OpenAPI (Planned)

**Tool**: `openapi-typescript` (to be added as devDependency)

**Workflow** (to be added):
```bash
# Generate types
npx openapi-typescript openapi.yaml -o src/lib/services/api-types.ts

# Add to package.json scripts
"typegen": "openapi-typescript openapi.yaml -o src/lib/services/api-types.ts"
"build": "npm run typegen && vite build"
```

**Output**: `src/lib/services/api-types.ts` with:
- `paths` — Typed endpoint definitions
- `components.schemas` — All schema types
- `components.parameters` — Typed path/query params

**Usage in services** (to be implemented):
```typescript
// Real service uses generated types for request/response
import type { paths } from './api-types';
type CreateJobResponse = paths['/jobs']['post']['responses']['201']['content']['application/json'];
```

---

## Deployment

- **Build**: `npm run build` → static assets in `dist/`
- **Preview**: `npm run preview`
- **Dev server**: `npm run dev` → `http://localhost:5173` (port set by `@lovable.dev/vite-tanstack-config`, not by this repo; it is what `playwright.config.ts` targets and what the backend CORS allow-list accepts)
- **Environment**:
  - `VITE_CLUSTER_BACKEND` — `mock` selects the in-memory implementation; every other value,
    including unset, selects `httpService`. There is no reachability probe and no fallback.
  - `VITE_API_URL` — the build-time default for `apiBaseUrl` in `src/lib/settings.ts` (falls
    back to `http://localhost:8000/api/v1` when unset). `localStorage["shc.settings"]` still
    overrides it, so a deployed build can be retargeted without a rebuild. All three committed
    env files leave it at localhost, which is what `npm run preview` expects.
  - Committed in `.env.development` (`http`), `.env.production` (`http`) and `.env.test` (`mock`);
    `.env.development.local` is gitignored and is the documented developer escape hatch for
    running the mock in dev.
- **Static hosting**: Any static host (Netlify, Vercel, Cloudflare Pages, nginx)
- **SPA fallback**: Required for client-side routing

---

## Backend Integration

Done — the service layer is wired to the FastAPI backend:

1. `src/services/http.ts` implements `ClusterService` over `fetch` + a WebSocket for
   `/jobs/{id}/logs`, against `{apiBaseUrl}` from settings.
2. It is the default implementation: `.env.development` and `.env.production` both set
   `VITE_CLUSTER_BACKEND=http`.
3. `VITE_CLUSTER_BACKEND=mock` is the only way to reach `src/services/mock.ts`.
4. Network failures are normalised to `ServiceError` with `status === 0` and
   `error_code === "BACKEND_UNREACHABLE"`, so a stopped backend shows a connection error rather
   than "An unexpected error occurred".
5. Not yet wired: node registration endpoint (`POST /nodes/register`) — the frontend only reads nodes.

---

## Open Questions for Implementation

1. **WebSocket message format**: Resolved — frontend uses plain text lines (not JSON `{type, payload}`)
2. **Job creation response**: Resolved — frontend expects and receives `JobState` on 201
3. **Node registration**: Frontend shows "Nodes appear after they register with POST /nodes/register" — consider adding to OpenAPI?
4. **Metrics for nodes**: Resolved — frontend calls `getJobMetrics(\`node:${nodeId}\`)` via real endpoint `GET /nodes/{id}/metrics`
5. **Rate limiting / pagination**: Current offset/limit — consider switching to cursor-based for large datasets?

---

## File Creation Order

1. `openapi.yaml` (project root)
2. `docs/spec.md` (project root)
3. `package.json` — vitest for unit/integration, `@playwright/test` for e2e
4. `vitest.config.ts` (`vitest.config.ts` collects `src/**/*.test.{ts,tsx}` + `tests/integration/**/*.test.{ts,tsx}`; `playwright.config.ts` owns `tests/e2e/`)
5. `src/services/` — the service layer
6. Route imports use `@/services`
7. Test files

---

*Status: implemented. This document is maintained against the working tree.*