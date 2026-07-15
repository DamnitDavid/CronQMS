"""User model for authentication and authorization."""

from datetime import datetime
from enum import Enum

from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.types import Integer

from app.database import Base


class Role(str, Enum):
    """System roles, ordered roughly from most to least privileged."""

    ADMIN = "Admin"
    QUALITY_MANAGER = "QualityManager"
    INVESTIGATOR = "Investigator"
    APPROVER = "Approver"
    VIEWER = "Viewer"


class User(Base):
    """User model representing a system user.

    Attributes:
        id: Primary key, auto-incrementing integer
        email: User email, unique and indexed
        hashed_password: BCrypt hashed password
        role: One of the :class:`Role` values, stored as its string value
        organization_id: Organization the user belongs to (access scope)
        is_active: Whether the user account is active
        created_at: Timestamp of user creation (audit trail)
        updated_at: Timestamp of last update (audit trail)
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), default=Role.VIEWER.value, nullable=False)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True
    )
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    organization = relationship("Organization", back_populates="users")

    def __repr__(self) -> str:
        """String representation of User."""
        return (
            f"<User(id={self.id}, email={self.email}, role={self.role}, is_active={self.is_active})>"
        )
