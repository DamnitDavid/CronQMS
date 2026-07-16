"""Admin-managed, database-backed roles and their permission grants.

Roles used to be a fixed :class:`app.models.user.Role` enum with a hard-coded
grant table in ``app.core.permissions``. These models make roles data instead:
each organization owns a set of roles, and each role grants a set of permission
strings (values of :class:`app.core.permissions.Permission`).

Two roles are seeded as ``is_system`` for every organization — "Admin" (grants
everything) and "User" (a basic default) — and cannot be deleted. All other
roles are created by admins. The class is named ``RoleDefinition`` so it does
not collide with the still-used ``Role`` enum (the source of the "Admin"/"User"
name constants and back-compat for un-seeded databases).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class RoleDefinition(Base):
    """A named role within an organization that grants a set of permissions."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_roles_org_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name = Column(String(50), nullable=False)
    description = Column(String(255), nullable=True)
    # System roles ("Admin", "User") are seeded per org and cannot be deleted.
    is_system = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    permissions = relationship(
        "RolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return (
            f"<RoleDefinition(id={self.id}, name={self.name}, "
            f"org={self.organization_id}, is_system={self.is_system})>"
        )


class RolePermission(Base):
    """A single permission grant belonging to a :class:`RoleDefinition`.

    ``permission`` stores a bare :class:`app.core.permissions.Permission` value
    such as ``"event:create"``. The permission catalog itself stays a code enum,
    so this is a plain string column rather than a foreign key.
    """

    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission", name="uq_role_permissions_role_perm"),
    )

    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(
        Integer,
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission = Column(String(50), nullable=False)

    role = relationship("RoleDefinition", back_populates="permissions")

    def __repr__(self) -> str:
        return f"<RolePermission(role_id={self.role_id}, permission={self.permission})>"
