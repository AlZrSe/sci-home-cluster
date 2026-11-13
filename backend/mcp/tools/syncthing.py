"""MCP tools for Syncthing."""

from pathlib import Path

from backend.core.config import settings


def _get_service(ctx):
    svc = getattr(ctx, "syncthing_service", None)
    if svc is not None:
        return svc
    # Fallback: try app state via a global
    try:
        from backend.mcp.runtime import get_syncthing_service

        return get_syncthing_service()
    except Exception:
        return None


async def syncthing_status(ctx, args: dict) -> dict:
    if not settings.SYNCTHING_ROOT or not Path(settings.SYNCTHING_ROOT).exists():
        raise ValueError("syncthing_unavailable")
    service = _get_service(ctx)
    syncthing_root = Path(settings.SYNCTHING_ROOT)
    if service is None:
        return {
            "running": False,
            "root_path": settings.SYNCTHING_ROOT,
            "jobs_dir_exists": (syncthing_root / "jobs").exists(),
            "nodes_dir_exists": (syncthing_root / "nodes").exists(),
            "processed_files_count": 0,
        }
    try:
        status_data = service.get_status()
    except Exception:
        status_data = {
            "running": False,
            "root_path": settings.SYNCTHING_ROOT,
            "jobs_dir_exists": (syncthing_root / "jobs").exists(),
            "nodes_dir_exists": (syncthing_root / "nodes").exists(),
            "processed_files_count": 0,
        }
    return dict(status_data)


async def syncthing_scan(ctx, args: dict) -> dict:
    if not settings.SYNCTHING_ROOT or not Path(settings.SYNCTHING_ROOT).exists():
        raise ValueError("syncthing_unavailable")
    service = _get_service(ctx)
    if service is None:
        raise ValueError("syncthing_unavailable")
    result = await service.manual_scan()
    return {
        "message": "Scan completed",
        "scanned": result["scanned"],
        "total_processed": result["total_processed"],
    }


def register(server):
    server.register_tool(
        "syncthing.status",
        "Get Syncthing sync service status.",
        {"type": "object", "properties": {}},
        syncthing_status,
    )
    server.register_tool(
        "syncthing.scan",
        "Trigger a manual scan of the Syncthing folder.",
        {"type": "object", "properties": {}},
        syncthing_scan,
        mutating=True,
    )
