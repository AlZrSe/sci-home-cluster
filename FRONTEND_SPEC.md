# Lovable.dev Prompt: Scientific Home Cluster Web UI

## Project Overview
Build a **React + Vite + Tailwind CSS** dashboard for a distributed scientific computing platform ("Scientific Home Cluster"). This platform runs long-running scientific applications across multiple computers connected via Syncthing.

**Backend API**: FastAPI server (to be implemented) with SQLite database, single bearer token auth.

---

## Core Features to Implement

### 1. Authentication
- Single shared bearer token (stored in localStorage)
- Login page with token input
- Token validation on API calls
- Persist token across sessions

### 2. Job Management Dashboard

#### Job List Page (`/jobs`)
- **Table/Grid** with columns: Job ID, Name, Status, Node, GPU %, CPU %, Created, Actions
- **Status badges**: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED (color-coded)
- **Filters**: Status dropdown, Node dropdown, Date range, Search by name/ID
- **Pagination** or infinite scroll
- **Real-time updates** via WebSocket or polling (5-10s interval)

#### Job Detail Page (`/jobs/:id`)
- **Header**: Job name, status badge, node assignment, timestamps
- **Tabs**:
  - **Overview**: Job spec (YAML), resource requirements, paths
  - **Logs**: Real-time log streaming via WebSocket (`GET /jobs/{id}/logs`), follow mode, search/filter, download
  - **Metrics**: GPU/CPU charts (memory, utilization, temperature) - use Recharts or Chart.js
  - **Actions**: Retry (with confirmation modal), Cancel, Delete
- **YAML Viewer**: Syntax-highlighted job spec and state

#### Job Submission Modal/Page
- **Form** with fields:
  - Name (required)
  - Command (required) - the script/command to run
  - Working directory
  - Environment variables (key-value pairs)
  - Resource requirements: GPU count, CPU cores, Memory (GB)
  - Input/Output paths (Syncthing-relative)
  - Retry policy: max retries, retry delay
- **YAML Preview** tab showing generated job.yaml
- **Validation** before submit
- POST to `/jobs`

### 3. Node Management (`/nodes`)

#### Node List
- **Card or Table** view: Node ID, Status (ONLINE/OFFLINE), GPU info, CPU cores, Memory, Last heartbeat, Current job
- **Status indicator**: Green (online), Red (offline >90s), Yellow (degraded)
- **Filter** by status

#### Node Detail (`/nodes/:id`)
- **Specs**: GPU model, VRAM, CPU cores, RAM, OS
- **Current Job**: Link to job detail
- **Metrics History**: Charts for GPU/CPU over time
- **Heartbeat timeline**

### 4. Settings Page (`/settings`)
- API base URL configuration
- Bearer token management
- Theme toggle (light/dark)
- WebSocket reconnection settings

---

## API Contract (from backend specs)

### Base URL
```
http://localhost:8000/api/v1  (configurable)
```

### Authentication
```
Authorization: Bearer <token>
```

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/jobs` | Create job (multipart/form-data: job.yaml + optional files) |
| GET | `/jobs` | List jobs (?status=&node=&limit=&offset=) |
| GET | `/jobs/:id` | Get job details |
| GET | `/jobs/:id/logs` | WebSocket: stream logs |
| GET | `/jobs/:id/metrics` | Get metrics (summary + history) |
| POST | `/jobs/:id/retry` | Retry failed job |
| POST | `/jobs/:id/cancel` | Cancel running job |
| DELETE | `/jobs/:id` | Delete job |
| GET | `/nodes` | List nodes |
| GET | `/nodes/:id` | Get node details |
| POST | `/nodes/register` | Register/update node |

### Data Models (TypeScript)

```typescript
type JobStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';

interface JobSpec {
  name: string;
  command: string;
  working_dir?: string;
  env?: Record<string, string>;
  resources: {
    gpus: number;
    cpus: number;
    memory_gb: number;
  };
  paths: {
    input?: string;
    output?: string;
  };
  retry?: {
    max_retries: number;
    retry_delay_seconds: number;
  };
}

interface JobState {
  job_id: string;
  spec: JobSpec;
  status: JobStatus;
  node_id?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  exit_code?: number;
  error?: string;
  retry_count: number;
}

interface NodeSpec {
  node_id: string;
  hostname: string;
  gpus: Array<{ name: string; memory_gb: number }>;
  cpus: number;
  memory_gb: number;
  os: string;
  status: 'ONLINE' | 'OFFLINE';
  last_heartbeat: string;
  current_job_id?: string;
}

interface GPUMetric {
  timestamp: string;
  gpu_index: number;
  memory_used_mb: number;
  memory_total_mb: number;
  utilization_percent: number;
  temperature_c: number;
}

interface CPUMetric {
  timestamp: string;
  cpu_percent: number;
  memory_percent: number;
}

interface JobMetrics {
  job_id: string;
  gpu_metrics: GPUMetric[];
  cpu_metrics: CPUMetric[];
  summary: {
    gpu_memory_min_mb: number;
    gpu_memory_max_mb: number;
    gpu_memory_avg_mb: number;
    gpu_util_min: number;
    gpu_util_max: number;
    gpu_util_avg: number;
    cpu_avg_percent: number;
  };
}
```

---

## UI/UX Requirements

### Design System
- **Tailwind CSS** with custom config
- **Color palette**: 
  - Primary: Indigo/Blue (scientific/tech feel)
  - Success: Green
  - Warning: Amber
  - Danger: Red
  - Neutral: Slate
- **Dark mode** support (system preference + manual toggle)
- **Responsive**: Mobile-first, works on 320px+
- **Accessibility**: WCAG AA, keyboard navigation, ARIA labels

### Components Needed
- `StatusBadge` - colored pill for job/node status
- `MetricCard` - value + label + trend
- `LogViewer` - virtualized list, syntax highlighting, follow mode
- `MetricChart` - line/area charts with tooltips
- `YAMLEditor` - Monaco Editor or CodeMirror for YAML with validation
- `ConfirmModal` - for destructive actions (delete, cancel, retry)
- `TokenInput` - masked input with show/hide
- `DataTable` - sortable, filterable, paginated
- `EmptyState` - friendly illustrations for empty lists

### Loading & Error States
- Skeleton loaders for tables/charts
- Toast notifications (success, error, warning)
- Global error boundary
- Offline indicator

---

## Technical Stack

| Layer | Technology |
|-------|------------|
| Framework | React 18 + TypeScript |
| Build | Vite 5 |
| Styling | Tailwind CSS 3 |
| Routing | React Router 6 |
| State | TanStack Query (React Query) + Zustand |
| Charts | Recharts |
| WebSocket | Native WebSocket + reconnection logic |
| Forms | React Hook Form + Zod |
| Code Editor | Monaco Editor (YAML) |
| Icons | Lucide React |
| Date | date-fns |
| Notifications | Sonner or React Hot Toast |

---

## Project Structure

```
web-ui/
├── src/
│   ├── components/
│   │   ├── ui/           # Reusable UI primitives
│   │   ├── jobs/         # Job-specific components
│   │   ├── nodes/        # Node-specific components
│   │   └── layout/       # Sidebar, Header, Footer
│   ├── pages/
│   │   ├── Login.tsx
│   │   ├── JobsList.tsx
│   │   ├── JobDetail.tsx
│   │   ├── JobSubmit.tsx
│   │   ├── NodesList.tsx
│   │   ├── NodeDetail.tsx
│   │   └── Settings.tsx
│   ├── hooks/
│   │   ├── useAuth.ts
│   │   ├── useJobs.ts
│   │   ├── useNodes.ts
│   │   ├── useWebSocket.ts
│   │   └── useTheme.ts
│   ├── services/
│   │   ├── api.ts        # Axios/fetch wrapper with interceptors
│   │   ├── websocket.ts  # WebSocket manager
│   │   └── storage.ts    # localStorage helpers
│   ├── types/
│   │   └── index.ts      # All TypeScript interfaces
│   ├── utils/
│   │   ├── format.ts     # Date, bytes, duration formatting
│   │   └── validation.ts # Zod schemas
│   ├── App.tsx
│   ├── main.tsx
│   └── index.css
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.js
└── postcss.config.js
```

---

## Key User Flows

1. **First Visit** → Login page → Enter token → Redirect to Jobs list
2. **Submit Job** → Click "New Job" → Fill form → Preview YAML → Submit → Redirect to Job Detail
3. **Monitor Job** → Job Detail → Watch logs stream → View metrics charts → Retry if failed
4. **Check Nodes** → Nodes page → See cluster health → Drill into node
5. **Configure** → Settings → Update API URL/Token → Save

---

## Deliverables

1. **Complete working React app** with all pages above
2. **Type-safe API layer** with TanStack Query hooks
3. **WebSocket log streaming** with reconnection
4. **Responsive layout** with collapsible sidebar
5. **Dark/light theme** persisted in localStorage
6. **Error boundaries** and toast notifications
7. **README** with setup instructions

---

## Notes for Lovable

- The backend doesn't exist yet - mock API responses for development
- Use MSW (Mock Service Worker) for API mocking
- Implement WebSocket mock for log streaming
- Focus on **realistic data** in mocks (multiple jobs, nodes, metrics history)
- Make it **production-ready** code quality (TypeScript strict, ESLint, Prettier)
- Include **loading skeletons** and **empty states**
- The job submission should generate valid YAML matching the backend spec
- Metrics charts should handle **time-series data** well

---

## Example Prompt to Start

> "Create a React + TypeScript + Vite + Tailwind dashboard for a distributed scientific computing cluster. The app manages jobs (submit, monitor, retry, cancel) and nodes (view specs, health, metrics) via a FastAPI backend. Key features: JWT-style bearer token auth, real-time log streaming via WebSocket, GPU/CPU metric charts, YAML job editor with preview, dark mode, responsive design. Use TanStack Query for data fetching, Recharts for charts, Monaco Editor for YAML. Mock the API with MSW since backend isn't ready. Production-quality code with TypeScript strict mode."