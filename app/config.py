"""Application configuration and environment variable management."""

import os
import tempfile
from typing import Optional
from functools import lru_cache
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

# Built-in placeholder secrets shipped in the repo. These are intentionally
# public, so they must never be used to sign tokens in a real deployment. The
# production guard below refuses to start if any of them survive into a
# non-development environment.
INSECURE_DEFAULT_SECRETS = {
    "your-secret-key-change-in-production",
    "your-jwt-secret-change-in-production",
    "dev-secret-key-change-in-production",
    "dev-jwt-secret-change-in-production",
    "",
}

# Environments that are allowed to run with the built-in defaults (local dev and
# the test suite). Anything else is treated as a real deployment.
_DEV_ENVIRONMENTS = {"development", "test", "testing", "local"}


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
    # Public self-service registration (POST /api/auth/register and the /register
    # page). Off by default: in this app users are provisioned by an admin under
    # /admin/users, and the first admin via the /setup wizard. Only enable this
    # if you deliberately want anyone to be able to create an account.
    allow_public_registration: bool = False
    # Idle window before the browser auto-logs the user out. Mirrored by the
    # client-side countdown in app/static/js/idle-logout.js.
    session_timeout_minutes: int = 15

    # Attachments: base directory for the local-disk storage backend. Swap the
    # backend in app/core/storage.py to move to S3.
    attachment_storage_dir: str = os.path.join(tempfile.gettempdir(), "proins_attachments")
    attachment_max_bytes: int = 25 * 1024 * 1024  # 25 MB

    class Config:
        """Pydantic config."""

        env_file = ".env"
        case_sensitive = False
        extra = "ignore"

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Normalize managed-Postgres URLs to a driver SQLAlchemy accepts.

        Managed platforms (Render, Railway, Heroku-style providers) hand out
        connection strings with the ``postgres://`` scheme, which SQLAlchemy 2.0
        rejects. Rewrite the legacy scheme to ``postgresql://`` so the app boots
        against a managed database without hand-editing the injected URL.
        """
        if value.startswith("postgres://"):
            return "postgresql://" + value[len("postgres://") :]
        return value

    @model_validator(mode="after")
    def _reject_insecure_production_secrets(self) -> "Settings":
        """Fail fast if a real deployment still uses the built-in signing keys.

        JWTs and cookies are signed with ``jwt_secret``/``secret_key``. If those
        are left at their public repo defaults in a non-development environment,
        anyone can forge a token for any user (including Admin). Rather than ship
        that silently, refuse to construct settings so the process never starts.
        """
        if self.environment.strip().lower() in _DEV_ENVIRONMENTS:
            return self

        offenders = []
        if self.secret_key in INSECURE_DEFAULT_SECRETS:
            offenders.append("SECRET_KEY")
        if self.jwt_secret in INSECURE_DEFAULT_SECRETS:
            offenders.append("JWT_SECRET")
        if offenders:
            raise ValueError(
                f"Refusing to start in environment='{self.environment}': "
                f"{', '.join(offenders)} still set to a built-in default. "
                "Set a strong, unique value (e.g. `openssl rand -hex 32`)."
            )
        return self


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance.

    Returns:
        Settings: Application settings instance.
    """
    return Settings()
