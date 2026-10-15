"""Tests for the job execution engine (agent/executor.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch


from agent.executor import (
    ExecutionResult,
    build_child_env,
    validate_and_prepare_working_dir,
)


class TestValidateAndPrepareWorkingDir:
    """Tests for validate_and_prepare_working_dir."""

    def test_inside_syncthing_root_creates_dir(self):
        """Test that directory is created if inside Syncthing root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            syncthing_root = Path(tmpdir) / "syncthing"
            syncthing_root.mkdir()
            working_dir = str(syncthing_root / "projects" / "my-job")

            with patch("agent.executor.is_within_syncthing_root", return_value=True):
                resolved, error = validate_and_prepare_working_dir(
                    working_dir, syncthing_root
                )

            assert error is None
            assert resolved == Path(working_dir).resolve()

    def test_outside_syncthing_root_exists(self):
        """Test that existing directory outside Syncthing root is accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            syncthing_root = Path(tmpdir) / "syncthing"
            syncthing_root.mkdir()
            outside_dir = Path(tmpdir) / "outside"
            outside_dir.mkdir()
            working_dir = str(outside_dir)

            with patch("agent.executor.is_within_syncthing_root", return_value=False):
                with patch("agent.executor.normalize_path", return_value=outside_dir):
                    with patch("pathlib.Path.exists", return_value=True):
                        resolved, error = validate_and_prepare_working_dir(
                            working_dir, syncthing_root
                        )

            assert error is None

    def test_outside_syncthing_root_not_found(self):
        """Test error when directory outside Syncthing root doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            syncthing_root = Path(tmpdir) / "syncthing"
            syncthing_root.mkdir()
            outside_dir = Path(tmpdir) / "outside-nonexistent"
            working_dir = str(outside_dir)

            with patch("agent.executor.is_within_syncthing_root", return_value=False):
                with patch("agent.executor.normalize_path", return_value=outside_dir):
                    with patch("pathlib.Path.exists", return_value=False):
                        resolved, error = validate_and_prepare_working_dir(
                            working_dir, syncthing_root
                        )

            assert error == "launch_failed:working_dir_not_found"

    def test_inside_syncthing_root_create_fails(self):
        """Test error when directory creation fails inside Syncthing root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            syncthing_root = Path(tmpdir) / "syncthing"
            syncthing_root.mkdir()
            working_dir = str(syncthing_root / "projects" / "my-job")

            with patch("agent.executor.is_within_syncthing_root", return_value=True):
                with patch(
                    "pathlib.Path.mkdir", side_effect=OSError("Permission denied")
                ):
                    resolved, error = validate_and_prepare_working_dir(
                        working_dir, syncthing_root
                    )

            assert "launch_failed:working_dir_create_failed" in error


class TestBuildChildEnv:
    """Tests for build_child_env."""

    def test_strips_internal_vars(self):
        """Test that internal variables are stripped."""
        parent_env = {
            "PATH": "/usr/bin",
            "SHARED_TOKEN": "secret",
            "SECRET_KEY": "secret",
            "SHC_STATE_DIR": "/state",
            "SYNCTHING_ROOT": "/sync",
            "NODE_ID": "node-01",
            "CUSTOM_TOKEN": "secret",
            "API_KEY": "secret",
            "KEEP_THIS": "value",
        }
        spec_env = {"NEW_VAR": "new_value"}

        child_env = build_child_env(parent_env, spec_env)

        # Internal vars should be stripped
        assert "SHARED_TOKEN" not in child_env
        assert "SECRET_KEY" not in child_env
        assert "SHC_STATE_DIR" not in child_env
        assert "SYNCTHING_ROOT" not in child_env
        assert "NODE_ID" not in child_env
        assert "CUSTOM_TOKEN" not in child_env
        assert "API_KEY" not in child_env

        # Non-internal vars should remain
        assert child_env["PATH"] == "/usr/bin"
        assert child_env["KEEP_THIS"] == "value"

        # Spec env should be applied
        assert child_env["NEW_VAR"] == "new_value"

    def test_sets_cuda_visible_devices(self):
        """Test that CUDA_VISIBLE_DEVICES is set from GPU indices."""
        parent_env = {"PATH": "/usr/bin"}
        spec_env = {}
        gpu_indices = [0, 1, 2]

        child_env = build_child_env(parent_env, spec_env, gpu_indices)

        assert child_env["CUDA_VISIBLE_DEVICES"] == "0,1,2"

    def test_no_gpu_indices_no_cuda_var(self):
        """Test that CUDA_VISIBLE_DEVICES is not set if no GPU indices."""
        parent_env = {"PATH": "/usr/bin", "CUDA_VISIBLE_DEVICES": "0"}
        spec_env = {}
        gpu_indices = None

        child_env = build_child_env(parent_env, spec_env, gpu_indices)

        # Should keep parent's CUDA_VISIBLE_DEVICES if not overridden
        # Actually, it should NOT be set by us, but parent's value remains
        assert "CUDA_VISIBLE_DEVICES" in child_env
        assert child_env["CUDA_VISIBLE_DEVICES"] == "0"

    def test_spec_env_overrides_parent(self):
        """Test that spec_env overrides parent environment."""
        parent_env = {"PATH": "/usr/bin", "MY_VAR": "parent"}
        spec_env = {"MY_VAR": "spec"}

        child_env = build_child_env(parent_env, spec_env)

        assert child_env["MY_VAR"] == "spec"


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_creation(self):
        """Test creating ExecutionResult."""
        result = ExecutionResult(
            exit_code=0,
            error_code=None,
            stdout_path="/path/to/stdout.log",
            stderr_path="/path/to/stderr.log",
        )

        assert result.exit_code == 0
        assert result.error_code is None
        assert result.stdout_path == "/path/to/stdout.log"
        assert result.stderr_path == "/path/to/stderr.log"
