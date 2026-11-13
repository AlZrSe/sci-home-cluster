"""Request context for a single MCP tool/resource/prompt invocation."""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from backend.core.config import settings


@dataclass
class MCPContext:
    """Carries auth + transport + read-only state for one invocation."""

    transport: str = "stdio"  # "stdio" | "sse"
    payload: Dict[str, Any] = field(default_factory=dict)
    read_only: bool = False
    syncthing_service: Any = None
    tool_allowlist: Optional[set] = None
    tool_denylist: Optional[set] = None

    @property
    def subject(self) -> str:
        return str(self.payload.get("sub", "anonymous"))

    def is_read_only(self) -> bool:
        if self.read_only:
            return True
        if settings.MCP_READ_ONLY:
            return True
        scope = self.payload.get(settings.MCP_SCOPE_TOKEN_CLAIM)
        if scope == "read":
            return True
        return False

    def tool_enabled(self, name: str) -> bool:
        allow = self.tool_allowlist
        if allow is None:
            raw = settings.MCP_TOOL_ALLOWLIST
            if raw:
                allow = {x.strip() for x in raw.split(",") if x.strip()}
        if allow is not None and name not in allow:
            return False
        deny = self.tool_denylist
        if deny is None:
            raw = settings.MCP_TOOL_DENYLIST
            if raw:
                deny = {x.strip() for x in raw.split(",") if x.strip()}
        if deny is not None and name in deny:
            return False
        return True


MUTATING_TOOLS = {
    "jobs.create",
    "jobs.delete",
    "jobs.retry",
    "jobs.cancel",
    "nodes.create",
    "syncthing.scan",
    "profiles.create",
    "profiles.update",
    "profiles.delete",
}
