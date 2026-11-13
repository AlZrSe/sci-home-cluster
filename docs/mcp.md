# MCP (Model Context Protocol) Support

Scientific Home Cluster exposes an MCP server so external AI clients and
scientific shells can launch and monitor jobs on the home cluster.

## Transports

- **stdio**: `run-mcp` (entry point `backend.mcp.transports.stdio:main`).
  JSON-RPC 2.0 over stdin/stdout, one message per line.
- **SSE/HTTP**: mounted in the FastAPI app at `MCP_SSE_PATH` (default `/mcp`).
  - `GET /mcp/sse` opens the SSE stream (auth via `Authorization: Bearer <jwt>`).
  - `POST /mcp/messages` accepts JSON-RPC requests.

## Authentication

- Reuses the existing Bearer JWT scheme.
- stdio: token from `SHC_MCP_TOKEN` / `SHC_TOKEN` env, or localhost bypass.
- HTTP: `Authorization: Bearer <jwt>`. Tokens are never accepted via URL.
- Read-only mode: set `MCP_READ_ONLY=true` or token claim `mcp_scope: "read"`.

## Claude Desktop example

```json
{
  "mcpServers": {
    "scientific-home-cluster": {
      "command": "run-mcp",
      "env": {
        "SHC_MCP_TOKEN": "<jwt>",
        "SYNCTHING_ROOT": "/path/to/syncthing"
      }
    }
  }
}
```

## Tools

See `docs/specs/163-mcp-support.md` for the full tool/resource/prompt list.
