"""Seeding and shared constants for the database-backed role system.

The permission *catalog* is the :class:`app.core.permissions.Permission` enum;
which permissions a role grants is data (``roles`` + ``role_permissions``). Two
roles are seeded per organization and flagged ``is_system`` so they can't be
deleted:

* **Admin** — granted every permission (also short-circuited in the resolver so
  newly added permissions auto-apply).
* **User** — a basic default: read-only access to the core surfaces.

New organizations call :func:`seed_system_roles` (from the setup wizard); the
Alembic data migration seeds existing organizations the same way.
"""

from sqlalchemy.orm import Session

from app.core.permissions import Permission
from app.models.role import RoleDefinition, RolePermission
from app.models.user import Role

# System role names, sourced from the still-used Role enum for consistency.
ADMIN_ROLE_NAME = Role.ADMIN.value  # "Admin"
USER_ROLE_NAME = "User"

# The basic default grant for the seeded "User" role (the legacy Viewer set).
DEFAULT_USER_PERMISSIONS: set[str] = {
    Permission.DASHBOARD_VIEW.value,
    Permission.EVENT_READ.value,
    Permission.CAPA_READ.value,
    Permission.ALERT_READ.value,
}

ALL_PERMISSIONS: set[str] = {p.value for p in Permission}


def _ensure_role(
    db: Session,
    organization_id: int,
    name: str,
    permissions: set[str],
    *,
    is_system: bool,
    description: str | None = None,
) -> RoleDefinition:
    """Create ``name`` for the org with ``permissions`` if it doesn't exist.

    Idempotent: an existing role of the same name is returned untouched so
    re-running seeding never clobbers admin edits. Does not commit.
    """
    role = (
        db.query(RoleDefinition)
        .filter(
            RoleDefinition.organization_id == organization_id,
            RoleDefinition.name == name,
        )
        .first()
    )
    if role is not None:
        return role

    role = RoleDefinition(
        organization_id=organization_id,
        name=name,
        description=description,
        is_system=is_system,
    )
    db.add(role)
    db.flush()  # assign role.id before adding its permission rows
    for perm in sorted(permissions):
        db.add(RolePermission(role_id=role.id, permission=perm))
    return role


def seed_system_roles(db: Session, organization_id: int) -> None:
    """Idempotently create the Admin + User system roles for an organization.

    Callers commit the surrounding transaction.
    """
    _ensure_role(
        db,
        organization_id,
        ADMIN_ROLE_NAME,
        ALL_PERMISSIONS,
        is_system=True,
        description="Full access to every part of the system.",
    )
    _ensure_role(
        db,
        organization_id,
        USER_ROLE_NAME,
        DEFAULT_USER_PERMISSIONS,
        is_system=True,
        description="Basic read-only access.",
    )
