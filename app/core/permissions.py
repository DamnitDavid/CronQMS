"""Role-based permissions.

A single mapping (:data:`ROLE_PERMISSIONS`) defines what each :class:`Role`
may do. Routes declare the permission they need via :func:`require_permission`,
replacing ad-hoc ``role != "Admin"`` string checks scattered across handlers.
"""

from enum import Enum

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.database import get_db
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
    DOCUMENT_CREATE = "document:create"
    DOCUMENT_READ = "document:read"
    DOCUMENT_UPDATE = "document:update"
    DOCUMENT_REVIEW = "document:review"
    DOCUMENT_APPROVE = "document:approve"
    DOCUMENT_OBSOLETE = "document:obsolete"
    DOCUMENT_DELETE = "document:delete"
    ALERT_CREATE = "alert:create"
    ALERT_READ = "alert:read"
    ALERT_ACKNOWLEDGE = "alert:acknowledge"
    ALERT_CLOSE = "alert:close"
    USER_MANAGE = "user:manage"
    SETTINGS_MANAGE = "settings:manage"
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
        Permission.DOCUMENT_CREATE,
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_UPDATE,
        Permission.DOCUMENT_REVIEW,
        Permission.DOCUMENT_APPROVE,
        Permission.DOCUMENT_OBSOLETE,
        Permission.DOCUMENT_DELETE,
        Permission.ALERT_CREATE,
        Permission.ALERT_READ,
        Permission.ALERT_ACKNOWLEDGE,
        Permission.ALERT_CLOSE,
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
        Permission.DOCUMENT_CREATE,
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_UPDATE,
        Permission.ALERT_CREATE,
        Permission.ALERT_READ,
        Permission.ALERT_ACKNOWLEDGE,
        Permission.DASHBOARD_VIEW,
    },
    Role.APPROVER: {
        Permission.EVENT_READ,
        Permission.EVENT_CHANGE_STATUS,
        Permission.EVENT_APPROVE_CLOSURE,
        Permission.EVENT_COMMENT,
        Permission.CAPA_READ,
        Permission.CAPA_VERIFY,
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_REVIEW,
        Permission.DOCUMENT_APPROVE,
        Permission.DOCUMENT_OBSOLETE,
        Permission.ALERT_READ,
        Permission.ALERT_ACKNOWLEDGE,
        Permission.DASHBOARD_VIEW,
    },
    Role.VIEWER: {
        Permission.EVENT_READ,
        Permission.CAPA_READ,
        Permission.DOCUMENT_READ,
        Permission.ALERT_READ,
        Permission.DASHBOARD_VIEW,
    },
}


# Human-readable labels + grouping for the roles-management UI. The prefix
# before ``:`` names the resource group; the map supplies friendly labels.
PERMISSION_LABELS: dict[Permission, str] = {
    Permission.EVENT_CREATE: "Create events",
    Permission.EVENT_READ: "View events",
    Permission.EVENT_UPDATE: "Edit events",
    Permission.EVENT_DELETE: "Delete events",
    Permission.EVENT_CHANGE_STATUS: "Change event status",
    Permission.EVENT_APPROVE_CLOSURE: "Approve event closure",
    Permission.EVENT_REOPEN: "Reopen events",
    Permission.EVENT_COMMENT: "Comment on events",
    Permission.CAPA_CREATE: "Create CAPAs",
    Permission.CAPA_READ: "View CAPAs",
    Permission.CAPA_UPDATE: "Edit CAPAs",
    Permission.CAPA_VERIFY: "Verify CAPAs",
    Permission.DOCUMENT_CREATE: "Create documents",
    Permission.DOCUMENT_READ: "View documents",
    Permission.DOCUMENT_UPDATE: "Edit documents & upload revisions",
    Permission.DOCUMENT_REVIEW: "Review documents",
    Permission.DOCUMENT_APPROVE: "Approve documents",
    Permission.DOCUMENT_OBSOLETE: "Obsolete documents",
    Permission.DOCUMENT_DELETE: "Delete documents",
    Permission.ALERT_CREATE: "Create alerts",
    Permission.ALERT_READ: "View alerts & inbox",
    Permission.ALERT_ACKNOWLEDGE: "Acknowledge alerts",
    Permission.ALERT_CLOSE: "Close alerts",
    Permission.USER_MANAGE: "Manage users",
    Permission.SETTINGS_MANAGE: "Manage settings & roles",
    Permission.DASHBOARD_VIEW: "View dashboard & reports",
}

_GROUP_TITLES = {
    "event": "Events",
    "capa": "CAPA",
    "document": "Documents",
    "alert": "Alerts",
    "user": "Users",
    "settings": "Settings",
    "dashboard": "Dashboard",
}


def permission_catalog() -> list[tuple[str, list[tuple[str, str]]]]:
    """Return the permission catalog grouped by resource for the roles form.

    Each entry is ``(group_title, [(permission_value, label), ...])`` in enum
    order, so the roles UI can render grouped checkboxes without hard-coding the
    permission list.
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    for perm in Permission:
        prefix = perm.value.split(":", 1)[0]
        groups.setdefault(prefix, []).append(
            (perm.value, PERMISSION_LABELS.get(perm, perm.value))
        )
    return [
        (_GROUP_TITLES.get(prefix, prefix.title()), items)
        for prefix, items in groups.items()
    ]


def role_has_permission(role: Role, permission: Permission) -> bool:
    """Return whether the legacy ``role`` statically grants ``permission``.

    Retained as the fallback grant source (see :func:`_resolve_from_db`) for
    databases that were never seeded with role rows — notably the test suite,
    which builds tables via ``create_all`` and creates legacy-role users
    directly. In a migrated database the ``roles`` rows are authoritative.
    """
    return permission in ROLE_PERMISSIONS.get(role, set())


def _coerce_role(raw_role: str) -> Role | None:
    """Map a stored role string to a :class:`Role`, or ``None`` if unknown."""
    try:
        return Role(raw_role)
    except ValueError:
        return None


def _resolve_from_db(db: Session, user: User) -> set[Permission]:
    """Resolve a user's granted permissions from the ``roles`` tables.

    Resolution order:

    1. **Admin fast-path** — the Admin role always grants every permission, so
       newly added permissions apply without a data change (matching the old
       ``ROLE_PERMISSIONS[Role.ADMIN] = set(Permission)``).
    2. **Org-scoped role row** — look up the user's role by ``(organization_id,
       name)``; its ``role_permissions`` are authoritative.
    3. **Legacy fallback** — if no role row exists (an un-seeded database) but
       the name matches a legacy :class:`Role`, use the static grant map so
       behavior is unchanged before the seeding migration runs.

    Anything else (unknown role, org-less user) fails closed with no grants.
    """
    if user.role == Role.ADMIN.value:
        return set(Permission)

    # Imported here to avoid importing the ORM model at module load (keeps the
    # core import graph lean and mirrors other lazy model imports).
    from app.models.role import RoleDefinition

    if user.organization_id is not None:
        role_row = (
            db.query(RoleDefinition)
            .filter(
                RoleDefinition.organization_id == user.organization_id,
                RoleDefinition.name == user.role,
            )
            .first()
        )
        if role_row is not None:
            granted = {rp.permission for rp in role_row.permissions}
            return {p for p in Permission if p.value in granted}

    legacy = _coerce_role(user.role)
    if legacy is not None:
        return set(ROLE_PERMISSIONS.get(legacy, set()))
    return set()


def granted_permissions(db: Session, user: User) -> set[Permission]:
    """Return the user's granted permission set, cached for the request.

    ``get_current_user`` hands the same :class:`User` instance to every
    dependency in a request, so caching on the instance means at most one roles
    query per request (zero for Admin).
    """
    cached = getattr(user, "_granted_permissions", None)
    if cached is not None:
        return cached
    perms = _resolve_from_db(db, user)
    user._granted_permissions = perms
    return perms


def user_has_permission(db: Session, user: User, permission: Permission) -> bool:
    """Whether ``user`` is granted ``permission`` (DB-resolved, cached)."""
    return permission in granted_permissions(db, user)


def require_permission(permission: Permission):
    """Build a dependency that requires ``permission`` of the current user.

    Returns the authenticated :class:`User` on success; raises 403 otherwise.
    Grants are resolved from the database (with the legacy fallback); the public
    signature is unchanged so existing ``Depends(require_permission(...))`` call
    sites are untouched.
    """

    async def checker(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not user_has_permission(db, current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions for this action",
            )
        return current_user

    return checker
