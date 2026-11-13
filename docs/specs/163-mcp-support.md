# Specification: MCP (Model Context Protocol) Support (Issue #163)

## Summary

Add a first working MCP server that exposes the Scientific Home Cluster backend to
external AI clients, agents, and scientific shells (Claude Desktop, Cursor, Cline,
OpenResearch, K-Dense BYOK, etc.). The server reuses the existing service layer
(`JobService`, `NodeService`, `SyncthingService`) and shared Pydantic schemas; it
does **not** duplicate business logic. Two transports are supported:

- **stdio** for local clients (Claude Desktop, Cursor, Cline).
- **SSE/HTTP** for remote/web clients and scientific shells, mounted inside the
  existing FastAPI process.

Authentication, authorization, read-only mode, audit, resources, and prompts are
included so the MCP surface is safe and self-describing.

### MVP slice implemented now

- MCP protocol core: `initialize`, `tools/list`, `tools/call`, `resources/list`,
  `resources/read`, `resources/subscribe`, `resources/unsubscribe`, `prompts/list`,
  `prompts/get`, `ping`.
- Full tool set listed in the issue (jobs, nodes, syncthing, cluster health).
- stdio + SSE/HTTP transports.
- Auth parity with REST (Bearer JWT, localhost bypass, no token in URL).
- Global read-only mode + tool allow/deny lists.
- Call auditing (structured, redacted).
- Resources with URIs from the issue + subscriptions (best-effort notifications).
- Prompts: `run_experiment`, `analyze_results`, `compare_metrics`.
- Unit + integration + contract tests.

### Out of scope for this MVP

- New compute primitives (GPU allocation, queue scheduler).
- Arbitrary remote code execution without auth.
- Migrating frontend profiles into MCP (issue #91) beyond read access.
- Guaranteed exactly-once delivery of subscription notifications (polling fallback).
- Scientific-shell-specific adapters beyond documented config examples.

---

## User Stories

### US-1: AI client operator - local cluster access via stdio
> As an **AI client operator**, I want to **connect Claude Desktop/Cursor/Cline to
> my home cluster over stdio** so that **the assistant can launch and monitor jobs
> on my GPUs**.

Acceptance criteria:
- Given a running backend and `SHC_TOKEN` (or localhost bypass), when the MCP
  server is launched via stdio, then `initialize` succeeds and `tools/list`
  returns the full tool set.
- Given a valid tool call, when `tools/call` is invoked, then it delegates to
  the same service used by REST and returns the same payload shape.

### US-2: Remote client - SSE/HTTP access
> As a **remote/web client**, I want to **connect over SSE/HTTP with a Bearer
> token** so that **I can use the cluster from a browser or scientific shell**.

Acceptance criteria:
- Given a valid `Authorization: Bearer <jwt>`, when a client opens
  `GET /mcp/sse`, then the SSE stream is established.
- Given an invalid/missing token, when a client posts to `/mcp/messages`, then
  the server returns 401 with no token leakage.

### US-3: Analytical client - read-only mode
> As an **analytical client**, I want a **read-only mode** so that **I can
> inspect jobs, logs, and metrics without mutating state**.

Acceptance criteria:
- Given `MCP_READ_ONLY=true`, when a mutating tool (`jobs.create`,
  `jobs.delete`, `jobs.retry`, `jobs.cancel`, `nodes.create`,
  `syncthing.scan`, profile writes) is called, then the tool returns an MCP
  error `read_only_mode` and no state changes.
- Given read-only mode, when a read tool is called, then it succeeds normally.

### US-4: Operator - audit trail
> As an **operator**, I want **every MCP tool call audited** so that **I can
> trace who did what and when**.

Acceptance criteria:
- Given any tool call (success or failure), when it completes, then an audit
  record is emitted with timestamp, transport, subject, tool name, redacted args,
  status, and duration.

### US-5: Scientific shell - structured results
> As a **scientific shell**, I want **structured job status, metrics, and
> artifact paths** so that **I can poll and analyze results programmatically**.

Acceptance criteria:
- Given `jobs.create` with a `JobSpec`, when the job is created, then the
  returned `JobState` matches the REST response shape and includes `job_id`.
- Given `jobs.get`/`jobs.metrics`/`jobs.logs`, when polled, then they return the
  same data as REST.

---

## Acceptance Criteria (Given/When/Then)

### AC-1: Server bootstrap
- Given the backend package is importable, when the MCP server starts (stdio or
  HTTP), then it initializes the database/store and Syncthing service exactly
  once and shuts them down cleanly.

### AC-2: Tool registry completeness
- Given `tools/list`, when called by any client, then it returns all tools in
  the table below with JSON Schema `inputSchema` derived from Pydantic/OpenAPI.

### AC-3: Auth parity
- Given a request without a valid token outside localhost bypass, when any tool
  is called over HTTP, then the server returns 401 and no data.
- Given localhost bypass conditions, when no token is provided, then the server
  behaves like REST localhost bypass.

### AC-4: No token in URL
- Given any MCP transport, when a client attempts token via query parameter,
  then it is ignored/rejected (never logged).

### AC-5: Read-only mode
- Given `MCP_READ_ONLY=true`, when a mutating tool is called, then it returns
  an error and the underlying service is not invoked.

### AC-6: Audit
- Given a tool call, when it finishes, then exactly one audit record is emitted.

### AC-7: Resources
- Given `resources/read` for `job://{job_id}`, when the job exists, then it
  returns the JobState JSON; when missing, then an MCP resource-not-found error.

### AC-8: Subscriptions
- Given `resources/subscribe` for a job resource, when called, then the server
  records the subscription and emits a notification on subsequent change
  (best-effort; polling remains valid).

### AC-9: Prompts
- Given `prompts/get` for `run_experiment`, when called with arguments, then it
  returns a message list referencing `jobs.create`.

### AC-10: Contract tests
- Given the test suite, when run, then MCP tool schemas match the shared
  Pydantic/OpenAPI schemas (no drift).

### AC-11: CI
- Given CI, when MCP tests are added, then existing gates remain green and MCP
  tests run in a dedicated job or marked `mcp`.

---

## Technical Design

### Module layout

```
backend/
  mcp/
    __init__.py
    server.py            # MCPServer: registry + dispatch
    context.py           # MCPContext (auth payload, read_only, transport)
    auth.py              # token validation helpers (reuse core.deps)
    audit.py             # redacted audit logging
    tools/
      __init__.py
      jobs.py
      nodes.py
      syncthing.py
      cluster.py
      profiles.py        # optional read-only profile access
    resources.py
    prompts.py
    transports/
      __init__.py
      stdio.py           # JSON-RPC over stdin/stdout
      sse.py             # SSE/HTTP (FastAPI router)
cli/
  mcp.py               # optional entrypoint (or backend.mcp.transports.stdio:main)
backend/tests/
  unit/test_mcp_*.py
  integration/test_mcp_flow.py
```

### Settings additions (`backend/core/config.py`)

- `MCP_ENABLED: bool = True`
- `MCP_READ_ONLY: bool = False`
- `MCP_TOOL_ALLOWLIST: Optional[str] = None` (comma-separated; None = all)
- `MCP_TOOL_DENYLIST: Optional[str] = None`
- `MCP_AUDIT_LOG_PATH: Optional[str] = None` (default `<SHC_STATE_DIR>/mcp_audit.jsonl`)
- `MCP_SSE_PATH: str = "/mcp"` (mount point)
- `SHC_BACKEND_URL: Optional[str] = None` (standalone client mode, future)
- `SHC_MCP_TOKEN: Optional[str] = None` (stdio token override)

Environment variables follow existing naming; `.env.example` updated.

### Transports

**stdio** (`backend/mcp/transports/stdio.py`):
- Reads JSON-RPC 2.0 requests (one per line) from stdin, writes responses to
  stdout.
- Bootstraps backend in-process: `init_database`, `run_migrations`, `get_store`,
  Syncthing service start (if root exists).
- Auth subject from `SHC_MCP_TOKEN` (JWT) or localhost bypass when unset locally.
- Entry point registered in `pyproject.toml`: `run-mcp = "backend.mcp.transports.stdio:main"`.

**SSE/HTTP** (`backend/mcp/transports/sse.py`):
- FastAPI `APIRouter` mounted at `settings.MCP_SSE_PATH`.
- `GET {path}/sse` establishes SSE stream (auth via `get_current_token_payload`).
- `POST {path}/messages` accepts JSON-RPC requests and returns results via SSE.
- Reuses existing CORS and middleware.

### Auth

- Reuse `backend.core.deps.get_current_token_payload` for HTTP; reuse
  `decode_access_token` for stdio.
- No token via query string (issue #156). Tokens only in env (stdio) or
  `Authorization` header (HTTP).
- Localhost bypass mirrors REST (`is_localhost`/`is_loopback_ip`).
- Read-only scope can also be derived from token payload claim `mcp_scope`
  (`"read"` forces read-only) in addition to global env.

### Tool list (names / args / returns)

Tool names use dotted form as in the issue. All tools return JSON-serializable
dicts matching REST response models.

**Jobs**

| Tool | Args | Returns |
|------|------|---------|
| `jobs.list` | `status?`, `node_id?`, `search?`, `limit=10`, `offset=0` | `{items: JobState[], total}` |
| `jobs.get` | `job_id` | `JobState` |
| `jobs.create` | `spec: JobSpec` | `JobState` (201) |
| `jobs.delete` | `job_id` | `{deleted: true}` |
| `jobs.retry` | `job_id` | `JobState` |
| `jobs.cancel` | `job_id`, `issued_by?` | `JobState` |
| `jobs.metrics` | `job_id` | `JobMetrics` |
| `jobs.logs` | `job_id` | `LogEntry[]` |
| `jobs.logs.history` | `job_id` | `LogEntry[]` |
| `jobs.logs.stream` | `job_id`, `cursor?` | `{job_id, available, next_cursor?, lines}` |
| `jobs.commands.list` | `job_id` | `{items, total}` |

**Nodes**

| Tool | Args | Returns |
|------|------|---------|
| `nodes.list` | (none) | `NodeSpec[]` |
| `nodes.get` | `node_id` | `NodeSpec` |
| `nodes.metrics` | `node_id` | `JobMetrics` |
| `nodes.create` | `node_spec: NodeSpec` | `NodeSpec` |

**Syncthing**

| Tool | Args | Returns |
|------|------|---------|
| `syncthing.status` | (none) | `SyncthingStatus` |
| `syncthing.scan` | (none) | `{message, scanned, total_processed}` |

**Cluster**

| Tool | Args | Returns |
|------|------|---------|
| `cluster.health` | (none) | health object (same as `/api/v1/health`) |

Mutating tools: `jobs.create`, `jobs.delete`, `jobs.retry`, `jobs.cancel`,
`nodes.create`, `syncthing.scan`. (Profile writes if exposed are also mutating.)

### Resources

| URI | Returns |
|-----|---------|
| `job://{job_id}` | `JobState` |
| `job://{job_id}/logs` | `LogEntry[]` |
| `job://{job_id}/metrics` | `JobMetrics` |
| `node://{node_id}` | `NodeSpec` |
| `node://{node_id}/metrics` | `JobMetrics` |
| `cluster://status` | health object |

Subscriptions: `job://{job_id}`, `job://{job_id}/logs`, `job://{job_id}/metrics`.
Server emits `notifications/resources/updated` on best-effort detection of change
(store mutation or Syncthing scan). Clients may poll as fallback.

### Prompts

| Prompt | Args | Description |
|--------|------|-------------|
| `run_experiment` | `name`, `command`, `working_dir?`, `resources?` | Guides creation of a `JobSpec` and calls `jobs.create`. |
| `analyze_results` | `job_id` | Guides fetching status, logs, metrics, artifacts. |
| `compare_metrics` | `job_ids` (array) | Guides multi-job metric comparison. |

### Read-only mode

- Config `MCP_READ_ONLY` (env) globally disables mutating tools.
- Token claim `mcp_scope: "read"` forces read-only for that client.
- Tool allow/deny lists (`MCP_TOOL_ALLOWLIST`, `MCP_TOOL_DENYLIST`) further
  restrict exposure.
- Mutating tool calls return MCP error code `read_only_mode` / `tool_disabled`
  and do not invoke services.

### Audit

- Every tool call emits one structured record to logger `mcp.audit` and to
  `MCP_AUDIT_LOG_PATH` (JSONL).
- Fields: `timestamp`, `transport` (`stdio`|`sse`), `subject`, `tool`, `args`
  (secrets redacted), `status` (`ok`|`error`), `error_code?`, `duration_ms`.
- Redaction: keys containing `token`, `secret`, `password`, `authorization` are
  replaced with `"[redacted]"`.

---

## Test Scenarios

### Unit
- `test_mcp_auth.py`: token validation, localhost bypass, read-only scope.
- `test_mcp_tools_jobs.py`: each job tool delegates to `JobService` (mocked) and
  maps errors.
- `test_mcp_tools_nodes.py`, `test_mcp_tools_syncthing.py`, `test_mcp_tools_cluster.py`.
- `test_mcp_readonly.py`: mutating tools blocked, reads allowed.
- `test_mcp_audit.py`: one record per call, redaction.
- `test_mcp_resources.py`: read success/not-found, subscription registration.
- `test_mcp_prompts.py`: prompt rendering.

### Integration
- `test_mcp_stdio_flow.py`: launch stdio server in-process, call initialize +
  tools/list + jobs.create/get + resources/read.
- `test_mcp_sse_flow.py`: FastAPI TestClient, SSE connect, auth 401, job flow.
- `test_mcp_contract.py`: compare MCP input schemas to Pydantic/OpenAPI.

### Contract
- Generate JSON schema from Pydantic models and assert equality with MCP
  `inputSchema` for each tool.

---

## Definition of Done

- MCP server runs over stdio and SSE/HTTP without duplicating business logic.
- All listed tools implemented and tested (unit + integration).
- Auth parity verified; read-only mode documented and tested.
- Resources and prompts implemented.
- Audit logging implemented and tested.
- Contract tests green.
- `.env.example` and docs updated; connection examples for Claude Desktop/Cursor
  and scientific shells.
- CI includes MCP tests without breaking existing gates.
- PM acceptance.
