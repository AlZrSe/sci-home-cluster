"""
Test configuration and fixtures for the backend test suite.
"""

import os
import tempfile
import pytest


@pytest.fixture(autouse=True)
def set_syncthing_root(monkeypatch):
    """
    Automatically set SYNCTHING_ROOT to a temporary directory for all tests.
    This ensures tests don't depend on external filesystem state.
    Only sets if not already set by the test (e.g., via _env_file).
    """
    # Only set if not already in environment (tests can override via .env file)
    if "SYNCTHING_ROOT" not in os.environ:
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("SYNCTHING_ROOT", tmpdir)
            yield
    else:
        yield


@pytest.fixture(autouse=True)
def reset_singleton_store():
    """
    Reset the in-memory store singleton before each test.
    This ensures test isolation for store-dependent tests.
    """
    # Import here to avoid circular imports
    from backend.store.memory import get_store

    store = get_store()
    # Use the existing reset method on the store instance
    import asyncio

    asyncio.run(store.reset())
    yield
    asyncio.run(store.reset())
