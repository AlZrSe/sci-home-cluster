"""
Job status enumeration for the Scientific Home Cluster.
"""

from enum import Enum


class JobStatus(str, Enum):
    """Status of a job in the cluster."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
