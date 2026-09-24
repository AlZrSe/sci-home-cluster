"""
Shared module for the Scientific Home Cluster.
Contains schemas, file operations, and metrics utilities.
"""

# Re-export from submodules for convenience
from .schemas import *  # noqa: F401,F403
from .file_ops import *  # noqa: F401,F403
from .metrics import *  # noqa: F401,F403

__all__ = [
    # From schemas
    "JobStatus",
    "GPUInfo",
    "GPUMetric",
    "CPUMetric",
    "JobResources",
    "JobPaths",
    "JobRetry",
    "JobSpec",
    "JobState",
    "JobMetrics",
    "JobMetricsSummary",
    "NodeSpec",
    # From file_ops
    "file_lock",
    "shared_lock",
    "read_yaml",
    "write_yaml",
    "update_yaml",
    "normalize_path",
    "ensure_directory",
    "get_syncthing_root",
    "get_jobs_directory",
    "get_nodes_directory",
    "get_job_directory",
    "get_job_state_file",
    "get_node_state_file",
    "is_within_syncthing_root",
    # From metrics
    "SlidingWindow",
    "TimestampedSlidingWindow",
    "GPUMetricsSummarizer",
    "CPUMetricsSummarizer",
]
