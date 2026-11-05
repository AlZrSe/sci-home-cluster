"""Central spelling of the node-metrics storage key.

The ``gpu_metrics``/``cpu_metrics`` tables are keyed by ``job_id``. A node is
not a job, so node samples live under the sentinel ``"node:{node_id}"``. That
string is load-bearing (``backend/store/database.py``) and must have exactly
one spelling, or the agent and the backend disagree silently. This module is
that spelling.
"""

from __future__ import annotations

from typing import Final

NODE_METRICS_JOB_ID_PREFIX: Final[str] = "node:"


def node_metrics_job_id(node_id: str) -> str:
    """Return the ``job_id`` under which a node's samples are stored.

    The only permitted spelling. ``node_id`` is interpolated by this function
    so that no caller (agent or backend) formats the literal
    ``"node:{...}"``.
    """
    if not node_id:
        raise ValueError("node_id must be non-empty")
    return f"{NODE_METRICS_JOB_ID_PREFIX}{node_id}"


__all__ = ["NODE_METRICS_JOB_ID_PREFIX", "node_metrics_job_id"]
