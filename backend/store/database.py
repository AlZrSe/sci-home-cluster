"""
Database models for the Scientific Home Cluster backend using SQLAlchemy.
"""

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    Text,
    ForeignKey,
    Enum as SQLEnum,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON

from shared.schemas.job_status import JobStatus


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


class JobModel(Base):
    """SQLAlchemy model for jobs."""

    __tablename__ = "jobs"

    job_id = Column(String(50), primary_key=True)
    spec = Column(SQLiteJSON, nullable=False)
    status = Column(
        SQLEnum(JobStatus),
        nullable=False,
        default=JobStatus.PENDING,
    )
    node_id = Column(String(50), ForeignKey("nodes.node_id"), nullable=True)
    created_at = Column(DateTime, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    exit_code = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)

    # Relationship to node
    node = relationship("NodeModel", back_populates="jobs", foreign_keys=[node_id])

    # Indexes
    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_node_id", "node_id"),
        Index("ix_jobs_created_at", "created_at"),
    )


class NodeModel(Base):
    """SQLAlchemy model for nodes."""

    __tablename__ = "nodes"

    node_id = Column(String(50), primary_key=True)
    hostname = Column(String(255), nullable=False)
    gpus = Column(SQLiteJSON, nullable=False, default=list)
    cpus = Column(Integer, nullable=False)
    memory_gb = Column(Integer, nullable=False)
    os = Column(String(100), nullable=False)
    status = Column(
        SQLEnum("ONLINE", "OFFLINE", name="node_status"),
        nullable=False,
        default="OFFLINE",
    )
    last_heartbeat = Column(DateTime, nullable=False)
    current_job_id = Column(String(50), ForeignKey("jobs.job_id"), nullable=True)

    # Relationship to jobs
    jobs = relationship(
        "JobModel", back_populates="node", foreign_keys="JobModel.node_id"
    )

    # Indexes
    __table_args__ = (
        Index("ix_nodes_status", "status"),
        Index("ix_nodes_last_heartbeat", "last_heartbeat"),
    )


class GPUMetricModel(Base):
    """SQLAlchemy model for GPU metrics."""

    __tablename__ = "gpu_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(50), ForeignKey("jobs.job_id"), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    gpu_index = Column(Integer, nullable=False)
    memory_used_mb = Column(Integer, nullable=False)
    memory_total_mb = Column(Integer, nullable=False)
    utilization_percent = Column(Integer, nullable=False)
    temperature_c = Column(Integer, nullable=False)

    __table_args__ = (Index("ix_gpu_metrics_job_timestamp", "job_id", "timestamp"),)


class CPUMetricModel(Base):
    """SQLAlchemy model for CPU metrics."""

    __tablename__ = "cpu_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(50), ForeignKey("jobs.job_id"), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    cpu_percent = Column(Integer, nullable=False)
    memory_percent = Column(Integer, nullable=False)

    __table_args__ = (Index("ix_cpu_metrics_job_timestamp", "job_id", "timestamp"),)


class LogEntryModel(Base):
    """SQLAlchemy model for log entries."""

    __tablename__ = "log_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(50), ForeignKey("jobs.job_id"), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    line = Column(Text, nullable=False)

    __table_args__ = (Index("ix_log_entries_job_timestamp", "job_id", "timestamp"),)
