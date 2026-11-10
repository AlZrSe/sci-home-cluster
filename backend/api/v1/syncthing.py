"""
Syncthing API endpoints.
Provides status and manual scan trigger for Syncthing synchronization.
"""

from fastapi import APIRouter, Depends, Request, Response
from pathlib import Path
import httpx
from backend.core.config import settings
from backend.core.deps import get_current_token_payload
from backend.core.errors import syncthing_unavailable
from backend.services.syncthing_service import SyncthingService
from shared.schemas.syncthing_status import SyncthingStatus
from shared.schemas import HTTPValidationError

# Reverse proxy for the embedded Syncthing GUI (Docker compose / co-located).
# Exposes the loopback-only Syncthing web UI through the backend's single
# public port so the iframe in SyncthingDashboard can render it.
proxy_router = APIRouter()

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
}


async def _proxy_to_syncthing(request: Request, upstream_path: str) -> Response:
    """Forward an HTTP request to the co-located Syncthing GUI."""
    # Reuse the standard auth (incl. localhost bypass + auth cookie).
    await get_current_token_payload(request)

    base = f"http://{settings.SYNCTHING_GUI_HOST}:{settings.SYNCTHING_GUI_PORT}"
    url = base + upstream_path
    if request.url.query:
        url = f"{url}?{request.url.query}"

    body = await request.body()
    headers = {}
    for k, v in request.headers.items():
        if k.lower() in _HOP_BY_HOP or k.lower() == "host":
            continue
        headers[k] = v

    async with httpx.AsyncClient(timeout=30.0) as client:
        upstream = await client.request(
            request.method,
            url,
            headers=headers,
            content=body if body else None,
        )

    resp_headers = {}
    for k, v in upstream.headers.items():
        if k.lower() in _HOP_BY_HOP:
            continue
        resp_headers[k] = v

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )

router = APIRouter()


def get_syncthing_service(request: Request) -> SyncthingService:
    """Get the Syncthing service instance from app state."""
    return request.app.state.syncthing_service  # type: ignore[no-any-return]


@router.get(
    "/status",
    response_model=SyncthingStatus,
    responses={
        503: {"model": HTTPValidationError, "description": "Syncthing not configured"},
    },
)
async def get_syncthing_status(
    request: Request, payload: dict = Depends(get_current_token_payload)
):
    """
    Get Syncthing sync service status.
    Returns information about the service state and Syncthing folder.
    """
    # Check if Syncthing is configured
    if not settings.SYNCTHING_ROOT or not Path(settings.SYNCTHING_ROOT).exists():
        raise syncthing_unavailable(
            "Syncthing is not configured on the server. "
            "Set SYNCTHING_ROOT env var and restart the API server."
        )

    # Get service from app state, handle if not started
    service = getattr(request.app.state, "syncthing_service", None)
    syncthing_root = Path(settings.SYNCTHING_ROOT)
    if service is None:
        # Service not started yet - return basic status without Syncthing-specific info
        return {
            "running": False,
            "root_path": settings.SYNCTHING_ROOT,
            "jobs_dir_exists": (syncthing_root / "jobs").exists(),
            "nodes_dir_exists": (syncthing_root / "nodes").exists(),
            "processed_files_count": 0,
            "jobs_folder": {
                "exists": (syncthing_root / "jobs").exists(),
                "path": str(syncthing_root / "jobs"),
                "state_files": 0,
            },
            "nodes_folder": {
                "exists": (syncthing_root / "nodes").exists(),
                "path": str(syncthing_root / "nodes"),
                "state_files": 0,
            },
            "gui": {
                "host": settings.SYNCTHING_GUI_HOST,
                "port": settings.SYNCTHING_GUI_PORT,
                "url": f"http://{settings.SYNCTHING_GUI_HOST}:{settings.SYNCTHING_GUI_PORT}",
                "reachable": settings.SYNCTHING_GUI_HOST != "127.0.0.1",
            },
        }

    try:
        status_data = service.get_status()
    except Exception:
        # Service exists but get_status failed
        return {
            "running": False,
            "root_path": settings.SYNCTHING_ROOT,
            "jobs_dir_exists": (syncthing_root / "jobs").exists(),
            "nodes_dir_exists": (syncthing_root / "nodes").exists(),
            "processed_files_count": 0,
            "jobs_folder": {
                "exists": (syncthing_root / "jobs").exists(),
                "path": str(syncthing_root / "jobs"),
                "state_files": 0,
            },
            "nodes_folder": {
                "exists": (syncthing_root / "nodes").exists(),
                "path": str(syncthing_root / "nodes"),
                "state_files": 0,
            },
            "gui": {
                "host": settings.SYNCTHING_GUI_HOST,
                "port": settings.SYNCTHING_GUI_PORT,
                "url": f"http://{settings.SYNCTHING_GUI_HOST}:{settings.SYNCTHING_GUI_PORT}",
                "reachable": settings.SYNCTHING_GUI_HOST != "127.0.0.1",
            },
        }

    # Add folder info
    jobs_dir = Path(settings.SYNCTHING_ROOT) / "jobs"
    nodes_dir = Path(settings.SYNCTHING_ROOT) / "nodes"

    status_data["jobs_folder"] = {
        "exists": jobs_dir.exists(),
        "path": str(jobs_dir),
    }
    status_data["nodes_folder"] = {
        "exists": nodes_dir.exists(),
        "path": str(nodes_dir),
    }

    # Count files
    if jobs_dir.exists():
        job_files = list(jobs_dir.rglob("state.yaml"))
        status_data["jobs_folder"]["state_files"] = len(job_files)
    else:
        status_data["jobs_folder"]["state_files"] = 0

    if nodes_dir.exists():
        node_files = list(nodes_dir.glob("*.yaml"))
        status_data["nodes_folder"]["state_files"] = len(node_files)
    else:
        status_data["nodes_folder"]["state_files"] = 0

    # Add Syncthing GUI info
    # On Render/container, GUI (8384) not publicly reachable (single public port).
    # Access via port-forward or locally.
    status_data["gui"] = {
        "host": settings.SYNCTHING_GUI_HOST,
        "port": settings.SYNCTHING_GUI_PORT,
        "url": f"http://{settings.SYNCTHING_GUI_HOST}:{settings.SYNCTHING_GUI_PORT}",
        "reachable": settings.SYNCTHING_GUI_HOST != "127.0.0.1",
    }

    return status_data


@router.post("/scan")
async def trigger_syncthing_scan(
    request: Request, payload: dict = Depends(get_current_token_payload)
):
    """
    Trigger a manual scan of the Syncthing folder.
    Scans for new or updated job/node state files.
    """
    service = get_syncthing_service(request)

    # Check if Syncthing is configured
    if not settings.SYNCTHING_ROOT or not Path(settings.SYNCTHING_ROOT).exists():
        raise syncthing_unavailable(
            "Syncthing is not configured on the server. "
            "Set SYNCTHING_ROOT env var and restart the API server."
        )

    result = await service.manual_scan()
    return {
        "message": "Scan completed",
        "scanned": result["scanned"],
        "total_processed": result["total_processed"],
    }


# ---------------------------------------------------------------------------
# Reverse proxy for the embedded Syncthing GUI (Docker compose / co-located).
# ---------------------------------------------------------------------------


@router.api_route(
    "/gui",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
@router.api_route(
    "/gui/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_syncthing_gui(request: Request, path: str = ""):
    upstream_path = "/" + path.lstrip("/") if path else "/"
    return await _proxy_to_syncthing(request, upstream_path)


@proxy_router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_syncthing_rest(request: Request, path: str = ""):
    upstream_path = "/rest/" + path.lstrip("/") if path else "/rest/"
    return await _proxy_to_syncthing(request, upstream_path)
