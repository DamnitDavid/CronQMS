"""Application configuration and environment variable management."""

import os
import tempfile
from typing import Optional
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = "Proins"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: str = "development"  # development, staging, production

    # Database
    database_url: str = "postgresql://user:password@localhost:5432/proins"
    database_echo: bool = False  # Log all SQL statements in development

    # Security
    secret_key: str = "your-secret-key-change-in-production"
    jwt_secret: str = "your-jwt-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = ["*"]
    cors_allow_headers: list[str] = ["*"]

    # Authentication
    password_min_length: int = 8
    session_timeout_minutes: int = 480  # 8 hours

    # Attachments: base directory for the local-disk storage backend. Swap the
    # backend in app/core/storage.py to move to S3.
    attachment_storage_dir: str = os.path.join(tempfile.gettempdir(), "proins_attachments")
    attachment_max_bytes: int = 25 * 1024 * 1024  # 25 MB

    class Config:
        """Pydantic config."""

        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance.

    Returns:
        Settings: Application settings instance.
    """
    return Settings()
