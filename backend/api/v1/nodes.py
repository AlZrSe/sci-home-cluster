"""
Node management API endpoints.

Node registration is file-based via Syncthing folder watcher (issue #92).
The POST /nodes endpoint writes a node spec YAML file to the Syncthing nodes/
folder, which the watcher picks up. GET endpoints provide read-only access
to node data for the frontend.
"""

import os
from typing import Any, Dict, List, Union
from fastapi import APIRouter, Depends, status, Request
from backend.core.config import settings
from backend.core.deps import get_current_token_payload
from backend.core.errors import APIError, node_not_found
from shared.schemas.node_spec import NodeSpec
from shared.schemas.job_metrics import JobMetrics
from shared.schemas.error_response import ErrorResponse
from shared.file_ops import write_yaml
from backend.services.node_service import NodeService

router = APIRouter()

# Declared on the decorator so the generated OpenAPI documents it.
NODE_METRICS_404: Dict[Union[int, str], Dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Node not found"}
}


@router.get("/", response_model=List[NodeSpec])
async def list_nodes(payload: dict = Depends(get_current_token_payload)):
    """
    List all cluster nodes.
    """
    node_service = NodeService()
    nodes = await node_service.list_nodes()
    return nodes


@router.get("/{node_id}", response_model=NodeSpec)
async def get_node(node_id: str, payload: dict = Depends(get_current_token_payload)):
    """
    Get node details.
    """
    node_service = NodeService()
    node = await node_service.get_node(node_id)
    if node is None:
        raise node_not_found(node_id)
    return node


@router.post("/", response_model=NodeSpec, status_code=status.HTTP_201_CREATED)
async def create_node(
    node_spec: NodeSpec,
    request: Request,
    payload: dict = Depends(get_current_token_payload),
):
    """
    Create a new node by writing its spec to the Syncthing nodes/ folder.

    This is the file-based registration mechanism (issue #92). The agent and
    seeding scripts use this endpoint to register nodes. The Syncthing watcher
    picks up the YAML file and updates the database.
    """
    # Use SYNCTHING_ROOT from settings
    syncthing_root = getattr(settings, "SYNCTHING_ROOT", None)
    if not syncthing_root:
        raise APIError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Syncthing is not configured on the server. "
            "Set SYNCTHING_ROOT env var and restart the API server.",
            "SYNCTHING_NOT_CONFIGURED",
        )

    nodes_dir = os.path.join(syncthing_root, "nodes")
    os.makedirs(nodes_dir, exist_ok=True)

    file_path = os.path.join(nodes_dir, f"{node_spec.node_id}.yaml")

    # Check if node already exists
    node_service = NodeService()
    existing = await node_service.get_node(node_spec.node_id)
    if existing:
        raise APIError(
            status.HTTP_409_CONFLICT,
            f"Node with ID {node_spec.node_id} already exists",
            "NODE_ALREADY_EXISTS",
        )

    # Write YAML file atomically
    write_yaml(file_path, node_spec)

    # Return the node spec (watcher will process it asynchronously)
    return node_spec


@router.get("/{node_id}/metrics", response_model=JobMetrics, responses=NODE_METRICS_404)
async def get_node_metrics(
    node_id: str, payload: dict = Depends(get_current_token_payload)
):
    """
    Get node GPU/CPU metrics.
    """
    node_service = NodeService()
    metrics = await node_service.get_node_metrics(node_id)
    if metrics is None:
        raise APIError(
            status.HTTP_404_NOT_FOUND,
            (
                f"Metrics for node {node_id} are not available. "
                "The node may be offline or metrics collection failed. "
                "Ensure the node is online and try again."
            ),
            "NODE_METRICS_NOT_FOUND",
        )
    return metrics
