"""User model for authentication and authorization."""

from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.types import Integer

from app.database import Base


class User(Base):
    """User model representing a system user.

    Attributes:
        id: Primary key, auto-incrementing integer
        email: User email, unique and indexed
        hashed_password: BCrypt hashed password
        is_active: Whether the user account is active
        created_at: Timestamp of user creation (audit trail)
        updated_at: Timestamp of last update (audit trail)
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        """String representation of User."""
        return f"<User(id={self.id}, email={self.email}, is_active={self.is_active})>"
