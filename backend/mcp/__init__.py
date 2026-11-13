"""MCP (Model Context Protocol) support for Scientific Home Cluster.

This package exposes the backend service layer to external AI clients over
stdio and SSE/HTTP transports. It deliberately contains no business logic of
its own: every tool delegates to ``JobService`` / ``NodeService`` /
``SyncthingService``.
"""

from backend.mcp.server import MCPServer

__all__ = ["MCPServer"]
