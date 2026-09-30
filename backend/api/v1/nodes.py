"""
Node management API endpoints.
"""

from typing import List
from fastapi import APIRouter, Depends, status
from backend.core.deps import get_current_token_payload
from backend.core.errors import APIError, node_not_found
from shared.schemas.node_spec import NodeSpec
from shared.schemas.job_metrics import JobMetrics
from backend.services.node_service import NodeService

router = APIRouter()


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


@router.get("/{node_id}/metrics", response_model=JobMetrics)
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
