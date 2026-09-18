"""
Unit tests for the configuration module.
"""

import os
import tempfile
from backend.core.config import Settings


def test_settings_defaults():
    """Test that settings have correct default values."""
    # Create a temporary .env file for testing
    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        f.write("SYNCTHING_ROOT=/tmp/test\n")
        env_file = f.name
    
    try:
        # Override the env file path
        os.environ["ENV_FILE"] = env_file
        settings = Settings(_env_file=env_file)
        
        assert settings.API_V1_STR == "/api/v1"
        assert settings.PROJECT_NAME == "Scientific Home Cluster API"
        assert settings.VERSION == "1.0.0"
        assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 60 * 24 * 8  # 8 days
        assert settings.SYNCTHING_ROOT == "/tmp/test"
        assert settings.DATABASE_URL == "sqlite:///./scientific_home_cluster.db"
        assert settings.LOG_LEVEL == "INFO"
        assert settings.LOCALHOST_BYPASS == True
    finally:
        # Clean up
        os.unlink(env_file)
        if "ENV_FILE" in os.environ:
            del os.environ["ENV_FILE"]


def test_cors_origins_parsing():
    """Test that CORS origins are parsed correctly."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
        f.write("SYNCTHING_ROOT=/tmp/test\n")
        f.write("BACKEND_CORS_ORIGINS=http://localhost:3000,http://localhost:5173\n")
        env_file = f.name
    
    try:
        settings = Settings(_env_file=env_file)
        assert settings.BACKEND_CORS_ORIGINS == [
            "http://localhost:3000",
            "http://localhost:5173"
        ]
    finally:
        os.unlink(env_file)