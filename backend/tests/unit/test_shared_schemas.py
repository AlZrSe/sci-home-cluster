"""
Unit tests for shared Pydantic schemas.
"""

import pytest
from pydantic import ValidationError
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_state import JobState
from shared.schemas.job_resources import JobResources
from shared.schemas.job_paths import JobPaths
from shared.schemas.job_retry import JobRetry
from shared.schemas.job_metrics import JobMetrics, JobMetricsSummary
from shared.schemas.node_spec import NodeSpec, GPUInfo
from shared.schemas.gpu_metric import GPUMetric
from shared.schemas.cpu_metric import CPUMetric
from shared.schemas.job_status import JobStatus


def test_job_status_enum():
    """Test JobStatus enum values."""
    assert JobStatus.PENDING == "PENDING"
    assert JobStatus.RUNNING == "RUNNING"
    assert JobStatus.COMPLETED == "COMPLETED"
    assert JobStatus.FAILED == "FAILED"
    assert JobStatus.CANCELLED == "CANCELLED"


def test_gpu_info():
    """Test GPUInfo model."""
    gpu = GPUInfo(name="NVIDIA RTX 4090", memory_gb=24)
    assert gpu.name == "NVIDIA RTX 4090"
    assert gpu.memory_gb == 24

    # Test serialization
    data = gpu.model_dump()
    assert data["name"] == "NVIDIA RTX 4090"
    assert data["memory_gb"] == 24

    # Test validation
    with pytest.raises(ValidationError):
        GPUInfo(name="Test", memory_gb=-1)  # Negative memory


def test_gpu_metric():
    """Test GPUMetric model."""
    metric = GPUMetric(
        timestamp=1234567890.0,
        memory_used_mb=8192,
        utilization_percent=75,
        temperature_c=65,
    )
    assert metric.timestamp == 1234567890.0
    assert metric.memory_used_mb == 8192
    assert metric.utilization_percent == 75
    assert metric.temperature_c == 65

    # Test serialization
    data = metric.model_dump()
    assert data["timestamp"] == 1234567890.0
    assert data["memory_used_mb"] == 8192
    assert data["utilization_percent"] == 75
    assert data["temperature_c"] == 65


def test_cpu_metric():
    """Test CPUMetric model."""
    metric = CPUMetric(timestamp=1234567890.0, cpu_percent=45.5, memory_percent=60.2)
    assert metric.timestamp == 1234567890.0
    assert metric.cpu_percent == 45.5
    assert metric.memory_percent == 60.2

    # Test serialization
    data = metric.model_dump()
    assert data["timestamp"] == 1234567890.0
    assert data["cpu_percent"] == 45.5
    assert data["memory_percent"] == 60.2


def test_job_resources():
    """Test JobResources model."""
    resources = JobResources(gpus=2, cpus=8, memory_gb=32, vram_gb=16)
    assert resources.gpus == 2
    assert resources.cpus == 8
    assert resources.memory_gb == 32
    assert resources.vram_gb == 16

    # Test serialization
    data = resources.model_dump()
    assert data["gpus"] == 2
    assert data["cpus"] == 8
    assert data["memory_gb"] == 32
    assert data["vram_gb"] == 16

    # Test validation
    with pytest.raises(ValidationError):
        JobResources(gpus=-1, cpus=2, memory_gb=4, vram_gb=2)  # Negative GPUs


def test_job_paths():
    """Test JobPaths model."""
    paths = JobPaths(input="/data/input", output="/data/output")
    assert paths.input == "/data/input"
    assert paths.output == "/data/output"

    # Test serialization
    data = paths.model_dump()
    assert data["input"] == "/data/input"
    assert data["output"] == "/data/output"


def test_job_retry():
    """Test JobRetry model."""
    retry = JobRetry(max_retries=5, retry_delay_seconds=120)
    assert retry.max_retries == 5
    assert retry.retry_delay_seconds == 120

    # Test serialization
    data = retry.model_dump()
    assert data["max_retries"] == 5
    assert data["retry_delay_seconds"] == 120

    # Test validation
    with pytest.raises(ValidationError):
        JobRetry(max_retries=-1, retry_delay_seconds=60)  # Negative retries


def test_job_spec():
    """Test JobSpec model."""
    resources = JobResources(gpus=1, cpus=4, memory_gb=16, vram_gb=8)
    paths = JobPaths(input="/tmp/input", output="/tmp/output")
    retry = JobRetry(max_retries=3, retry_delay_seconds=60)

    job_spec = JobSpec(
        name="test-job",
        command="python train.py",
        working_dir="/workspace",
        env={"VAR": "value"},
        resources=resources,
        paths=paths,
        retry=retry,
    )

    assert job_spec.name == "test-job"
    assert job_spec.command == "python train.py"
    assert job_spec.working_dir == "/workspace"
    assert job_spec.env == {"VAR": "value"}
    assert job_spec.resources == resources
    assert job_spec.paths == paths
    assert job_spec.retry == retry

    # Test serialization
    data = job_spec.model_dump()
    assert data["name"] == "test-job"
    assert data["command"] == "python train.py"
    assert data["working_dir"] == "/workspace"
    assert data["env"] == {"VAR": "value"}

    # Test validation
    with pytest.raises(ValidationError):
        JobSpec(
            name="",  # Empty name
            command="test",
            working_dir="/tmp",
            env={},
            resources=JobResources(gpus=1, cpus=1, memory_gb=1, vram_gb=1),
            paths=JobPaths(input="/tmp", output="/tmp"),
            retry=JobRetry(max_retries=1, retry_delay_seconds=1),
        )


def test_job_state():
    """Test JobState model."""
    from datetime import datetime

    job_state = JobSpec(
        name="test-job",
        command="echo hello",
        working_dir="/tmp",
        env={},
        resources=JobResources(gpus=1, cpus=1, memory_gb=1, vram_gb=1),
        paths=JobPaths(input="/tmp/in", output="/tmp/out"),
        retry=JobRetry(max_retries=1, retry_delay_seconds=1),
    )

    state = JobState(
        job_id="job-123",
        spec=job_state,
        status=JobStatus.RUNNING,
        node_id="node-01",
        created_at=datetime.now(),
        started_at=datetime.now(),
        finished_at=None,
        exit_code=None,
        retry_count=0,
    )

    assert state.job_id == "job-123"
    assert state.spec.name == "test-job"
    assert state.status == JobStatus.RUNNING
    assert state.node_id == "node-01"
    assert state.retry_count == 0

    # Test serialization
    data = state.model_dump()
    assert data["job_id"] == "job-123"
    assert data["status"] == JobStatus.RUNNING
    assert data["node_id"] == "node-01"
    assert data["retry_count"] == 0


def test_node_spec():
    """Test NodeSpec model."""
    from datetime import datetime

    gpu = GPUInfo(name="NVIDIA RTX 3080", memory_gb=10)
    node_spec = NodeSpec(
        node_id="worker-01",
        hostname="worker01.local",
        gpus=[gpu],
        cpus=16,
        memory_gb=64,
        os="Linux",
        status="ONLINE",
        last_heartbeat=datetime.now(),
        current_job_id="job-123",
    )

    assert node_spec.node_id == "worker-01"
    assert node_spec.hostname == "worker01.local"
    assert len(node_spec.gpus) == 1
    assert node_spec.gpus[0].name == "NVIDIA RTX 3080"
    assert node_spec.cpus == 16
    assert node_spec.memory_gb == 64
    assert node_spec.os == "Linux"
    assert node_spec.status == "ONLINE"
    assert node_spec.current_job_id == "job-123"

    # Test serialization
    data = node_spec.model_dump()
    assert data["node_id"] == "worker-01"
    assert data["hostname"] == "worker01.local"
    assert data["cpus"] == 16
    assert data["memory_gb"] == 64
    assert data["os"] == "Linux"
    assert data["status"] == "ONLINE"


def test_job_metrics():
    """Test JobMetrics model."""

    gpu_metric = GPUMetric(
        timestamp=1234567890.0,
        memory_used_mb=4096,
        utilization_percent=60,
        temperature_c=70,
    )

    cpu_metric = CPUMetric(
        timestamp=1234567890.0, cpu_percent=45.0, memory_percent=50.0
    )

    metrics = JobMetrics(
        gpu_metric=gpu_metric,
        cpu_metric=cpu_metric,
        disk_read_mb=100,
        disk_write_mb=50,
        network_rx_mb=200,
        network_tx_mb=150,
    )

    assert metrics.gpu_metric == gpu_metric
    assert metrics.cpu_metric == cpu_metric
    assert metrics.disk_read_mb == 100
    assert metrics.disk_write_mb == 50
    assert metrics.network_rx_mb == 200
    assert metrics.network_tx_mb == 150

    # Test serialization
    data = metrics.model_dump()
    assert data["gpu_metric"]["memory_used_mb"] == 4096
    assert data["cpu_metric"]["cpu_percent"] == 45.0
    assert data["disk_read_mb"] == 100
    assert data["disk_write_mb"] == 50
    assert data["network_rx_mb"] == 200
    assert data["network_tx_mb"] == 150


def test_job_metrics_summary():
    """Test JobMetricsSummary model."""
    summary = JobMetricsSummary(
        gpu_avg_memory_mb=3072,
        gpu_avg_utilization=65.5,
        gpu_avg_temperature=68.0,
        cpu_avg_percent=45.2,
        mem_avg_percent=60.1,
    )

    assert summary.gpu_avg_memory_mb == 3072
    assert summary.gpu_avg_utilization == 65.5
    assert summary.gpu_avg_temperature == 68.0
    assert summary.cpu_avg_percent == 45.2
    assert summary.mem_avg_percent == 60.1

    # Test serialization
    data = summary.model_dump()
    assert data["gpu_avg_memory_mb"] == 3072
    assert data["gpu_avg_utilization"] == 65.5
    assert data["gpu_avg_temperature"] == 68.0
    assert data["cpu_avg_percent"] == 45.2
    assert data["mem_avg_percent"] == 60.1


def test_json_yaml_serialization():
    """Test that models can be serialized to JSON and YAML."""
    import json
    import yaml

    resources = JobResources(gpus=1, cpus=2, memory_gb=4, vram_gb=2)
    paths = JobPaths(input="/tmp/in", output="/tmp/out")
    retry = JobRetry(max_retries=3, retry_delay_seconds=60)

    job_spec = JobSpec(
        name="serialization-test",
        command="echo hello",
        working_dir="/tmp",
        env={},
        resources=resources,
        paths=paths,
        retry=retry,
    )

    # Test JSON serialization
    json_data = job_spec.model_dump_json()
    parsed = json.loads(json_data)
    assert parsed["name"] == "serialization-test"
    assert parsed["command"] == "echo hello"

    # Test YAML serialization (via model_dump)
    yaml_data = job_spec.model_dump()
    yaml_output = yaml.dump(yaml_data)
    assert "serialization-test" in yaml_output
    assert "echo hello" in yaml_output
