"""Audit logging for MCP tool calls."""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from backend.core.config import settings

logger = logging.getLogger("mcp.audit")

REDACT_KEYS = {
    "token",
    "secret",
    "password",
    "authorization",
    "access_token",
    "shared_token",
}


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(rk in str(k).lower() for rk in REDACT_KEYS):
                out[k] = "[redacted]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def audit_path() -> Optional[Path]:
    raw = settings.MCP_AUDIT_LOG_PATH
    if raw:
        return Path(raw)
    state = settings.SHC_STATE_DIR or str(Path(__file__).resolve().parents[2] / ".shc")
    return Path(state) / "mcp_audit.jsonl"


def emit_record(record: Dict[str, Any]) -> None:
    """Emit an audit record to logger and JSONL file."""
    try:
        logger.info(json.dumps(record, default=str))
    except Exception:
        pass
    p = audit_path()
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


class AuditTimer:
    def __init__(self):
        self.start = time.perf_counter()

    def elapsed_ms(self) -> float:
        return float((time.perf_counter() - self.start) * 1000.0)


def make_record(
    transport: str,
    subject: str,
    tool: str,
    args: Any,
    status: str,
    duration_ms: float,
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    from datetime import datetime, timezone

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transport": transport,
        "subject": subject,
        "tool": tool,
        "args": _redact(args if isinstance(args, (dict, list)) else {"value": args}),
        "status": status,
        "error_code": error_code,
        "duration_ms": round(duration_ms, 2),
    }
