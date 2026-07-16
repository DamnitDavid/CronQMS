"""Server-rendered admin UI for managing roles and their permission grants.

Sits beside the other Settings tabs (gated by ``SETTINGS_MANAGE``) and follows
the Post/Redirect/Get style of ``admin_pages.py``. Admins can create custom
roles and toggle which permissions each role grants; the two seeded system roles
are protected:

* **Admin** — fully locked (name, permissions, existence).
* **User** — cannot be renamed or deleted, but its permission set is editable
  (it is the basic default handed to new users).
"""

import os
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.permissions import Permission, permission_catalog, require_permission
from app.database import get_db
from app.models import RoleDefinition, RolePermission, User
from app.services.rbac import ADMIN_ROLE_NAME, USER_ROLE_NAME

router = APIRouter(tags=["Admin"])

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

VALID_PERMISSIONS = {p.value for p in Permission}


def _roles_redirect(error: Optional[str] = None) -> RedirectResponse:
    url = "/admin/settings/roles"
    if error:
        url += f"?error={quote(error)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _role_in_org(db: Session, role_id: int, organization_id: int) -> Optional[RoleDefinition]:
    return (
        db.query(RoleDefinition)
        .filter(
            RoleDefinition.id == role_id,
            RoleDefinition.organization_id == organization_id,
        )
        .first()
    )


def _org_roles(db: Session, organization_id: int) -> list[RoleDefinition]:
    # System roles first (Admin, User), then custom roles alphabetically.
    return (
        db.query(RoleDefinition)
        .filter(RoleDefinition.organization_id == organization_id)
        .order_by(RoleDefinition.is_system.desc(), RoleDefinition.name.asc())
        .all()
    )


def _clean_permissions(submitted: list[str]) -> list[str]:
    """Keep only recognized permission values, de-duplicated."""
    return sorted({p for p in submitted if p in VALID_PERMISSIONS})


def _user_count_with_role(db: Session, organization_id: int, name: str) -> int:
    return (
        db.query(User)
        .filter(User.organization_id == organization_id, User.role == name)
        .count()
    )


@router.get("/admin/settings/roles")
async def roles_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
    saved: Optional[str] = None,
):
    roles = _org_roles(db, current_user.organization_id)
    counts = {
        r.id: _user_count_with_role(db, current_user.organization_id, r.name)
        for r in roles
    }
    return templates.TemplateResponse(
        "admin/settings/roles/list.html",
        {
            "request": request,
            "current_user": current_user,
            "roles": roles,
            "user_counts": counts,
            "error": error,
            "saved": saved,
        },
    )


@router.get("/admin/settings/roles/new")
async def role_new_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    error: Optional[str] = None,
):
    return templates.TemplateResponse(
        "admin/settings/roles/form.html",
        {
            "request": request,
            "current_user": current_user,
            "role": None,
            "granted": set(),
            "catalog": permission_catalog(),
            "name_locked": False,
            "permissions_locked": False,
            "error": error,
        },
    )


@router.post("/admin/settings/roles")
async def role_create(
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    name: str = Form(...),
    description: str = Form(""),
    permissions: list[str] = Form(default=[]),
):
    name = name.strip()
    if not name:
        return _roles_redirect("Role name is required.")
    existing = (
        db.query(RoleDefinition)
        .filter(
            RoleDefinition.organization_id == current_user.organization_id,
            RoleDefinition.name == name,
        )
        .first()
    )
    if existing is not None:
        return _roles_redirect(f"A role named “{name}” already exists.")

    role = RoleDefinition(
        organization_id=current_user.organization_id,
        name=name,
        description=description.strip() or None,
        is_system=False,
    )
    db.add(role)
    db.flush()
    for perm in _clean_permissions(permissions):
        db.add(RolePermission(role_id=role.id, permission=perm))
    db.commit()
    return RedirectResponse(
        "/admin/settings/roles?saved=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/admin/settings/roles/{role_id}")
async def role_edit_page(
    role_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    role = _role_in_org(db, role_id, current_user.organization_id)
    if role is None:
        return _roles_redirect("Role not found.")
    is_admin_role = role.is_system and role.name == ADMIN_ROLE_NAME
    is_user_role = role.is_system and role.name == USER_ROLE_NAME
    return templates.TemplateResponse(
        "admin/settings/roles/form.html",
        {
            "request": request,
            "current_user": current_user,
            "role": role,
            "granted": {rp.permission for rp in role.permissions},
            "catalog": permission_catalog(),
            # Admin: everything locked. User: name locked, perms editable.
            "name_locked": role.is_system,
            "permissions_locked": is_admin_role,
            "error": error,
        },
    )


@router.post("/admin/settings/roles/{role_id}")
async def role_update(
    role_id: int,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
    name: str = Form(...),
    description: str = Form(""),
    permissions: list[str] = Form(default=[]),
):
    role = _role_in_org(db, role_id, current_user.organization_id)
    if role is None:
        return _roles_redirect("Role not found.")

    is_admin_role = role.is_system and role.name == ADMIN_ROLE_NAME
    if is_admin_role:
        return _roles_redirect("The Admin role cannot be modified.")

    # Rename (custom roles only); system roles keep their name.
    new_name = name.strip()
    if not role.is_system and new_name and new_name != role.name:
        clash = (
            db.query(RoleDefinition)
            .filter(
                RoleDefinition.organization_id == current_user.organization_id,
                RoleDefinition.name == new_name,
                RoleDefinition.id != role.id,
            )
            .first()
        )
        if clash is not None:
            return _roles_redirect(f"A role named “{new_name}” already exists.")
        old_name = role.name
        role.name = new_name
        # Cascade the rename to users carrying the old role string.
        db.execute(
            text(
                "UPDATE users SET role = :new "
                "WHERE organization_id = :org AND role = :old"
            ),
            {"new": new_name, "org": current_user.organization_id, "old": old_name},
        )

    role.description = description.strip() or None

    # Replace the permission set: clear existing grants, add the submitted ones.
    role.permissions.clear()
    db.flush()
    for perm in _clean_permissions(permissions):
        db.add(RolePermission(role_id=role.id, permission=perm))

    db.add(role)
    db.commit()
    return RedirectResponse(
        "/admin/settings/roles?saved=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/settings/roles/{role_id}/delete")
async def role_delete(
    role_id: int,
    current_user: User = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
):
    role = _role_in_org(db, role_id, current_user.organization_id)
    if role is None:
        return _roles_redirect("Role not found.")
    if role.is_system:
        return _roles_redirect("System roles cannot be deleted.")
    in_use = _user_count_with_role(db, current_user.organization_id, role.name)
    if in_use:
        return _roles_redirect(
            f"“{role.name}” is assigned to {in_use} user(s). Reassign them before deleting."
        )
    db.delete(role)
    db.commit()
    return _roles_redirect()
