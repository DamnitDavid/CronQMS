"""Dependency injection for API routes."""

from typing import Optional
from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.auth import get_current_user, get_current_user_optional
from app.schemas.user import CurrentUser

# Export common dependencies for use in routes
__all__ = [
    "get_db",
    "get_current_user",
    "get_current_user_optional",
]
