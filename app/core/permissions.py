"""Role-based permissions.

A single mapping (:data:`ROLE_PERMISSIONS`) defines what each :class:`Role`
may do. Routes declare the permission they need via :func:`require_permission`,
replacing ad-hoc ``role != "Admin"`` string checks scattered across handlers.
"""

from enum import Enum

from fastapi import Depends, HTTPException, status

from app.core.auth import get_current_user
from app.models.user import Role, User


class Permission(str, Enum):
    """Discrete, action-level permissions."""

    EVENT_CREATE = "event:create"
    EVENT_READ = "event:read"
    EVENT_UPDATE = "event:update"
    EVENT_DELETE = "event:delete"
    EVENT_CHANGE_STATUS = "event:change_status"
    EVENT_APPROVE_CLOSURE = "event:approve_closure"
    EVENT_REOPEN = "event:reopen"
    EVENT_COMMENT = "event:comment"
    CAPA_CREATE = "capa:create"
    CAPA_READ = "capa:read"
    CAPA_UPDATE = "capa:update"
    CAPA_VERIFY = "capa:verify"
    USER_MANAGE = "user:manage"
    DASHBOARD_VIEW = "dashboard:view"


# Permission grants per role. Admin is granted everything explicitly so adding a
# new permission fails closed for other roles until it is deliberately granted.
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: set(Permission),
    Role.QUALITY_MANAGER: {
        Permission.EVENT_CREATE,
        Permission.EVENT_READ,
        Permission.EVENT_UPDATE,
        Permission.EVENT_DELETE,
        Permission.EVENT_CHANGE_STATUS,
        Permission.EVENT_APPROVE_CLOSURE,
        Permission.EVENT_REOPEN,
        Permission.EVENT_COMMENT,
        Permission.CAPA_CREATE,
        Permission.CAPA_READ,
        Permission.CAPA_UPDATE,
        Permission.CAPA_VERIFY,
        Permission.DASHBOARD_VIEW,
    },
    Role.INVESTIGATOR: {
        Permission.EVENT_CREATE,
        Permission.EVENT_READ,
        Permission.EVENT_UPDATE,
        Permission.EVENT_CHANGE_STATUS,
        Permission.EVENT_COMMENT,
        Permission.CAPA_CREATE,
        Permission.CAPA_READ,
        Permission.CAPA_UPDATE,
        Permission.DASHBOARD_VIEW,
    },
    Role.APPROVER: {
        Permission.EVENT_READ,
        Permission.EVENT_CHANGE_STATUS,
        Permission.EVENT_APPROVE_CLOSURE,
        Permission.EVENT_COMMENT,
        Permission.CAPA_READ,
        Permission.CAPA_VERIFY,
        Permission.DASHBOARD_VIEW,
    },
    Role.VIEWER: {
        Permission.EVENT_READ,
        Permission.CAPA_READ,
        Permission.DASHBOARD_VIEW,
    },
}


def role_has_permission(role: Role, permission: Permission) -> bool:
    """Return whether ``role`` is granted ``permission``."""
    return permission in ROLE_PERMISSIONS.get(role, set())


def _coerce_role(raw_role: str) -> Role | None:
    """Map a stored role string to a :class:`Role`, or ``None`` if unknown."""
    try:
        return Role(raw_role)
    except ValueError:
        return None


def require_permission(permission: Permission):
    """Build a dependency that requires ``permission`` of the current user.

    Returns the authenticated :class:`User` on success; raises 403 otherwise.
    """

    async def checker(current_user: User = Depends(get_current_user)) -> User:
        role = _coerce_role(current_user.role)
        if role is None or not role_has_permission(role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions for this action",
            )
        return current_user

    return checker
