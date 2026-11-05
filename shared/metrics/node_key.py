"""Backwards-compatible alias for the node-metrics storage key.

The canonical spelling lives in :mod:`shared.metrics.sentinel`; this module
re-exports it so existing imports keep working.
"""

from shared.metrics.sentinel import (
    NODE_METRICS_JOB_ID_PREFIX,
    node_metrics_job_id,
)

__all__ = ["NODE_METRICS_JOB_ID_PREFIX", "node_metrics_job_id"]
