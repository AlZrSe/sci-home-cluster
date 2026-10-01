"""
Unit tests for the configuration module.
"""

import os
import tempfile
from backend.core.config import Settings


def test_settings_defaults():
    """Test that settings have correct default values."""
    # Create a temporary .env file for testing
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("SYNCTHING_ROOT=/tmp/test\n")
        env_file = f.name

    try:
        # Set env var BEFORE creating Settings (fixture checks os.environ first)
        os.environ["SYNCTHING_ROOT"] = "/tmp/test"
        # Hermeticity: a SEED_DEMO_DATA left in the developer's environment or
        # in a repo-root .env would otherwise decide this assertion.
        os.environ.pop("SEED_DEMO_DATA", None)
        settings = Settings(_env_file=env_file)

        assert settings.API_V1_STR == "/api/v1"
        assert settings.PROJECT_NAME == "Scientific Home Cluster API"
        assert settings.VERSION == "0.1.0"  # from pyproject.toml
        assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 1440  # 24 hours
        assert settings.SYNCTHING_ROOT == "/tmp/test"
        assert settings.DATABASE_URL == "sqlite:///./scientific_home_cluster.db"
        assert settings.LOG_LEVEL == "INFO"
        assert settings.LOCALHOST_BYPASS
        # Demo seeding must be opt-in: a production database starts empty.
        assert settings.SEED_DEMO_DATA is False
    finally:
        # Clean up
        os.unlink(env_file)
        if "ENV_FILE" in os.environ:
            del os.environ["ENV_FILE"]
        if "SYNCTHING_ROOT" in os.environ:
            del os.environ["SYNCTHING_ROOT"]
        if "SEED_DEMO_DATA" in os.environ:
            del os.environ["SEED_DEMO_DATA"]


def test_cors_origins_parsing():
    """Test that CORS origins are parsed correctly from string."""
    # Test the validator logic directly with a string input
    from backend.core.config import Settings

    # Test that comma-separated string gets parsed to list
    cors_string = "http://localhost:3000,http://localhost:5173"
    result = Settings.assemble_cors_origins(cors_string)
    assert result == ["http://localhost:3000", "http://localhost:5173"]

    # Test that list input is returned as-is
    cors_list = ["http://localhost:3000", "http://localhost:5173"]
    result = Settings.assemble_cors_origins(cors_list)
    assert result == cors_list

    # Test default value
    settings = Settings()
    assert "http://localhost:3000" in settings.BACKEND_CORS_ORIGINS
    assert "http://localhost:5173" in settings.BACKEND_CORS_ORIGINS
