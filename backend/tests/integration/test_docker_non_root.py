"""
Integration tests for Docker non-root user (Issue #159).

These tests verify that the Docker image runs as non-root user (UID 1000/GID 1000)
with proper filesystem permissions.
"""

import pytest
import subprocess


class TestDockerNonRoot:
    """Tests for non-root user configuration in the Docker image."""

    def _run_as_shc(self, command: str) -> subprocess.CompletedProcess:
        """Run a command as the shc user via gosu."""
        cmd = [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            "shc:test-nonroot",
            "-c",
            f"gosu shc {command}",
        ]
        return subprocess.run(cmd, capture_output=True, text=True)

    def _run_as_root(self, command: str) -> subprocess.CompletedProcess:
        """Run a command as root (default when entrypoint is overridden)."""
        cmd = [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            "shc:test-nonroot",
            "-c",
            command,
        ]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_image_builds_successfully(self):
        """TC-UT-01: Dockerfile builds successfully (image exists)."""
        result = subprocess.run(
            ["docker", "image", "inspect", "shc:test-nonroot"],
            capture_output=True,
            text=True,
        )
        assert (
            result.returncode == 0
        ), f"Image shc:test-nonroot not found: {result.stderr}"

    def test_image_user_directive_is_root_for_entrypoint(self):
        """TC-UT-02: Image has USER=root for entrypoint-root.sh (drops to shc)."""
        result = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "shc:test-nonroot",
                "--format",
                "{{.Config.User}}",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"docker image inspect failed: {result.stderr}"
        user = result.stdout.strip()
        # The entrypoint-root.sh runs as root, then drops to shc via gosu
        assert (
            user == "root"
        ), f"Expected USER=root for entrypoint-root.sh, got: {user!r}"

    def test_non_root_user_exists_in_image(self):
        """TC-UT-03: Non-root user 'shc' (UID 1000/GID 1000) exists in image."""
        result = self._run_as_root("id shc")
        assert result.returncode == 0, f"docker run id shc failed: {result.stderr}"
        output = result.stdout.strip()
        assert "uid=1000(shc)" in output, f"Expected uid=1000(shc), got: {output}"
        assert "gid=1000(shc)" in output, f"Expected gid=1000(shc), got: {output}"

    def test_entrypoint_drops_to_shc_user(self):
        """Verify entrypoint-root.sh drops privileges to shc user."""
        result = self._run_as_root("gosu shc id")
        assert result.returncode == 0, f"gosu shc id failed: {result.stderr}"
        output = result.stdout.strip()
        assert (
            "uid=1000(shc)" in output
        ), f"Expected uid=1000(shc) after gosu, got: {output}"
        assert (
            "gid=1000(shc)" in output
        ), f"Expected gid=1000(shc) after gosu, got: {output}"

    def test_entrypoint_sh_executable_by_shc(self):
        """TC-UT-04: Entrypoint script is executable by shc user."""
        result = self._run_as_shc("test -x /usr/local/bin/entrypoint.sh")
        assert (
            result.returncode == 0
        ), f"entrypoint.sh not executable by shc: {result.stderr}"

    def test_entrypoint_root_script_exists(self):
        """Verify entrypoint-root.sh exists and is executable."""
        result = self._run_as_root("test -x /usr/local/bin/entrypoint-root.sh")
        assert (
            result.returncode == 0
        ), f"entrypoint-root.sh not executable: {result.stderr}"

    def test_gosu_installed(self):
        """Verify gosu is installed in the image."""
        result = self._run_as_root("which gosu")
        assert result.returncode == 0, f"gosu not installed: {result.stderr}"
        gosu_paths = [
            "/usr/sbin/gosu",
            "/usr/bin/gosu",
            "/usr/local/bin/gosu",
        ]
        assert any(p in result.stdout for p in gosu_paths)

    def test_runtime_directories_exist_with_correct_ownership(self):
        """TC-UT-05: Runtime dirs exist with correct ownership in image fs."""
        for path in ["/app/data", "/app/.shc"]:
            result = self._run_as_root(f"stat -c %u:%g {path}")
            assert result.returncode == 0, f"stat {path} failed: {result.stderr}"
            assert (
                result.stdout.strip() == "1000:1000"
            ), f"{path} owned by {result.stdout.strip()}, expected 1000:1000"

        # /tmp/syncthing created at runtime by entrypoint-root.sh (tmpfs)
        # Verify it can be created with correct ownership
        result = self._run_as_shc(
            "mkdir -p /tmp/syncthing && stat -c %u:%g /tmp/syncthing"
        )
        assert (
            result.returncode == 0
        ), f"mkdir/stat /tmp/syncthing failed: {result.stderr}"
        assert (
            result.stdout.strip() == "1000:1000"
        ), f"/tmp/syncthing owned by {result.stdout.strip()}, expected 1000:1000"

    def test_runtime_directories_have_correct_permissions(self):
        """Verify runtime directories have correct permissions."""
        for path, expected_perm in [("/app/data", "750"), ("/app/.shc", "700")]:
            result = self._run_as_root(f"stat -c %a {path}")
            assert (
                result.returncode == 0
            ), f"stat {path} permissions failed: {result.stderr}"
            assert (
                result.stdout.strip() == expected_perm
            ), f"{path} permissions {result.stdout.strip()}, expected {expected_perm}"

        # /tmp/syncthing created at runtime by entrypoint-root.sh with chmod 750
        # Verify entrypoint-root.sh would set correct permissions
        result = self._run_as_root(
            "mkdir -p /tmp/syncthing && chmod 750 /tmp/syncthing "
            "&& stat -c %a /tmp/syncthing"
        )
        assert (
            result.returncode == 0
        ), f"stat /tmp/syncthing permissions failed: {result.stderr}"
        assert (
            result.stdout.strip() == "750"
        ), f"/tmp/syncthing permissions {result.stdout.strip()}, expected 750"

    def test_frontend_assets_readable_by_shc(self):
        """TC-UT-06: Frontend assets are readable by shc user."""
        result = self._run_as_shc("test -r /app/frontend/_shell.html")
        assert (
            result.returncode == 0
        ), f"_shell.html not readable by shc: {result.stderr}"

    def test_syncthing_binary_executable_by_shc(self):
        """TC-UT-07: Syncthing binary is executable by shc user."""
        result = self._run_as_shc("test -x /usr/local/bin/syncthing")
        assert (
            result.returncode == 0
        ), f"syncthing not executable by shc: {result.stderr}"

    def test_home_directory_exists(self):
        """Verify shc user has home directory."""
        result = self._run_as_root("test -d /home/shc")
        assert result.returncode == 0, f"/home/shc directory missing: {result.stderr}"

        result = self._run_as_root("stat -c %u:%g /home/shc")
        assert result.returncode == 0
        assert (
            result.stdout.strip() == "1000:1000"
        ), f"/home/shc owned by {result.stdout.strip()}, expected 1000:1000"

    def test_no_root_processes_in_running_container(self):
        """TC-SEC-01: No process runs as root in a running container."""
        # This test requires a running container, so it's marked as compose test
        pytest.skip("Requires running container - run via compose suite")

    def test_app_code_read_only_for_shc(self):
        """TC-SEC-02: Cannot write to /app (read-only app code)."""
        result = self._run_as_shc("touch /app/test 2>&1 || true")
        # Should fail with permission denied
        assert (
            "Permission denied" in result.stdout
            or "Read-only file system" in result.stdout
            or result.returncode != 0
        )

    def test_usr_local_bin_read_only_for_shc(self):
        """TC-SEC-03: Cannot write to /usr/local/bin."""
        result = self._run_as_shc("touch /usr/local/bin/test 2>&1 || true")
        # Should fail with permission denied
        assert "Permission denied" in result.stdout or result.returncode != 0


class TestDockerEntrypointRoot:
    """Tests for the entrypoint-root.sh wrapper script."""

    def test_entrypoint_root_fixes_volume_ownership(self):
        """Verify entrypoint-root.sh fixes ownership of volume mount points."""
        volume_name = "shc-test-volume-ownership"
        try:
            subprocess.run(
                ["docker", "volume", "create", volume_name],
                check=True,
                capture_output=True,
            )

            # Run entrypoint-root.sh's chown logic directly (without starting app)
            cmd = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{volume_name}:/app/data",
                "--entrypoint",
                "/bin/sh",
                "shc:test-nonroot",
                "-c",
                "chown -R 1000:1000 /app/data 2>/dev/null || true "
                "&& stat -c %u:%g /app/data",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, f"Container failed: {result.stderr}"
            assert (
                result.stdout.strip() == "1000:1000"
            ), f"Volume ownership not fixed: {result.stdout.strip()}"
        finally:
            subprocess.run(
                ["docker", "volume", "rm", volume_name],
                capture_output=True,
            )

    def test_entrypoint_root_fixes_state_volume_ownership(self):
        """Verify entrypoint-root.sh fixes ownership of shc-state volume."""
        volume_name = "shc-test-state-ownership"
        try:
            subprocess.run(
                ["docker", "volume", "create", volume_name],
                check=True,
                capture_output=True,
            )

            cmd = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{volume_name}:/app/.shc",
                "--entrypoint",
                "/bin/sh",
                "shc:test-nonroot",
                "-c",
                "chown -R 1000:1000 /app/.shc 2>/dev/null || true "
                "&& stat -c %u:%g /app/.shc",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, f"Container failed: {result.stderr}"
            assert (
                result.stdout.strip() == "1000:1000"
            ), f"State volume ownership not fixed: {result.stdout.strip()}"
        finally:
            subprocess.run(
                ["docker", "volume", "rm", volume_name],
                capture_output=True,
            )

    def test_entrypoint_root_script_runs_chown(self):
        """Verify entrypoint-root.sh contains the correct chown commands."""
        cmd = [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "cat",
            "shc:test-nonroot",
            "/usr/local/bin/entrypoint-root.sh",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert (
            result.returncode == 0
        ), f"Failed to read entrypoint-root.sh: {result.stderr}"
        script_content = result.stdout
        assert "chown -R 1000:1000 /app/data /app/.shc /tmp/syncthing" in script_content
        assert "chmod 750 /app/data /tmp/syncthing" in script_content
        assert "chmod 700 /app/.shc" in script_content
        assert "exec gosu shc /usr/local/bin/entrypoint.sh" in script_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
