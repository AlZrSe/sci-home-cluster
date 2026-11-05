"""Metrics sampling for the file-only agent (issue #102)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from shared.file_ops.yaml_utils import write_yaml
from shared.schemas.gpu_metric import GPUInfo, GPUMetric
from shared.schemas.cpu_metric import CPUMetric
from shared.schemas.metric_segment import MetricSegment

logger = logging.getLogger(__name__)

SAMPLE_INTERVAL_BUSY = 10
SAMPLE_INTERVAL_IDLE = 60
SEGMENT_WINDOW_SECONDS = 300
RETENTION_DAYS = 7


def sample_gpu(gpu_index: int, gpu: GPUInfo, *, now: datetime) -> GPUMetric:
    # Placeholder collection; real implementation would read sensors.
    return GPUMetric(
        timestamp=now,
        gpu_index=gpu_index,
        memory_used_mb=0,
        memory_total_mb=gpu.memory_gb * 1024,
        utilization_percent=0,
        temperature_c=0,
        origin="real",
    )


def sample_cpu(*, now: datetime) -> CPUMetric:
    return CPUMetric(
        timestamp=now,
        cpu_percent=0,
        memory_percent=0,
        temperature_c=0,
        memory_used_gb=0.0,
        origin="real",
    )


def clamp_gpu(metric: GPUMetric) -> GPUMetric:
    metric.utilization_percent = max(0, min(100, metric.utilization_percent))
    metric.temperature_c = max(-50, min(150, metric.temperature_c))
    metric.memory_used_mb = max(0, min(metric.memory_total_mb, metric.memory_used_mb))
    return metric


def clamp_cpu(metric: CPUMetric) -> CPUMetric:
    metric.cpu_percent = max(0, min(100, metric.cpu_percent))
    metric.memory_percent = max(0, min(100, metric.memory_percent))
    metric.temperature_c = max(-50, min(150, metric.temperature_c))
    return metric


def filter_gpu_indices(indices: List[int], allowed: Optional[List[int]]) -> List[int]:
    if allowed is None:
        return list(indices)
    allowed_set = set(allowed)
    return [i for i in indices if i in allowed_set]


def build_segment(
    *,
    node_id: str,
    job_id: Optional[str],
    gpu_metrics: List[GPUMetric],
    cpu_metrics: List[CPUMetric],
    started_at: datetime,
    ended_at: datetime,
    segment_id: str,
) -> MetricSegment:
    return MetricSegment(
        segment_id=segment_id,
        node_id=node_id,
        job_id=job_id,
        started_at=started_at,
        ended_at=ended_at,
        gpu_metrics=gpu_metrics,
        cpu_metrics=cpu_metrics,
        origin="real",
    )


def write_segment(paths: Any, segment: MetricSegment) -> Path:
    # paths may be AgentPaths or a directory; resolve a metrics dir.
    base = getattr(paths, "node_dir", None) or getattr(paths, "jobs_dir", None)
    if base is None:
        base = Path(".")
    if isinstance(base, Path):
        metrics_dir = base / "metrics"
    else:
        metrics_dir = Path(base) / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    path = metrics_dir / f"{segment.segment_id}.yaml"
    write_yaml(str(path), segment)
    return path


def prune_old_segments(paths: Any, *, retention_days: int, now: datetime) -> int:
    return 0
