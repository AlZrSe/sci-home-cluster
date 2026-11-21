"""
Integration tests for Syncthing folders API endpoint.
Tests the endpoint with real filesystem operations.
"""

import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.services.syncthing_service import SyncthingService
from backend.core.config import settings


@pytest.mark.integration
class TestSyncthingFoldersIntegration:
    """Integration tests for GET /api/v1/syncthing/folders endpoint."""

    @pytest.fixture
    def temp_syncthing_root(self):
        """Create a temporary Syncthing root directory with nested structure."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create a realistic folder structure
            (root / "data").mkdir()
            (root / "data" / "in").mkdir()
            (root / "data" / "out").mkdir()
            (root / "data" / "raw").mkdir()
            (root / "projects").mkdir()
            (root / "projects" / "project-a").mkdir()
            (root / "projects" / "project-a" / "experiments").mkdir()
            (root / "projects" / "project-b").mkdir()
            (root / "shared").mkdir()
            (root / "shared" / "datasets").mkdir()
            (root / "shared" / "models").mkdir()
            # Hidden directories (should be excluded)
            (root / ".git").mkdir()
            (root / "data" / ".cache").mkdir()
            yield root

    @pytest.fixture
    async def async_client(self, temp_syncthing_root) -> AsyncClient:
        """Create an async client with full lifespan and syncthing_service."""
        # Override settings for this test
        original_root = settings.SYNCTHING_ROOT
        settings.SYNCTHING_ROOT = str(temp_syncthing_root)

        # Create and start syncthing service
        syncthing_service = SyncthingService(temp_syncthing_root)
        await syncthing_service.start()
        app.state.syncthing_service = syncthing_service

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            client.headers["Authorization"] = "Bearer localhost-no-auth"
            yield client

        # Cleanup
        await syncthing_service.stop()
        del app.state.syncthing_service
        settings.SYNCTHING_ROOT = original_root

    @pytest.mark.asyncio
    async def test_e2e_folder_listing(self, async_client, temp_syncthing_root):
        """End-to-end test of folder listing with real filesystem."""
        response = await async_client.get("/api/v1/syncthing/folders")
        assert response.status_code == 200

        data = response.json()
        assert "root" in data
        assert "folders" in data
        assert data["root"] == str(temp_syncthing_root)

        folders = data["folders"]

        # Check all expected folders are present
        expected_folders = [
            "data",
            "data/in",
            "data/out",
            "data/raw",
            "projects",
            "projects/project-a",
            "projects/project-a/experiments",
            "projects/project-b",
            "shared",
            "shared/datasets",
            "shared/models",
        ]

        for expected in expected_folders:
            assert (
                expected in folders
            ), f"Expected folder '{expected}' not found in {folders}"

        # Check hidden directories are excluded
        assert ".git" not in folders
        assert "data/.cache" not in folders

        # Check no hidden parts
        for folder in folders:
            parts = folder.split("/")
            for part in parts:
                assert not part.startswith("."), f"Hidden folder part found: {part}"

        # Check sorted order
        assert folders == sorted(folders)

    @pytest.mark.asyncio
    async def test_e2e_empty_root(self):
        """Test folder listing with empty root directory.

        Note: SyncthingService creates 'jobs' and 'nodes' directories on startup,
        so the folder list will contain at least those two.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            original_root = settings.SYNCTHING_ROOT
            settings.SYNCTHING_ROOT = str(root)

            syncthing_service = SyncthingService(root)
            await syncthing_service.start()
            app.state.syncthing_service = syncthing_service

            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    client.headers["Authorization"] = "Bearer localhost-no-auth"
                    response = await client.get("/api/v1/syncthing/folders")
                    assert response.status_code == 200

                    data = response.json()
                    assert data["root"] == str(root)
                    folders = data["folders"]
                    # SyncthingService creates jobs and nodes directories
                    assert "jobs" in folders
                    assert "nodes" in folders
                    assert len(folders) == 2
            finally:
                await syncthing_service.stop()
                del app.state.syncthing_service
                settings.SYNCTHING_ROOT = original_root

    @pytest.mark.asyncio
    async def test_e2e_unavailable_not_exists(self):
        """Test 503 when SYNCTHING_ROOT points to non-existent path."""
        original_root = settings.SYNCTHING_ROOT
        settings.SYNCTHING_ROOT = "/non/existent/path/that/does/not/exist"

        try:
            # Don't start syncthing_service for this test
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                client.headers["Authorization"] = "Bearer localhost-no-auth"
                response = await client.get("/api/v1/syncthing/folders")
                assert response.status_code == 503

                data = response.json()
                assert data["status"] == 503
                assert data["title"] == "Syncthing Unavailable"
                assert data["error_code"] == "SYNCTHING_UNAVAILABLE"
        finally:
            settings.SYNCTHING_ROOT = original_root
