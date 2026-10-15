"""Tests for the terminal state writing (agent/terminal.py)."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from agent.paths import AgentPaths, resolve_paths
from agent.terminal import write_terminal_state
from agent.watcher import FolderWatcher
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_resources import Resources
from shared.schemas.job_paths import Paths
from shared.schemas.job_state import JobState
from shared.schemas.job_status import JobStatus
from shared.file_ops.yaml_utils import write_yaml, read_yaml


class TestWriteTerminalState:
    """Tests for write_terminal_state."""

    def _create_test_state(self, paths: AgentPaths, job_id: str) -> JobState:
        """Create a test JobState file."""
        job_spec = JobSpec(
            name="test-job",
            command="echo hello",
            working_dir="/tmp",
            env={},
            resources=Resources(gpus=0, cpus=1, memory_gb=1, vram_gb=0),
            paths=Paths(input=None, output=None),
        )
        state = JobState(
            job_id=job_id,
            spec=job_spec,
            status=JobStatus.RUNNING,
            node_id="node-01",
            created_at=datetime(2026, 1, 1, 12, 0, 0),
            started_at=datetime(2026, 1, 1, 12, 0, 1),
            completed_at=None,
            exit_code=None,
            error=None,
            retry_count=0,
        )
        state_file = paths.jobs_dir / job_id / "state.yaml"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(str(state_file), state)
        return state

    @pytest.mark.asyncio
    async def test_write_completed_state(self):
        """Test writing COMPLETED terminal state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True)
            (root / "nodes").mkdir(parents=True)

            job_id = "job-1"
            self._create_test_state(paths, job_id)

            await write_terminal_state(
                paths=paths,
                job_id=job_id,
                status=JobStatus.COMPLETED,
                exit_code=0,
                error=None,
            )

            # Read back and verify
            state_file = paths.jobs_dir / job_id / "state.yaml"
            state = read_yaml(str(state_file), JobState)

            assert state.status == JobStatus.COMPLETED
            assert state.exit_code == 0
            assert state.error is None
            assert state.completed_at is not None
            assert state.retry_count == 0  # Not incremented
            assert state.spec.name == "test-job"
            assert state.node_id == "node-01"
            assert state.created_at == datetime(2026, 1, 1, 12, 0, 0)
            assert state.started_at == datetime(2026, 1, 1, 12, 0, 1)

    @pytest.mark.asyncio
    async def test_write_failed_state_with_error(self):
        """Test writing FAILED terminal state with error code."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True)
            (root / "nodes").mkdir(parents=True)

            job_id = "job-2"
            self._create_test_state(paths, job_id)

            await write_terminal_state(
                paths=paths,
                job_id=job_id,
                status=JobStatus.FAILED,
                exit_code=1,
                error="process_failed:exit_code=1",
            )

            state_file = paths.jobs_dir / job_id / "state.yaml"
            state = read_yaml(str(state_file), JobState)

            assert state.status == JobStatus.FAILED
            assert state.exit_code == 1
            assert state.error == "process_failed:exit_code=1"
            assert state.completed_at is not None
            assert state.retry_count == 0

    @pytest.mark.asyncio
    async def test_write_cancelled_state(self):
        """Test writing CANCELLED terminal state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True)
            (root / "nodes").mkdir(parents=True)

            job_id = "job-3"
            self._create_test_state(paths, job_id)

            await write_terminal_state(
                paths=paths,
                job_id=job_id,
                status=JobStatus.CANCELLED,
                exit_code=-1,
                error="cancelled:by_operator",
            )

            state_file = paths.jobs_dir / job_id / "state.yaml"
            state = read_yaml(str(state_file), JobState)

            assert state.status == JobStatus.CANCELLED
            assert state.exit_code == -1
            assert state.error == "cancelled:by_operator"
            assert state.completed_at is not None
            assert state.retry_count == 0

    @pytest.mark.asyncio
    async def test_preserves_retry_count(self):
        """Test that retry_count is preserved, not incremented."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True)
            (root / "nodes").mkdir(parents=True)

            job_id = "job-4"
            # Create state with retry_count = 2
            job_spec = JobSpec(
                name="test-job",
                command="echo hello",
                resources=Resources(gpus=0, cpus=1, memory_gb=1, vram_gb=0),
            )
            state = JobState(
                job_id=job_id,
                spec=job_spec,
                status=JobStatus.RUNNING,
                node_id="node-01",
                created_at=datetime(2026, 1, 1, 12, 0, 0),
                started_at=datetime(2026, 1, 1, 12, 0, 1),
                completed_at=None,
                exit_code=None,
                error=None,
                retry_count=2,  # Pre-existing retry count
            )
            state_file = paths.jobs_dir / job_id / "state.yaml"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            write_yaml(str(state_file), state)

            await write_terminal_state(
                paths=paths,
                job_id=job_id,
                status=JobStatus.COMPLETED,
                exit_code=0,
                error=None,
            )

            state = read_yaml(str(state_file), JobState)
            assert state.retry_count == 2  # Preserved, not incremented

    @pytest.mark.asyncio
    async def test_with_watcher(self):
        """Test writing with watcher to suppress re-observation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True)
            (root / "nodes").mkdir(parents=True)

            job_id = "job-5"
            self._create_test_state(paths, job_id)

            watcher = FolderWatcher(paths)
            # Don't start the watcher, just use its ledger

            await write_terminal_state(
                paths=paths,
                job_id=job_id,
                status=JobStatus.COMPLETED,
                exit_code=0,
                error=None,
                watcher=watcher,
            )

            # Verify ledger recorded the path
            state_file = paths.jobs_dir / job_id / "state.yaml"
            assert state_file in watcher.ledger._entries

    @pytest.mark.asyncio
    async def test_without_watcher(self):
        """Test writing without watcher (None)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True)
            (root / "nodes").mkdir(parents=True)

            job_id = "job-6"
            self._create_test_state(paths, job_id)

            await write_terminal_state(
                paths=paths,
                job_id=job_id,
                status=JobStatus.COMPLETED,
                exit_code=0,
                error=None,
                watcher=None,
            )

            state_file = paths.jobs_dir / job_id / "state.yaml"
            state = read_yaml(str(state_file), JobState)
            assert state.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_naive_local_timestamp(self):
        """Test that completed_at is naive local (not timezone-aware)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True)
            (root / "nodes").mkdir(parents=True)

            job_id = "job-7"
            self._create_test_state(paths, job_id)

            await write_terminal_state(
                paths=paths,
                job_id=job_id,
                status=JobStatus.COMPLETED,
                exit_code=0,
                error=None,
            )

            state_file = paths.jobs_dir / job_id / "state.yaml"
            state = read_yaml(str(state_file), JobState)

            # completed_at should be naive (no timezone info)
            assert state.completed_at is not None
            assert state.completed_at.tzinfo is None

    @pytest.mark.asyncio
    async def test_does_not_delete_file(self):
        """Test that the state file is not deleted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = resolve_paths(root, "node-01", root / ".agent" / "node-01")
            (root / "jobs").mkdir(parents=True)
            (root / "nodes").mkdir(parents=True)

            job_id = "job-8"
            self._create_test_state(paths, job_id)

            state_file = paths.jobs_dir / job_id / "state.yaml"
            assert state_file.exists()

            await write_terminal_state(
                paths=paths,
                job_id=job_id,
                status=JobStatus.COMPLETED,
                exit_code=0,
                error=None,
            )

            # File should still exist
            assert state_file.exists()

            # And be readable
            state = read_yaml(str(state_file), JobState)
            assert state.status == JobStatus.COMPLETED
