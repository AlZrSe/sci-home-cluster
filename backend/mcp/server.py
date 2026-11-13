"""Core MCP server: registry + JSON-RPC dispatch."""

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Set

from backend.mcp.context import MCPContext

logger = logging.getLogger("mcp.server")

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "scientific-home-cluster"
SERVER_VERSION = "0.1.0"


class MCPServer:
    def __init__(self):
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.resources: List[Dict[str, Any]] = []
        self.resource_readers: Dict[str, Callable] = {}
        self.resource_templates: List[Dict[str, Any]] = []
        self.prompts: Dict[str, Dict[str, Any]] = {}
        self.subscriptions: Set[str] = set()
        self._resource_patterns: List[tuple] = []  # (prefix, reader)

    # ---- registration ----
    def register_tool(self, name, description, input_schema, handler, mutating=False):
        self.tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
            "handler": handler,
            "mutating": mutating,
        }

    def register_resource(
        self, uri, name, description, reader, mime_type="application/json"
    ):
        entry = {
            "uri": uri,
            "name": name,
            "description": description,
            "mimeType": mime_type,
        }
        self.resources.append(entry)
        self.resource_readers[uri] = reader

    def register_resource_template(self, uri_template, name, description, reader):
        self.resource_templates.append(
            {"uriTemplate": uri_template, "name": name, "description": description}
        )
        self._resource_patterns.append((uri_template, reader))

    def register_prompt(self, name, description, arguments, handler):
        self.prompts[name] = {
            "name": name,
            "description": description,
            "arguments": arguments,
            "handler": handler,
        }

    # ---- helpers ----
    def _match_resource_reader(self, uri: str):
        if uri in self.resource_readers:
            return self.resource_readers[uri]
        for template, reader in self._resource_patterns:
            # simple prefix/template matching for job:///{id} style
            if self._uri_matches(template, uri):
                return reader
        return None

    @staticmethod
    def _uri_matches(template: str, uri: str) -> bool:
        # Convert {var} segments to wildcard
        import re

        pattern = re.escape(template)
        pattern = pattern.replace(r"\{", "").replace(r"\}", "")  # crude
        # Better: replace {name} with [^/]+
        pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/]+", re.escape(template))
        # re.escape already applied? redo cleanly
        pattern = re.escape(template)
        pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/]+", pattern)
        return re.fullmatch(pattern, uri) is not None

    # ---- dispatch ----
    async def handle(self, request: dict, ctx: MCPContext) -> Optional[dict]:
        """Handle a single JSON-RPC request.

        Returns response dict or None for notification.
        """
        if not isinstance(request, dict):
            return self._error(None, -32600, "Invalid Request")
        jsonrpc = request.get("jsonrpc")
        if jsonrpc != "2.0":
            return self._error(request.get("id"), -32600, "Invalid JSON-RPC version")
        method = request.get("method")
        req_id = request.get("id")
        params = request.get("params") or {}
        is_notification = "id" not in request

        try:
            result = await self._dispatch(method, params, ctx)
        except Exception as e:
            logger.exception("MCP handler error")
            if is_notification:
                return None
            return self._error(req_id, -32603, str(e))

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    async def _dispatch(self, method, params, ctx):  # noqa: C901
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": True, "listChanged": False},
                    "prompts": {"listChanged": False},
                    "logging": {},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        if method == "ping":
            return {}
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {"tools": [self._tool_public(t) for t in self.tools.values()]}
        if method == "tools/call":
            return await self._call_tool(params, ctx)
        if method == "resources/list":
            return {"resources": list(self.resources)}
        if method == "resources/templates/list":
            return {"resourceTemplates": list(self.resource_templates)}
        if method == "resources/read":
            return await self._read_resource(params, ctx)
        if method == "resources/subscribe":
            uri = params.get("uri")
            self.subscriptions.add(uri)
            return {}
        if method == "resources/unsubscribe":
            uri = params.get("uri")
            self.subscriptions.discard(uri)
            return {}
        if method == "prompts/list":
            return {
                "prompts": [
                    {
                        "name": p["name"],
                        "description": p["description"],
                        "arguments": p["arguments"],
                    }
                    for p in self.prompts.values()
                ]
            }
        if method == "prompts/get":
            return await self._get_prompt(params, ctx)
        if method == "logging/setLevel":
            return {}
        raise ValueError(f"Method not found: {method}")

    def _tool_public(self, t):
        return {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["inputSchema"],
        }

    async def _call_tool(self, params, ctx):
        from backend.mcp.audit import AuditTimer, make_record, emit_record
        from backend.mcp.context import MUTATING_TOOLS

        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in self.tools:
            raise ValueError(f"Unknown tool: {name}")
        tool = self.tools[name]

        # enabled / read-only checks (tool-level errors, not protocol errors)
        try:
            if not ctx.tool_enabled(name):
                raise ValueError("tool_disabled")
            if ctx.is_read_only() and (tool.get("mutating") or name in MUTATING_TOOLS):
                raise ValueError("read_only_mode")
        except Exception as e:
            from backend.mcp.audit import (
                AuditTimer as _AT,
                make_record as _mr,
                emit_record as _er,
            )

            _t = _AT()
            _er(
                _mr(
                    ctx.transport,
                    ctx.subject,
                    name,
                    arguments,
                    "error",
                    _t.elapsed_ms(),
                    str(e),
                )
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": str(e), "code": str(e)}),
                    }
                ],
                "isError": True,
            }

        timer = AuditTimer()
        status = "ok"
        error_code = None
        try:
            result = await tool["handler"](ctx, arguments)
        except Exception as e:
            status = "error"
            error_code = getattr(e, "error_code", None) or type(e).__name__
            # Re-raise as MCP error? We'll convert to tool result with isError
            duration = timer.elapsed_ms()
            record = make_record(
                ctx.transport,
                ctx.subject,
                name,
                arguments,
                status,
                duration,
                error_code,
            )
            emit_record(record)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": str(e), "code": error_code}),
                    }
                ],
                "isError": True,
            }
        duration = timer.elapsed_ms()
        record = make_record(
            ctx.transport, ctx.subject, name, arguments, status, duration, None
        )
        emit_record(record)

        # Normalize result into MCP content
        if isinstance(result, dict) and "content" in result and "isError" in result:
            return result
        text = json.dumps(result, default=str)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": False,
        }

    async def _read_resource(self, params, ctx):
        uri = params.get("uri")
        reader = self._match_resource_reader(uri)
        if reader is None:
            raise ValueError("resource not found")
        result = await reader(ctx, uri)
        if isinstance(result, dict) and "contents" in result:
            return result
        # default wrap
        import json as _json

        text = _json.dumps(result, default=str)
        return {
            "contents": [{"uri": uri, "mimeType": "application/json", "text": text}]
        }

    async def _get_prompt(self, params, ctx):
        name = params.get("name")
        if name not in self.prompts:
            raise ValueError(f"Unknown prompt: {name}")
        prompt = self.prompts[name]
        args = params.get("arguments") or {}
        messages = await prompt["handler"](ctx, args)
        return {"description": prompt["description"], "messages": messages}

    def _error(self, req_id, code, message) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }

    def notify_subscribers(self, uri, payload=None):
        # Best-effort: callers may use this to push notifications; transports
        # handle delivery.
        pass
