"""
Node management API endpoints.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from backend.core.deps import get_current_token_payload
from backend.models.node_spec import NodeSpec
from backend.models.job_metrics import JobMetrics
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Node {node_id} does not exist. "
                "It may have been removed from the cluster or the ID is incorrect. "
                "Check the node list and try again."
            ),
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Metrics for node {node_id} are not available. "
                "The node may be offline or metrics collection failed. "
                "Ensure the node is online and try again."
            ),
        )
    return metrics
