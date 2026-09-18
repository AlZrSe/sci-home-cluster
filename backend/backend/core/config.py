"""
Configuration module for the Scientific Home Cluster Backend.
Uses Pydantic v2 BaseSettings for environment variable management.
"""

from pydantic import PostgresDsn, AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic.networks import AnyUrl
from typing import List, Union, Optional
import secrets


class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Scientific Home Cluster API"
    VERSION: str = "1.0.0"
    
    # Security settings
    SECRET_KEY: str = secrets.token_urlsafe(32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days
    
    # Syncthing configuration
    SYNCTHING_ROOT: str
    
    # Database configuration
    DATABASE_URL: str = "sqlite:///./scientific_home_cluster.db"
    
    # CORS origins
    BACKEND_CORS_ORIGINS: List[Union[str, AnyHttpUrl]] = [
        "http://localhost:3000",
        "http://localhost:5173"
    ]
    
    @field_validator("BACKEND_CORS_ORIGINS", mode='before')
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[Union[str, AnyHttpUrl]]]) -> Union[List[str], str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Localhost bypass for development
    LOCALHOST_BYPASS: bool = True
    
    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()