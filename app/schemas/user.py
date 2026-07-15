"""Pydantic schemas for user-related requests and responses."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator

from app.models.user import Role


class UserCreate(BaseModel):
    """Schema for user registration request."""

    email: EmailStr
    password: str
    role: Role = Role.VIEWER
    organization_id: Optional[int] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password meets minimum requirements.

        Args:
            v: Password string.

        Returns:
            str: Validated password.

        Raises:
            ValueError: If password is too short.
        """
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v


class UserLogin(BaseModel):
    """Schema for user login request."""

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """Schema for user response data."""

    id: int
    email: str
    role: str
    organization_id: Optional[int]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        """Pydantic config."""

        from_attributes = True


class CurrentUser(BaseModel):
    """Schema for current authenticated user information."""

    id: int
    email: str
    role: str
    organization_id: Optional[int]
    is_active: bool

    class Config:
        """Pydantic config."""

        from_attributes = True


class TokenResponse(BaseModel):
    """Schema for authentication token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
