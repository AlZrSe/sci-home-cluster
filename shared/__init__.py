"""
Shared module for the Scientific Home Cluster.

Contains the API contract (schemas), file operations, and metrics
utilities used by the API server, the worker agent and the CLI.

Imports are explicit rather than star imports so that every re-exported
name is listed in __all__ and resolvable by type checkers.
"""

from .file_ops import (
    ensure_directory,
    file_lock,
    get_job_directory,
    get_job_state_file,
    get_jobs_directory,
    get_node_state_file,
    get_nodes_directory,
    get_syncthing_root,
    is_within_syncthing_root,
    normalize_path,
    read_yaml,
    shared_lock,
    update_yaml,
    write_yaml,
)
from .metrics import (
    CPUMetricsSummarizer,
    GPUMetricsSummarizer,
    SlidingWindow,
    TimestampedSlidingWindow,
)
from .schemas import (
    CPUMetric,
    ErrorResponse,
    GPUInfo,
    GPUMetric,
    JobListResult,
    JobMetrics,
    JobMetricsSummary,
    JobPaths,
    JobQuery,
    JobResources,
    JobRetry,
    JobSpec,
    JobState,
    JobStatus,
    NodeSpec,
    Paths,
    Resources,
    RetryPolicy,
    TokenCreateRequest,
    TokenCreateResponse,
    TokenRefreshRequest,
    TokenRefreshResponse,
    TokenValidationRequest,
    TokenValidationResponse,
)

__all__ = [
    # Schemas
    "CPUMetric",
    "ErrorResponse",
    "GPUInfo",
    "GPUMetric",
    "JobListResult",
    "JobMetrics",
    "JobMetricsSummary",
    "JobPaths",
    "JobQuery",
    "JobResources",
    "JobRetry",
    "JobSpec",
    "JobState",
    "JobStatus",
    "NodeSpec",
    "Paths",
    "Resources",
    "RetryPolicy",
    "TokenCreateRequest",
    "TokenCreateResponse",
    "TokenRefreshRequest",
    "TokenRefreshResponse",
    "TokenValidationRequest",
    "TokenValidationResponse",
    # File operations
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
    # Metrics
    "SlidingWindow",
    "TimestampedSlidingWindow",
    "GPUMetricsSummarizer",
    "CPUMetricsSummarizer",
]
