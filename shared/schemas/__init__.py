"""
Shared schemas package for the Scientific Home Cluster.

This package is the single source of truth for the API contract. The API
server, the worker agent and the CLI all import from here; the frontend
consumes the resulting JSON over HTTP.
"""

from .cpu_metric import CPUMetric
from .error_response import ErrorResponse
from .gpu_metric import GPUInfo, GPUMetric
from .job_list_result import JobListResult
from .job_metrics import JobMetrics, JobMetricsSummary
from .job_query import JobQuery
from .job_spec import JobSpec
from .job_state import JobState
from .job_status import JobStatus
from .node_spec import NodeSpec
from .paths import Paths
from .paths import Paths as JobPaths
from .resources import Resources
from .resources import Resources as JobResources
from .retry import RetryPolicy
from .retry import RetryPolicy as JobRetry
from .token_validation import (
    TokenCreateRequest,
    TokenCreateResponse,
    TokenRefreshRequest,
    TokenRefreshResponse,
    TokenValidationRequest,
    TokenValidationResponse,
)

__all__ = [
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
]
