"""Assignee groups: a named set of users an event can be assigned to.

An event can be assigned to a single user (``Event.assigned_to``) OR to a group
(``Event.assigned_group_id``). Groups are optional and organization-scoped.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
)
from sqlalchemy.orm import relationship

from app.database import Base


# Many-to-many: a group has many users; a user can be in many groups.
assignee_group_members = Table(
    "assignee_group_members",
    Base.metadata,
    Column("group_id", Integer, ForeignKey("assignee_groups.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class AssigneeGroup(Base):
    """A named group of users within an organization."""

    __tablename__ = "assignee_groups"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    members = relationship("User", secondary=assignee_group_members, lazy="selectin")

    def __repr__(self) -> str:
        return f"<AssigneeGroup(id={self.id}, org={self.organization_id}, name={self.name})>"
