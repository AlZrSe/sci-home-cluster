"""
Syncthing API endpoints.
Provides status and manual scan trigger for Syncthing synchronization.
"""

from fastapi import APIRouter, Request
from pathlib import Path
from backend.core.config import settings
from backend.services.syncthing_service import SyncthingService

router = APIRouter()


def get_syncthing_service(request: Request) -> "SyncthingService":
    """Get the Syncthing service instance from app state."""
    return request.app.state.syncthing_service


@router.get("/status")
async def get_syncthing_status(request: Request):
    """
    Get Syncthing sync service status.
    Returns information about the service state and Syncthing folder.
    """
    service = get_syncthing_service(request)
    status = service.get_status()

    # Add folder info
    jobs_dir = Path(settings.SYNCTHING_ROOT) / "jobs"
    nodes_dir = Path(settings.SYNCTHING_ROOT) / "nodes"

    status["jobs_folder"] = {
        "exists": jobs_dir.exists(),
        "path": str(jobs_dir),
    }
    status["nodes_folder"] = {
        "exists": nodes_dir.exists(),
        "path": str(nodes_dir),
    }

    # Count files
    if jobs_dir.exists():
        job_files = list(jobs_dir.rglob("state.yaml"))
        status["jobs_folder"]["state_files"] = len(job_files)
    else:
        status["jobs_folder"]["state_files"] = 0

    if nodes_dir.exists():
        node_files = list(nodes_dir.glob("*.yaml"))
        status["nodes_folder"]["state_files"] = len(node_files)
    else:
        status["nodes_folder"]["state_files"] = 0

    return status


@router.post("/scan")
async def trigger_syncthing_scan(request: Request):
    """
    Trigger a manual scan of the Syncthing folder.
    Scans for new or updated job/node state files.
    """
    service = get_syncthing_service(request)
    result = await service.manual_scan()
    return {
        "message": "Scan completed",
        "scanned": result["scanned"],
        "total_processed": result["total_processed"],
    }
