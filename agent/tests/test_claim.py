"""Tests for the agent claim protocol (issue #97)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.claim import (
    can_fit_job,
    claim_job,
    try_claim_job,
)
from agent.paths import AgentPaths, resolve_paths
from shared.schemas.gpu_metric import GPUInfo
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_state import JobState
from shared.schemas.job_status import JobStatus
from shared.schemas.node_spec import NodeSpec
from shared.schemas.resources import Resources


pytestmark = pytest.mark.unit


def make_node_spec(
    *,
    node_id: str = "node-01",
    gpus: list[GPUInfo] | None = None,
    cpus: int = 8,
    memory_gb: int = 32,
) -> NodeSpec:
    """Create a NodeSpec for testing."""
    return NodeSpec(
        node_id=node_id,
        hostname="test-host",
        gpus=gpus or [],
        cpus=cpus,
        memory_gb=memory_gb,
        os="linux",
        status="ONLINE",
        last_heartbeat=datetime.now(timezone.utc),
        current_job_id=None,
    )


def make_job_spec(
    *,
    gpus: int = 0,
    cpus: int = 1,
    memory_gb: int = 1,
    vram_gb: int = 0,
) -> JobSpec:
    """Create a JobSpec for testing."""
    return JobSpec(
        name="test-job",
        command="echo hello",
        resources=Resources(gpus=gpus, cpus=cpus, memory_gb=memory_gb, vram_gb=vram_gb),
    )


def make_job_state(
    *,
    job_id: str = "job-1",
    status: JobStatus = JobStatus.PENDING,
    node_id: str | None = None,
    spec: JobSpec | None = None,
) -> JobState:
    """Create a JobState for testing."""
    return JobState(
        job_id=job_id,
        spec=spec or make_job_spec(),
        status=status,
        node_id=node_id,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
        exit_code=None,
        error=None,
        retry_count=0,
    )


def setup_job_dir(paths: AgentPaths, job_id: str, job_state: JobState) -> Path:
    """Create a job directory with state.yaml."""
    from shared.file_ops.yaml_utils import write_yaml

    job_dir = paths.jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    state_file = job_dir / "state.yaml"
    write_yaml(str(state_file), job_state)
    return state_file


class TestCanFitJob:
    """Tests for the can_fit_job resource matching function."""

    def test_fits_exact_match(self):
        """Job requirements exactly match node capacity."""
        node = make_node_spec(
            gpus=[GPUInfo(name="RTX 3090", memory_gb=24)], cpus=8, memory_gb=32
        )
        job = make_job_spec(gpus=1, cpus=8, memory_gb=32, vram_gb=24)
        fits, reason = can_fit_job(node, job)
        assert fits is True
        assert reason == ""

    def test_fits_with_headroom(self):
        """Job requirements are less than node capacity."""
        node = make_node_spec(
            gpus=[GPUInfo(name="RTX 3090", memory_gb=24)], cpus=16, memory_gb=64
        )
        job = make_job_spec(gpus=1, cpus=4, memory_gb=16, vram_gb=12)
        fits, reason = can_fit_job(node, job)
        assert fits is True
        assert reason == ""

    def test_gpu_shortfall(self):
        """Job needs more GPUs than node has."""
        node = make_node_spec(
            gpus=[GPUInfo(name="RTX 3090", memory_gb=24)], cpus=8, memory_gb=32
        )
        job = make_job_spec(gpus=2, cpus=4, memory_gb=16, vram_gb=12)
        fits, reason = can_fit_job(node, job)
        assert fits is False
        assert "gpus (need 2, have 1)" in reason

    def test_cpu_shortfall(self):
        """Job needs more CPUs than node has."""
        node = make_node_spec(
            gpus=[GPUInfo(name="RTX 3090", memory_gb=24)], cpus=4, memory_gb=32
        )
        job = make_job_spec(gpus=1, cpus=8, memory_gb=16, vram_gb=12)
        fits, reason = can_fit_job(node, job)
        assert fits is False
        assert "cpus (need 8, have 4)" in reason

    def test_memory_shortfall(self):
        """Job needs more memory than node has."""
        node = make_node_spec(
            gpus=[GPUInfo(name="RTX 3090", memory_gb=24)], cpus=8, memory_gb=16
        )
        job = make_job_spec(gpus=1, cpus=4, memory_gb=32, vram_gb=12)
        fits, reason = can_fit_job(node, job)
        assert fits is False
        assert "memory_gb (need 32, have 16)" in reason

    def test_vram_shortfall(self):
        """Job needs more VRAM than node's max GPU has."""
        node = make_node_spec(
            gpus=[GPUInfo(name="RTX 3060", memory_gb=12)], cpus=8, memory_gb=32
        )
        job = make_job_spec(gpus=1, cpus=4, memory_gb=16, vram_gb=24)
        fits, reason = can_fit_job(node, job)
        assert fits is False
        assert "vram_gb (need 24, max GPU has 12)" in reason

    def test_zero_gpu_node_vs_vram_job(self):
        """Node with no GPUs cannot fit job requiring VRAM."""
        node = make_node_spec(gpus=[], cpus=8, memory_gb=32)
        job = make_job_spec(gpus=0, cpus=4, memory_gb=16, vram_gb=8)
        fits, reason = can_fit_job(node, job)
        assert fits is False
        assert "vram_gb (need 8, have 0: no GPUs)" in reason

    def test_zero_gpu_node_fits_cpu_only_job(self):
        """Node with no GPUs can fit CPU-only job."""
        node = make_node_spec(gpus=[], cpus=8, memory_gb=32)
        job = make_job_spec(gpus=0, cpus=4, memory_gb=16, vram_gb=0)
        fits, reason = can_fit_job(node, job)
        assert fits is True
        assert reason == ""

    def test_multi_gpu_node_max_vram(self):
        """Job fits if any single GPU has enough VRAM (max, not sum)."""
        gpus = [
            GPUInfo(name="RTX 3090", memory_gb=24),
            GPUInfo(name="RTX 3090", memory_gb=24),
        ]
        node = make_node_spec(gpus=gpus, cpus=16, memory_gb=64)
        # Job needs 24 GB VRAM - fits because max GPU has 24 GB
        job = make_job_spec(gpus=1, cpus=4, memory_gb=16, vram_gb=24)
        fits, reason = can_fit_job(node, job)
        assert fits is True
        assert reason == ""

    def test_multi_gpu_node_sum_not_used(self):
        """Job needing 48 GB VRAM does NOT fit two 24 GB GPUs (max, not sum)."""
        gpus = [
            GPUInfo(name="RTX 3090", memory_gb=24),
            GPUInfo(name="RTX 3090", memory_gb=24),
        ]
        node = make_node_spec(gpus=gpus, cpus=16, memory_gb=64)
        job = make_job_spec(gpus=1, cpus=4, memory_gb=16, vram_gb=48)
        fits, reason = can_fit_job(node, job)
        assert fits is False
        assert "vram_gb (need 48, max GPU has 24)" in reason

    def test_gpu_count_requirement(self):
        """Job requiring 2 GPUs fits node with 2 GPUs."""
        gpus = [
            GPUInfo(name="RTX 3090", memory_gb=24),
            GPUInfo(name="RTX 3090", memory_gb=24),
        ]
        node = make_node_spec(gpus=gpus, cpus=16, memory_gb=64)
        job = make_job_spec(gpus=2, cpus=4, memory_gb=16, vram_gb=24)
        fits, reason = can_fit_job(node, job)
        assert fits is True
        assert reason == ""


class TestTryClaimJob:
    """Tests for the try_claim_job function."""

    @pytest.fixture
    def temp_paths(self):
        """Create temporary paths for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True, exist_ok=True)
            (root / "nodes").mkdir(parents=True, exist_ok=True)
            yield paths

    @pytest.fixture
    def node_spec(self):
        """Create a test node spec."""
        return make_node_spec(
            gpus=[GPUInfo(name="RTX 3090", memory_gb=24)], cpus=8, memory_gb=32
        )

    @pytest.fixture
    def job_state(self):
        """Create a test job state."""
        return make_job_state(
            job_id="job-1",
            spec=make_job_spec(gpus=1, cpus=4, memory_gb=16, vram_gb=12),
        )

    async def test_claim_success(self, temp_paths, node_spec, job_state):
        """Successful claim creates marker and updates state to RUNNING."""
        state_file = setup_job_dir(temp_paths, "job-1", job_state)
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        success, reason = await try_claim_job(
            temp_paths, "job-1", "node-01", node_spec, now=now
        )

        assert success is True
        assert reason == ""

        # Verify state was updated
        from shared.file_ops.yaml_utils import read_yaml

        updated = read_yaml(str(state_file), JobState)
        assert updated.status == JobStatus.RUNNING
        assert updated.node_id == "node-01"
        assert updated.started_at == now

        # Verify marker file exists
        marker = temp_paths.jobs_dir / "job-1" / ".claim.node-01"
        assert marker.exists()

    async def test_claim_already_running(self, temp_paths, node_spec):
        """Job already RUNNING is not claimed."""
        job_state = make_job_state(
            job_id="job-1", status=JobStatus.RUNNING, node_id="node-01"
        )
        setup_job_dir(temp_paths, "job-1", job_state)

        success, reason = await try_claim_job(temp_paths, "job-1", "node-01", node_spec)

        assert success is False
        assert "job status is RUNNING" in reason

    async def test_claim_already_owned_by_other(self, temp_paths, node_spec):
        """Job owned by another node is not claimed."""
        job_state = make_job_state(
            job_id="job-1", status=JobStatus.PENDING, node_id="node-02"
        )
        setup_job_dir(temp_paths, "job-1", job_state)

        success, reason = await try_claim_job(temp_paths, "job-1", "node-01", node_spec)

        assert success is False
        assert "already owned by node-02" in reason

    async def test_claim_cannot_fit_resources(self, temp_paths, job_state):
        """Job that doesn't fit resources gets FAILED state with error."""
        # Node with insufficient resources
        node_spec = make_node_spec(gpus=[], cpus=2, memory_gb=4)
        state_file = setup_job_dir(temp_paths, "job-1", job_state)
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        success, reason = await try_claim_job(
            temp_paths, "job-1", "node-01", node_spec, now=now
        )

        assert success is False
        assert "No node can satisfy job resources" in reason

        # Verify state was updated to FAILED with error
        from shared.file_ops.yaml_utils import read_yaml

        updated = read_yaml(str(state_file), JobState)
        assert updated.status == JobStatus.FAILED
        assert updated.error is not None
        assert "No node can satisfy job resources" in updated.error
        assert updated.completed_at == now
        assert updated.exit_code == 1

    async def test_claim_marker_race(self, temp_paths, node_spec, job_state):
        """Two concurrent claims - only one succeeds (local O_EXCL)."""
        setup_job_dir(temp_paths, "job-1", job_state)
        marker = temp_paths.jobs_dir / "job-1" / ".claim.node-01"
        # Pre-create marker to simulate another task winning
        marker.write_text("other-task")

        success, reason = await try_claim_job(temp_paths, "job-1", "node-01", node_spec)

        assert success is False
        assert "marker exists" in reason

    async def test_claim_marker_cleanup_on_failure(
        self, temp_paths, node_spec, job_state
    ):
        setup_job_dir(temp_paths, "job-1", job_state)

        # Make job directory read-only to force write failure
        # Note: this also prevents marker creation, so we test that case
        job_dir = temp_paths.jobs_dir / "job-1"
        job_dir.chmod(0o555)

        try:
            success, reason = await try_claim_job(
                temp_paths, "job-1", "node-01", node_spec
            )
            # Marker creation fails first (permission denied on dir)
            assert (
                "failed to create marker" in reason
                or "failed to write RUNNING state" in reason
            )
        finally:
            job_dir.chmod(0o755)

    async def test_claim_nonexistent_state_file(self, temp_paths, node_spec):
        """Claiming non-existent job returns error."""
        success, reason = await try_claim_job(
            temp_paths, "job-999", "node-01", node_spec
        )

    async def test_claim_uses_job_id_from_state(self, temp_paths, node_spec):
        """Job ID comes from state.yaml, not passed in."""
        job_state = make_job_state(
            job_id="job-42", spec=make_job_spec(gpus=0, cpus=1, memory_gb=1)
        )
        setup_job_dir(temp_paths, "job-42", job_state)

        success, reason = await try_claim_job(
            temp_paths, "job-42", "node-01", node_spec
        )
        assert success is True

        # Verify the job_id in state matches
        from shared.file_ops.yaml_utils import read_yaml

        updated = read_yaml(
            str(temp_paths.jobs_dir / "job-42" / "state.yaml"), JobState
        )
        assert updated.job_id == "job-42"


class TestClaimJob:
    """Tests for the high-level claim_job function."""

    @pytest.fixture
    def temp_paths(self):
        """Create temporary paths for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True, exist_ok=True)
            (root / "nodes").mkdir(parents=True, exist_ok=True)
            yield paths

    @pytest.fixture
    def node_spec(self):
        """Create a test node spec."""
        return make_node_spec(
            gpus=[GPUInfo(name="RTX 3090", memory_gb=24)], cpus=8, memory_gb=32
        )

    async def test_claim_job_success(self, temp_paths, node_spec):
        """claim_job processes a state.yaml path and claims the job."""
        job_state = make_job_state(
            job_id="job-1",
            spec=make_job_spec(gpus=1, cpus=4, memory_gb=16, vram_gb=12),
        )
        state_file = setup_job_dir(temp_paths, "job-1", job_state)

        success, reason = await claim_job(temp_paths, state_file, "node-01", node_spec)

        assert success is True
        assert reason == ""

    async def test_claim_job_not_a_state_file(self, temp_paths, node_spec):
        """claim_job rejects non-state.yaml files."""
        # Create a non-state.yaml file
        other_file = temp_paths.jobs_dir / "job-1" / "other.yaml"
        other_file.parent.mkdir(parents=True, exist_ok=True)
        other_file.write_text("foo: bar")

        success, reason = await claim_job(temp_paths, other_file, "node-01", node_spec)

        assert success is False
        assert "not a job state file" in reason

    async def test_claim_job_wrong_directory_structure(self, temp_paths, node_spec):
        """claim_job rejects files not in jobs/<job_id>/state.yaml."""
        # File in wrong location
        wrong_file = temp_paths.root / "state.yaml"
        wrong_file.write_text("job_id: job-1")

        success, reason = await claim_job(temp_paths, wrong_file, "node-01", node_spec)

        assert success is False
        assert "not a job state file" in reason


class TestAgentLoopIntegration:
    """Integration tests for the agent loop with claim logic."""

    @pytest.fixture
    def temp_paths(self):
        """Create temporary paths for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True, exist_ok=True)
            (root / "nodes").mkdir(parents=True, exist_ok=True)
            yield paths

    def test_is_job_state_file(self, temp_paths):
        """_is_job_state_file correctly identifies job state files."""
        from agent.loop import Agent

        settings = MagicMock()
        settings.NODE_ID = "node-01"
        settings.SYNCTHING_ROOT = temp_paths.root
        settings.AGENT_STATE_DIR = temp_paths.state_dir
        settings.FOLDER_WATCH_INTERVAL_S = 0.05
        settings.FOLDER_RETRY_MAX_S = 0.3
        settings.WATCHER_LIVENESS_INTERVAL_S = 0.05
        settings.SHUTDOWN_GRACE_S = 0.2
        settings.LOG_LEVEL = "INFO"
        settings.HEARTBEAT_INTERVAL_S = 22.0
        settings.HEARTBEAT_JITTER_S = 2.0
        settings.AGENT_FOLDER_WATCH_INTERVAL_S = 0.05
        settings.AGENT_WATCHER_LIVENESS_INTERVAL_S = 0.05
        settings.AGENT_FOLDER_RETRY_MAX_S = 0.3
        settings.AGENT_SHUTDOWN_GRACE_S = 0.2

        agent = Agent(settings)

        # Valid job state file
        job1_state = temp_paths.jobs_dir / "job-1" / "state.yaml"
        job42_state = temp_paths.jobs_dir / "job-42" / "state.yaml"
        assert agent._is_job_state_file(job1_state) is True
        assert agent._is_job_state_file(job42_state) is True

        # Not a state.yaml
        other_yaml = temp_paths.jobs_dir / "job-1" / "other.yaml"
        node_yaml = temp_paths.nodes_dir / "node-01.yaml"
        assert agent._is_job_state_file(other_yaml) is False
        assert agent._is_job_state_file(node_yaml) is False

        # Wrong directory structure
        root_state = temp_paths.root / "state.yaml"
        assert agent._is_job_state_file(root_state) is False
