"""
Factory-boy factories for generating test data.

Provides factories for JobSpec, NodeSpec, GPUInfo, and related models.
"""

import factory
from datetime import datetime
from typing import Optional

from shared.schemas.job_spec import JobSpec
from shared.schemas.paths import Paths
from shared.schemas.resources import Resources
from shared.schemas.retry import RetryPolicy
from shared.schemas.node_spec import NodeSpec, GPUInfo
from shared.schemas.job_status import JobStatus
from shared.schemas.job_state import JobState


class GPUInfoFactory(factory.Factory):
    """Factory for generating GPUInfo instances."""

    class Meta:
        model = GPUInfo

    name = factory.Sequence(lambda n: f"NVIDIA RTX {4090 + n}")
    memory_gb = factory.Iterator([8, 12, 16, 24, 36, 48])


class ResourcesFactory(factory.Factory):
    """Factory for generating Resources instances."""

    class Meta:
        model = Resources

    gpus = factory.Iterator([1, 2, 4, 8])
    cpus = factory.Iterator([4, 8, 12, 16, 24, 32])
    memory_gb = factory.Iterator([8, 16, 32, 64, 128])


class PathsFactory(factory.Factory):
    """Factory for generating Paths instances."""

    class Meta:
        model = Paths

    input = factory.Sequence(lambda n: f"/sync/data/in/{n}")
    output = factory.Sequence(lambda n: f"/sync/data/out/{n}")


class RetryPolicyFactory(factory.Factory):
    """Factory for generating RetryPolicy instances."""

    class Meta:
        model = RetryPolicy

    max_retries = factory.Iterator([0, 1, 3, 5, 10])
    retry_delay_seconds = factory.Iterator([10, 30, 60, 120, 300])


class JobSpecFactory(factory.Factory):
    """Factory for generating JobSpec instances with realistic data."""

    class Meta:
        model = JobSpec

    name = factory.Sequence(lambda n: f"test-job-{n:04d}")
    command = factory.Sequence(lambda n: f"python train_{n}.py --config config.yaml")
    working_dir = factory.Sequence(lambda n: f"/sync/projects/test-job-{n:04d}")
    env = factory.LazyFunction(
        lambda: {
            "PYTHONUNBUFFERED": "1",
            "CUDA_VISIBLE_DEVICES": "0",
        }
    )
    resources = factory.SubFactory(ResourcesFactory)
    paths = factory.SubFactory(PathsFactory)
    retry = factory.SubFactory(RetryPolicyFactory)


class NodeSpecFactory(factory.Factory):
    """Factory for generating NodeSpec instances with realistic data."""

    class Meta:
        model = NodeSpec

    node_id = factory.Sequence(lambda n: f"node-{n:03d}")
    hostname = factory.LazyAttribute(lambda o: f"{o.node_id}.lan")
    gpus = factory.LazyAttribute(
        lambda o: [
            GPUInfoFactory()
            for _ in range(o._random_choices if hasattr(o, "_random_choices") else 1)
        ]
    )
    cpus = factory.Iterator([4, 8, 12, 16, 24, 32])
    memory_gb = factory.Iterator([16, 32, 64, 128, 256])
    os = factory.Iterator(
        [
            "Ubuntu 22.04",
            "Ubuntu 24.04",
            "Debian 12",
            "macOS 15",
            "Rocky Linux 9",
        ]
    )
    status = factory.Iterator(["ONLINE", "OFFLINE"])
    last_heartbeat = factory.LazyFunction(datetime.now)
    current_job_id = None


class JobStateFactory(factory.Factory):
    """Factory for generating JobState instances with realistic data."""

    class Meta:
        model = JobState

    job_id = factory.Sequence(lambda n: f"job-{n:06d}")
    spec = factory.SubFactory(JobSpecFactory)
    status = factory.Iterator(list(JobStatus))
    node_id = None
    created_at = factory.LazyFunction(datetime.now)
    started_at = None
    completed_at = None
    exit_code = None
    error = None
    retry_count = 0

    @factory.post_generation
    def with_running_status(self, create, extracted, **kwargs):
        """Optionally set running status with node assignment."""
        if extracted:
            self.status = JobStatus.RUNNING
            self.node_id = extracted
            self.started_at = datetime.now()

    @factory.post_generation
    def with_completed_status(self, create, extracted, **kwargs):
        """Optionally set completed status with exit code."""
        if extracted:
            self.status = JobStatus.COMPLETED
            self.completed_at = datetime.now()
            self.exit_code = 0

    @factory.post_generation
    def with_failed_status(self, create, extracted, **kwargs):
        """Optionally set failed status with error message."""
        if extracted:
            self.status = JobStatus.FAILED
            self.completed_at = datetime.now()
            self.exit_code = 1
            self.error = (
                extracted if isinstance(extracted, str) else "CUDA out of memory"
            )


# ============================================================================
# Convenience Factory Functions
# ============================================================================


def create_job_spec(
    name: Optional[str] = None,
    gpus: Optional[int] = None,
    cpus: Optional[int] = None,
    memory_gb: Optional[int] = None,
    **kwargs,
) -> JobSpec:
    """
    Create a JobSpec with optional overrides.

    Args:
        name: Job name (auto-generated if not provided)
        gpus: Number of GPUs (default: 1)
        cpus: Number of CPUs (default: 4)
        memory_gb: Memory in GB (default: 16)
        **kwargs: Additional overrides

    Returns:
        JobSpec instance
    """
    factory_kwargs = {
        "resources": ResourcesFactory(
            gpus=gpus if gpus is not None else 1,
            cpus=cpus if cpus is not None else 4,
            memory_gb=memory_gb if memory_gb is not None else 16,
        ),
        **kwargs,
    }
    if name is not None:
        factory_kwargs["name"] = name

    return JobSpecFactory(**factory_kwargs)


def create_node_spec(
    node_id: Optional[str] = None,
    gpus: Optional[int] = None,
    cpus: Optional[int] = None,
    memory_gb: Optional[int] = None,
    status: str = "ONLINE",
    **kwargs,
) -> NodeSpec:
    """
    Create a NodeSpec with optional overrides.

    Args:
        node_id: Node ID (auto-generated if not provided)
        gpus: Number of GPUs (default: 1)
        cpus: Number of CPUs (default: 16)
        memory_gb: Memory in GB (default: 64)
        status: Node status (default: "ONLINE")
        **kwargs: Additional overrides

    Returns:
        NodeSpec instance
    """
    gpu_list = []
    if gpus and gpus > 0:
        gpu_list = [GPUInfoFactory(memory_gb=24) for _ in range(gpus)]

    factory_kwargs = {
        "gpus": gpu_list,
        "cpus": cpus or 16,
        "memory_gb": memory_gb or 64,
        "status": status,
        **kwargs,
    }
    if node_id is not None:
        factory_kwargs["node_id"] = node_id

    return NodeSpecFactory(**factory_kwargs)


def create_gpu_info(
    name: Optional[str] = None,
    memory_gb: Optional[int] = None,
) -> GPUInfo:
    """
    Create a GPUInfo with optional overrides.

    Args:
        name: GPU name (default: "NVIDIA RTX 4090")
        memory_gb: Memory in GB (default: 24)

    Returns:
        GPUInfo instance
    """
    return GPUInfoFactory(
        name=name,
        memory_gb=memory_gb or 24,
    )


def job_spec_to_yaml_bytes(spec: JobSpec) -> bytes:
    """Convert a JobSpec to YAML bytes for multipart upload."""
    import yaml

    return yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False).encode()
