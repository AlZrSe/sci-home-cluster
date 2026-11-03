"""Shared helper for the node-metrics storage key.

Node samples are stored in the same ``gpu_metrics`` / ``cpu_metrics``
tables as job samples (see #102). Because the tables are keyed by
``job_id`` (a FK to ``jobs.job_id``), a node has no job of its own, so
its samples are stored under the synthetic key ``"node:<node_id>"``.

This is a *convention*, not a real job id: it does not match
``JobState``'s ``^job-\\d+$`` pattern and it is not a foreign key. It
lives here so the backend and the agent agree on the exact spelling
instead of each formatting the string independently.

Issue #102 open question 1 tracks the eventual migration to a real
``node_id`` column; until then this helper is the single source of truth.
"""


def node_metrics_job_id(node_id: str) -> str:
    """Return the storage key used for a node's metric samples."""
    return f"node:{node_id}"


__all__ = ["node_metrics_job_id"]