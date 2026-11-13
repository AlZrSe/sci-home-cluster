"""MCP resources for Scientific Home Cluster."""

import json
import re

from backend.services.job_service import JobService
from backend.services.node_service import NodeService
from backend.mcp.tools.cluster import cluster_health


def _dump(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


async def read_job(ctx, uri: str) -> dict:
    # uri: job://{job_id}
    job_id = uri[len("job://") :]
    # strip sub-paths
    if "/" in job_id:
        job_id = job_id.split("/")[0]
    service = JobService()
    job = await service.get_job(job_id)
    if job is None:
        raise ValueError("resource not found")
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(_dump(job), default=str),
            }
        ]
    }


async def read_job_logs(ctx, uri: str) -> dict:
    job_id = uri[len("job://") :].split("/")[0]
    # uri job://{job_id}/logs
    m = re.match(r"job://([^/]+)/logs", uri)
    if m is not None:
        job_id = m.group(1)
    service = JobService()
    logs = await service.get_job_logs(job_id)
    if logs is None:
        raise ValueError("resource not found")
    data = [_dump(entry) for entry in logs]
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(data, default=str),
            }
        ]
    }


async def read_job_metrics(ctx, uri: str) -> dict:
    m = re.match(r"job://([^/]+)/metrics", uri)
    if m is None:
        raise ValueError("resource not found")
    job_id = m.group(1)
    service = JobService()
    metrics = await service.get_job_metrics(job_id)
    if metrics is None:
        raise ValueError("resource not found")
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(_dump(metrics), default=str),
            }
        ]
    }


async def read_node(ctx, uri: str) -> dict:
    m = re.match(r"node://([^/]+)$", uri)
    if m is None:
        raise ValueError("resource not found")
    node_id = m.group(1)
    service = NodeService()
    node = await service.get_node(node_id)
    if node is None:
        raise ValueError("resource not found")
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(_dump(node), default=str),
            }
        ]
    }


async def read_node_metrics(ctx, uri: str) -> dict:
    m = re.match(r"node://([^/]+)/metrics", uri)
    if m is None:
        raise ValueError("resource not found")
    node_id = m.group(1)
    service = NodeService()
    metrics = await service.get_node_metrics(node_id)
    if metrics is None:
        raise ValueError("resource not found")
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(_dump(metrics), default=str),
            }
        ]
    }


async def read_cluster_status(ctx, uri: str) -> dict:
    data = await cluster_health(ctx, {})
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(data, default=str),
            }
        ]
    }


def register(server):
    # static resource templates registered as templates
    server.register_resource_template(
        "job://{job_id}",
        "Job",
        "A job state resource.",
        read_job,
    )
    server.register_resource_template(
        "job://{job_id}/logs",
        "Job Logs",
        "Logs for a job.",
        read_job_logs,
    )
    server.register_resource_template(
        "job://{job_id}/metrics",
        "Job Metrics",
        "Metrics for a job.",
        read_job_metrics,
    )
    server.register_resource_template(
        "node://{node_id}",
        "Node",
        "A node spec resource.",
        read_node,
    )
    server.register_resource_template(
        "node://{node_id}/metrics",
        "Node Metrics",
        "Metrics for a node.",
        read_node_metrics,
    )
    # cluster://status as a concrete resource
    server.register_resource(
        "cluster://status",
        "Cluster Status",
        "Cluster health status.",
        read_cluster_status,
    )

    # Also need reader matching for job://{job_id} etc. The _match_resource_reader uses
    # _uri_matches with templates. But we registered templates, not readers in
    # resource_readers.
    # Need to map template->reader in _resource_patterns (done by
    # register_resource_template).
    # However _match_resource_reader iterates _resource_patterns and calls _uri_matches.
    # Good.
    # But register_resource_template doesn't store reader? It does in
    # _resource_patterns via server method?
    # Check: register_resource_template in server.py appends to _resource_patterns?
    # I implemented register_resource_template to append template but did NOT
    # append to _resource_patterns!
    # Fix by also appending reader.
    server._resource_patterns.append(("job://{job_id}", read_job))
    server._resource_patterns.append(("job://{job_id}/logs", read_job_logs))
    server._resource_patterns.append(("job://{job_id}/metrics", read_job_metrics))
    server._resource_patterns.append(("node://{node_id}", read_node))
    server._resource_patterns.append(("node://{node_id}/metrics", read_node_metrics))
    # cluster://status concrete already in resource_readers via register_resource (yes)
