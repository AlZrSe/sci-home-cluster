"""
Unit tests for Syncthing folders API endpoint.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.services.syncthing_service import SyncthingService
from backend.core.config import settings


@pytest.mark.unit
class TestSyncthingFoldersAPI:
    """Tests for GET /api/v1/syncthing/folders endpoint."""

    @pytest.fixture
    def temp_syncthing_root(self, isolated_syncthing_root):
        """Create a temporary Syncthing root directory with nested structure."""
        root = Path(isolated_syncthing_root)
        # Create some directories (jobs and nodes already exist from fixture)
        (root / "data").mkdir(exist_ok=True)
        (root / "data" / "in").mkdir(exist_ok=True)
        (root / "data" / "out").mkdir(exist_ok=True)
        (root / "projects").mkdir(exist_ok=True)
        (root / "projects" / "my-project").mkdir(exist_ok=True)
        (root / "shared").mkdir(exist_ok=True)
        # Create a hidden directory (should be excluded)
        (root / ".hidden").mkdir(exist_ok=True)
        (root / "data" / ".hidden-nested").mkdir(exist_ok=True)
        yield root

    @pytest.fixture
    async def client_with_syncthing(self, temp_syncthing_root):
        """Create an async client with syncthing_service set up."""
        syncthing_service = SyncthingService(temp_syncthing_root)
        await syncthing_service.start()
        app.state.syncthing_service = syncthing_service
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                client.headers["Authorization"] = "Bearer localhost-no-auth"
                yield client
        finally:
            await syncthing_service.stop()
            del app.state.syncthing_service

    @pytest.mark.asyncio
    async def test_list_folders_success(
        self, client_with_syncthing, temp_syncthing_root
    ):
        """Test successful folder listing with nested structure."""
        response = await client_with_syncthing.get("/api/v1/syncthing/folders")
        assert response.status_code == 200

        data = response.json()
        assert "root" in data
        assert "folders" in data
        assert data["root"] == str(temp_syncthing_root)

        # Check expected folders are present
        folders = data["folders"]
        assert "data" in folders
        assert "data/in" in folders
        assert "data/out" in folders
        assert "projects" in folders
        assert "projects/my-project" in folders
        assert "shared" in folders
        # jobs and nodes are created by the fixture
        assert "jobs" in folders
        assert "nodes" in folders

        # Should be sorted
        assert folders == sorted(folders)

    @pytest.mark.asyncio
    async def test_list_folders_excludes_hidden(
        self, client_with_syncthing, temp_syncthing_root
    ):
        """Test that hidden directories (starting with .) are excluded."""
        response = await client_with_syncthing.get("/api/v1/syncthing/folders")
        assert response.status_code == 200

        data = response.json()
        folders = data["folders"]

        # Hidden directories should not be in the list
        assert ".hidden" not in folders
        assert "data/.hidden-nested" not in folders
        # No folder should start with .
        for folder in folders:
            parts = folder.split("/")
            for part in parts:
                assert not part.startswith("."), f"Hidden folder part found: {part}"

    @pytest.mark.asyncio
    async def test_list_folders_sorted(
        self, client_with_syncthing, temp_syncthing_root
    ):
        """Test that folders are returned in sorted order."""
        response = await client_with_syncthing.get("/api/v1/syncthing/folders")
        assert response.status_code == 200

        data = response.json()
        folders = data["folders"]
        assert folders == sorted(folders)

    @pytest.mark.asyncio
    async def test_list_folders_minimal_root(self):
        """Test folder listing with minimal root (only jobs and nodes
        from SyncthingService)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            syncthing_service = SyncthingService(root)
            await syncthing_service.start()
            app.state.syncthing_service = syncthing_service
            # Override settings for this test
            original_root = settings.SYNCTHING_ROOT
            settings.SYNCTHING_ROOT = str(root)
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    client.headers["Authorization"] = "Bearer localhost-no-auth"
                    response = await client.get("/api/v1/syncthing/folders")
                    assert response.status_code == 200

                    data = response.json()
                    assert data["root"] == str(root)
                    # SyncthingService creates jobs and nodes directories
                    folders = data["folders"]
                    assert "jobs" in folders
                    assert "nodes" in folders
                    assert len(folders) == 2
            finally:
                await syncthing_service.stop()
                del app.state.syncthing_service
                settings.SYNCTHING_ROOT = original_root

    @pytest.mark.asyncio
    async def test_list_folders_unavailable_no_root(self):
        """Test 503 error when SYNCTHING_ROOT is not set."""
        with patch.object(settings, "SYNCTHING_ROOT", ""):
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

    @pytest.mark.asyncio
    async def test_list_folders_unavailable_not_exists(self):
        """Test 503 error when SYNCTHING_ROOT points to non-existent path."""
        with patch.object(settings, "SYNCTHING_ROOT", "/non/existent/path"):
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

    @pytest.mark.asyncio
    async def test_list_folders_requires_auth(self):
        """Test that endpoint requires authentication when localhost
        bypass is not active."""
        # The localhost bypass is based on the client IP address (127.0.0.1).
        # We disable the bypass for this test.
        from backend.core.config import LocalhostBypassMode

        bypass_mode = LocalhostBypassMode.DISABLED
        with patch.object(settings, "LOCALHOST_BYPASS_MODE", bypass_mode):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                # No Authorization header
                response = await client.get("/api/v1/syncthing/folders")
                assert response.status_code == 401
