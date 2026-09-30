"""
Job status enumeration, shared by the API server, the worker agent and the CLI.
"""

from enum import Enum


class JobStatus(str, Enum):
    """Status of a job in the cluster."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
