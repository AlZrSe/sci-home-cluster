"""
Unit tests for shared file operations.
"""

import pytest
import tempfile
import os
import yaml
from pydantic import ValidationError
from shared.file_ops.locking import file_lock, shared_lock
from shared.file_ops.yaml_utils import read_yaml, write_yaml, update_yaml
from shared.schemas.job_spec import JobSpec
from shared.schemas.job_resources import JobResources
from shared.schemas.job_paths import JobPaths
from shared.schemas.job_retry import JobRetry


def test_write_and_read_yaml():
    """Test basic YAML write and read operations."""
    # Create a test JobSpec
    resources = JobResources(gpus=1, cpus=2, memory_gb=4, vram_gb=2)
    paths = JobPaths(input="/tmp/input", output="/tmp/output")
    retry = JobRetry(max_retries=3, retry_delay_seconds=60)
    original_spec = JobSpec(
        name="test-job",
        command="echo hello",
        working_dir="/tmp",
        env={"TEST": "value"},
        resources=resources,
        paths=paths,
        retry=retry,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_file = os.path.join(tmpdir, "test.yaml")

        # Write YAML
        write_yaml(yaml_file, original_spec)
        assert os.path.exists(yaml_file)

        # Read YAML
        loaded_spec = read_yaml(yaml_file, JobSpec)

        # Verify data integrity
        assert loaded_spec.name == original_spec.name
        assert loaded_spec.command == original_spec.command
        assert loaded_spec.working_dir == original_spec.working_dir
        assert loaded_spec.env == original_spec.env
        assert loaded_spec.resources == original_spec.resources
        assert loaded_spec.paths == original_spec.paths
        assert loaded_spec.retry == original_spec.retry


def test_write_yaml_creates_directory():
    """Test that write_yaml creates directories if they don't exist."""
    resources = JobResources(gpus=1, cpus=1, memory_gb=1, vram_gb=1)
    paths = JobPaths(input="/tmp/in", output="/tmp/out")
    retry = JobRetry(max_retries=1, retry_delay_seconds=1)
    job_spec = JobSpec(
        name="dir-test",
        command="test",
        working_dir="/tmp",
        env={},
        resources=resources,
        paths=paths,
        retry=retry,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Use a nested directory that doesn't exist
        yaml_file = os.path.join(tmpdir, "nested", "dir", "test.yaml")

        # This should create the nested directory structure
        write_yaml(yaml_file, job_spec)

        assert os.path.exists(yaml_file)
        assert os.path.exists(os.path.dirname(yaml_file))


def test_read_yaml_file_not_found():
    """Test that read_yaml raises FileNotFoundError for missing files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_file = os.path.join(tmpdir, "nonexistent.yaml")

        with pytest.raises(FileNotFoundError):
            read_yaml(yaml_file, JobSpec)


def test_read_yaml_invalid_yaml():
    """Test that read_yaml raises YAMLError for invalid YAML."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_file = os.path.join(tmpdir, "invalid.yaml")

        # Write invalid YAML
        with open(yaml_file, "w") as f:
            f.write("invalid: [unclosed bracket")

        with pytest.raises(yaml.YAMLError):
            read_yaml(yaml_file, JobSpec)


def test_read_yaml_validation_error():
    """Test that read_yaml raises ValidationError for invalid data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_file = os.path.join(tmpdir, "validation_error.yaml")

        # Write YAML that doesn't match JobSpec schema
        invalid_data = {
            "name": "test",
            # Missing required fields like command, working_dir, etc.
        }
        with open(yaml_file, "w") as f:
            yaml.dump(invalid_data, f)

        with pytest.raises(ValidationError):
            read_yaml(yaml_file, JobSpec)


def test_update_yaml():
    """Test atomic update operation."""
    resources = JobResources(gpus=1, cpus=2, memory_gb=4, vram_gb=2)
    paths = JobPaths(input="/tmp/in", output="/tmp/out")
    retry = JobRetry(max_retries=3, retry_delay_seconds=60)
    original_spec = JobSpec(
        name="update-test",
        command="echo hello",
        working_dir="/tmp",
        env={"OLD": "value"},
        resources=resources,
        paths=paths,
        retry=retry,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_file = os.path.join(tmpdir, "update.yaml")

        # Write initial spec
        write_yaml(yaml_file, original_spec)

        # Define update function
        def add_env_var(spec):
            spec.env["NEW"] = "added"
            return spec

        # Update the YAML file
        updated_spec = update_yaml(yaml_file, JobSpec, add_env_var)

        # Verify update was applied
        assert updated_spec.env["OLD"] == "value"
        assert updated_spec.env["NEW"] == "added"
        assert updated_spec.name == original_spec.name  # Unchanged

        # Read back and verify
        reloaded_spec = read_yaml(yaml_file, JobSpec)
        assert reloaded_spec.env["OLD"] == "value"
        assert reloaded_spec.env["NEW"] == "added"


def test_file_lock_basic():
    """Test basic file locking functionality."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = os.path.join(tmpdir, "test.lock")

        # Test that we can acquire and release a lock
        with file_lock(lock_file, mode="w", timeout=2.0) as f:
            f.write("test data")
            f.flush()

        # Verify file was written
        with open(lock_file, "r") as f:
            content = f.read()
            assert content == "test data"


def test_shared_lock_basic():
    """Test basic shared (read) file locking functionality."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = os.path.join(tmpdir, "shared_test.lock")

        # Write initial content
        with open(lock_file, "w") as f:
            f.write("shared content")

        # Test that we can acquire and release a shared lock
        with shared_lock(lock_file, mode="r", timeout=2.0) as f:
            content = f.read()
            assert content == "shared content"


import sys


def test_lock_timeout():
    """Test that lock acquisition times out appropriately."""
    if sys.platform == "win32":
        pytest.skip("File locking timeout test has issues on Windows")
    import threading
    import time

    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = os.path.join(tmpdir, "timeout_test.lock")

        # Acquire lock in a background thread
        def hold_lock():
            with file_lock(lock_file, mode="w", timeout=10.0) as _:
                time.sleep(2)  # Hold lock for 2 seconds

        thread = threading.Thread(target=hold_lock)
        thread.start()

        # Give thread time to acquire lock
        time.sleep(0.5)

        # Try to acquire lock with short timeout - should fail
        start_time = time.time()
        with pytest.raises(
            Exception
        ):  # Should timeout (could be TimeoutError or similar)
            with file_lock(lock_file, mode="w", timeout=1.0) as _:
                pass

        end_time = time.time()
        # Should have timed out after approximately 1 second
        assert end_time - start_time >= 1.0

        thread.join()


def test_lock_cleanup_on_exception():
    """Test that locks are properly released even when exceptions occur."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = os.path.join(tmpdir, "cleanup_test.lock")

        # Test that lock is released after exception
        try:
            with file_lock(lock_file, mode="w", timeout=2.0) as f:
                raise ValueError("Test exception")
        except ValueError:
            pass  # Expected

        # Should be able to acquire lock again immediately
        with file_lock(lock_file, mode="w", timeout=2.0) as f:
            f.write("recovered")

        with open(lock_file, "r") as f:
            assert f.read() == "recovered"
